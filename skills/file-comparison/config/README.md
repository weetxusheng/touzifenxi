# File Comparison Skill 配置说明

本目录存放 `file-comparison` skill 的本地运行配置。

## 文件说明

- `runtime.example.json`
  - 对外分发的模板
- `runtime.local.json`
  - 本机实际运行配置
  - 由用户复制模板后填写

## 推荐启动方式

- 页面入口推荐使用：
  - `uv run python scripts/file_comparison_web.py`
- 单次对照推荐使用：
  - `uv run python scripts/file_comparison.py --old <旧文件> --new <新文件> --docx-output <输出路径>`
- 当前三个正式入口脚本会优先切到项目根目录 `.venv`：
  - `scripts/file_comparison.py`
  - `scripts/file_comparison_web.py`
  - `scripts/rerender_from_run.py`
- 如果误用系统 `python` 或 conda `python` 启动，上述脚本会自动 re-exec 到项目 `.venv/bin/python3`

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

### `llm.max_compare_blocks_per_batch`

- 单个 batch 最多包含的块级 compare block 数量
- 当前默认 `4`
- 用于处理“字符数未超限，但模型一次返回的 block 操作过多导致响应截断”的情况
- 只允许在不同一级章节之间拆分；同一章节内即使超过该数量，也必须保持在同一个 batch

### `llm.max_compare_block_chars`

- 单个 compare block 的左右条目合计字符数上限
- 当前默认 `10000`
- 历史兼容字段；当前主流程不再按该字段拆分单个 compare block
- 同一章节和同一父标题块的结构完整性优先级高于字数限制

### `paths`

- `output_mode`
  - `skill`：输出到 skill 自身目录
  - `project`：输出到共享项目目录
- `output_root`
  - 输出根目录

### `ui`

- `poll_interval_seconds`
  - 页面轮询间隔，默认 `5` 秒；发起任务后会立即拉取一次状态，终态后停止轮询。
- `host`
- `port`

## 当前推荐

- 联调时优先填写 `api_key`
- 对外分发时优先让用户只填写环境变量名：
  - `MINIMAX_API_KEY`
  - `KIMI_CODE_API_KEY`
  - `ARK_API_KEY`
  - `DEEPSEEK_API_KEY`
