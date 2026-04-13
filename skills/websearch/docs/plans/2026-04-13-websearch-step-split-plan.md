# Websearch Step 大文件拆分实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持 `skills/websearch/scripts/websearch.py run --source c114/infoq --date YYYY-MM-DD` 对外入口不变的前提下，把 `skills/websearch/src/c114/` 内超过 1000 行的主流程大文件拆成企业级分层结构。

**Architecture:** 当前 Python 包名 `c114` 暂不整体改名，先把 step 编排、横向能力、CLI 命令拆开。旧的 `c114_content_analysis.py`、`c114_intelligence.py`、`c114_content.py` 可以短期保留为“薄门面”，但主体逻辑必须迁入 `steps/`、`analysis/`、`content/`、`brief/` 等子包。

**Tech Stack:** Python 3.14、pytest、ruff、现有 websearch skill、现有 checkpoint/LLM/search/runtime 公共层。

---

## 0. 当前基线与执行原则

当前已经完成第一批低风险收口：

- `search_*` 已进入 `skills/websearch/src/c114/search/`
- `llm_runtime` 和 `llm.py` 已进入 `skills/websearch/src/c114/llm/`
- `checkpoint/config/settings/execution` 已进入 `skills/websearch/src/c114/runtime/`
- `tests` 全量通过，`ruff check skills/websearch/src src/touzifenxi tests` 已通过

下一轮不要再做“只移动少量文件”的小改动。推荐按下面顺序执行：

1. 第二批一次完成 3 个主 step 文件拆分：`c114_content_analysis.py`、`c114_intelligence.py`、`c114_content.py`
2. 第三批再拆 `cli.py`
3. `review` 暂不拆，只保持 import 跟随
4. `src/touzifenxi/storage.py` 和 `src/touzifenxi/cli.py` 暂不混入本轮 websearch 拆分，单独排下一轮项目级重构

每一批都必须满足：

- 对外入口不变
- YAML/Markdown/checkpoint/log 格式不变
- C114 与 InfoQ 现有 source 分流不变
- 旧大文件若保留，只能做薄门面，不再承载新增主体逻辑
- 每批结束必须跑对应 pytest、ruff、入口 `--help`

---

## 1. 目标目录结构

拆完第二批后，`skills/websearch/src/c114/` 至少形成：

```text
skills/websearch/src/c114/
  analysis/
    __init__.py
    csv_io.py
    models.py
    normalization.py
    title_matching.py
    topic_grouping.py
    validation.py

  brief/
    __init__.py
    links.py
    markdown.py
    render.py

  content/
    __init__.py
    fetch.py
    html.py
    types.py
    yaml_io.py

  steps/
    __init__.py
    step1_analysis.py
    step1_5_topic_grouping.py
    step2_keywords.py
    step4_content_fetch.py
    step5_content_analysis.py
    step6_brief.py

  c114_content_analysis.py
  c114_intelligence.py
  c114_content.py
```

第三批后补：

```text
skills/websearch/src/c114/
  commands/
    __init__.py
    automation.py
    config.py
    run.py
    step_commands.py

  cli.py
  pipeline.py
```

---

## 2. 第二批：一次拆 3 个主流程大文件

### Task 1: 拆 `c114_content_analysis.py`，分离 Step 5 与 Step 6

**Files:**

- Create: `skills/websearch/src/c114/analysis/__init__.py`
- Create: `skills/websearch/src/c114/analysis/title_matching.py`
- Create: `skills/websearch/src/c114/analysis/validation.py`
- Create: `skills/websearch/src/c114/brief/__init__.py`
- Create: `skills/websearch/src/c114/brief/links.py`
- Create: `skills/websearch/src/c114/brief/markdown.py`
- Create: `skills/websearch/src/c114/steps/__init__.py`
- Create: `skills/websearch/src/c114/steps/step5_content_analysis.py`
- Create: `skills/websearch/src/c114/steps/step6_brief.py`
- Modify: `skills/websearch/src/c114/c114_content_analysis.py`
- Modify: `tests/skills/c114/test_content_analysis.py`

- [ ] **Step 1: 先写结构测试**

在 `tests/skills/c114/test_package_layout.py` 增加：

```python
def test_step5_and_step6_modules_are_split_from_content_analysis_facade() -> None:
    """Step 5/6 主体逻辑应由 steps 和 brief/analysis 横向模块承载。"""

    from c114.brief.markdown import render_brief_markdown
    from c114.steps.step5_content_analysis import auto_complete_content_analysis
    from c114.steps.step6_brief import generate_brief_markdown

    assert callable(auto_complete_content_analysis)
    assert callable(generate_brief_markdown)
    assert callable(render_brief_markdown)
```

