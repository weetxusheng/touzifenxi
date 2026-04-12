---
name: c114-daily-hot-topics
description: 当用户需要抓取、搜索、分析并审查 C114 当日热点时使用。该 skill 现在由 Python 内置主备模型自动完成 Step 1.5、Step 2、Step 3、Step 5、Step 6、Step 7。
---

# C114 当日热点 Skill

## 适用场景

- 用户希望抓取 C114 首页或指定栏目当天可见文章
- 用户希望自动完成搜索关键词、外链精筛、正文分析、研究简报和审查
- 用户希望直接运行完整链路，而不是手工补中间 YAML
- 用户后续还要把这套链路迁移给别的机器或别的 agent 使用

## 默认行为

- 默认栏目：
  - `home`
  - `quantum`
  - `satellite`
  - `la`
  - `ai`
- 默认输出写入 skill 自带的 `output/`
- 只有当 `config/runtime.local.json` 显式设置 `paths.output_mode = project` 时，才写入当前工作区共享目录
- 默认执行模式是：
  - `execution.mode = builtin`
- `Step 1.5 / Step 2 / Step 3 / Step 5 / Step 6 / Step 7` 默认由 Python 内置主备模型自动完成
- 若切到：
  - `execution.mode = controller-agent`
  - 则 `Step 1.5 / Step 2 / Step 3 / Step 5 / Step 6 / Step 7` 改由当前控制 skill 的 agent 思考
  - Python 只负责模板、manifest、校验、落盘、下一步推进
- 默认推荐只使用一条命令直接跑完整链路：
  - `python scripts/c114.py run --date YYYY-MM-DD`
- 除非命中显式阻断条件，否则不应在 `Step 2` 到 `Step 7` 之间中途停下、请求确认、展示中间产物或要求人工补写
- 外部 agent 不得自行改写中间 YAML，不得自行调用其它模型 API 代替 skill 内置模型

## Quick Start

1. 初始化本地配置模板：
  - `python scripts/c114.py c114-config-init`
2. 编辑：
  - `config/runtime.local.json`
3. 检查缺项：
  - `python scripts/c114.py c114-config-status`
4. 至少确认：
  - 执行模式：
    - `execution.mode`
  - 搜索 provider key 至少一个：
    - `keys.tavily_api_key`
    - `keys.metaso_api_key`
    - `keys.baidu_api_key`
  - 若已配置 `keys.tavily_api_key`，则 `keys.metaso_api_key` 与 `keys.baidu_api_key` 可以留空，不构成阻断
  - 正文提取 key：
    - `keys.aliyun_iqs_api_key`
  - 首选模型配置：
    - `llm.providers[0].api_key`
    - `llm.providers[0].model`
    - `llm.providers[0].base_url`
  - 若启用 provider 链切换：
    - `llm.providers[1].api_key`
    - `llm.providers[1].model`
    - `llm.providers[1].base_url`
    - `llm.providers[2].api_key`
    - `llm.providers[2].model`
    - `llm.providers[2].base_url`
  - 可选审查开关：
    - `review.enable_step7`
    - 不写时默认 `false`
5. 直接运行完整链路：
  - `python scripts/c114.py run --date YYYY-MM-DD`

## 安装后首次配置

- 首次安装后，不要直接开始抓取；应先确认运行配置完整。
- 先运行：
  - `python scripts/c114.py c114-config-init`
  - `python scripts/c114.py c114-config-status`
- `c114-config-init` 负责从 `runtime.example.json` 生成本地 `runtime.local.json`
- `c114-config-status` 负责检查缺项，不会替你补配置
- 只要 `Tavily / Metaso / Baidu` 三者里至少一个已配置，`step 3` 就允许继续；不要把其余留空项误判为阻断
- 当前 skill 采用 provider 链执行，默认配置位于：
  - `config/runtime.local.json`
- 当前支持两种执行模式：
  - `builtin`
  - `controller-agent`
