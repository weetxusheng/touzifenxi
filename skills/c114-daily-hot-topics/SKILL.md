---
name: c114-daily-hot-topics
description: 当用户要求抓取、汇总或分析 C114 首页、量子信息、卫星互联网、数智低空或 Cloud&AI 当日热点，并希望得到标题、链接、关键词或可复用日常工作流时使用。
---

# C114 当日热点 Skill

## 适用场景

- 用户希望抓取 C114 首页或指定栏目当天可见文章
- 用户需要标题、链接、日期、关键词等结构化结果
- 用户希望基于默认栏目快速生成 CSV 结果
- 用户后续还要在此基础上继续做 AI 分析、搜索清单或简报

## 默认行为

- 默认栏目为：
  - `home`
  - `quantum`
  - `satellite`
  - `la`
  - `ai`
- 默认输出格式为 CSV
- 默认写入 skill 自带的 `output/`
- 只有当 `config/runtime.local.json` 中显式设置 `paths.output_mode = project` 时，才写入当前工作区的共享目录

## 输入语义

- `--date`
  - 目标日期，格式为 `YYYY-MM-DD`
- `--start-date` + `--end-date`
  - 日期区间，格式为 `YYYY-MM-DD`
  - 按闭区间逐天运行
- `--channels`
  - 手动指定栏目集合
- `--replace-query`
  - 用自然语言覆盖默认栏目
- `--add-query`
  - 用自然语言在当前栏目集合上追加栏目

栏目识别规则：

- 没有给出栏目要求时，使用默认栏目
- `只看...`、`仅看...`、`只抓...`：覆盖默认栏目
- `再加...`、`增加...`、`加上...`：在当前选择上追加
- 两者同时出现时，先覆盖，再追加

## 输出产物

- 文章明细 CSV
- 频道级热点词汇总
- 可选后续分析产物：
  - 搜索清单 YAML 模板
  - 热点简报
  - AI 增强字段

## 步骤总览

当前 C114 业务链路共定义 `7` 个步骤。

1. `Step 1：文章分析 CSV`
2. `Step 2：搜索清单 YAML`
3. `Step 3：搜索结果 YAML`
4. `Step 4：正文抓取 YAML`
5. `Step 5：正文分析 YAML`
6. `Step 6：行业研究员简报 Markdown`
7. `Step 7：简报审查 YAML`

## Agent 执行约束

- 使用本 skill 时，先抓取，再分析，再由 agent 自行补全搜索清单中的 `keywords`
- `c114-analyze` 生成的是 YAML 模板，不是最终可搜索清单
- 只有当 agent 按 `prompts/search-keyword-agent.md` 为每条标题补满 2 组 `keywords` 后，才可以进入 `c114-search`
- agent 在补全 `keywords` 前，必须先读取 YAML 中 `prompt_path` 指向的提示词文件
- agent 不允许跳过 `prompt_path`，也不允许自行另起一套提示词替代 skill 内提示词
- 不允许把 Python 规则结果、占位词、空数组直接当成最终 `keywords`
- 如果某条标题难以提炼，agent 也必须基于原标题做保守压缩，不允许跳过

## 规则边界约束

- 控制本 skill 流程的 agent 不得在执行过程中擅自修改任何中间规则、筛选口径、阈值、保留标准或步骤职责。
- 哪些规则由 Python 控制，哪些判断由 agent 控制，必须严格按本 skill 已定义的边界执行，不得临时“放宽”“收紧”或改口径。
- `Python（代码）` 只负责硬规则与确定性逻辑，例如：
  - 路径与文件生成
  - 日期过滤
  - 黑名单
  - 去重
  - step 顺序
  - `step 4` 只抓 `strong + weak`
- `Agent（提示词）` 只负责语义判断与研究输出，例如：
  - `keywords`
  - `keep_level`
  - 正文分析
  - 研究员简报
  - 审查报告
- 如果需要调整任何规则、字段、阈值、保留口径或步骤职责，必须先修改 skill 规范与实现，再执行；不允许在单次运行中临时改变。
- 若发现当前结果“不够宽”或“不够严”，也不得由控制流程的 agent 直接拍板修改，必须按既有规则继续执行，并把问题留给对应层：
  - 规则问题 -> 改 Python / skill 规范
  - 语义判断问题 -> 交给对应 agent 提示词

## Agent 执行步骤

