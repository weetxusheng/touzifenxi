# Websearch LLM 重试、轮询与补齐机制说明

本文单独说明 `websearch` skill 里大模型在各步骤中的调用、重试、provider 轮询、checkpoint 续跑和失败诊断逻辑。它面向后续继续扩展 C114、InfoQ 以及更多网站时使用。

当前代码里仍有部分模块路径保留 `c114` 命名，这是历史目录名；本文描述的是现在 `skills/websearch` 内实际复用的 LLM 机制。

## 1. 总体分层

LLM 相关稳定性不是一层重试，而是分成五层：

| 层级 | 解决的问题 | 典型日志状态 | 主要位置 |
| --- | --- | --- | --- |
| 请求级重试 | 网络错误、超时、HTTP 429/529/5xx、provider 基础设施异常 | `started`、`error`、`success` | `llm_runtime/client.py` |
| provider 轮询 | 同一步骤内多个 provider 按顺序分摊任务，并在 infra 错误时换下一个 provider | `provider` 字段变化 | `llm_runtime/client.py` |
| JSON 解析重试 | 模型返回了文本，但不是合法 JSON | `parse_error` | `StructuredChatClient.complete_json` |
| 后处理重试 | JSON 合法，但本地结构校验不通过，比如缺字段、漏 item、枚举值非法 | `postprocess_error` | `complete_json_with_postprocess_retry` |
| 业务级补齐 | 某个 topic、batch、article 已失败，但其它成功结果要保留，后续只补失败单元 | checkpoint 状态 | `StepCheckpointStore` 及各 step |

排障时不要只看 `logs/*.jsonl`，也要看 `checkpoints/*.json`：

- `logs/*.jsonl` 回答“这次请求为什么失败、发了什么、回了什么”。
- `checkpoints/*.json` 回答“哪些业务单元已经成功，哪些还需要补跑”。

## 2. Provider 配置与默认策略

运行配置来自：

- `skills/websearch/config/runtime.example.json`
- `skills/websearch/config/runtime.local.json`

默认 provider 链路是：

1. `kimi-code`
2. `kimi`
3. `minimax`

默认重试分类是：

| retry class | 默认总尝试次数 | 含义 |
| --- | ---: | --- |
| `infra` | 3 | 网络、超时、限流、服务端错误等基础设施问题 |
| `parse` | 3 | 返回内容不能解析成 JSON |
| `postprocess` | 3 | JSON 能解析，但业务结构不合规 |
| `fatal` | 2 | 被判定为非基础设施类的 provider 错误 |

默认补充策略：

- `failover.enabled=true`。
- 连续 `3` 次 infra 失败后，全局 active provider 可以切到下一个 provider。
- `step_5` 默认最小请求间隔 `40s`，避免高频请求造成 provider 过载。
- `step_5 / step_6 / step_7` 默认启用 streaming 接收。
- `step_3 / step_5` 默认启用 step task routing。

## 3. Provider 轮询逻辑

### 3.1 非路由步骤

没有配置 `step_task_routing` 的步骤，会先使用当前 active provider。

如果 provider 请求出现 infra 类错误：

1. 单个 provider 内按 retry classifier 做请求级重试。
2. 如果仍失败，记录失败次数。
3. 当连续失败次数达到 `failover.consecutive_failures`，切到下一个 provider。
4. 每个 step 开始时会调用 `begin_step(step_name)`，provider index、连续失败计数会重置。

### 3.2 路由步骤

配置了 `step_task_routing` 的步骤，目前默认包括：

- `step_3`
- `step_5`

路由逻辑是 round-robin，不是随机：

- 假设配置顺序是 `kimi-code -> minimax -> kimi`。
- 第 1 个请求从 `kimi-code` 开始；如果 infra 失败，再试 `minimax`，再试 `kimi`。
- 第 2 个请求从 `minimax` 开始；如果 infra 失败，再试 `kimi`，再试 `kimi-code`。
- 第 3 个请求从 `kimi` 开始；如果 infra 失败，再试 `kimi-code`，再试 `minimax`。

