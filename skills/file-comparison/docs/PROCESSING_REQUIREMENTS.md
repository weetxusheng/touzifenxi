# 文件对照处理逻辑需求说明

本文档记录 `file-comparison` skill 的业务处理逻辑。后续每次新增、修改或删除对照规则时，都应同步更新本文档，避免规则只存在于代码或口头沟通中。

## 目标

- 比较两份或多组基金合同、招募说明书等章节型 Word 文档。
- 输出适合汇报和人工复核的 Word 对照表，而不是机器式逐字符 diff。
- 在保证正文内容不丢失的前提下，减少无意义相同行、编号变化和签署页等噪音。
- 支持规则模式和 LLM 模式；LLM 模式下本地 fallback 仅作为诊断，不作为正式对照结果。

## 整体流程

1. 文件配对：按文件名月份识别旧版和新版。
2. 文本抽取：读取 `.doc/.docx` 文本和 Word 自动编号。
3. 章节切分：识别 `第X部分` 一级章节，以及 `一、 / 1、 / （1）` 等层级。
4. 前置规则清洗：剔除或裁剪不参与正文对比的内容。
5. 前置 diff：只保留发生变化的章节、条目和行。
6. LLM 批次识别：按章节批次发送给模型，默认每批 `chapter_batch_size = 4` 个一级章节；如果单批正文超过 `chapter_batch_char_limit = 10000` 字符，自动改为每批 `oversized_chapter_batch_size = 2` 个一级章节。
7. 模型结果解析：模型返回 compare unit 级变更判定，系统校验 JSON schema、修复可修复结构问题、记录失败。
8. 回退处理：模型不可用时优先使用 compare-units，再回退到原始规则 diff。
9. 后置清洗：过滤纯编号变化、完全一致小行、重复基金名称行。
10. Word 渲染：按对照表样式输出 `.docx/.doc`。
11. 过程落盘：保存请求、响应、解析、修复、checkpoint、状态和预处理摘要。

## 前置规则

### 章节与条目识别

- 一级章节以 `第X部分` 为主。
- 二级层级支持 `一、`。
- 条目层级支持 `1、`。
- 小项层级支持 `（1）` 和 `（一）`。
- Word 自动编号会被还原到对照内容中，避免阅读时看不出原编号。
- 自动编号恢复时，必须同时尊重 Word 的编号格式和起始值：
  - 例如 `numFmt = chineseCounting` 且 `w:start = 4` 时，应抽成 `四、/五、/六、`
  - 不允许把这类自动编号误重建成 `1、/2、/3、`
- `.docx` 若包含 Word 修订痕迹，文本抽取时应按“当前可见正文”处理：
  - `<w:ins>`（插入修订）视为正文，必须并入抽取结果。
  - `<w:del>`（删除修订）不回并到正文，避免把已删除内容误当成新版有效文本。
  - 普通正文 `<w:t>` 与 `<w:ins>` 中的文本按文档顺序拼接，效果等价于“接受插入、忽略删除”后看到的正文。
  - 该规则只针对 `.docx` XML 抽取；`.doc` 仍按 `textutil` 转文本结果为准。
- `（一）基金管理人简况`、`（二）基金管理人的权利与义务` 这类中文括号标题属于结构标题，必须作为二级上下文分隔保留。
- `（16）`、`（24）` 这类数字括号条款属于正文条款编号，默认不作为独立分隔层级，避免基金权利义务条款被切碎。
- 标题对齐时忽略行首编号，只用标题正文匹配；例如旧版 `（一）基金管理人简况` 与新版 `（二）基金管理人简况` 应识别为同一内容块。
- 最终展示时必须保留左右原始编号，不允许为了对齐而把新版标题强行改成旧版编号，或把旧版标题强行改成新版编号。
- 多行二级标题（例如 `一、基金管理人` + `（一）基金管理人简况`）如果已经出现在正文开头，渲染时不得再次补一遍，避免标题重复。
- 多行二级标题中各行编号都要参与归一化匹配；例如 `五、公开披露的基金信息 / （四）基金净值信息` 与 `五、公开披露的基金信息 / （二）基金净值信息` 应按同一小节对照展示，但左右列仍保留各自原编号。

