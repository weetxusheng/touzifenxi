---
name: file-comparison
description: 批量比较基金等章节型 Word 文档，按章节输出对照表，并支持本地预处理、多模型路由、失败回退和网页批处理界面。
---

# 文件对照 Skill

## 适用场景

- 用户需要比较两份或一批 `.doc/.docx` 文档
- 文档有明显的章节层级，如 `第X部分 / 一、 / 1、 / （1）`
- 用户希望输出 Word 对照表，而不是纯文本 diff
- 用户希望批量上传文件、查看配对、发起任务并轮询结果

## 默认行为

- 默认正式入口有两个：
  - `uv run python scripts/file_comparison.py`
  - `uv run python scripts/file_comparison_web.py`
- 本地开发辅助入口：
  - `uv run python scripts/restart_file_comparison_web.py`
  - `./scripts/restart_file_comparison_web.sh`
- 三个正式脚本入口都会优先切到项目根目录的 `.venv` 解释器：
  - 如果你误用系统 `python` 或 conda `python` 启动，脚本会自动 re-exec 到项目 `.venv/bin/python3`
  - 推荐命令仍然是 `uv run python ...`，这样最直观，也最容易排查环境问题
- 默认走 `llm_mode = responses`
- 默认 provider 链：
  - `minimax`
  - `kimi-code`
  - `deepseek-ark`
  - `deepseek`
- 默认 `chapter_batch_size = 4`
- 默认单批正文超过 `chapter_batch_char_limit = 10000` 字符时，先按 `oversized_chapter_batch_size = 2` 个一级章节重新拆分；如果 2 章仍超限，继续拆到单章
- 默认单批超过 `max_compare_blocks_per_batch = 4` 个块级 compare block 时，只在不同一级章节之间拆分；同一章节不拆开
- `max_compare_block_chars` 保留为兼容配置；当前主流程不再把单个 compare block 拆成 part，同章结构完整性高于字数限制
- 默认每个文件对内部并行：
  - `execution.per_pair_max_workers = 2`
- 默认开启 `batch` 级首发轮转：
  - `batch-001 -> minimax`
  - `batch-002 -> kimi-code`
  - `batch-003 -> deepseek-ark`
  - `batch-004 -> deepseek`
- provider 链全失败后只写本地诊断 fallback，不生成正式对照文档

## 输入语义

- 输入文件类型：
  - `.doc`
  - `.docx`
- `.docx` 若保留了 Word 修订痕迹，当前抽取规则按“可见正文”处理：
  - 插入修订内容会并入正文
  - 删除修订内容不会并回正文
- `.docx` 若使用 Word 自动编号，当前抽取规则会尽量恢复“Word 实际显示出来的编号”：
  - 不只恢复编号样式，也会读取编号起始值
  - 例如 Word 里显示为 `四、/五、/六、` 的条目，不会再误抽成 `1、/2、/3、`
- 推荐输入文本结构：
  - 有明确一级章节，如 `第X部分`
  - 有明确二级或三级层级，如 `一、 / 1、 / （1）`
- 批量页面模式下，会先按文件名去掉月份后自动配对；每个 `pair` 代表一组“旧版文件 vs 新版文件”。

## 配置与最小可运行路径

- 配置文件位置：
  - `config/runtime.example.json`
  - `config/runtime.local.json`
- 初始化方式：
  - `cp config/runtime.example.json config/runtime.local.json`
- 最小可运行路径：
  - 若只需本地规则比较，可把 `llm_mode` 设为 `rule`
  - 这种模式不依赖任何外部模型 API key
- 模型模式硬门槛：
  - `llm_mode = responses` 时，至少需要第一顺位 provider 的可用 API key
  - 若希望完整启用三模型 failover，则三条 provider 都应配置
- 非硬门槛但推荐配置：
  - `llm.concurrency`
  - `llm.task_routing`
  - `ui.poll_interval_seconds`
- 当前正式测试位置：
  - `tests/skills/file-comparison/`

## 排障说明

- 若看到 `缺少 python-docx，请使用项目 uv 环境启动 file-comparison 页面。`：
  - 先用 `uv run python scripts/file_comparison_web.py`
  - 如果你是直接执行脚本，当前版本也会自动切回项目 `.venv`，但推荐仍以 `uv run` 为准
- 若页面可启动但模型请求始终失败，先检查：
  - `MINIMAX_API_KEY`
  - `KIMI_CODE_API_KEY`
  - `ARK_API_KEY`
  - `DEEPSEEK_API_KEY`
