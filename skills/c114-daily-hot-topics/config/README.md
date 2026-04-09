# C114 Skill 配置说明

本目录存放 `c114-daily-hot-topics` 的本地运行配置和静态映射。

## 文件说明

- `runtime.example.json`
  - 对外分发的配置模板
  - 使用前复制为 `runtime.local.json`
- `runtime.local.json`
  - 本机实际运行配置
  - 不进入分发包
- `channels.json`
  - C114 栏目抓取配置
- `search_domains.json`
  - 搜索黑名单等静态域名规则

## runtime.example.json 字段说明

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
  - 缺失时 `Step 2 / 3 / 5 / 6 / 7` 都不能执行
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

### `brief`

- `role`
  - `Step 6` 的研究员口径
  - 当前默认 `senior_researcher`

### `review`

- `enable_step7`
  - 是否执行 `Step 7`
  - `true`：继续生成审查 YAML
  - `false`：流程停在 `Step 6`

### `paths`

- `output_mode`
  - 输出目录模式
  - `skill`：默认写入 skill 自带 `output/`
  - `project`：写入当前项目共享目录
