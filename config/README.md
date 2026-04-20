# Websearch Skill 配置说明

本目录存放 `websearch` 的本地运行配置和静态映射。

## 文件说明

- `runtime.example.json`
  - 对外分发的配置模板
  - 使用前复制为 `runtime.local.json`
- `runtime.local.json`
  - 本机实际运行配置
  - 不进入分发包
- `channels.json`
  - C114 栏目抓取配置；InfoQ 当前通过站点适配器获取首页当日文章
- `search_domains.json`
  - 搜索黑名单等静态域名规则

## runtime.example.json 字段说明

### `execution`

- `mode`
  - 执行模式开关
  - `builtin`
    - 默认值
    - `Step 1.5 / 2 / 3 / 5 / 6 / 7` 由 skill 内置 provider 链自动完成
    - 适合直接 CLI、无人值守和对外分发
  - `controller-agent`
    - `Step 1.5 / 2 / 3 / 5 / 6 / 7` 改由当前控制 skill 的 agent 完成思考
    - Python 只负责模板、检查点、校验、落盘、推进
    - 适合 Codex 自动化、OpenClaw 等能按 manifest 接力步骤的控制 agent

## 主题分类补充说明

- `Step 1.5` 是 `step 1` 之后、`step 2` 之前的研究主题分类步骤
- 这一步当前不新增独立配置项，默认跟随 `execution.mode` 和 `llm.providers[*]`
- `builtin` 模式下，会把当日文章一次性发给内置模型做主题分类
- 输入字段固定为：
  - `article_id`
  - `title`
  - `source_bucket`
  - `publish_date`
  - `entities`
  - `signals`
  - `normalized_keywords`
  - `core_summary`
- 输出会生成每篇文章的：
  - `topic_id`
  - `topic_name`
  - `reason`
- 后续 `Step 2 / Step 3 / Step 5 / Step 6` 会统一沿用这一步产生的 `topic`
- 这一步的中间结果也会写入运行目录下的：
  - `checkpoints/c114_step_1_5_checkpoint_YYYYMMDD.json`

### `keys`

- `tavily_api_key`
  - 搜索 provider 之一
  - `Step 3` 可用
  - 与 `metaso_api_key / baidu_api_key` 三选一即可
- `metaso_api_key`
  - 搜索 provider 之一
  - `Step 3` 可用
  - 与 `tavily_api_key / baidu_api_key` 三选一即可
- `baidu_api_key`
  - 搜索 provider 之一
  - `Step 3` 可用
  - 与 `tavily_api_key / metaso_api_key` 三选一即可
- `aliyun_iqs_api_key`
  - 正文提取接口 key
  - `Step 4` 使用
  - 缺失时正文抓取能力会明显下降

### `llm`

- `providers`
  - 内置模型 provider 链，按顺序依次尝试
  - 当前默认链路是：
    - `providers[0] = kimi-code`
    - `providers[1] = kimi`
    - `providers[2] = minimax`
  - 每个 provider 都支持：
    - `provider`
    - `model`
    - `api_key`
    - `base_url`
    - `timeout_seconds`
    - `max_retries`
    - `retry_backoff_seconds`
- `providers[0].api_key`
  - 首选模型调用 key
  - 仅在 `execution.mode = builtin` 时是硬门槛
- `providers[1...].api_key`
  - 后续 provider 调用 key
  - 只有在开启 failover 时才视为硬门槛
- `providers[*].timeout_seconds`
  - 单次模型请求超时秒数
  - 默认已放宽到 `180` 秒，适合 `Step 5 / 6 / 7` 这类较慢分析步骤
- `providers[*].max_retries`
  - 当前 provider 内部请求失败后的最大重试次数
- `providers[*].retry_backoff_seconds`
  - 当前 provider 请求失败后的退避秒数
  - 遇到 `529`、`429`、`5xx`、超时等可重试错误时，会按这个值逐次递增等待后再重试
- `failover`
  - provider 链切换规则
  - `enabled = true` 时，当前默认规则是：
    - `kimi-code` 连续 `3` 次基础设施错误后，当前步骤剩余请求切到 `Kimi`
    - `Kimi` 连续 `3` 次基础设施错误后，当前步骤剩余请求切到 `MiniMax`
    - 进入下一步时，再重新优先尝试 `kimi-code`
  - `JSON` 结构错误不会直接触发切换，仍会在当前 provider 内按结构修复逻辑重试