- [ ] **Step 2: 运行测试确认当前失败**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_package_layout.py::test_step5_and_step6_modules_are_split_from_content_analysis_facade -q
```

Expected:

```text
ModuleNotFoundError: No module named 'c114.steps'
```

- [ ] **Step 3: 创建新模块并迁移纯函数**

迁移原则：

- `brief/links.py` 放链接格式化、URL 清洗、引用信息拼接
- `brief/markdown.py` 放 `render_brief_markdown`、栏目渲染、标题推导中不依赖 LLM 的逻辑
- `analysis/title_matching.py` 放标题归一、相似度匹配、搜索结果与正文分析映射
- `analysis/validation.py` 放 `collect_missing_analysis_fields`、字段校验、结构兼容

每个新模块开头必须有中文模块 docstring，例如：

```python
"""正文分析阶段的标题匹配能力。

本模块只负责把 step 4/5/6 中出现的标题、URL 和文章标识归一到同一匹配口径。
它不调用 LLM，不读写文件，避免匹配规则和 step 编排互相耦合。
"""
```

- [ ] **Step 4: 迁移 Step 5 编排**

把下面这些 Step 5 主体函数迁入 `steps/step5_content_analysis.py`：

- `auto_complete_content_analysis`
- `build_content_analysis_prompt_payload`
- `normalize_content_analysis_draft`
- `normalize_content_analysis_topic_response`
- `save_content_analysis_yaml`
- `render_content_analysis_yaml`
- `resolve_content_analysis_output_paths`
- `load_content_analysis_inputs`

`steps/step5_content_analysis.py` 模块 docstring：

```python
"""Step 5 正文分析编排。

本模块负责读取 step 4 正文、按 topic 调用 LLM、写入 checkpoint 并渲染 step 5 YAML。
纯文本匹配、字段校验和 Markdown 渲染不放在这里，避免 step 编排继续变成大杂烩。
"""
```

- [ ] **Step 5: 迁移 Step 6 编排**

把下面这些 Step 6 主体函数迁入 `steps/step6_brief.py`：

- `generate_brief_markdown`
- `auto_complete_brief_sections`
- `build_brief_prompt_payload`
- `normalize_brief_sections`
- `infer_brief_title`
- `save_layer_issues_yaml`
- `generate_layer_issues`

`steps/step6_brief.py` 模块 docstring：

```python
"""Step 6 简报生成编排。

本模块负责从 step 5 分析结果生成分 topic 简报，并将章节级结果写入 checkpoint。
Markdown 细节委托给 brief 包，LLM 重试和结构化解析仍走 llm 公共层。
"""
```

- [ ] **Step 6: 把旧文件压成薄门面**

`c114_content_analysis.py` 保留对外 import 兼容，但只 re-export：

```python
"""Step 5/6 兼容门面。

主体逻辑已拆入 `steps.step5_content_analysis`、`steps.step6_brief`、`analysis` 和 `brief`。
旧测试和 CLI 仍可从本模块导入公开函数，但新增逻辑不得继续写在这里。
"""

from __future__ import annotations

from .steps.step5_content_analysis import *
from .steps.step6_brief import *
```

如果 `__all__` 过长，显式列出测试和 CLI 当前导入的公开函数，不使用隐式通配污染。

- [ ] **Step 7: 跑 Step 5/6 测试**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_content_analysis.py tests/skills/c114/test_package_layout.py -q
```

Expected:

```text
passed
```

---

### Task 2: 拆 `c114_intelligence.py`，分离 Step 1 / 1.5 / 2

**Files:**

- Create: `skills/websearch/src/c114/analysis/models.py`
- Create: `skills/websearch/src/c114/analysis/csv_io.py`
- Create: `skills/websearch/src/c114/analysis/normalization.py`
- Create: `skills/websearch/src/c114/analysis/topic_grouping.py`
- Create: `skills/websearch/src/c114/steps/step1_analysis.py`
- Create: `skills/websearch/src/c114/steps/step1_5_topic_grouping.py`
- Create: `skills/websearch/src/c114/steps/step2_keywords.py`
- Modify: `skills/websearch/src/c114/c114_intelligence.py`
- Modify: `tests/skills/c114/test_intelligence.py`
- Modify: `tests/skills/c114/test_package_layout.py`

