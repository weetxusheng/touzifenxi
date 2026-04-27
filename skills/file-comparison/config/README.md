# File Comparison Skill 配置说明

本目录存放 `file-comparison` skill 的本地运行配置。

## 文件说明

- `runtime.example.json`
  - 对外分发的模板
- `runtime.local.json`
  - 本机实际运行配置
  - 由用户复制模板后填写

## 关键字段

### `execution`

- `per_pair_max_workers`
  - 单个文件对内部的 batch 并行数
  - 默认 `2`

### `llm.providers`

- provider 链按顺序声明
- 当前默认建议：
  - `minimax`
  - `kimi-code`
  - `deepseek-ark`

每个 provider 支持：

- `provider`
- `model`
- `api_key`
- `api_key_env`
- `base_url`
- `timeout_seconds`
- `max_retries`
- `retry_backoff_seconds`
- `min_interval_seconds`

### `llm.retry`

- `honor_retry_after`
  - 是否优先遵守服务端 `Retry-After`
- `jitter_seconds`
  - 重试退避随机抖动

### `llm.retry_classifier`

- `infra_max_attempts`
- `parse_max_attempts`
- `postprocess_max_attempts`
- `fatal_max_attempts`

### `llm.concurrency`

- `default`
  - provider 默认并发上限
- `providers`
  - 按 provider 单独覆盖并发上限

### `llm.task_routing`

- `enabled`
  - 是否开启 batch 首发轮转
- `batch_compare`
  - batch 任务轮转的 provider 顺序

### `llm.chapter_batch_size`

- 每次送模型的一级章节数量
- 当前默认 `4`

### `paths`

- `output_mode`
  - `skill`：输出到 skill 自身目录
  - `project`：输出到共享项目目录
- `output_root`
  - 输出根目录

### `ui`

- `poll_interval_seconds`
- `host`
- `port`

## 当前推荐

- 联调时优先填写 `api_key`
- 对外分发时优先让用户只填写环境变量名：
  - `MINIMAX_API_KEY`
  - `KIMI_CODE_API_KEY`
  - `ARK_API_KEY`
