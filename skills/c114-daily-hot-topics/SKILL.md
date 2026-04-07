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

## Quick Start

1. 初始化本地配置模板：
  - `python scripts/c114.py c114-config-init`
2. 编辑：
  - `config/runtime.local.json`
3. 检查缺项：
  - `python scripts/c114.py c114-config-status`
4. 单日试跑：
  - `python scripts/c114.py c114-hot-topics --date 2026-04-07`
  - `python scripts/c114.py c114-analyze --date 2026-04-07`
5. 读取 `step 2` 顶部 `prompt_path`，为每条标题补满 2 组 `keywords`
6. 继续：
  - `python scripts/c114.py c114-search --date 2026-04-07`
7. 读取 `step 3` 顶部 `review_prompt_path`，为每条 `selected_results` 填完 `ai_review.review_status` 与 `ai_review.keep_level`
8. 继续：
  - `python scripts/c114.py c114-fetch-content --date 2026-04-07`
  - `python scripts/c114.py c114-analyze-content --date 2026-04-07`
  - `python scripts/c114.py c114-review-brief --date 2026-04-07`

## 安装后首次配置

- 首次安装后，不要直接开始抓取；应先检查运行配置是否完整。
- 先运行：
  - `python scripts/c114.py c114-config-init`
  - `python scripts/c114.py c114-config-status`
- `c114-config-init` 只负责从 `runtime.example.json` 生成本地 `runtime.local.json`
- `c114-config-status` 负责检查缺项，不会替你补配置
- 若希望只跑到下一个 agent 检查点，可运行：
  - `python scripts/c114.py run --date 2026-04-07`
- `run` 会先完成确定性步骤，并在遇到必须由 agent 填写的检查点时停住给出下一步提示
- 若缺少配置，应先补齐：
  - `config/runtime.local.json`
- 至少需要确认这些项：
  - 搜索 provider key 至少一个：
    - `keys.tavily_api_key`
    - `keys.metaso_api_key`
    - `keys.baidu_api_key`
  - `keys.aliyun_iqs_api_key`
  - `search.recent_days`
  - `search.max_external_results`
  - `paths.output_mode`
- 当前实现下，`step 1` 和 `step 2` 不依赖任何外部 API key；但从 `step 3` 开始，搜索侧至少需要一个可用 provider key：
  - `keys.tavily_api_key`
  - `keys.metaso_api_key`
  - `keys.baidu_api_key`
- 原因：
  - `step 3` 搜索可以在 `Tavily / Metaso / Baidu` 中使用任意一个已配置 provider
  - 若配置了 `Tavily`，`step 3` 会额外尝试补充 `extract_text`
  - 若未配置 `Tavily`，`step 3` 仍可执行，只是不做 `Tavily extract`
  - `step 4` 优先使用 `Aliyun IQS` 做正文提取
- 也就是说：
  - 只跑 `step 1-2` 时，可以先不配这些 key
  - 想跑 `step 3` 时，三种搜索 key 里至少配置一个即可
  - 想跑完整链路 `step 3-7` 时，还要补齐 `keys.aliyun_iqs_api_key`
- 其余运行参数中：
  - `search.recent_days`
  - `search.max_external_results`
  - `content.fetch_keep_levels`
  - `brief.role`
  也应视为基础配置，缺失时先补齐，再继续后续步骤
- 若安装后的机器处于公司网络、代理网络或 HTTPS 检查环境，还需要先确认网络环境：
  - `HTTP_PROXY`
  - `HTTPS_PROXY`
  - `ALL_PROXY`
  - `SSL_CERT_FILE`
- 当前 skill 没有单独的代理配置字段，默认读取系统环境变量；若目标机器存在 `ssl.SSLCertVerificationError: self-signed certificate in certificate chain`，应优先检查公司代理和 CA 证书配置，而不是直接改 skill 代码或关闭 SSL 校验。
- 只有在配置和网络环境确认完成后，才继续执行 `step 1` 到 `step 7`。

## 代理与 SSL 排障

- 若运行时出现：
  - `ssl.SSLCertVerificationError: self-signed certificate in certificate chain`
- 应先判断为：
  - 目标机器处于公司代理、HTTPS 检查网关或自签 CA 证书环境
  - 这通常不是 skill 代码 bug
- 当前 skill 的网络请求默认读取系统环境变量，不提供单独的代理开关；应优先配置：
  - `HTTP_PROXY`
  - `HTTPS_PROXY`
  - `ALL_PROXY`
  - `SSL_CERT_FILE`
- 推荐排查顺序：
  1. 确认机器是否必须走代理上网
  2. 若必须走代理，先设置 `HTTP_PROXY` / `HTTPS_PROXY`
  3. 若公司网络做了 HTTPS 检查，还要把公司 CA 证书路径写到 `SSL_CERT_FILE`
  4. 再重新运行 skill
