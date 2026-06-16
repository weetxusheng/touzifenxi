# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

仓库内并行运行五条业务线，共用一套底层工具：

1. **A 股多 Agent 投研系统** (`src/utils/`, `src/utils/steps/`, `src/utils/pipeline.py`)
   - 每日全市场股票池 → 主题路由 → 委员会打分 → 推荐报告
   - 入口命令：`touzifenxi`（pyproject.toml 中映射到 `utils.cli:main`）
2. **C114 / InfoQ Websearch 当日简报** (`src/c114/`, `src/infoq/`)
   - 抓取 C114 或 InfoQ 当天热点 → LLM 主题分类、搜索补全、正文分析 → 简报 → 邮件
   - 入口命令：`python scripts/websearch.py run --source c114|infoq --date YYYY-MM-DD`
3. **36Kr 周报** (`src/kr36/`)
   - 结构同 C114，按需扩展；scripts 下有 Windows 定时任务批处理脚本
4. **chip 半导体产业新闻** (`src/chip/`)
   - 聚合 SEMI 中国 + 爱集微两站点 → CHIP_THEMES 关键词分桶 + 非半导体过滤 → LLM 简报 → kr36 风格 HTML + 邮件
   - 关键词分类规则在 `src/utils/tools/research/chip_themes.py`；HTML 渲染走 `src/utils/tools/output/chip_email.py`
   - 入口：`touzifenxi run-chip-daily-brief` / `touzifenxi send-chip-latest-brief-email`
   - Windows bat：`scripts/chip/{run,send,register}_*.bat`
5. **FeedCore 美国国际新闻简报** (`src/feedcore/`, `feedcore_remote_fetcher/`)
   - 多 RSS 源 → 远程 fetcher（FastAPI 服务，http://81.69.47.226:3000）跨语种正文抓取 → 多 Agent 四要素分析（事实/背景/影响/反面观点）→ 动态子类规划 → 阅读主题聚合 → 输出**中文简报** brief.md / brief.html
   - 单一 profile `scripts/feedcore_us_news.py`：60 个全免费英文 RSS 源（4 大类 × 15 源：国际形势 / AI 与科技 / 金融市场与宏观 / 财经信息），T-1 数据窗口自动应用
   - 邮件可手动触发 `scripts/feedcore/send_feedcore_latest_brief_email_windows.bat`，或由 `register_feedcore_schedule_windows.bat` 注册每日 19:20 定时发信
   - Windows bat：`scripts/feedcore/{run_feedcore_us_news,send_feedcore_latest_brief_email,register_feedcore_schedule}_windows.bat`；四业务每日定时分别用各目录下 `register_*_split_schedule_windows.bat`（或 feedcore 的 `register_feedcore_schedule_windows.bat`）

业务模块的所有共用能力（DB、LLM、邮件、市场数据、主题、报告渲染等）一律走 `src/utils/tools/`，不要在业务模块内重复实现。

## 常用命令

开发环境：

```bash
source .venv/bin/activate
pip install -e ".[dev]"          # 包含 pytest / ruff / pre-commit
pre-commit install
```

测试与静态检查：

```bash
pytest                                       # tests/ 下全部用例
pytest tests/path/to/test_x.py::test_y       # 单测
pytest -m c114_network                       # 触网用例（需 C114_NETWORK_TEST=1）
ruff check src                               # E F I B；line-length 120；忽略 E501
ruff format src
```

投研系统：

```bash
touzifenxi init-db                                          # 初始化数据库（PG 优先，SQLite fallback）
touzifenxi db-info                                          # 查看当前 DB 后端状态
touzifenxi paths                                            # 列出所有系统路径
touzifenxi run --data-source akshare --network-mode direct --report
touzifenxi daily-cycle ...                                  # 全套日终流程
touzifenxi build-weekly-pool / refresh-weekly-pool          # 周度预筛池
touzifenxi sync-universe / sync-financials / sync-factors   # 基础数据回灌
touzifenxi serve-web --host 127.0.0.1 --port 8787           # 本地 Dashboard
touzifenxi performance                                      # 推荐胜率统计
```

Websearch（C114 / InfoQ）：

```bash
python scripts/websearch.py c114-config-init                # 生成 config/runtime.local.json
python scripts/websearch.py c114-config-status              # 校验 key / 模式 / 路径
python scripts/websearch.py run --source c114 --date 2026-05-19
python scripts/websearch.py run --source infoq --date 2026-05-19
touzifenxi run-c114-daily-brief --date 2026-05-19           # 跑 C114 流水线 + 发邮件
touzifenxi send-c114-latest-brief-email --t1-gate           # 不跑流水线，按门控只发信
touzifenxi send-kr36-latest-brief-email                     # 36Kr 同上
```