- 配置项逐项解释见：
  - `config/README.md`
- `llm` 段最小字段：
  - `llm.providers[0].provider`
  - `llm.providers[0].model`
  - `llm.providers[0].api_key`
  - `llm.providers[0].base_url`
  - `llm.providers[0].timeout_seconds`
  - `llm.providers[0].max_retries`
  - 若启用 provider 链切换，还需：
    - `llm.providers[1].provider`
    - `llm.providers[1].model`
    - `llm.providers[1].api_key`
    - `llm.providers[1].base_url`
    - `llm.failover.enabled`
    - `llm.failover.consecutive_failures`
- `review` 段当前支持：
  - `review.enable_step7`
  - 默认值为 `false`
  - 若设为 `false`，`run` 会在 `step 6` 停止，不再自动生成 `step 7`

## 代理与 SSL 排障

- 若运行时出现：
  - `ssl.SSLCertVerificationError: self-signed certificate in certificate chain`
- 应优先判断为：
  - 目标机器处于公司代理、HTTPS 检查网关或自签 CA 证书环境
  - 这通常不是 skill 代码 bug
- 当前 skill 的网络请求默认读取系统环境变量，应优先检查：
  - `HTTP_PROXY`
  - `HTTPS_PROXY`
  - `ALL_PROXY`
  - `SSL_CERT_FILE`
- 示例：
  - `export HTTP_PROXY=http://127.0.0.1:7890`
  - `export HTTPS_PROXY=http://127.0.0.1:7890`
  - `export ALL_PROXY=socks5://127.0.0.1:7890`
  - `export SSL_CERT_FILE=/path/to/company-ca.pem`
- 注意：
  - `7890` 只是示例端口，不是 skill 固定要求
  - 不要直接改代码关闭 SSL 校验
  - 先排查代理和证书，再继续运行

## 配置硬门槛

| 步骤 | 不配置也能跑 | 必须先配置 |
| --- | --- | --- |
| `Step 1：抓取` | 可以 | 无 |
| `Step 1.5：主题聚类` | 不可以 | `llm.providers[0].api_key`、`llm.providers[0].model`、`llm.providers[0].base_url` |
| `Step 2：搜索清单 YAML` | 不可以 | `llm.providers[0].api_key`、`llm.providers[0].model`、`llm.providers[0].base_url` |
| `Step 3：搜索结果 YAML` | 不可以 | 至少一个搜索 provider key：`keys.tavily_api_key` / `keys.metaso_api_key` / `keys.baidu_api_key`，以及 `search.recent_days`、`search.max_external_results`、`llm.providers[0].api_key` |
| `Step 4：正文抓取 YAML` | 不建议 | `keys.aliyun_iqs_api_key`、`content.fetch_keep_levels` |
| `Step 5：正文分析 YAML` | 不可以直接跳过前置 | 已完成 `step 4`，并配置 `llm.providers[0].api_key` |
| `Step 6：行业研究员简报 Markdown` | 不可以直接跳过前置 | 已完成 `step 5`、`brief.role`、`llm.providers[0].api_key` |
| `Step 7：简报审查 YAML` | 默认关闭 | 已完成 `step 6`、`llm.providers[0].api_key`；只有 `review.enable_step7 = true` 时才执行 |

- `Step 1` 可以单独运行
- 想跑 `Step 3` 时，三种搜索 key 里至少配置一个即可
- 如果已经配置 `keys.tavily_api_key`，则不应因为 `keys.metaso_api_key` 或 `keys.baidu_api_key` 为空而中止流程
- 想跑完整流程时，应补齐：
  - `keys.aliyun_iqs_api_key`
  - `llm.providers[0].api_key`
  - `search.recent_days`
  - `search.max_external_results`
  - `content.fetch_keep_levels`
  - `brief.role`
- 若需要审查层，可设置：
  - `review.enable_step7 = true`