所以日志里看到同一个 step 不同请求打到不同 provider，是预期行为。它的目的有两个：

- 把大量 topic/batch 请求分散到多个 provider。
- 某个 provider 短时 429/529/5xx 时，不让整步卡死在一个 provider 上。

注意：路由步骤主要依赖本次请求链路里的 provider 轮询；全局连续失败 failover 对它不是主要机制。

## 4. LLM 请求日志状态

LLM 日志位于每次运行目录的 `logs/` 下，按 source 和 step 拆分，例如：

- `c114_llm_trace_step_5_YYYYMMDD.jsonl`
- `infoq_llm_trace_step_5_YYYYMMDD.jsonl`

状态含义如下：

| status | 含义 |
| --- | --- |
| `started` | 请求已经发出，尚未拿到终态 |
| `success` | provider 已成功返回文本 |
| `error` | provider 请求失败，包括网络、限流、HTTP 错误等 |
| `parse_error` | provider 返回了文本，但无法解析成 JSON |
| `postprocess_error` | JSON 已解析，但本地结构校验失败 |
| `aborted` | 进程退出时仍有未收尾请求，系统补记为中断 |

`success` 只代表 provider 返回成功，不等于业务单元一定成功。比如模型返回了 JSON，但漏掉某个 `original_title`，后续会记录 `postprocess_error`，对应 checkpoint entry 也不会变成 `success`。

## 5. Checkpoint 续跑规则

每个多次调用步骤都有单步累计 checkpoint：

- `checkpoints/<source>_step_X_checkpoint_YYYYMMDD.json`

统一规则：

- 只有 `status=success` 的 entry 会参与最终 YAML/Markdown 渲染。
- 重跑某一步时，程序会先读 checkpoint。
- 已成功 entry 会跳过，不重复请求。
- 非成功 entry，包括 `pending / error / parse_error / postprocess_error / aborted`，会在该步重跑时继续补。
- 如果最终产物丢失，但 checkpoint 已完整，理论上可以从 checkpoint 重建最终产物，不需要重新打外部接口或模型。

## 6. 各 Step 的 LLM 使用方式

### Step 1：单篇文章分析

用途：

- 把采集到的文章转成结构化分析字段。
- 为后续 topic grouping 和搜索关键词生成提供基础材料。

当前机制：

- 逐篇文章调用模型。
- 请求级、解析级、后处理级重试都走统一 LLM client。
- 具体成功项会进入后续 step 产物；如果这一层失败，后续 topic grouping 无法完整执行。

后续扩站点时要注意：

- 不同网站只应改变文章输入字段和 source adapter。
- 单篇分析输出结构应保持站点无关，避免后续步骤依赖网站私有字段。

### Step 1.5：文章 topic 分类

用途：

- 对当天该 source 的全部文章做一次主题归并。
- 这是现在控制 topic 数量和质量的关键步骤。

调用方式：

- 一次性把当天全部文章的精简结构发给模型。
- 输入包含 `source_site`，用于让 agent 理解不同网站的分类口径。
- 不发送全文，主要发送标题、来源桶、频道、发布时间、实体、信号、关键词、摘要。

重试与补齐：

- JSON 解析错误走 `parse` 重试。
- 结构校验错误走 `postprocess` 重试。
- 成功后写入一个整体 checkpoint entry。
- 如果模型漏文章、topic id 对不上、缺 `items`，会视为 `postprocess_error`。

当前边界：

- 这是整批分类，不是每篇文章单独补。
- 如果整批失败，重跑时会重新做整批 topic grouping。

### Step 2：搜索关键词生成

用途：

- 为每篇文章生成 2 组搜索关键词。
- 后续 step 3 会用标题和关键词去搜索外部补充信息。

调用方式：

- 先按 topic 批量发送。
- 每个 topic 下多篇文章一起生成关键词。
- 模型必须按输入 `original_title` 返回每篇文章的关键词。

补齐方式：

- 已有 checkpoint 成功结果会先回填。
- topic 批量返回里如果漏掉某些标题，会对缺失文章走单篇 fallback。
- 单篇 fallback 成功后，也会写入同一个 step 2 checkpoint。
- 如果 topic 批量整体失败，会把该 topic 下相关文章记录为错误，并抛出当前错误。

