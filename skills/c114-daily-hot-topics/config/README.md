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

- `provider`
  - 当前固定写 `minimax`
- `model`
  - 当前默认 `MiniMax M2.7`
  - `Step 2 / 3 / 5 / 6 / 7` 都会用到
- `api_key`
  - 固定模型调用 key
  - 缺失时 `Step 2 / 3 / 5 / 6 / 7` 都不能执行
- `base_url`
  - MiniMax 兼容接口地址
- `timeout_seconds`
  - 单次模型请求超时秒数
- `max_retries`
  - 模型结构化输出失败或超时时的最大重试次数

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