- `retry`
  - 统一的重试补充策略
  - `honor_retry_after = true` 时，若服务端返回 `Retry-After`，优先按服务端建议等待
  - `jitter_seconds` 用于在退避时间上增加随机抖动，避免并发重试雪崩
- `concurrency`
  - provider 级并发限制
  - `default` 为未单独指定 provider 时的默认并发
  - `providers` 可分别设置：
    - `kimi-code`
    - `kimi`
    - `minimax`
  - 当前建议保守值：
    - `kimi-code = 2`
    - `kimi = 2`
    - `minimax = 3`
- `streaming`
  - 是否对长响应步骤启用流式接收
  - 当前默认：
    - `enabled = true`
    - `steps = ["step_5", "step_6", "step_7"]`
  - `step_2 / step_3` 仍默认走普通模式
- `step_rate_limits`
  - 按步骤限制模型请求最小发起间隔
  - 适合 `Step 5` 这类大批量分析任务，避免短时间连续打满单一 provider
  - 当前默认：
    - `step_5.min_interval_seconds = 40`
- `step_task_routing`
  - 按步骤把同一步里的多个任务分发到不同 provider
  - 这是“单步任务分片路由”，不是整步切主模型
  - 当前推荐对 `step_3` 和 `step_5` 开启，例如：
    - `step_3.providers = ["kimi-code", "minimax", "kimi"]`
    - `step_5.providers = ["kimi-code", "minimax", "kimi"]`
  - 行为是：
    - 每个分析任务轮流选择首发 provider
    - 若当前任务遇到基础设施错误，会在该任务内部继续尝试下一个 provider

### `network`

- `request_timeout_seconds`
  - 通用联网请求超时秒数
  - 影响 `Step 1` 的页面抓取、`Step 3` 的搜索 provider 请求、`Step 4` 的 HTML fallback 抓取
  - 默认 `45` 秒
- `aliyun_timeout_seconds`
  - 阿里云 IQS 单次请求超时秒数
  - 默认 `45` 秒
- `aliyun_max_retries`
  - 阿里云 IQS 超时或限流时的最大重试次数
- `aliyun_retry_backoff_seconds`
  - 阿里云 IQS 每次重试前的退避秒数

### `search`

- `recent_days`
  - 搜索结果时间窗口
  - `Step 3` 使用
- `max_external_results`
  - 单篇文章最多保留多少补充外链
  - `Step 3` 使用

### `content`

- `fetch_keep_levels`
  - `Step 4` 会抓取哪些 `keep_level`
  - 默认是 `["strong", "weak"]`
- `analysis_mode`
  - 控制 `Step 5` 的正文分析调用粒度
  - `per_topic`
    - 默认值
    - 同一个 `topic` 下无论有多少篇文章，都只发一次模型请求
    - 适合减少请求次数，降低单模型连续调用过多的问题
  - `per_item`
    - 按单篇文章分别调用模型
    - 适合作为回退方案，排查单篇异常时更直观
- `analysis_batch_retry_attempts`
  - `Step 5` 在 `per_topic` 模式下的整主题重试次数
  - 若某个 topic 返回结构不完整，Python 会记录日志后按同一主题重试

### `brief`

- `role`
  - `Step 6` 的研究员口径
  - 当前默认 `senior_researcher`

### `review`

- `enable_step7`
  - 是否执行 `Step 7`
  - 默认 `false`
  - `true`：继续生成审查 YAML
  - `false`：流程停在 `Step 6`

## controller-agent 模式补充说明

- `controller-agent` 模式下，skill 会在运行目录里额外写：
  - `c114_execution_manifest_YYYYMMDD.yaml`
- 这个 manifest 会告诉控制 agent：
  - 当前停在哪一步
  - 该读哪个 prompt
  - 该看哪些输入文件
  - 该写哪个输出文件
  - 哪些字段必须补齐
- 这套契约不依赖 Codex 私有接口，因此 OpenClaw 也可以按同一套文件契约接入

### `paths`

- `output_mode`
  - 输出目录模式
  - `skill`：默认写入 skill 自带 `output/`
  - `project`：写入当前项目共享目录
