# 多 Agent 投研与新闻简报系统

本仓库同时运行 A 股投研流水线、行业/科技资讯简报和美国国际新闻简报。底层能力统一沉淀在 `src/utils/tools/`，业务模块只保留各自的入口、站点适配和编排逻辑。

## 业务线

- `src/utils/`：A 股多 Agent 投研系统，覆盖股票池、主题路由、委员会打分、报告归档和推荐跟踪。
- `src/c114/`、`src/infoq/`：C114 / InfoQ 当日热点 Websearch 简报。
- `src/kr36/`：36Kr 当日热点 / 周报简报。
- `src/chip/`：半导体产业新闻简报，聚合 SEMI 中国和爱集微。
- `src/feedcore/`、`feedcore_remote_fetcher/`：美国国际新闻 FeedCore 简报，远程 fetcher 抓正文，本地完成多 Agent 分析、聚合和中文输出。

## 环境准备

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:PYTHONPATH = "src"
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export PYTHONPATH=src
```

`.venv/` 是本地虚拟环境，不纳入仓库；如果删除后需要运行测试或命令，按上面步骤重建即可。

## 常用命令

投研系统：

```bash
touzifenxi init-db
touzifenxi db-info
touzifenxi paths
touzifenxi run --data-source akshare --network-mode direct --report
touzifenxi serve-web --host 127.0.0.1 --port 8787
```

Websearch 简报：

```bash
python scripts/websearch.py run --source c114 --date YYYY-MM-DD
python scripts/websearch.py run --source infoq --date YYYY-MM-DD
python scripts/websearch.py run --source 36kr --date YYYY-MM-DD
touzifenxi run-c114-daily-brief --date YYYY-MM-DD
touzifenxi send-c114-latest-brief-email --t1-gate
touzifenxi send-kr36-latest-brief-email
```

半导体与 FeedCore：

```bash
touzifenxi run-chip-daily-brief
touzifenxi send-chip-latest-brief-email
python scripts/feedcore_us_news.py
python scripts/feedcore_send_brief_email.py
```

测试与静态检查：

```bash
pytest
pytest tests/path/to/test_x.py::test_y
ruff check src
ruff format src
```

## 邮件通道

邮件发送走 `src/utils/tools/output/email.py`。项目根目录 `.env` / `.env.local` 会被自动读取。

当前默认配置：

```env
TOUZIFENXI_EMAIL_BACKEND=auto
```

`auto` 表示双通道：

1. 主通道：企业 Exchange EWS，HTTPS 443，`tylxts@cjhxfund.com`，NTLM。
2. 备用通道：QQ SMTP，`smtp.qq.com:465`，SSL。

QQ 备用通道需要配置：

```env
TOUZIFENXI_EMAIL_QQ_FROM=944532395@qq.com
TOUZIFENXI_EMAIL_QQ_PASSWORD=QQ邮箱SMTP授权码
TOUZIFENXI_EMAIL_QQ_FROM_NAME=投研简报机器人
```

可选 `TOUZIFENXI_EMAIL_BACKEND`：

- `auto`：EWS 失败后自动切 QQ。
- `ews`：只用企业 EWS。
- `qq`：只用 QQ SMTP。
- `smtp`：只用传统 SMTP，并读取 `TOUZIFENXI_EMAIL_SMTP_*`。

详细说明见 `docs/channels.md`。

## Python 定时任务服务

各业务线的实际执行脚本仍集中在 `scripts/<业务>/`，例如：

- `scripts/c114/run_c114_daily_brief_no_email_windows.bat`
- `scripts/c114/send_c114_daily_brief_email_t1_gate_windows.bat`
- `scripts/chip/run_chip_daily_brief_no_email_windows.bat`
- `scripts/kr36/run_kr36_daily_brief_no_email_windows.bat`
- `scripts/feedcore/run_feedcore_us_news_windows.bat`

当前推荐不再依赖 Windows 任务计划器，而是启动本机 Python 调度服务。服务会把任务配置和运行记录写入 `state/schedule_admin.db`，并按 SQLite 中的时间每日触发对应 bat。定时发信脚本会读取 `.env`，因此会自动使用 `auto` 双通道。

启动管理页和调度循环：

```powershell
touzifenxi schedule-admin --host 127.0.0.1 --port 9999 --open-browser
```

管理页能力：

- 查看 C114、chip、36Kr、FeedCore 默认任务。
- 修改任务时间、启停任务、立即运行任务。
- 查看 `logs/scheduler/` 中的调度执行日志，以及最近运行记录。
- 所有任务都由 Python 进程直接执行，执行时设置 `cwd=项目根目录`，避免新设备从 `System32` 启动。

如果只想打开页面、不自动跑调度循环：

```powershell
touzifenxi schedule-admin --no-scheduler
```

注意：Python 调度服务必须保持运行。机器重启或关闭该进程后，定时任务不会自动触发；后续如需无人值守开机自启，可再单独封装 Windows 服务或启动项。

可选本地覆盖配置：`config/schedule.local.json`（已忽略，不提交），示例：

```json
{
  "tasks": {
    "c114_run": { "time": "17:40" },
    "c114_mail": { "time": "19:20" }
  }
}
```

## 数据与输出目录

```text
data/raw/                 原始数据
data/processed/           中间结构化数据
output/data/raw/          Websearch 运行时抓取产物
output/reports/           C114 / 36Kr / chip / FeedCore 简报产物
reports/                  项目级报告与搜索清单
state/                    SQLite、checkpoint、本地缓存
logs/                     运行日志
alembic/                  PostgreSQL 迁移脚手架
docs/                     架构、渠道、规范文档
```

运行目录应保持可排序命名，例如 `c114_search_YYYYMMDDHHMM`、`kr36_hot_topics_YYYYMMDDHHMM`、`c114_range_YYYYMMDD_YYYYMMDD_<timestamp>`。

## 数据库

设置 `TOUZIFENXI_DATABASE_URL` 时使用 PostgreSQL；未设置时回退到 `state/touzifenxi.db`。

```bash
export TOUZIFENXI_DATABASE_URL='postgresql://user:password@host:5432/touzifenxi'
touzifenxi init-db
alembic upgrade head
```

PostgreSQL schema 参考 `docs/postgresql_schema.sql`。

## 开发规范

协作入口：

- `AGENTS.md`
- `CLAUDE.md`
- `docs/project-conventions.md`
- `docs/development-workflow.md`

模块归位原则：

- 通用能力进 `src/utils/tools/`。
- 业务专属逻辑进 `src/c114/`、`src/infoq/`、`src/kr36/`、`src/chip/`、`src/feedcore/`。
- 脚本入口放 `scripts/`，不要把临时调试脚本长期留在仓库。
- 原始数据、中间数据、报告、日志和构建产物分目录存放。

## 风险说明

本项目仍处于重构和自动化运行阶段，所有投研输出仅供研究参考，不构成投资建议，也不应直接用于实盘交易。
