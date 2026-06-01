# Global Article Fetch Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make RSS feed fetching serial by default and move article text fetching to one global concurrency pool so large RSS runs do not multiply thread/browser pressure.

**Architecture:** Keep the existing concurrent workflow file, but split RSS feed parsing from article text fetching and model analysis. RSS feed files are written per source first; then all feed items enter one global article fetch pool capped by `workflow.article_fetch_concurrency`; then low-quality gating and article four-dimension analysis run globally with the existing model semaphore.

**Tech Stack:** Python `ThreadPoolExecutor`, existing `RunContext`, existing workflow models, pytest.

---

### Task 1: Add Regression Test For Global Article Fetch Limit

**Files:**
- Modify: `tests/test_workflow_orchestrator.py`

- [ ] Add a test with 3 RSS sources and 3 articles per source.
- [ ] Use a blocking fake article client that counts active calls and records max active calls.
- [ ] Run workflow with `rss_concurrency=1`, `article_fetch_concurrency=2`, `model_concurrency=2`.
- [ ] Assert `max_active <= 2` and all per-RSS checkpoint files still exist.

### Task 2: Refactor Workflow Stages

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`

- [ ] Replace `_run_rss_tasks()` usage with serial feed fetching when `rss_concurrency=1`.
- [ ] Add `_fetch_all_text_records()` that accepts all `(source, feed item, article id)` jobs and uses one `ThreadPoolExecutor(max_workers=article_fetch_concurrency)`.
- [ ] Keep per-RSS `rss_tasks/{rss_id}_articles_text.json` outputs by grouping fetched records after the global pool completes.
- [ ] Add `_analyze_all_articles()` using one global model worker pool capped by `model_concurrency` plus the existing `model_semaphore`.
- [ ] Keep per-RSS `rss_tasks/{rss_id}_article_four_dims.json` outputs by grouping analysis records after analysis completes.

### Task 3: Update Defaults For Large Runs

**Files:**
- Modify: `config.yaml`
- Modify: `config.example.yaml`

- [ ] Set `workflow.rss_concurrency: 1`.
- [ ] Set `workflow.article_fetch_concurrency: 12`.
- [ ] Keep `workflow.model_concurrency: 8` for a strong but bounded model pool.
- [ ] Keep type classification and synthesis at 3.

### Task 4: Verify

**Files:**
- No production file changes.

- [ ] Run targeted workflow tests.
- [ ] Run full pytest.
- [ ] Optionally run a bounded 120-source smoke test with `quick_sample_size=120` and `max_articles=1`.