### 签署页处理

- 配置字段：`compare.skip_section_patterns`。
- 默认关键词：
  - `签署页`
  - `签字页`
  - `盖章页`
  - `签章页`
- 如果关键词出现在章节标题中，整章跳过。
- 如果关键词出现在章节正文中，从命中行开始裁剪到该章节结尾。
- 签署页不会进入规则 diff、LLM 请求和最终对照表。
- 跳过或裁剪记录写入：
  - `extracted/old_sections.json`
  - `extracted/new_sections.json`
  - `extracted/preprocess_summary.json`
- 记录字段包括 `side`、`section_number`、`section_title`、`action`、`reason`、`matched_pattern`、`removed_line_count`。

### 前置 diff 压缩

- 完全相同章节不进入 LLM。
- 同一章节内只保留有变化的二级条目或小项。
- 同一个条目内，完全一致的小行默认不展示。
- 如果相同行位于两个变化块中间，会保留 `……` 作为省略标记，表示中间内容仍存在但无变化。
- 仅编号变化不展示，例如旧版 `（25）` 变成新版 `（24）`，但正文不变时跳过。
- 如果一个结构标题编号发生顺延但标题正文和块内正文完全不变，可以按“仅编号变化”过滤；如果块内正文也发生变化，则展示该块，并保留左右各自的原始结构标题。

## 模型处理

### 触发条件

- `llm_mode = responses` 时启用模型。
- `llm_mode = rule` 时只走本地规则。
- 模型输入不是整份文档，而是前置 diff 后的 compare units。
- 模型输出不是最终对照表内容，而是每个 compare unit 的变更判定结果；最终左右展示内容由程序回查原始 compare unit 后生成。
- `old_focus_text/new_focus_text` 只作为变化锚点和排查线索，禁止替代最终左右列正文；最终正文必须保留原始 compare unit 的上下文，再按明确规则剔除真实未变小行。
- 模型返回的 `chapter/subchapter` 只作辅助，最终章节名与二级标题优先使用本地 compare unit 的完整标题，避免模型把 `第二部分 释义` 截短成 `第二部分`。
- 模型返回的 `unchanged_lines` 只允许用于删除/新增项里的编号顺延清洗；普通 `replace/rewrite` 不允许用该字段裁剪结构性上下文。
- 如果模型把条目误判为 `delete_item/add_item`，但本地 compare unit 的左右两侧都有正文，则判定为 `postprocess_error`，记录错误并切换下一个 provider 重试；禁止代码静默兜底改写为正式结果。

### 模型输出结构

- 顶层字段为 `units`。
- 每个 unit 必须包含：
  - `unit_id`：对应输入 compare unit 的标识，程序用它回查原始旧文和新文。
  - `chapter`：所属一级章节。
  - `subchapter`：所属二级标题或条目标题。
  - `change_type`：真实变化类型，例如 `replace`、`rewrite`、`add_item`、`delete_item`、`numbering_only`。
  - `display_strategy`：展示策略，例如 `compare_changed_only`、`whole_replace`、`delete_old_only`、`add_new_only`、`skip`。
  - `numbering_only`：如果只是编号变化则为 `true`。
  - `unchanged_lines`：同一 unit 中实际未变的小行；编号上移时可不带原编号，程序会忽略行首编号比对。
  - `old_focus_text` / `new_focus_text`：真正变化的锚点短句；可为空，程序会回查原文。
  - `confidence`：模型对判定的置信度。
- 模型不得直接生成最终 Word 表格左右列内容。
- 对“删除一项导致后续编号上移”的场景，模型应返回 `change_type = delete_item`、`display_strategy = delete_old_only`，并把后续未变正文写入 `unchanged_lines`。
- 系统仍兼容旧版 `chapters/subsections` 解析，用于历史 run 和旧响应修复，但新请求 schema 只要求 `units`。

### 批次策略