- [ ] **Step 1: 先写结构测试**

在 `tests/skills/c114/test_package_layout.py` 增加：

```python
def test_step1_step1_5_and_step2_modules_are_split_from_intelligence_facade() -> None:
    """Step 1/1.5/2 主体逻辑应由 steps 与 analysis 模块承载。"""

    from c114.analysis.models import ArticleAnalysis
    from c114.steps.step1_5_topic_grouping import auto_group_analysis_topics
    from c114.steps.step1_analysis import analyze_daily_articles
    from c114.steps.step2_keywords import generate_search_checklist

    assert ArticleAnalysis.__name__ == "ArticleAnalysis"
    assert callable(analyze_daily_articles)
    assert callable(auto_group_analysis_topics)
    assert callable(generate_search_checklist)
```

- [ ] **Step 2: 运行测试确认当前失败**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_package_layout.py::test_step1_step1_5_and_step2_modules_are_split_from_intelligence_facade -q
```

Expected:

```text
ModuleNotFoundError
```

- [ ] **Step 3: 迁移模型与 CSV I/O**

迁移到 `analysis/models.py`：

- `ArticleAnalysis`
- `DailyArticle`
- 与 step 1/2 共享的数据类

迁移到 `analysis/csv_io.py`：

- CSV headers
- `load_daily_articles_from_csv`
- `write_analysis_outputs`
- CSV/YAML 基础渲染中与 LLM 无关的函数

模块 docstring 示例：

```python
"""Step 1/2 共享数据模型。

这里放跨步骤传递的稳定结构，不放 LLM prompt、文件路径推导或具体执行流程。
"""
```

- [ ] **Step 4: 迁移 Step 1 单篇分析**

迁入 `steps/step1_analysis.py`：

- `analyze_daily_articles`
- 单篇 prompt payload 构造
- 单篇 LLM 结构化解析
- step 1 checkpoint 写入和补跑逻辑

关键方法体必须在 checkpoint 回填前加注释：

```python
# 先落 checkpoint 再参与最终 CSV 渲染，避免长批次中断后丢失已成功文章。
```

- [ ] **Step 5: 迁移 Step 1.5 主题聚类**

迁入 `steps/step1_5_topic_grouping.py`：

- `auto_group_analysis_topics`
- topic grouping prompt payload
- topic 数量约束和模型输出兼容处理

`analysis/topic_grouping.py` 只放纯规则辅助，例如 source-site 口径、topic 字段归一、最大 topic 数校验。

- [ ] **Step 6: 迁移 Step 2 关键词生成**

迁入 `steps/step2_keywords.py`：

- `generate_search_checklist`
- step 2 checkpoint 读取/补跑
- keyword payload 构造
- search checklist YAML 渲染入口

如果当前没有单独 `generate_search_checklist` 函数，需要先从旧文件中抽出这个明确入口，CLI 只调用这个入口。

- [ ] **Step 7: 把旧文件压成薄门面**

`c114_intelligence.py` 只保留：

- 路径命名函数：`step_1_analysis_name`、`step_2_checklist_name` 等
- 对旧公开函数的 re-export
- 迁移说明 docstring

目标行数：`<= 400`。

- [ ] **Step 8: 跑 Step 1/2 测试**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_intelligence.py tests/skills/c114/test_config.py tests/skills/c114/test_package_layout.py -q
```

Expected:

```text
passed
```

---

### Task 3: 拆 `c114_content.py`，分离 Step 4 正文抓取

**Files:**

- Create: `skills/websearch/src/c114/content/__init__.py`
- Create: `skills/websearch/src/c114/content/types.py`
- Create: `skills/websearch/src/c114/content/html.py`
- Create: `skills/websearch/src/c114/content/fetch.py`
- Create: `skills/websearch/src/c114/content/yaml_io.py`
- Create: `skills/websearch/src/c114/steps/step4_content_fetch.py`
- Modify: `skills/websearch/src/c114/c114_content.py`
- Modify: `tests/skills/c114/test_content.py`
- Modify: `tests/skills/c114/test_package_layout.py`

- [ ] **Step 1: 先写结构测试**

在 `tests/skills/c114/test_package_layout.py` 增加：

```python
def test_step4_content_fetch_is_split_from_content_facade() -> None:
    """Step 4 正文抓取应由 content 与 steps 模块承载。"""

    from c114.content.fetch import fetch_article_content
    from c114.content.html import extract_readable_text
    from c114.steps.step4_content_fetch import run_content_fetch_workflow

    assert callable(fetch_article_content)
    assert callable(extract_readable_text)
    assert callable(run_content_fetch_workflow)
```

