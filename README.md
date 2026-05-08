# 多 Agent A 股投研系统

这个仓库不再按“演示级原型”推进，而是按“可持续运行的投研系统”重构。

当前目标不是做一个会打印 5 只股票的脚本，而是搭出这 4 层：

1. `数据底座`
全市场股票池、行情快照、财务指标、运行日志、推荐结果归档。

2. `研究层`
风格、基本面、技术面、风险、事件等 Agent 的统一输入输出协议。

前面新增一层：

- `主题/事件路由层`
  - 人工一级主题框架
  - 公告优先、政策补充的主题识别
  - 主题白名单、自动扩展股、旁路候选

3. `组合层`
行业暴露、风格暴露、风险预算、持仓约束。

4. `验证层`
每日推荐持久化、后续表现跟踪、回测与归因。

## 当前状态

已具备：

- 项目内 `direct` 网络模式，避免抓数继承 macOS/Clash 代理
- 真实日线抓取的双源结构
  - 首选 `Eastmoney`
  - 失败回退 `Sina`
- 每日推荐结果落地为 Markdown 报告
- PostgreSQL 正式主库
- SQLite 本地 fallback
- 研究流水线入口
- 主题/事件前置路由
- 本地主题配置文件: `data/themes_v1.json`
- 规则版本、周度池变更、候选决策日志
- 股票生命周期、主题生命周期追踪
- PostgreSQL schema 与 Alembic 脚手架

仍未完成：

- 全市场股票池采集
- 完整真实财务/估值底座
- 更完整的公告/新闻/事件层
- 回测与命中率跟踪
- 调度和失败恢复

## 命令

初始化数据库：

```bash
cd /Users/xusheng/Documents/project/touzifenxi
source .venv/bin/activate
touzifenxi init-db
```

查看数据库后端状态：

```bash
touzifenxi db-info
```

运行当前研究流水线：

```bash
touzifenxi run --data-source akshare --network-mode direct --report
```

启动本地页面 Dashboard：

```bash
touzifenxi serve-web --host 127.0.0.1 --port 8787
```

打开：

```text
http://127.0.0.1:8787
```

查看系统目录：

```bash
touzifenxi paths
```

配置 PostgreSQL 正式主库：

```bash
export TOUZIFENXI_DATABASE_URL='postgresql://postgres:123456@192.168.3.37:5432/touzifenxi'
touzifenxi init-db
```

说明：

- 当前正式主库是 PostgreSQL；只要设置了 `TOUZIFENXI_DATABASE_URL`，`run / daily-cycle / build-weekly-pool / serve-web` 都会默认走 PostgreSQL。
- SQLite 只保留为本地开发与调试 fallback。
- PostgreSQL 以“新系统起点”重新开始积累，不导入旧 SQLite 历史数据。
- PostgreSQL 迁移脚手架在 `alembic/`，schema 文件在 `docs/postgresql_schema.sql`。

安装/刷新每日自动化任务：

```bash
touzifenxi install-automation
```

查看自动化真实状态和最近日志：

```bash
touzifenxi automation-status
```

自动化说明：

- macOS 后台任务使用 `launchd`，任务文件在 `~/Library/LaunchAgents/com.touzifenxi.daily-cycle.plist`。
- 实际执行脚本在 `~/Library/Scripts/touzifenxi/run_daily_cycle.sh`。
- 运行副本默认在 `~/Projects/touzifenxi-auto`，用于避开 macOS 对 `Documents` 目录的后台访问限制。
- 日志默认写入 `~/Projects/touzifenxi-auto/runtime/daily-cycle-launchd.log` 和 `daily-cycle-launchd.err.log`。
- 触发时间为周一到周五 `18:30/19:30/20:30/21:30/22:30/23:30`；脚本会加锁，且当天推荐和日因子已更新时不会重复生成推荐。
- 即使当天推荐已更新，自动化仍会执行收益更新与数据健康检查，保证次日复盘和数据状态持续刷新。
- 修改项目代码后，重新执行 `touzifenxi install-automation` 会刷新运行副本、脚本和 `launchd` 配置。

## 目录

```text
data/
  raw/
  processed/
  themes_v1.json
  universe_sample.json
  watchlist_v2.json
reports/
state/
  touzifenxi.db
src/touzifenxi/
  cli.py
  pipeline.py
  settings.py
  storage.py
  schema.py
  ...
docs/
  architecture.md
```

## 设计原则

- 没有持久化的数据，不算系统能力。
- 没有全量股票池和过滤规则，不算选股系统。
- 没有验证闭环和归档，不算投研流程。
- 原型模块可以保留，但不会再冒充“可用系统”。

## 开发规范

项目协作与开发入口见：

- `AGENTS.md`

详细规范正文见：

- `docs/project-conventions.md`

后续新增 Python 模块、提示词、skill 目录、打包脚本时，默认都按这两份文档执行。

## 风险说明

当前仍处于系统重构期，不构成投资建议，也不应直接用于实盘交易。