- 默认每次发送 `chapter_batch_size = 4` 个一级章节。
- 单个 batch 的左右正文合计字符数超过 `chapter_batch_char_limit = 10000` 时，先按 `oversized_chapter_batch_size = 2` 个一级章节重新拆分。
- 如果 2 个一级章节仍超过阈值，继续拆到单章；单章自身超过阈值时不再继续切小，避免把同一章节内容切碎导致模型误判。
- 单个 batch 的 compare unit 数超过 `max_compare_units_per_batch = 4` 时，会按 unit 顺序继续拆分，避免模型需要一次性输出过多 subsection 而截断。
- compare unit 拆分只拆条目边界，不切碎同一个 unit 的正文。
- 单个 compare unit 的左右正文合计字符数超过 `max_compare_unit_chars = 10000` 时，会按行/段落边界拆成 `part-001 / part-002` 等多个 part。
- unit part 独立调用模型、独立落盘，并按原始 part 顺序合并；如果单行本身超过阈值，保留整行不做硬切字。
- 单个文件对内部默认并行 `execution.per_pair_max_workers = 2` 个 batch。
- batch 的最终结果按原始 batch 顺序合并，不按完成时间排序。

### Provider 路由

- 默认 provider 链：
  - `minimax`
  - `kimi-code`
  - `deepseek-ark`
- 默认开启 batch 首发轮转：
  - `batch-001 -> minimax`
  - `batch-002 -> kimi-code`
  - `batch-003 -> deepseek-ark`
- 单个 batch 内首发失败后切换下一个 provider。
- provider 全部失败后进入 fallback。

### 调用治理

- 每个 batch 有独立状态机和过程文件。
- 状态包括 `prepared`、`requesting`、`responded`、`normalized`、`parse_error`、`repaired`、`postprocess_error`、`fallback_succeeded`、`succeeded`、`failed`、`timeout`、`infra_error`。
- 基础设施错误、解析错误、后处理错误分别按配置重试。
- 任一 provider 出现网络失败、超时、HTTP 错误、JSON 解析失败或后处理失败后，会触发该 provider 的失败冷却。
- 失败冷却配置字段为 `failure_cooldown_seconds`，默认 `40` 秒；冷却只影响同一 provider 的下一次请求，不阻塞其它 provider。
- 可修复的 JSON/结构问题会进入 repair 流程。
- fatal 错误不重试。
- 正式对照文档只允许使用 `final_status.json` 指向的模型成功解析文件，例如 `parsed.attempt-02.kimi-code.json`，或成功修复后的结构化结果。
- 本地 `compare-units` / `rule` fallback 仅用于诊断排查，必须写入 `fallback.diagnostic.json`，不得标记为成功，不得进入正式 rows，不得生成正式 `comparison.docx`。
- 如果任一 batch 只有 fallback 诊断结果而没有模型可用结果，整个文件对必须失败，避免把本地推断内容误当作模型识别结论。

### 过程文件

每个 batch 目录保留：

- `batch_input.json`
- `request.*.json`
- `response.raw.*.json`
- `response.normalized.*.json`
- `parsed.*.json`
- `repair.*.json`
- `fallback.diagnostic.json`
- `timeline.json`
- `final_status.json`

其中 `timeline.json` 是 batch 调用治理的结构化主日志，必须记录每一次失败的 `status / provider / attempt / retry_class / duration_ms / error`；不再单独生成 `error.txt`，避免同一错误信息维护两份。`final_status.json` 必须记录最终采用的 `parsed_file`，恢复和重新生成文档按该字段读取结果。

## 后置规则

### 基金名称对照

- 对照表第一条固定为 `基金名称`。
- 旧版基金名称与新版基金名称不一致时，提升到最前面。
- 原章节中的重复基金名称行会移除，避免重复展示。

### 相同行过滤

- 一个条目内另起一行且左右完全一致时，两边都不展示。
- 适用于例如：
  - `设立日期`
  - `组织形式`
  - `注册资本`
  - `联系电话`
- 如果同一块中只有部分小项变化，未变化小项不展示。

### 编号变化过滤

- 仅 `1、2、3` 或 `（1）（2）（3）` 编号变化，正文未变化时不列入对照表。
- 删除某一项导致后续编号上移时，只展示真正删除的那一项。

### 大段改写平滑

