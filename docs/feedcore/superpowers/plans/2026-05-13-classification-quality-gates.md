# Classification Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve brief classification by filtering low-quality article text, merging overly fine short types, and assigning fixed top-level categories without relying on `final_category`.

**Architecture:** Keep the concurrent workflow intact. Add deterministic quality gates before article analysis, consolidate type collections after model planning, and assign one of four fixed category groups from type names plus article evidence.

**Tech Stack:** Python dataclasses, existing workflow checkpoints, pytest.

---

### Task 1: Filter Low-Quality Text Before Model Analysis

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Test: `tests/test_workflow_orchestrator.py`

- [ ] Add a helper that marks text as low quality when it is too short or mostly boilerplate such as `播放中`.
- [ ] Write low-quality records to `low_quality_articles.json`.
- [ ] Exclude low-quality records from `step4_article_four_dims`.
- [ ] Log skipped counts in `step4_article_four_dims`.

### Task 2: Merge Overly Fine Type Collections

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Test: `tests/test_workflow_orchestrator.py`

- [ ] Add a canonical short-type mapping for common aliases such as `通胀冲击` and `美联储加息预期`.
- [ ] Merge collections by canonical type name after validation.
- [ ] Preserve source RSS ids, validation errors, and rationale.

### Task 3: Fixed Category Group Assignment

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Test: `tests/test_workflow_orchestrator.py`

- [ ] Assign only `国际形式`, `人工智能与科技`, `金融市场与宏观`, or `财经信息`.
- [ ] Prefer type name and article evidence over `final_category`.
- [ ] Use `final_category` only as a weak fallback.
- [ ] Store `category_group` in `step7_type_four_dims.json`.

### Task 4: Verify and Regenerate Current Output

**Files:**
- Modify generated output under `output/news_brief_20260513095954/`

- [ ] Run targeted workflow tests.
- [ ] Run full pytest.
- [ ] Re-render current latest output without model calls.
