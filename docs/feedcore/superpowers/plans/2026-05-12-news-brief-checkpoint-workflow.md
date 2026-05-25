# News Brief Checkpoint Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a traceable news brief workflow that writes RSS sources, feed items, article content, article-level four dimensions, grouped topics, and topic-level briefs into `output/<run_id>/`.

**Architecture:** Add a small workflow layer above the current RSS, article fetching, taxonomy, and summary clients. Keep each step testable and avoid a heavyweight workflow engine. The CLI will call the new orchestrator and keep compatibility with existing config.

**Tech Stack:** Python 3.10, dataclasses, JSON checkpoint files, existing RSS/article/LLM modules.

---

### Task 1: Environment JSON Loader

**Files:**
- Modify: `src/news_brief/env.py`
- Modify: `tests/test_env.py`
- Create: `config/env.json`

- [ ] Add a failing test showing `load_env_json()` reads `config/env.json` without overriding existing variables.
- [ ] Implement `load_env_json(path=Path("config/env.json"))`.
- [ ] Add a placeholder `config/env.json` with expected environment variable names and empty values.

### Task 2: Workflow Models And Run Context

**Files:**
- Modify: `src/news_brief/models.py`
- Create: `src/news_brief/workflow/__init__.py`
- Create: `src/news_brief/workflow/run_context.py`
- Create: `tests/test_workflow_context.py`

- [ ] Add failing tests for creating `output/<run_id>/`, writing JSON checkpoints, and writing `execution_log.md`.
- [ ] Add compact dataclasses for source, feed item, content, article dimensions, topic groups, and topic briefs.
- [ ] Implement `RunContext` with `write_json()`, `log_step()`, and `write_execution_log()`.

### Task 3: Workflow Steps

**Files:**
- Create: `src/news_brief/workflow/steps.py`
- Create: `tests/test_workflow_steps.py`

- [ ] Add failing tests for source selection, feed parsing, content translation, category override, grouping, and topic synthesis.
- [ ] Implement step functions using dependency injection for RSS, article, translation, and summary clients.

### Task 4: Orchestrator And CLI

**Files:**
- Create: `src/news_brief/workflow/orchestrator.py`
- Modify: `src/news_brief/cli.py`
- Modify: `README.md`
- Create: `tests/test_workflow_orchestrator.py`

- [ ] Add failing tests proving the orchestrator writes all step files under `output/<run_id>/`.
- [ ] Implement orchestration and route CLI execution through it.
- [ ] Preserve compatibility with existing config and `--max-articles`.

### Task 5: Cleanup And Verification

**Files:**
- Review: `tests/`

- [ ] Remove only obsolete generated caches or demo-only test artifacts, not behavioral tests.
- [ ] Verify no output is written to `output/runs/`.