- [ ] **Step 2: 运行测试确认当前失败**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_package_layout.py::test_step4_content_fetch_is_split_from_content_facade -q
```

Expected:

```text
ModuleNotFoundError
```

- [ ] **Step 3: 迁移正文类型**

迁入 `content/types.py`：

- `FetchResult`
- `ArticleContentPayload`
- `ContentCategoryPayload`
- `ContentWorkflowPayload`
- `ContentOutputPaths`

模块 docstring：

```python
"""Step 4 正文抓取数据模型。

这些结构是 step 3 搜索结果进入 step 5 分析前的标准中间形态。
模块不负责网络请求和 YAML 渲染。
"""
```

- [ ] **Step 4: 迁移 HTML 清洗**

迁入 `content/html.py`：

- HTML parser
- 可读正文提取
- fallback 文本截断
- 域名或编码相关的纯处理函数

- [ ] **Step 5: 迁移网络抓取**

迁入 `content/fetch.py`：

- `fetch_article_content`
- URL 请求、超时、重试、html fallback 状态分类

关键分支前加注释：

```python
# 抓取失败也要形成 FetchResult，后续 checkpoint 才能判断是补跑还是跳过。
```

- [ ] **Step 6: 迁移 YAML I/O**

迁入 `content/yaml_io.py`：

- `load_search_results_yaml`
- `save_content_results`
- `render_content_results_yaml`
- YAML parse/render 辅助

- [ ] **Step 7: 迁移 Step 4 编排**

迁入 `steps/step4_content_fetch.py`：

- `run_content_fetch_workflow`
- `resolve_content_output_paths`
- `validate_content_fetch_inputs`
- checkpoint 接线

- [ ] **Step 8: 把旧文件压成薄门面**

`c114_content.py` 只 re-export 旧公开接口，目标行数 `<= 300`。

- [ ] **Step 9: 跑 Step 4 测试**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_content.py tests/skills/c114/test_search.py tests/skills/c114/test_package_layout.py -q
```

Expected:

```text
passed
```

---

### Task 4: 第二批整体回归与提交

**Files:**

- Modify: 第二批涉及的全部文件

- [ ] **Step 1: 跑 websearch/c114 全量测试**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114 -q
```

Expected:

```text
passed
```

- [ ] **Step 2: 跑项目全量测试**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests -q
```

Expected:

```text
passed
```