- 示例：
  - `export HTTP_PROXY=http://127.0.0.1:7890`
  - `export HTTPS_PROXY=http://127.0.0.1:7890`
  - `export ALL_PROXY=socks5://127.0.0.1:7890`
  - `export SSL_CERT_FILE=/path/to/company-ca.pem`
- 注意：
  - `7890` 只是本地代理常见示例端口，不是 skill 固定要求
  - 应替换成目标机器真实可用的代理地址和端口
  - 代理端口是否冲突，取决于目标机器上代理软件本身，不是 skill 占用端口
- 不要直接：
  - 修改 skill 代码关闭 SSL 校验
  - 把这类错误误判为 C114 站点不可用
  - 在未确认代理和证书前反复重试所有步骤
- 若用户在别的机器上安装后报这类错，控制 skill 的 agent 应先提醒检查代理与证书环境，再继续后续步骤。

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

## 配置硬门槛


| 步骤             | 不配置也能跑    | 必须先配置                                                                                                               |
| -------------- | --------- | ------------------------------------------------------------------------------------------------------------------- |
| `Step 1：抓取`    | 可以        | 无                                                                                                                   |
| `Step 2：分析模板`  | 可以        | 无                                                                                                                   |
| `Step 3：搜索`    | 不可以       | 至少一个搜索 provider key：`keys.tavily_api_key` / `keys.metaso_api_key` / `keys.baidu_api_key`，以及 `search.recent_days`、`search.max_external_results` |
| `Step 4：正文抓取`  | 不建议       | `keys.aliyun_iqs_api_key`、`content.fetch_keep_levels`                                                               |
| `Step 5：正文分析`  | 不可以直接跳过前置 | 依赖 `step 4` 成功产物                                                                                                    |
| `Step 6：研究员简报` | 不可以直接跳过前置 | `brief.role`，以及已完成的 `step 5`                                                                                        |
| `Step 7：审查`    | 不可以直接跳过前置 | 已完成的 `step 6`                                                                                                       |


- 若你只是试装 skill，先跑 `step 1-2` 即可，不必先配全所有 key。
- 若你要开始跑 `step 3`，先确认三种搜索 key 至少有一个可用。
- 若你要跑完整流程，当前最稳的做法是先把上表中的必填项全部补齐。

## 步骤总览

当前 C114 业务链路共定义 `7` 个步骤。

1. `Step 1：文章分析 CSV`
2. `Step 2：搜索清单 YAML`
3. `Step 3：搜索结果 YAML`
4. `Step 4：正文抓取 YAML`
5. `Step 5：正文分析 YAML`
6. `Step 6：行业研究员简报 Markdown`
7. `Step 7：简报审查 YAML`

## 职责对照

### Python（代码）负责什么


| 层级       | Python（代码）职责                                                                           |
| -------- | -------------------------------------------------------------------------------------- |
| `Step 1` | 抓取 C114 原始文章、去重、写入原始 CSV / JSON                                                        |
| `Step 2` | 生成搜索清单 YAML 模板、写入 `prompt_path`、不生成最终 `keywords`                                       |
| `Step 3` | 调搜索 provider、做日期过滤/黑名单/去重/结果归一化、生成搜索结果 YAML、只在 `selected_results` 下保留 `ai_review` 占位字段 |
| `Step 4` | 检查 `step 3` 是否已完成 `ai_review`、未完成则直接拒绝执行、只抓 `keep_level = strong / weak` 的补充链接正文       |
| `Step 5` | 生成正文分析 YAML 模板、把原文正文和补充正文整理进去、不填写分析结论                                                  |
| `Step 6` | 生成行业研究员简报 Markdown 模板、不替代研究员写结论                                                        |
| `Step 7` | 生成审查 YAML 模板或承载结构、不替代审查官做内容判断                                                          |
| 通用职责     | 路径、文件命名、运行目录、配置读取、硬规则与阻断校验                                                             |


### Agent（提示词）负责什么


| Agent                    | 负责阶段               | Agent（提示词）职责                                                                                                              |
| ------------------------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `search-keyword-agent`   | `Step 2 -> Step 3` | 根据原标题补全 2 组 `keywords`                                                                                                    |
| `search-review-agent`    | `Step 3 -> Step 4` | 逐条填写 `selected_results[*].ai_review`，必须填写 `status`、`keep_level`、`reason`、`relevance_note`、`value_type`                    |
| `content-analysis-agent` | `Step 5`           | 填写正文分析字段，例如 `summary`、`core_points`、`new_facts`、`entities`、`signals`、`risk_or_uncertainty`、`why_it_matters`、`layer_notes` |
| `brief-agent`            | `Step 6`           | 以行业研究员口径写主题简报，重点输出 `核心判断`、`增量信息`、`产业/公司影响`、`需要继续跟踪的点`                                                                     |
| `brief-review-agent`     | `Step 7`           | 审查 `step 6` 的内容质量，输出问题、证据和修改建议                                                                                            |


### 一句话分工