- 若报网络、超时、SSL 或代理错误，先检查：
  - `HTTP_PROXY`
  - `HTTPS_PROXY`
  - `ALL_PROXY`
  - `SSL_CERT_FILE`
- 若模型返回结构不完整，运行目录里的 `timeline.json / final_status.json / request.attempt-*.json / response.raw.attempt-*.json / response.normalized.attempt-*.json / parsed.attempt-*.json` 是第一现场。

## 输出产物

- 默认输出目录：
  - `output/file-comparison/<task_id>/`
- 核心产物：
  - `task.json`
  - `status.json`
  - `checkpoints/task_checkpoint.json`
  - `checkpoints/pair_<pair_id>_checkpoint.json`
  - `pairs/<pair_id>/llm/batch-xxx/timeline.json`
  - `pairs/<pair_id>/llm/batch-xxx/final_status.json`
  - `pairs/<pair_id>/llm/batch-xxx/request.attempt-*.json`
  - `pairs/<pair_id>/llm/batch-xxx/response.raw.attempt-*.json`
  - `pairs/<pair_id>/llm/batch-xxx/response.normalized.attempt-*.json`
  - `pairs/<pair_id>/llm/batch-xxx/parsed.attempt-*.json`
  - `pairs/<pair_id>/outputs/<前文件名> 与 <后文件名> 对照表 <YYYYMMDD_HHMMSS>.docx`
  - `pairs/<pair_id>/outputs/<前文件名> 与 <后文件名> 对照表 <YYYYMMDD_HHMMSS>.doc`

## 目录说明

- `agents/`
  - skill 的 agent 展示与默认提示配置
- `config/`
  - 运行参数模板与配置说明
- `prompts/`
  - 预留给后续拆分出的外置 prompt
- `scripts/`
  - 正式入口脚本
- `src/`
  - skill 私有运行代码
- `output/`
  - skill 独立运行时的默认输出目录

## 结果规则

- 发送给模型前先做本地预处理，只保留疑似变更块
- 父标题对齐时忽略行首编号；例如旧 `十六、其他` 与新 `十七、其他` 会按同一父标题块处理，正文完全一致时不送模型、不展示
- 模型只返回 compare block 级操作集，不直接生成最终对照表左右列内容
- 程序按 `block_id + item_id` 回查原始 compare block，再展开成最终对照行和 Word 样式
- compare block 的 `old_items` 与 `new_items` 各自独立编号，分别从 `old-001`、`new-001` 开始
- 默认剔除或裁剪签署页、签字页、盖章页、签章页等非正文尾页
- 完全一致的小行不展示
- 仅编号变化不展示
- 左侧删除内容使用红色删除线
- 右侧变更内容使用蓝色强调
- provider 返回异常时记录 attempt 历史与耗时
- provider 返回不可用后，同一个 provider 默认冷却 `40` 秒再发下一次请求，其它 provider 不受影响
- 页面轮询只读取 `status.json` 和 checkpoint 摘要
- 页面任务状态支持展开文件对查看 batch 明细；点击单个 batch 的“重跑批次”会清理该 batch 过程文件与 checkpoint entry，再复用其它成功 batch 恢复生成
- 页面任务状态里，单个文件对在 `completed` 且不含 fallback 诊断时，可点击“重新生成 DOCX”
  - 该按钮不会重新请求模型
  - 它会直接复用该 pair 已落盘的成功 batch 解析结果，重新生成正式 `.docx/.doc`

完整处理逻辑见：

- `docs/PROCESSING_REQUIREMENTS.md`
- `docs/chunking-development-guide.md`（切块、compare block 与送模批次）
- `docs/engine-development-guide.md`（单 pair / 多 pair 任务编排）
- `docs/postprocess-development-guide.md`（模型结果后处理模块分工与改动落点）

## 分发约束

- skill 打包时必须以 `skills/file-comparison/` 目录为完整单元
- `runtime.local.json` 不进入分发包
- `output/` 下只保留 `.gitkeep` 或说明文件，不保留真实运行产物
- `__pycache__`、`.pyc`、临时任务目录不得作为交付内容
- 若修改 provider 链、脚本入口、checkpoint 契约、输出目录或配置项说明，必须同步更新：
  - `SKILL.md`
  - `config/README.md`
  - `docs/PROCESSING_REQUIREMENTS.md`
  - `agents/agent.yaml`