- 如果某段大部分内容都已改写，中间夹着少量相同词，不再把这些短相同词保留下来。
- 夹在大段修改中的短相同片段会计入改动比例。
- 适用于例如 `基金`、`3个月`、`1000万元`、`中国证监会`、`生效` 等短公共词。
- 对同一句后半段大幅改写的场景，保留真实相同前缀，从大段改写开始整体划删除线或蓝色新增。

## Word 展示规则

### 表格结构

- 使用三列表格：
  - `章节`
  - `原版本内容`
  - `修订后版本内容`
- 第二级内容不单独作为一列，而是并回正文展示。
- 同一章节连续多行时，第一列合并单元格。
- 章节列宽较窄，适合两个字左右换行。
- 页面页边距较小，提升表格可读空间。

### 删除与新增样式

- 左侧旧文删除内容：红色删除线。
- 右侧新增或替换内容：蓝色加粗。
- 短新增可加下划线。
- 长新增不加下划线，避免整段下划线影响阅读。

### 精细修改

- 小范围修改保留字级 diff。
- 例如基金名称中 `3个月` 改为 `6个月`，只标记 `3` 和 `6`。

### 高比例修改

- 标题类短文本超过 `50%` 改动时，标题正文整体删除/新增。
- `第四部分  `、`第五部分  ` 等章节编号前缀不划掉，只处理后面的真实标题。
- 正文超过 `90%` 改动时，旧文整段红色删除线，新文整段蓝色加粗。
- 被大段修改包住的短相同词会纳入改动比例，避免噪音。

## 输出与状态

### 输出文件

- 输出文件名使用：
  - `<前文件名> 与 <后文件名> 对照表 <YYYYMMDD_HHMMSS>`
- 产物文件名不再使用英文固定名 `comparison.docx/.doc`，避免下载后无法区分来源。
- 同一次重新生成不会覆盖已有文件。
- 离线重新生成正式文档时，必须复用 `final_status.json` 指向的成功解析结果：
  - 不重新发模型请求
  - 不把 fallback 诊断结果升级成正式文档
  - 若任一 batch 只有 fallback 诊断或缺少可复用解析结果，则重新生成必须失败并提示人工排查

### 页面状态

- 页面轮询只读轻量状态：
  - `status.json`
  - `task_checkpoint.json`
  - `pair.json`
- 页面不直接读取大模型完整 response，避免前端负担过重。
- 页面任务状态表支持展开文件对查看 batch 明细。
- 单个 batch 卡住、失败或结果明显异常时，页面可点击“重跑批次”。
- 重跑批次只清理目标 `llm/batch-xxx/`、对应 checkpoint entry 和旧输出文档；其它成功 batch 继续通过恢复机制复用。
- 若任务线程仍在运行，后端会拒绝并提示等待当前请求结束，避免同一任务并发写入互相覆盖。
- 单个文件对已经 `completed` 且不含 fallback 诊断时，页面可点击“重新生成 DOCX”。
- “重新生成 DOCX”只复用当前 pair 已有的成功 batch 解析结果重新写 Word，不重新请求模型；适用于：
  - 批次已全部完成，但之前文档生成失败
  - 想在不重跑模型的情况下重出正式 `.docx/.doc`

### 过程排查

- `extracted/preprocess_summary.json` 用于查看前置压缩、签署页裁剪、发送字符数。
- `extracted/compare_units.json` 用于查看实际送模型的差异单元。
- `extracted/batch_plan.json` 用于查看 batch 拆分。
- `llm/batch-xxx/timeline.json` 用于查看模型调用过程。
- `llm/batch-xxx/final_status.json` 用于查看 batch 最终状态。
- `llm/batch-xxx/parsed.attempt-xx.provider.json` 用于查看最终或历史模型解析结果；最终采用哪一份以 `final_status.json` 的 `parsed_file` 为准。

## 维护要求

- 每次新增或修改业务规则，必须同步更新本文档。
- 每次新增规则，必须补测试覆盖：
  - 规则输入输出测试
  - Word run 样式测试
  - 必要时补配置读取测试
- 每次修改模型调用治理，必须确认过程文件契约是否变化。
- 如果过程文件字段变化，必须同步更新本文档和 `config/README.md`。
