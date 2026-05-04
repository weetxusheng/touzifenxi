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

### `compare`

- `skip_section_patterns`
  - 对比前需要剔除的非正文页关键词
  - 默认跳过或裁剪 `签署页`、`签字页`、`盖章页`、`签章页`
  - 如果关键词出现在章节标题中，整章跳过
  - 如果关键词出现在章节正文中，从命中行开始裁剪尾部

### `llm.providers`

- provider 链按顺序声明
- 当前默认建议：
  - `minimax`
  - `kimi-code`
  - `deepseek-ark`
  - `deepseek`

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
- `failure_cooldown_seconds`

说明：

- `min_interval_seconds`
  - 正常请求之间的最小发起间隔
- `failure_cooldown_seconds`
  - 某个 provider 返回不可用后的冷却时间
  - 当前默认 `40`
  - 只限制同一个 provider 的下一次请求，不影响其它 provider 继续执行
  - 网络失败、超时、HTTP 错误、JSON 解析失败、后处理失败都会触发

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

### `llm.chapter_batch_char_limit`

- 单个 batch 送模型的左右正文合计字符数上限
- 当前默认 `10000`
- 如果按 `chapter_batch_size` 生成的 batch 超过该值，会触发自动缩小 batch

### `llm.oversized_chapter_batch_size`

- 超大 batch 自动缩小后的一级章节数量
- 当前默认 `2`
- 例如 `chapter_batch_size = 4` 时，如果某个 4 章节 batch 超过 `10000` 字符，会拆成 `2 + 2` 章节继续发送
- 如果 2 章节 batch 仍超过 `10000` 字符，会继续拆到单章；单章自身超过阈值时不再切碎章节正文

### `llm.max_compare_units_per_batch`

- 单个 batch 最多包含的条目级 compare unit 数量
- 当前默认 `4`
- 用于处理“字符数未超限，但模型输出 subsection 太多导致返回截断”的情况
- 如果同一章节内有很多变化条目，会按原顺序拆成多个 batch，但每个 unit 自身不切碎

### `llm.max_compare_unit_chars`

- 单个 compare unit 的左右正文合计字符数上限
- 当前默认 `10000`
- 如果单个 unit 超过该值，会按行/段落边界拆成 `part-001 / part-002`
- 拆分后的 part 会独立调用模型并按原顺序合并，避免单次返回 JSON 被截断

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
  - `DEEPSEEK_API_KEY`