- `Python` 负责：
  - 确定性工作
  - 硬规则
  - 阻断校验
  - 文件生成
- `Agent` 负责：
  - 语义判断
  - 研究分析
  - 内容写作
  - 内容审查
  - 且所有 `prompts/*.md` 对应步骤都必须由模型逐条思考完成，不允许用脚本、规则批量补全或模板化整批生成

## Agent 必填检查点

- `Step 2 -> Step 3` 之间：
  - agent 必须先读取 `prompt_path`
  - 为每条标题补满 2 组 `keywords`
- `Step 3 -> Step 4` 之间：
  - agent 必须先读取 `review_prompt_path`
  - 为每条 `selected_results` 补全：
    - `ai_review.review_status`
    - `ai_review.keep_level`
    - `ai_review.reason`
    - `ai_review.relevance_note`
    - `ai_review.value_type`
  - 其中 `keep_level` 只能是：
    - `strong`
    - `weak`
    - `drop`
  - 不允许用批量规则、域名映射表、脚本模板或“同域名统一判定”的方式直接批量补全 `keep_level`
  - 必须逐条阅读该结果的 `result_title`、`snippet`、`matched_terms`，再做语义判断
  - 如果多条结果的 `reason` 或 `relevance_note` 明显是同一句模板复用，应视为本步骤未认真完成
- `Step 4 -> Step 5` 之间：
  - agent 必须按 `content-analysis-agent` 提示词填写正文分析
  - `summary`、`core_points`、`new_facts`、`entities`、`signals`、`risk_or_uncertainty`、`why_it_matters`、`layer_notes` 必须全部补齐
  - 若 `step 5` 仍有缺失字段，Python 不得继续生成 `step 6`，而必须提示 agent 先按 `content-analysis-agent.md` 补完后再继续
- `Step 5 -> Step 6` 之间：
  - agent 必须按 `brief-agent` 提示词写行业研究员简报
- `Step 6 -> Step 7` 之间：
  - agent 必须按 `brief-review-agent` 提示词生成审查报告

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
- 对所有需要 agent 思考的步骤，一律不允许：
  - 用脚本批量生成结论
  - 用域名映射、词表映射、正则拼接直接替代语义判断
  - 用统一模板句整批覆盖不同条目
  - 只做字段非空填充而不阅读对应内容

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
12. 先查看 `c114_layer_issues_YYYYMMDD.yaml`，明确每一层当前暴露的问题，再填写正文分析结果
13. 如果 `step 5` 仍有缺失字段，流程必须停在 `step 5`，不得继续生成 `step 6`
14. 补齐 `step 5` 后，再继续运行 `c114-analyze-content` 生成 `step 6`
15. 完成 `step 6` 后，再运行 `c114-review-brief`
16. 读取 `prompts/brief-review-agent.md`
17. 只生成 `step 7` 审查报告，不自动改写 `step 6`
18. 如果某一天在 `step 1` 后文章数为 `0`，流程必须停留在 `step 1`，不得继续生成该日的 `step 2` 到 `step 7`

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
- `step 3` 当前要求保持 skill 自己生成的 YAML 结构；虽然文件本身是 YAML，但 `step 4` 读取时依赖既定层级与字段位置。
- 因此在补 `ai_review` 时：
  - 只允许修改 `selected_results[*].ai_review`
  - 不允许用 `yaml.dump`、`safe_dump` 或其它整文件重写方式覆盖整个 `step 3`
  - 不允许改变 `selected_results` 的缩进层级、列表结构、字段顺序和其它元数据
- 若把整个 `step 3` 用通用 YAML 重写，文件语法可能仍然合法，但 `step 4` 可能读不到 `selected_results` 或 `ai_review`，导致流程中断。
- 若确实需要程序化回写，必须使用 skill 自己的渲染方式，而不是通用 YAML dump。
- `step 4` 默认只抓 `strong + weak`，`drop` 不进入正文抓取
- 只要 `selected_results` 中还有任何一条未完成 `ai_review.review_status=reviewed` 或 `ai_review.keep_level` 为空，`c114-fetch-content` 必须拒绝执行，不能静默跳过

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

## 已知限制

- 搜索结果仍受外部 provider 配额、限流和网络环境影响
- 某些站点正文抓取可能失败，尤其是视频页、聚合页或带强防爬限制的页面
- 公司代理网络若启用了 HTTPS 检查，必须先配置代理和证书环境；skill 不会替你关闭 SSL 校验
- `step 4` 之后的质量仍依赖 agent 是否按提示词补全 `keywords` 与 `keep_level`

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
- 若配置缺失，应先提醒用户需要补哪些 key 和运行参数，而不是直接尝试抓取
- 若用户运行环境存在代理或自签证书问题，应先提醒用户检查：
  - `HTTP_PROXY`
  - `HTTPS_PROXY`
  - `ALL_PROXY`
  - `SSL_CERT_FILE`
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
