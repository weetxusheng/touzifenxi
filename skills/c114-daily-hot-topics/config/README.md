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

- `primary`
  - 默认首选模型配置
  - 当前默认是：
    - `provider = kimi`
    - `model = kimi-k2.5`
    - `base_url = https://api.moonshot.cn/v1`
- `fallback`
  - 备用模型配置
  - 当前默认是：
    - `provider = minimax`
    - `model = MiniMax M2.7`
    - `base_url = https://api.minimaxi.com/v1`
- `primary.api_key`
  - 主模型调用 key
  - 缺失时 `Step 2 / 3 / 5 / 6 / 7` 都不能执行
- `fallback.api_key`
  - 备用模型调用 key
  - 只有在开启 failover 时才视为硬门槛
- `primary.timeout_seconds / fallback.timeout_seconds`
  - 单次模型请求超时秒数
  - 默认已放宽到 `180` 秒，适合 `Step 5 / 6 / 7` 这类较慢分析步骤
- `primary.max_retries / fallback.max_retries`
  - 当前 provider 内部请求失败后的最大重试次数
- `primary.retry_backoff_seconds / fallback.retry_backoff_seconds`
  - 当前 provider 请求失败后的退避秒数
  - 遇到 `529`、`429`、`5xx`、超时等可重试错误时，会按这个值逐次递增等待后再重试
- `failover`
  - 主备切换规则
  - `enabled = true` 时，当前默认规则是：
    - `Kimi` 连续 `3` 次基础设施错误后，当前步骤剩余请求切到 `MiniMax`
    - 进入下一步时，再重新优先尝试 `Kimi`
  - `JSON` 结构错误不会直接触发切换，仍会在当前 provider 内按结构修复逻辑重试

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