## 步骤总览

当前 C114 业务链路共定义 `7` 个步骤。

1. `Step 1：文章分析 CSV`
2. `Step 1.5：主题聚类`
3. `Step 2：搜索清单 YAML`
4. `Step 3：搜索结果 YAML`
5. `Step 4：正文抓取 YAML`
6. `Step 5：正文分析 YAML`
7. `Step 6：行业研究员简报 Markdown`
8. `Step 7：简报审查 YAML`

## 职责对照

### Python（代码）负责什么

| 层级 | Python（代码）职责 |
| --- | --- |
| `Step 1` | 抓取 C114 原始文章、去重、写入原始 CSV / JSON |
| `Step 1.5` | 读取 `prompts/topic-grouping-agent.md`，把当日全部文章一次性分配到研究主题，并把分类结果写回后续步骤使用的 `topic` |
| `Step 2` | 读取 `prompts/search-keyword-agent.md`，按 topic 批量调用内置主备模型，为每篇文章自动生成两组 `keywords` |
| `Step 3` | 调搜索 provider、做日期过滤/黑名单/去重/结果归一化，并读取 `prompts/search-review-agent.md` 自动补全 `selected_results[*].ai_review` |
| `Step 4` | 只抓 `keep_level = strong / weak` 的补充链接正文 |
| `Step 5` | 读取 `prompts/content-analysis-agent.md`，默认按 `topic` 批量补齐 `summary`、`core_points`，其余分析字段按模型返回情况补充 |
| `Step 6` | 读取 `prompts/brief-agent.md`，按产业研究员口径自动生成主题简报 |
| `Step 7` | 读取 `prompts/brief-review-agent.md`，自动生成审查报告 |
| 通用职责 | 路径、文件命名、运行目录、配置读取、硬规则与结构校验 |

### Prompt 文件负责什么

| Prompt 文件 | 对应阶段 | 负责内容 |
| --- | --- | --- |
| `prompts/search-keyword-agent.md` | `Step 2` | 定义关键词生成口径 |
| `prompts/topic-grouping-agent.md` | `Step 1.5` | 定义当日文章研究主题分组口径 |
| `prompts/search-review-agent.md` | `Step 3` | 定义外链精筛和 `keep_level` 判断口径 |
| `prompts/content-analysis-agent.md` | `Step 5` | 定义正文分析字段和分析分寸 |
| `prompts/brief-agent.md` | `Step 6` | 定义行业研究员简报写法 |
| `prompts/brief-review-agent.md` | `Step 7` | 定义审查官质检标准 |

### 一句话分工

- `Python` 负责：
  - 确定性工作
  - 模型调用
  - 结构校验
  - 文件生成
  - 阻断错误
- `Prompt` 负责：
  - 语义判断标准
  - 研究分析口径
  - 审查口径

## 自动执行规则

- `Step 1.5 / Step 2 / Step 3 / Step 5 / Step 6 / Step 7` 默认由 skill 内置模型自动完成
- `Step 5` 默认按 `content.analysis_mode = per_topic` 执行
  - 同一主题下的多篇文章会合并成一次模型请求
  - 若返回结构不完整，会按 `content.analysis_batch_retry_attempts` 对该主题重试
- `Step 1.5` 会把当天文章一次性发给模型做研究主题分类
  - 输入字段只包含：
    - `article_id`
    - `title`
    - `source_bucket`
    - `publish_date`
    - `entities`
    - `signals`
    - `normalized_keywords`
    - `core_summary`
  - 输出为每篇文章的：
    - `topic_id`
    - `topic_name`
    - `reason`
  - 后续 `Step 2 / Step 3 / Step 5 / Step 6` 都沿用这里产出的 `topic`
- 若 `execution.mode = controller-agent`
  - skill 会在运行目录中写出 `c114_execution_manifest_YYYYMMDD.yaml`
  - 当前控制 agent 应按 manifest 读取 prompt、输入文件、目标输出和必填字段
  - 适用于 Codex 自动化、OpenClaw 或其它能按 manifest 接力步骤的控制 agent
