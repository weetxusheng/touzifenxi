# Websearch 单技能迁移计划

> **给执行型 agent 的要求：** 实施本计划时，必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`。任务使用 `- [ ]` 复选框格式跟踪。

**目标：** 用一个新的通用 skill `websearch` 替换当前的 `c114-daily-hot-topics` 与独立的 `infoq-daily-hot-topics`，并通过 `--source c114|infoq` 在同一个 skill 内选择站点。

**架构：** 保留当前最成熟的 C114 主流程作为执行主干，把 skill 目录名改成 `websearch`，再把独立的 InfoQ 入口并入同一个 CLI。迁移完成后，仓库里只保留一个可执行 skill，InfoQ 不再作为单独 skill 存在。

**技术栈：** Python、argparse CLI、现有 C114 各步骤流水线、InfoQ 抓取逻辑、pytest、Ruff

---

### 任务 1：先用测试锁住“单 skill + 多 source”的目标

**涉及文件：**
- 修改：`/Users/xusheng/Documents/project/touzifenxi/tests/skills/c114/test_scripts.py`
- 修改：`/Users/xusheng/Documents/project/touzifenxi/tests/test_c114_automation.py`
- 修改：`/Users/xusheng/Documents/project/touzifenxi/tests/governance/test_governance.py`
- 删除或改写：`/Users/xusheng/Documents/project/touzifenxi/tests/skills/infoq/test_cli.py`
- 删除或改写：`/Users/xusheng/Documents/project/touzifenxi/tests/skills/infoq/test_scripts.py`

- [ ] **步骤 1：先写失败测试，锁定新的仓库形态**

测试需要明确要求：
- 唯一 skill 目录是 `skills/websearch`
- 唯一脚本入口是 `skills/websearch/scripts/websearch.py`
- `run` 命令必须支持 `--source c114` 和 `--source infoq`
- 自动化输出目录必须落到 `skills/websearch/output/reports/<source>_report/...`
- 仓库里不再保留独立 `infoq` skill

- [ ] **步骤 2：运行聚焦测试并确认它们先失败**

运行：
```bash
./.venv/bin/python -m pytest \
  tests/skills/c114/test_scripts.py \
  tests/test_c114_automation.py \
  tests/governance/test_governance.py \
  -q
```

预期：失败。因为当前仓库还保留旧 skill 目录和旧脚本入口。

### 任务 2：把主 skill 目录改成 `websearch`

**涉及文件：**
- 移动/修改：`/Users/xusheng/Documents/project/touzifenxi/skills/c114-daily-hot-topics/**`
- 修改：`/Users/xusheng/Documents/project/touzifenxi/pyproject.toml`
- 修改：`/Users/xusheng/Documents/project/touzifenxi/AGENTS.md`

- [ ] **步骤 1：重命名 skill 目录与脚本入口**

把当前主 skill 目录改为：
- `skills/websearch`

把脚本入口改为：
- `skills/websearch/scripts/websearch.py`

- [ ] **步骤 2：同步工具链路径**

更新 `pytest` 和 `ruff` 的路径，让它们加载：
- `skills/websearch/src`

### 任务 3：给单一 skill 增加 `--source`，把 InfoQ 合并进来

**涉及文件：**
- 修改：`/Users/xusheng/Documents/project/touzifenxi/skills/websearch/src/c114/cli.py`
- 在同目录下新增或修改 source 选择辅助模块
- 迁入逻辑来源：`/Users/xusheng/Documents/project/touzifenxi/skills/infoq-daily-hot-topics/src/infoq/cli.py`

- [ ] **步骤 1：先写失败测试，要求统一入口能分流 source**

测试需要要求：
- 单一 `websearch` skill 接受 `--source`
- `--source c114` 走原来的 C114 流程
- `--source infoq` 走原来的 InfoQ 采集入口，但复用同一条后续流水线

- [ ] **步骤 2：运行聚焦测试并确认失败**

运行：
```bash
./.venv/bin/python -m pytest tests/skills/c114/test_scripts.py -q
```

预期：失败。因为 CLI 目前还不支持统一的 source 分流。

- [ ] **步骤 3：只写最小实现，把 source 分流接进同一条 CLI**

把 `run --source ...` 接到同一个 CLI 里：
- `c114` 继续走当前成熟的 C114 主流程
- `infoq` 把原独立 skill 的 `run` 编排并入这里
- 不再通过第二个 skill 或第二个 CLI 来执行

### 任务 4：删除独立的 InfoQ skill

**涉及文件：**
- 删除：`/Users/xusheng/Documents/project/touzifenxi/skills/infoq-daily-hot-topics/**`
- 更新所有文档、测试、治理规则中的引用

- [ ] **步骤 1：在行为已经迁入单一 skill 后，删除独立 InfoQ skill**

删除整个：
- `skills/infoq-daily-hot-topics`

- [ ] **步骤 2：同步治理与文档**

确保文档和治理检查都明确表达：
- `websearch` 是唯一 skill
- `infoq` 是 `--source` 的一个可选站点，而不是第二个 skill

### 任务 5：把自动化与输出目录切到新的单 skill

**涉及文件：**
- 修改：`/Users/xusheng/Documents/project/touzifenxi/src/touzifenxi/c114_automation.py`
- 修改任何负责推导 run_dir 和 step 6 文件路径的辅助逻辑

- [ ] **步骤 1：让自动化调用 `websearch`**

自动化要改为调用：
```bash
./.venv/bin/python skills/websearch/scripts/websearch.py run --source c114 --date YYYY-MM-DD
```

并把输出目录改为：
- `skills/websearch/output/reports/<source>_report/<source>_search_<timestamp>/`

- [ ] **步骤 2：让 step 6 和邮件正文文件按 source 参数定位**

自动化必须能正确找到：
- `<source>_step_6_brief_YYYYMMDD.md`
- `<source>_step_6_brief_YYYYMMDD_email.html`
- `<source>_step_6_brief_YYYYMMDD_email.txt`

### 任务 6：完成后做单 skill 结果验证

**涉及文件：**
- 不新增文件，直接验证迁移后的结果

- [ ] **步骤 1：先跑聚焦回归**

运行：
```bash
./.venv/bin/python -m pytest \
  tests/skills/c114/test_scripts.py \
  tests/test_c114_automation.py \
  tests/governance/test_governance.py \
  -q
```

- [ ] **步骤 2：再跑 skill 相关更大范围回归**

运行：
```bash
./.venv/bin/python -m pytest tests/skills/c114 -q
./.venv/bin/ruff check .
```

- [ ] **步骤 3：人工确认最终仓库形态**

确认这些事实成立：
- `skills/websearch/` 存在且可执行
- `skills/infoq-daily-hot-topics/` 已被删除
- `skills/c114-daily-hot-topics/` 已被替换为 `skills/websearch/`
- 对外只剩一个 skill
