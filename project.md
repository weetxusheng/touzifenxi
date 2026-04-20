# 投资分析系统 — 项目目录说明

## 项目定位

A 股多智能体每日研究系统。核心流程：抓取行业资讯 → LLM 分析 → 生成简报 → 邮件推送。

---

## 架构原则

- **`src/utils/`** — 所有模块共用的能力（db、email、models、settings 等），任何模块需要这些能力直接从这里 import，不重复实现
- **`src/c114/`** — 只包含 C114 专属内容：入口、命令、prompts、C114 特有步骤逻辑；需要共用能力时从 `utils.tools` 取
- **`src/infoq/`** — 同上，InfoQ 专属
- 没有重复代码，没有平行实现

---

## 目录结构（目标态）

```
touzifenxi/
│
├── src/
│   ├── utils/                       # 投研编排 + 所有共用工具
│   │   ├── pipeline.py              # 主入口，按顺序调用各 step
│   │   ├── step1_load_universe.py   # 加载股票池 & 行情快照
│   │   ├── step2_filter_themes.py   # 主题路由 & 周期预筛
│   │   ├── step3_score_stocks.py    # 多 Agent 委员会打分
│   │   ├── step4_output.py          # 生成推荐报告 & 持久化
│   │   ├── cli.py                   # CLI 入口
│   │   └── tools/                   # 所有模块可复用的底层能力
│   │       ├── db.py                # 数据库连接、schema、连接池（合并自 db.py + db_runtime.py + schema.py）
│   │       ├── storage.py           # 数据库读写
│   │       ├── models.py            # 核心数据模型
│   │       ├── settings.py          # 路径 & 环境配置
│   │       ├── market_data.py       # 行情数据加载
│   │       ├── universe.py          # 股票池管理
│   │       ├── industry.py          # 行业分类（合并自 industry.py + industry_mapping.py）
│   │       ├── fundamentals.py      # 财务指标
│   │       ├── tagging.py           # 股票标签
│   │       ├── tracking.py          # 持仓追踪
│   │       ├── db/                  # 数据库
│   │       │   ├── db.py            # 连接、schema、连接池
│   │       │   ├── storage.py       # 读写操作
│   │       │   └── migration.py     # 迁移工具
│   │       ├── market/              # 行情 & 股票数据
│   │       │   ├── market_data.py
│   │       │   ├── universe.py
│   │       │   ├── fundamentals.py
│   │       │   ├── industry.py
│   │       │   ├── tagging.py
│   │       │   └── data_loader.py
│   │       ├── research/            # 投研分析
│   │       │   ├── committee.py
│   │       │   ├── theme_router.py
│   │       │   ├── weekly_prefilter.py
│   │       │   ├── tracking.py
│   │       │   └── agents.py
│   │       └── output/              # 输出
│   │           ├── email.py
│   │           ├── report.py
│   │           ├── dashboard.py
│   │           └── briefing.py
│   │
│   ├── c114/                        # C114 专属（入口 + 命令 + prompts + 步骤）
│   │   ├── cli.py                   # C114 CLI 入口
│   │   ├── pipeline.py              # C114 pipeline 编排
│   │   ├── prompts/                 # C114 专属 LLM Prompts
│   │   │   ├── brief-agent.md
│   │   │   ├── brief-review-agent.md
│   │   │   ├── content-analysis-agent.md
│   │   │   ├── search-keyword-agent.md
│   │   │   ├── search-review-agent.md
│   │   │   └── topic-grouping-agent.md
│   │   ├── steps/                   # C114 专属步骤（调 utils.tools 获取共用能力）
│   │   │   ├── step1_analysis.py
│   │   │   ├── step1_5_topic_grouping.py
│   │   │   ├── step2_keywords.py
│   │   │   ├── step4_content_fetch.py
│   │   │   ├── step5_content_analysis.py
│   │   │   └── step6_brief.py
│   │   ├── llm/                     # LLM 客户端（C114 专属，utils 无同类）
│   │   ├── search/                  # 搜索工作流（C114 专属）
│   │   ├── content/                 # 正文抓取与解析（C114 专属）
│   │   ├── analysis/                # 分析模型与 IO（C114 专属）
│   │   ├── brief/                   # 简报生成（C114 专属）
│   │   ├── runtime/                 # 配置加载、Checkpoint（C114 专属）
│   │   └── commands/                # CLI 子命令处理器
│   │
│   └── infoq/                       # InfoQ 专属（结构同 c114，按需扩展）
│       ├── cli.py
│       └── settings.py
│
├── config/                          # 运行时配置（从 skills/websearch/config/ 提上来）
│   ├── runtime.example.json
│   ├── runtime.local.json
│   ├── channels.json
│   └── search_domains.json
│
├── agents/                          # Agent 配置
│   └── agent.yaml
│
├── prompts/                         # 项目级 prompts（如有跨模块的）
│
├── scripts/                         # 运维 & 入口脚本
│   ├── websearch.py                 # websearch 启动脚本
│   ├── c114/                        # Windows 定时任务
│   └── quick_commit.sh
│
├── output/                          # 运行时输出
│   ├── data/raw/                    # 原始抓取数据
│   └── reports/c114_report/         # 报告及 checkpoint
│
├── docs/                            # 项目文档
├── alembic/                         # 数据库迁移脚本
├── state/                           # SQLite 数据库
├── build/                           # 打包产物
├── SKILL.md
├── AGENTS.md
├── pyproject.toml
└── .env

```

---

## 待删除（当前存在，整理后去掉）

```
skills/                              # 整个 skills/ 层，内容全部提到顶层
data/                                # 空目录，仅有 README

src/touzifenxi/c114_automation.py    # 被 src/c114/ 替代
src/touzifenxi/content_sources/      # 被 src/c114/ + src/infoq/ 替代
src/touzifenxi/briefing/             # 被 src/c114/brief/ 替代
src/touzifenxi/config.py             # 被 settings.py 替代
src/touzifenxi/skill_packaging.py    # skills 层取消后无用
src/touzifenxi/industry_mapping.py   # 合并进 tools/industry.py
src/touzifenxi/db_runtime.py         # 合并进 tools/db.py
src/touzifenxi/schema.py             # 合并进 tools/db.py
src/touzifenxi/channels/             # 合并进 tools/email.py
```

---

## pyproject.toml 同步修改

```toml
[project.scripts]
touzifenxi = "utils.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.ruff]
src = ["src"]
include = ["pyproject.toml", "src/**/*.py"]
```