注意：

- Step 2 的最小 checkpoint 单元是单篇文章关键词。
- 所以重跑 step 2 时，已经成功的文章不会重复请求，只会补还没成功的文章。

### Step 3：搜索与 AI Review

Step 3 有两类工作：外部搜索和 LLM 审查。

#### 外部搜索

用途：

- 对每篇文章的标题和关键词发起搜索。
- 搜索结果排序、去重、抓取摘要或正文片段。

这部分不是 LLM 调用，不受 LLM provider 轮询控制。

checkpoint 单元：

- `query::<topic>::<original_title>::<query_type>::<query>`
- `article::<topic>::<original_title>`

已成功 query 或 article 会在重跑时跳过。

#### AI Review

用途：

- 审查搜索结果是否值得保留。
- 输出 `keep_level=strong|weak|drop`、原因、相关性说明和价值类型。

调用方式：

- 按 topic 拆 batch。
- 单个 batch 默认最多 4 篇文章。
- Step 3 默认启用 provider round-robin。

补齐方式：

- 如果整篇文章的 review checkpoint 已成功，直接复用。
- 如果 batch 返回缺某些结果，会对缺失的单条搜索结果走 single fallback。
- batch 成功、single fallback 成功、整篇 article review 成功都会写入同一个 step 3 checkpoint。

当前边界：

- batch 或 single fallback 最终失败时，会记录 checkpoint 错误并中止当前 step。
- 下次从 step 3 重跑，会跳过已成功的 query/review，只补缺失或失败的部分。

### Step 4：正文抓取

用途：

- 抓原文和补充链接正文。

LLM 使用：

- Step 4 本身不调用 LLM。
- 它使用 checkpoint 记录每个 URL 的抓取状态。

checkpoint 单元：

- `url::<normalized_url>`

重跑规则：

- 已成功 URL 直接复用。
- 失败 URL 下次继续抓。
- 最终 step 4 YAML 只从成功抓取或明确 fallback 的内容组装。

### Step 5：正文分析

用途：

- 基于原文正文和补充正文，为每篇文章生成摘要、核心观点、事实增量、风险、不确定性等字段。

默认调用方式：

- 默认 `content.analysis_mode=per_topic`。
- 按 topic 发送，不是一篇一篇发送。
- 如果某个 topic 文章过多，会按固定篇数拆成 topic batch。
- 当前单 batch 默认最多 4 篇文章。
- Step 5 默认启用 provider round-robin。
- Step 5 默认启用 streaming。
- Step 5 默认最小请求间隔 40 秒。

重试与补齐：

- provider 层 infra 错误走请求级重试和 provider 轮询。
- JSON 解析错误走 `parse` 重试。
- JSON 合法但漏 `summary/core_points`、漏 `original_title`、标题匹配不上等，走 `postprocess` 重试。
- Step 5 还有一层业务级 batch 补齐：`analysis_batch_retry_attempts` 默认 3 次。
- 每次业务级失败都会把该 batch 写入 checkpoint，状态为 `postprocess_error` 或 `error`。
- 成功后按 batch 写 checkpoint。

标题匹配兼容：

- 模型轻微改写标题中的空格、全半角、中英文标点时，本地会用归一化 title key 做兼容。
- 这属于结构兼容，不是代码兜底生成分析内容。
- 如果确实缺文章，仍然会报 `postprocess_error` 并重试。

重要口径：

- Step 5 是按 topic batch 发，不是默认一篇一篇发。
- 如果日志里看到单次请求内容很少，通常是因为某个 topic 被拆成较小 batch，或是在补某个失败 batch。

### Step 6：简报章节生成

用途：

- 基于 step 5 的结构化分析，为每个 topic 生成简报章节。

调用方式：

- 按 topic 调用模型。
- 每个 topic 生成四块内容：核心判断、增量信息、产业/公司影响、需要继续跟踪的点。
- Step 6 默认启用 streaming。

补齐方式：