- 不再要求外部 agent 手工补 `keywords`
- 不再要求外部 agent 手工补 `keep_level`
- 不再要求外部 agent 手工补 `analysis`
- 不再要求外部 agent 手工写 `step 6`
- 不再要求外部 agent 手工写 `step 7`
- 外部 agent 的主要职责是：
  - 初始化配置
  - 启动命令
  - 处理代理 / SSL / 配额 / 网络错误
  - 检查最终产物
- 外部 agent 不应：
  - 中途停下询问“是否继续执行 step 6 / step 7”
  - 中途要求用户人工审查简报后再继续
  - 看到中间产物后改成手工补步骤
  - 因 `metaso_api_key` 或 `baidu_api_key` 留空而误判 `step 3` 无法执行（前提是至少已有一个搜索 key）

## 模型执行约束

- 当前固定执行模型来自：
  - `config/runtime.local.json`
  - `llm.providers[*]`
  - `llm.failover.*`
- 当前统一运行时还会读取：
  - `llm.retry.*`
  - `llm.concurrency.*`
  - `llm.streaming.*`
  - `llm.step_rate_limits.*`
  - `llm.step_task_routing.*`
- 当前默认 provider 链：
  - `kimi-code / kimi-for-coding`
  - `Kimi / kimi-k2.5`
  - `MiniMax / MiniMax M2.7`
- 当前默认稳定性策略：
  - 同一步内仅在基础设施错误下切换 provider
  - 连续 `3` 次基础设施错误才切到下一个 provider
  - 若服务端返回 `Retry-After`，优先按服务端建议等待
  - provider 级默认并发：
    - `kimi-code = 2`
    - `kimi = 2`
    - `minimax = 3`
  - `step_5 / step_6 / step_7` 默认启用流式接收
  - `step_3 / step_5` 默认可开启任务分片路由：
    - `step_3` 的单条补充结果审查任务会在 `kimi-code / minimax / kimi` 之间轮流选择首发 provider
    - `step_5` 的单篇文章分析任务会在 `kimi-code / minimax / kimi` 之间轮流选择首发 provider
    - 若当前任务遇到基础设施错误，会在该任务内部继续尝试下一个 provider
- 切换规则：
  - `kimi-code` 连续 `3` 次基础设施错误后，当前步骤剩余请求切到 `Kimi`
  - `Kimi` 连续 `3` 次基础设施错误后，当前步骤剩余请求切到 `MiniMax`
  - 进入下一步时，重新优先尝试 `kimi-code`
- `JSON` 结构错误不会直接触发主备切换
- skill 不对模型名做本地白名单校验；若远端接口不接受该模型名，由接口错误直接返回
- 当 `execution.mode = controller-agent` 时：
  - 不调用内置 provider 链完成 `Step 2 / 3 / 5 / 6 / 7`
  - 改由当前控制 agent 思考并补全目标文件
  - builtin 与 controller-agent 共用同一套 prompt、结构校验和后置校验规则
- 各步骤都要求模型只返回目标结构，不返回额外解释
- Python 会校验结构：
  - `Step 2` 必须返回恰好两组 `keywords`
  - `Step 3` 必须返回合法 `keep_level = strong / weak / drop`
  - `Step 5` 必须补齐 `summary` 与 `core_points`
  - `Step 6` 必须包含：
    - `核心判断`
    - `增量信息`
    - `产业/公司影响`
    - `需要继续跟踪的点`
    - `源地址`
    - `补充地址`
  - `Step 7` 必须返回：
    - `overall_decision`
    - `summary`
    - `findings`
    - `strengths`
- 若模型返回结构不合法：
  - Python 会自动重试一次修复提示
  - 再失败则停在当前步骤