1. 运行抓取，拿到原始文章结果
2. 运行 `c114-analyze`，生成搜索清单 YAML 模板
3. 读取 `prompts/search-keyword-agent.md`
4. 确认 `prompt_path` 与 skill 内提示词一致后，针对 YAML 中每条 `original_title` 自行生成 2 组 `keywords`
5. 将生成结果写回同一份 YAML
6. 确认所有条目都已补全后，再运行 `c114-search`
7. 读取 `prompts/search-review-agent.md`
8. 只填写 `selected_results` 下每条结果的 `ai_review`，判断补充链接是否应保留
9. 完成外链精筛后，再运行 `c114-fetch-content`，只抓 `keep_level = strong / weak` 的补充链接正文
10. 运行 `c114-analyze-content`，生成正文分析 YAML 模板、主题简报 Markdown 和分层问题 YAML
11. 读取 `prompts/content-analysis-agent.md`
12. 先查看 `c114_layer_issues_YYYYMMDD.yaml`，明确每一层当前暴露的问题，再填写正文分析结果与主题简报
13. 完成 `step 6` 后，再运行 `c114-review-brief`
14. 读取 `prompts/brief-review-agent.md`
15. 只生成 `step 7` 审查报告，不自动改写 `step 6`
16. 如果某一天在 `step 1` 后文章数为 `0`，流程必须停留在 `step 1`，不得继续生成该日的 `step 2` 到 `step 7`

## 日期区间约束

- 本 skill 支持：
  - `--date`
  - `--start-date` + `--end-date`
- `--date` 与 `--start-date/--end-date` 不允许混用
- 区间模式必须按天拆分运行，不生成跨多天混合 step 文件
- 区间模式的运行目录固定为：
  - `reports/c114_report/c114_range_YYYYMMDD_YYYYMMDD_<timestamp>/`
- 区间总目录下，每个日期必须有自己的子目录：
  - `YYYY-MM-DD/`
- 每天的 `step 1` 到 `step 7` 仍然按单日文件名生成
- 若某一天在 `Step 1` 分析后文章数为 `0`，该天只允许保留 `step 1` 文件，不生成后续 step 文件

## Search Checklist 契约

- 输入字段：
  - `original_title`
  - `channel`
  - `url`
  - `topic`
- 输出字段：
  - `keywords`
- `keywords` 约束：
  - 必须恰好 2 组
  - 必须由 agent 自行生成
  - 必须基于 `prompt_path` 指向的提示词文件生成
  - 必须贴近原标题
  - 不允许补充标题中没有明显依据的信息
  - 不允许只写行业大词或空泛主题词

## 搜索结果精筛契约

- `c114-search` 生成的是搜索结果与 AI 精筛模板，不会替 agent 自动判断链接价值
- agent 在补全 `ai_review` 前，必须先读取：
  - `prompts/search-review-agent.md`
- `ai_review` 只允许填写：
  - `status`
  - `keep_level`
  - `reason`
  - `relevance_note`
  - `value_type`
- `keep_level（保留等级）` 只允许填写：
  - `strong`
  - `weak`
  - `drop`
- agent 不得改写搜索结果元数据本身
- agent 必须优先过滤：
  - 主体明显跑偏的结果
  - 站点壳页、聚合页、低质量搬运页
  - 只同主题、不属同一事件、也没有新增事实的结果
- `step 4` 默认只抓 `strong + weak`，`drop` 不进入正文抓取

## 正文分析契约

- `c114-analyze-content` 生成的是正文分析模板，不会替 agent 填写分析结果
- agent 在填写正文分析前，必须先读取 YAML 中的 `prompt_path`
- agent 必须使用 `prompts/content-analysis-agent.md`，不得另起一套提示词
- `c114-analyze-content` 会同时生成 `c114_step_6_brief_YYYYMMDD.md`，作为 `资深研究员 agent` 按主题深挖的简报模板
- agent 填写简报前，必须先读取 `prompts/brief-agent.md`
- agent 填写正文分析前，必须先查看同目录下的 `c114_layer_issues_YYYYMMDD.yaml`
- `c114_layer_issues_YYYYMMDD.yaml` 用来提示每一层当前暴露的问题，便于持续优化，不得跳过
- 若某篇正文是 `html_fallback`、`filtered`、`failed` 或 `empty`，agent 必须在 `layer_notes` 中保留问题痕迹
- `step 6` 不再是短句总结，而是行业研究员口径的深度主题简报

## 简报审查契约

- `c114-review-brief` 默认只审 `step 6` 成品，但允许回看 `step 5/4` 做证据校验
- 审查官 agent 只做质检，不得直接改写 `step 6`
- agent 在填写 `step 7` 前，必须先读取：
  - `prompts/brief-review-agent.md`