- 每个 topic 有独立 checkpoint entry。
- 已成功 topic 直接复用。
- 未成功 topic 会继续调用模型。
- 当前 run 内会按配置的 `infra/postprocess` 尝试次数进行多轮补齐。
- 如果最终仍有 topic 未完成，会明确抛出 `step 6 仍有 N 个主题未完成`，不会生成假完整简报。

注意：

- Step 6 目前不是配置在 `step_task_routing` 里的默认路由步骤。
- 它主要依赖 provider 内部请求级重试、解析/后处理重试，以及 topic 级多轮补齐。

### Step 7：简报审查

用途：

- 对最终简报做质量审查。
- 输出问题、严重级别、证据和修改建议。

当前状态：

- 默认 `review.enable_step7=false`。
- 开启后按 topic 审查。

重试与补齐：

- 每个 topic 有独立 checkpoint entry。
- 已成功 topic 复用。
- 失败 topic 记录 `error` 或 `postprocess_error`。
- 请求级、解析级、后处理级重试复用统一 LLM client。

## 7. 常见问题定位方式

### 7.1 为什么有很多 `started`，但 `success` 少？

优先判断是否存在终态：

- 如果同一个 `request_id` 后面有 `success/error/parse_error/postprocess_error/aborted`，说明请求已收尾。
- 如果长期只有 `started`，通常是进程退出前没有正常收尾，或请求被挂住；退出时会补记 `aborted`。

然后看 checkpoint：

- 如果 checkpoint 对应 entry 已经 `success`，说明业务结果可用。
- 如果 checkpoint 还是 `postprocess_error/error`，说明 provider 可能返回了内容，但本地结构校验没通过。

### 7.2 为什么 provider 会突然换成 minimax？

可能原因：

- 当前 step 配了 `step_task_routing`，这是按请求轮询的正常行为。
- 前一个 provider 出现 infra 错误，本次请求链路自动尝试了下一个 provider。
- 非路由步骤累计 infra 失败达到阈值，触发了全局 failover。

### 7.3 `null` 是模型返回的吗？

要看日志字段：

- `response` 是原始记录下来的 provider 返回文本或本地序列化后的 payload。
- `response_text_present=false` 表示当前日志没有可记录的返回文本。
- `response_kind` 用来区分 provider 文本、空响应、网络错误、本地后处理错误等。

如果是 `postprocess_error`，通常不是 provider 接口直接失败，而是“provider 已返回，代码校验不通过”。

### 7.4 为什么重跑没有从头全部请求？

这是 checkpoint 设计决定的：

- `success` entry 会跳过。
- 只有缺失或非成功 entry 会补。
- 这样可以保留已经成功的搜索、审查、分析和简报章节，避免重复花费和重复触发限流。

## 8. 扩展新网站时的要求

后续新增网站时，LLM 机制应该继续保持公共化：

- 新 source 必须传入自己的 `source_site`，供 topic grouping agent 判断分类口径。
- LLM 日志、checkpoint、step 文件必须使用 source 前缀，不能写死 `c114`。
- 站点差异只放在采集、清洗、字段标准化。
- Step 1.5 之后的 topic grouping、关键词生成、搜索审查、正文分析、简报生成，应尽量复用同一套重试、轮询和 checkpoint 机制。
- 如果某个新网站需要特殊分类口径，优先通过 agent prompt 的 `source_site` 分支表达，不要在代码里写死分类规则。

## 9. 最小排障顺序

遇到某一步卡住或缺产物时，建议按下面顺序查：

1. 找到本次运行目录。
2. 看 `checkpoints/<source>_step_X_checkpoint_YYYYMMDD.json` 的 `status_summary`。
3. 如果有非成功 entry，看 entry 的 `error.message` 和 `request_context`。
4. 再打开对应 `logs/<source>_llm_trace_step_X_YYYYMMDD.jsonl`，按 `request_id` 查完整链路。
5. 判断属于 `infra / parse / postprocess / fatal` 哪一类。
6. 如果 checkpoint 已有成功项，不要删除它；重跑当前 step，让程序只补未成功项。