- 若模型返回多个连续 JSON 对象、前后夹杂解释文字、Markdown 代码块或 `<think>` 片段：
  - skill 会先在统一 LLM 解析层做有限兼容
  - 多个顶层 JSON 对象会按“后者覆盖、列表去重合并”规则合并
  - 若顶层类型冲突或合并后仍不满足步骤结构要求，流程仍会停在当前步骤
- 外部 agent 不应因为看到 `Extra data` 或 JSON 噪音，就去手工改 YAML 或改写中间产物

## 运行规则

- 默认主路径：
  - `python scripts/c114.py run --date YYYY-MM-DD`
- 这是最高优先级推荐入口；除排障外，不应改成逐步拆开手工执行
- `run` 现在会默认从 `Step 1` 直接跑到 `Step 7`
- 若未配置 `review.enable_step7`，则 `run` 默认从 `Step 1` 跑到 `Step 6`
- 只有 `review.enable_step7 = true` 时，才会继续生成 `Step 7`
- 只有以下情况才会阻断：
  - `llm.providers[0].api_key` 缺失
  - 搜索 provider key 全缺失
  - 模型输出结构多次修复失败
  - `Step 4` 正文抓取硬失败
- 若某一天在 `Step 1` 分析后文章数为 `0`，流程必须停留在 `Step 1`，不得继续生成该日的 `Step 2` 到 `Step 7`

## 区间运行

- 支持：
  - `--date YYYY-MM-DD`
  - `--start-date YYYY-MM-DD --end-date YYYY-MM-DD`
- 区间运行时：
  - 会新建一个总目录
  - 再按天生成子目录
- 每天的 `Step 1` 到 `Step 7` 仍然按单日文件名生成

## 目录规范

- skill 文档唯一入口：
  - `SKILL.md`
- 代码包目录：
  - `src/c114`
- 本地运行配置：
  - `config/runtime.example.json`
  - `config/runtime.local.json`
- 单入口脚本：
  - `scripts/c114.py`
- 输出目录：
  - `output/`

## 输出产物

- 默认最终最值得查看的文件：
  - `c114_step_6_brief_YYYYMMDD.md`
  - `c114_step_7_brief_review_YYYYMMDD.yaml`
- 中间产物包括：
  - `c114_step_2_search_checklist_YYYYMMDD.yaml`
  - `c114_step_3_search_results_YYYYMMDD.yaml`
  - `c114_step_4_content_YYYYMMDD.yaml`
  - `c114_step_5_content_analysis_YYYYMMDD.yaml`

## 目录说明

- `agents/agent.yaml`
  - skill 默认入口提示
- `config/runtime.example.json`
  - 对外分发的配置模板
- `config/runtime.local.json`
  - 本地运行配置，不应进入分发包
- `prompts/`
  - Python 内置模型调用时使用的提示词
- `scripts/c114.py`
  - skill 唯一脚本入口
- `src/c114`
  - skill 私有实现代码
- `output/`
  - 默认输出目录

## 分发约束

- 对外打包时必须包含：
  - `SKILL.md`
  - `agents/`
  - `config/runtime.example.json`
  - `prompts/`
  - `scripts/`
  - `src/`
- 对外打包时不得包含：
  - `config/runtime.local.json`
  - `output/data/**`
  - `output/reports/**`
  - `output/state/**`
  - 任意历史运行产物
- 若配置或步骤契约发生变化，必须先更新 `SKILL.md` 再打包

## 已知限制

- 搜索结果仍受 provider 配额和外站质量影响
- 正文抓取仍受目标站可访问性影响
- 代理 / SSL / 公司证书链问题属于运行环境问题，不属于 skill 业务逻辑
- `Step 4` 依赖现有 `step 3` YAML 结构，不建议用通用 YAML 重写器改写整份文件
- 虽然中间步骤已自动化，但最终质量仍受搜索结果质量和正文提取质量影响