- [ ] **Step 3: 跑 ruff**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/ruff check skills/websearch/src src/touzifenxi tests
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: 跑入口冒烟**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python skills/websearch/scripts/websearch.py --help
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python skills/websearch/scripts/websearch.py run --help
```

Expected:

```text
两个命令都正常输出帮助，不出现 ImportError。
```

- [ ] **Step 5: 行数验收**

Run:

```bash
wc -l \
  skills/websearch/src/c114/c114_content_analysis.py \
  skills/websearch/src/c114/c114_intelligence.py \
  skills/websearch/src/c114/c114_content.py \
  skills/websearch/src/c114/steps/*.py \
  skills/websearch/src/c114/analysis/*.py \
  skills/websearch/src/c114/content/*.py \
  skills/websearch/src/c114/brief/*.py
```

Expected:

```text
旧门面文件 <= 400 行；
新增文件原则上 <= 400 行；
如有 401-600 行，必须在提交说明中解释为什么暂时接受；
不得出现 > 600 行的新文件。
```

- [ ] **Step 6: 提交第二批**

Run:

```bash
git add skills/websearch/src/c114 tests/skills/c114
git commit -m "refactor(websearch): split c114 step workflow modules"
```

Expected:

```text
提交成功，且只包含第二批拆分相关文件。
```

---

## 3. 第三批：CLI 瘦身与统一入口固化

### Task 5: 拆 `cli.py` 为 parser、pipeline、commands

**Files:**

- Create: `skills/websearch/src/c114/pipeline.py`
- Create: `skills/websearch/src/c114/commands/__init__.py`
- Create: `skills/websearch/src/c114/commands/run.py`
- Create: `skills/websearch/src/c114/commands/step_commands.py`
- Create: `skills/websearch/src/c114/commands/config.py`
- Create: `skills/websearch/src/c114/commands/automation.py`
- Modify: `skills/websearch/src/c114/cli.py`
- Modify: `tests/skills/c114/test_scripts.py`
- Modify: `tests/governance/test_governance.py`

- [ ] **Step 1: 写 CLI 结构测试**

在 `tests/skills/c114/test_package_layout.py` 增加：

```python
def test_cli_is_split_into_pipeline_and_commands() -> None:
    """CLI 只能做 parser 和分发，完整流程进入 pipeline/commands。"""

    from c114.commands.run import handle_run_command
    from c114.pipeline import run_daily_pipeline

    assert callable(handle_run_command)
    assert callable(run_daily_pipeline)
```

- [ ] **Step 2: 运行测试确认当前失败**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_package_layout.py::test_cli_is_split_into_pipeline_and_commands -q
```

Expected:

```text
ModuleNotFoundError
```

- [ ] **Step 3: 迁移完整 run 编排**

迁入 `pipeline.py`：

- `run --source c114/infoq --date` 主流程串接
- source 分流
- run_dir 创建
- step 1 到 step 6 顺序控制
- 出错时的失败步骤和日志路径判断

`pipeline.py` 模块 docstring：

```python
"""Websearch 完整日报流水线。

本模块负责按 source 串接 step 1-6，并保持运行目录、checkpoint 和日志命名一致。
CLI 不在这里解析参数，step 模块不在这里实现业务细节。
"""
```

- [ ] **Step 4: 迁移 commands**

迁入：

- `commands/run.py`：完整日报入口 handler
- `commands/step_commands.py`：单步调试命令 handler
- `commands/config.py`：runtime config init/status/apply
- `commands/automation.py`：自动化 runner 相关 handler

每个 handler 签名保持简单：

```python
def handle_run_command(args: argparse.Namespace) -> int:
    """执行完整日报命令，返回进程退出码。"""
```

- [ ] **Step 5: `cli.py` 压缩为 parser + dispatch**

`cli.py` 保留：

- `build_parser`
- `main`
- 子命令注册
- handler 映射

目标行数：`<= 400`。

- [ ] **Step 6: 跑 CLI 测试和入口冒烟**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests/skills/c114/test_scripts.py tests/governance/test_governance.py tests/skills/c114/test_package_layout.py -q
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python skills/websearch/scripts/websearch.py --help
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python skills/websearch/scripts/websearch.py run --help
```

Expected:

```text
测试通过，两个 help 命令正常。
```

- [ ] **Step 7: 第三批整体回归与提交**

Run:

```bash
PYTHONPATH=src:skills/websearch/src ./.venv/bin/python -m pytest tests -q
PYTHONPATH=src:skills/websearch/src ./.venv/bin/ruff check skills/websearch/src src/touzifenxi tests
git add skills/websearch/src/c114 tests
git commit -m "refactor(websearch): split cli commands and pipeline"
```

Expected:

```text
测试和 ruff 均通过，提交成功。
```

---

## 4. 暂不做内容

本计划明确不做：

- 不拆 `c114_brief_review.py`
- 不把 Python 包名 `c114` 全量改成 `websearch`
- 不新增第三个网站
- 不做跨站点合并日报
- 不在同一批里拆 `src/touzifenxi/storage.py` 和 `src/touzifenxi/cli.py`

原因：

- `review` 使用频率低，当前收益不如 step 1-6 主流程拆分
- 包名迁移会触发大量历史 import 和测试重写，应该等目录职责稳定后再做
- 项目级 `storage.py`/`cli.py` 是另一条线，混入本轮会让验收边界变脏

---

## 5. 最终验收标准

第二批完成后：

- `c114_content_analysis.py`、`c114_intelligence.py`、`c114_content.py` 不再是 1000+ 行主体文件
- Step 1/1.5/2/4/5/6 都有对应 `steps/` 编排模块
- 横向能力分别进入 `analysis/`、`content/`、`brief/`
- `tests/skills/c114` 全量通过
- `pytest tests` 全量通过
- `ruff check skills/websearch/src src/touzifenxi tests` 通过

第三批完成后：

- `cli.py` 只保留 parser 和分发
- 完整流程进入 `pipeline.py`
- 子命令进入 `commands/`
- `websearch.py run --source c114` 和 `websearch.py run --source infoq` 入口不变

---

## 6. 执行建议

推荐下一次实际执行不要只做一个小函数迁移，而是直接执行完整第二批：

1. Task 1：拆 Step 5/6
2. Task 2：拆 Step 1/1.5/2
3. Task 3：拆 Step 4
4. Task 4：统一回归并提交

这是一批有明确边界的“大块改造”，但不会碰 CLI 和包名迁移，风险仍然可控。