- `step 7` 不只检查模板残留和链接分组，还必须审：
  - `核心判断` 是否有证据支撑
  - `增量信息` 是否真增量
  - `产业/公司影响` 是否过度外推
  - `需要继续跟踪的点` 是否空泛
- `step 7` 必须输出：
  - `overall_decision`
  - `summary`
  - `findings`
  - `strengths`
- `overall_decision` 只允许填写：
  - `pass`
  - `revise`
- 审查官必须给每条问题提供：
  - 问题描述
  - 证据
  - 可执行修改建议
- 如果 `step 6` 仍残留模板语句、链接分组错误或主题跑偏，`step 7` 必须明确指出

## 目录说明

- `agents/openai.yaml`
  - skill 的 agent 配置
- `config/channels.json`
  - 栏目配置、别名和默认栏目
- `config/runtime.example.json`
  - 运行配置模板
- `config/runtime.local.json`
  - 本地运行配置，不进入分发包
- `prompts/`
  - skill 私有提示词
- `scripts/c114.py`
  - skill 单一命令入口
  - 通过子命令运行 `step 1` 到 `step 7` 与配置检查
- `src/c114/`
  - skill 私有运行代码
- `output/`
  - 独立运行时的默认输出目录
- `SKILL.md`
  - C114 全链路需求总览、步骤定义、当前实现状态与后续跟进入口

## 总览文档约束

- 使用本 skill 时，agent 应先阅读：
  - `SKILL.md`
- 这份文档是 C114 链路的总状态入口，后续跟进默认以它为准
- 只要出现以下任一变化，就必须同步更新这份文档：
  - 步骤数量变化
  - step 文件名变化
  - 输入输出契约变化
  - prompt 职责变化
  - provider 路由变化
  - 运行目录规则变化
  - 已完成 / 未完成范围变化
- 不允许只改代码或只改 skill，而不更新总览文档
- 如果某次改动影响了 step 顺序、步骤职责或后续跟进方式，应优先更新总览文档，再继续后续实现

## 配置向导约束

- 配置文件路径固定为：
  - `config/runtime.local.json`
- 路径模式配置为：
  - `paths.output_mode = skill`
    - 默认值
    - 所有产物写入 skill 自带的 `output/`
  - `paths.output_mode = project`
    - 可选模式
    - 所有产物写入当前工作区的共享目录
- 当其它 agent 调用本 skill 时，应先检查配置是否完整：
  - 先运行 `c114-config-status`
  - 若存在缺项，再向用户收集配置
  - 收集后使用 `c114-config-apply` 写回 skill 配置
- 缺配置时才收集，不允许每次调用都打断用户
- Python 代码只读取配置文件，不负责配置问答或弹窗

## 分发约束

- `SKILL.md` 正文使用中文
- 一个 skill 只保留一个总览文档：`SKILL.md`
- 提示词、配置、脚本和私有运行代码必须保留在 skill 目录内部
- skill 文档中不得写死开发机绝对路径，应优先使用 skill 内相对路径描述
- 正式测试统一放在项目级 `tests/skills/c114/`，skill 目录内不放正式测试代码
- 打包前应先运行 skill 校验
- 分发 zip 不应包含 `__pycache__`、`.pyc`、临时输出或错误路径依赖

## 使用方式

执行入口：

```bash
python3 skills/c114-daily-hot-topics/scripts/c114.py c114-hot-topics --date YYYY-MM-DD
```

示例：

```bash
python3 skills/c114-daily-hot-topics/scripts/c114.py c114-hot-topics --date YYYY-MM-DD
```

## AI 增强约束

- 先运行抓取，再基于生成的 CSV 做 AI 分析
- AI 提示词必须留在本 skill 的 `prompts/` 中
- 未接入真实模型 provider 前，不应声称本 skill 已内建模型能力
- `c114-analyze` 产出的搜索清单只提供标题、主题、链接和 YAML 结构，不负责生成 `keywords`
- 搜索清单中的 `keywords` 必须由调用本 skill 的 agent 读取 `prompts/search-keyword-agent.md` 后自行生成
- agent 必须以 YAML 中的 `prompt_path` 为准，不得自行替换为其它提示词
- 每条标题只允许填写 2 组 `keywords`
- 两组 `keywords` 都必须贴近原标题，不允许用规则兜底结果替代 agent 判断
- 正文分析结果也必须由调用本 skill 的 agent 读取 `prompts/content-analysis-agent.md` 后自行生成
- 每次运行都要先查看 `c114_layer_issues_YYYYMMDD.yaml`，再继续进入正文分析或后续简报阶段