数据库后端切换：

```bash
export TOUZIFENXI_DATABASE_URL='postgresql://postgres:123456@host:5432/touzifenxi'
touzifenxi init-db                                          # 不设此变量时退回 state/touzifenxi.db
alembic upgrade head                                        # 迁移脚手架在 alembic/，schema 见 docs/postgresql_schema.sql
```

## 架构关键点

### 分层（投研流水线）

`数据底座 → 主题/事件路由 → 研究层（多 Agent 委员会）→ 组合层（行业/风格暴露约束）→ 验证层（推荐归档 + 1/5/20/60 日跟踪）`

- 主入口 `src/utils/pipeline.py::DailyResearchPipeline.run`
- 委员会构建在 `tools/research/committee.py`，主题路由 `tools/research/theme_router.py`，周度预筛 `tools/research/weekly_prefilter.py`
- 报告渲染 `tools/output/report.py`，存档 + 跟踪走 `tools/db/storage.py::ResearchStore`
- 主题白名单与扩展规则数据：`data/themes_v1.json`

### Websearch 执行模型（C114 / InfoQ）

两种执行模式由 `config/runtime.local.json` 中的 `execution.mode` 控制：

- `builtin`（默认）：Python 内置 provider 链（kimi-code → Kimi → MiniMax）自动跑 Step 1.5 / 2 / 3 / 5 / 6 / 7
- `controller-agent`：由当前控制 skill 的 agent 思考，Python 只负责模板、manifest、校验、落盘、推进；agent 必须严格按运行目录里的 `c114_execution_manifest_YYYYMMDD.yaml` 推进，**不得跳步骤、不得手改无关字段**

步骤含义见 `SKILL.md`。Step 3 搜索 provider：Tavily / Metaso / Baidu 至少一个 key 即可；配置了 Tavily 就允许其他两个为空。

### 模块边界

- `src/utils/tools/` 是唯一的"共用层"。任何"放哪里"的疑问，遵循 `project.md` 与 `docs/project-conventions.md`：通用能力进 `tools/`，业务专属步骤进对应 `src/c114/`、`src/infoq/`、`src/kr36/`、`src/utils/steps/`
- `src/utils/tools/orchestration/` 下是按业务拆分的编排器（目前主要是 `c114/`）；步骤命令处理器 `step_commands.py`、运行控制 `run.py`、状态机 `state_engine.py`
- `src/utils/tools/facades/` 是给业务模块用的薄门面（intelligence / content / content_analysis），业务代码优先 import facades，避免直接穿透到深层模块
- 数据存储分层：`tools/db/db.py`（连接 + schema）、`tools/db/storage.py`（读写）、`tools/db/migration.py`（SQLite → PostgreSQL 迁移）

### 数据 / 报告输出目录

- `data/raw/`、`data/processed/`：原始 / 中间结构化结果
- `output/data/raw/`、`output/reports/c114_report/`：Websearch 运行时输出与 checkpoint
- `reports/`：项目级简报与搜索清单
- `state/`：SQLite 与本地缓存
- `logs/`：运行日志（C114 / kr36 各自分目录）
- 运行目录命名必须可排序，例如 `c114_search_YYYYMMDDHHMM`、`c114_range_YYYYMMDD_YYYYMMDD_<timestamp>`、`kr36_hot_topics_YYYYMMDDHHMM`

### 网络模式

`--network-mode direct` 会跳过 macOS / Clash 系统代理，专用于行情抓取等场景；遇代理或证书错误时优先检查 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `SSL_CERT_FILE`。

## 开发约定（来自 AGENTS.md / docs/project-conventions.md）

- 写代码前先把主流程、边界、失败和 fallback 用例列清；改完按列表逐条回归
- 注释只写"为什么"，不写"做了什么"
- 方法过长先拆；暂不能拆时必须在关键子流程前写明边界与失败语义
- 多站点扩展默认收敛在 `content_sources` / 站点 adapter 层；主题聚类、搜索补充、正文分析、简报、邮件等后半段一律复用共享流水线
- 新增 skill 必须基于 `docs/templates/skill-template/`；打包前跑 `validate-skill` 与 `package-skill`
- 不要把临时调试脚本长期放仓库根目录；`build/` 仅作为产物目录，不当源码引用
- 完整规范见 `AGENTS.md`、`docs/project-conventions.md`、`docs/development-workflow.md`、`docs/skill-packaging.md`

## 风险说明

仍处于系统重构期，不构成投资建议，也不应直接用于实盘交易。
