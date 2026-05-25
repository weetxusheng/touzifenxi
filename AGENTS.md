# AGENTS

本文件是本仓库的协作与开发总入口。

后续无论是人还是 agent，在这个项目里新增 Python 模块、提示词、skill、测试、打包脚本时，都应先看这里，再看详细规范文档和模板。

## 1. 入口规则

- 项目级开发与协作规范，以本文件为入口。
- 详细规范正文见：
  - `docs/project-conventions.md`
  - `docs/development-workflow.md`
  - `docs/skill-packaging.md`
- 架构与运行说明同步参见 `CLAUDE.md`、`project.md`、`docs/architecture.md`。
- 如果本文件与其他零散说明冲突，以本文件和 `docs/project-conventions.md` 为准。

## 2. 模块布局（当前）

仓库内并行三条业务线 + 一个共用工具层：

- `src/utils/` — 投研主流水线 + **唯一**的共用工具层 `src/utils/tools/`
- `src/c114/` — C114 Websearch 当日热点专属入口与步骤
- `src/infoq/` — InfoQ 当日热点专属入口
- `src/kr36/` — 36Kr 当日热点 / 周报专属入口
- `scripts/` — 运维入口（`websearch.py`、Windows 定时任务批处理）
- `agents/agent.yaml` — Websearch skill 的对外门面描述

任何"通用能力 vs 站点专属"的归位疑问：通用能力进 `src/utils/tools/`，站点专属步骤进对应 `src/<site>/`。`src/utils/tools/facades/` 是给业务模块用的薄门面，业务代码优先 import facades，避免穿透。

## 3. 本项目当前关注的规范范围

当前先统一两类规范：

1. Python 开发规范
2. Skill 开发与分发规范

## 4. 快速规则

### 4.1 Python

- 通用、可复用逻辑放 `src/utils/tools/`；CLI 只做参数入口和编排，不堆业务细节
- 新增公共函数尽量写类型标注
- 新增或修改需求前，先把主场景、边界场景、失败场景和 fallback 用例想清楚
- 需求完成后，必须对照上述用例至少执行一遍回归验证
- 注释只解释"为什么"，不解释显然动作
- 方法过长时先拆分；暂时不能拆时，必须在方法体关键子流程前写清边界和失败语义
- 原始数据、中间数据、报告、构建产物分目录存放
- 多站点扩展默认收敛在 `content_sources` / 站点 adapter 层；主题聚类、搜索补充、正文分析、简报、邮件等后半段一律复用共享流水线
- Lint / Format：`ruff check src` + `ruff format src`（target py39，line-length 120，select E F I B，ignore E501）
- 测试：`pytest`；触网用例打 `@pytest.mark.c114_network` 并需要 `C114_NETWORK_TEST=1`

### 4.2 Skill

- 每个 skill 独立放在 `skills/<skill-name>/`
- `SKILL.md` 正文统一用中文
- 提示词、配置、脚本必须尽量留在 skill 自己目录内
- 单个 skill 专属的说明文档、运行机制文档、排障文档必须放在该 skill 目录内，优先使用 `skills/<skill-name>/docs/`
- skill 目录名、提示词文件名使用短横线风格
- 可打包产物输出到 `build/`
- Websearch skill 默认 `execution.mode = builtin`，Python 自动跑 Step 1.5/2/3/5/6/7；切到 `controller-agent` 模式时，agent 必须严格按运行目录里的 `c114_execution_manifest_YYYYMMDD.yaml` 推进，不得跳步骤、不得手改无关字段

## 5. 目录原则

- `src/` 放项目级能力（按业务模块 + `utils/tools/` 共用层组织）
- `skills/` 放可分发 skill；skill 专属文档、提示词、配置、脚本随对应 skill 存放
- `tests/` 放项目级测试
- `data/raw/` 放原始结果
- `data/processed/` 放中间结构化结果
- `reports/`、`output/reports/` 放简报和搜索清单
- `output/data/raw/` 放运行时抓取产物
- `state/` 放 SQLite 与本地缓存
- `logs/` 放运行日志
- `build/` 放 zip 等构建产物
- `alembic/` 放 PostgreSQL 迁移脚本，schema 文件在 `docs/postgresql_schema.sql`

运行目录命名必须可排序，例如 `c114_search_YYYYMMDDHHMM`、`c114_range_YYYYMMDD_YYYYMMDD_<timestamp>`、`kr36_hot_topics_YYYYMMDDHHMM`。

## 6. 开发前自查

开始新增功能前，先确认：

1. 这段逻辑应该放 `src/utils/tools/`、某个业务模块（`src/c114/` 等），还是 `skills/`
2. 提示词、配置、模板、skill 专属文档是否会被放错目录
3. 主流程场景、边界场景、失败场景和 fallback 用例是否已经先列清
4. 输出应该进入 `data/raw/`、`data/processed/`、`reports/`、`output/` 还是 `build/`
5. 这次改动是否需要补测试
6. 完成后要按哪些用例逐条回归验证
7. 新增 skill 是否基于 `docs/templates/skill-template/` 创建
8. 是否需要运行 `validate-skill` 和 `package-skill`

## 7. 常用入口速查

```bash
# 投研主流水线
touzifenxi init-db
touzifenxi run --data-source akshare --network-mode direct --report
touzifenxi serve-web --host 127.0.0.1 --port 8787

# Websearch / 当日简报
python scripts/websearch.py run --source c114 --date YYYY-MM-DD
python scripts/websearch.py run --source infoq --date YYYY-MM-DD
touzifenxi run-c114-daily-brief --date YYYY-MM-DD
touzifenxi send-c114-latest-brief-email --t1-gate

# 数据库后端切换
export TOUZIFENXI_DATABASE_URL='postgresql://user:pwd@host:5432/touzifenxi'
touzifenxi init-db
```

## 8. 详细规范

完整规则见：

- `docs/project-conventions.md`
- `docs/development-workflow.md`
- `docs/skill-packaging.md`
- `CLAUDE.md`（架构 + 命令速查 + 关键执行模型）

后续如果项目进入更强约束阶段，再在这里补：

- 格式化与静态检查约定
- pre-commit 约定
- skill 打包检查流程
