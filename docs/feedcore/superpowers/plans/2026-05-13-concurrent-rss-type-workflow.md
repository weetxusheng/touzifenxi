# Concurrent RSS Type Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process RSS sources concurrently, persist per-RSS text and article analysis JSON files, group articles into short type collections, and synthesize each type with detailed logs.

**Architecture:** Add a concurrent workflow path under the existing workflow package while reusing RSS parsing, article fetching, text extraction, model client, and rendering helpers. RSS sources become task partitions; short types become the final reading groups. Bounded thread pools provide concurrency, with a higher default model concurrency of 6 and clear JSONL logs for RSS tasks, model calls, and type synthesis.

**Tech Stack:** Python 3.10, dataclasses, ThreadPoolExecutor, JSON/JSONL checkpoint files, pytest.

---

## File Structure

- Modify `src/news_brief/models.py`: add source-aware records for RSS tasks, article text, type plans, type collections, and type four-dimension summaries.
- Create `src/news_brief/workflow/concurrent.py`: concurrent orchestrator and helper functions for per-RSS tasks, type grouping, logs, and rendering.
- Modify `src/news_brief/workflow/orchestrator.py`: either delegate `run_workflow()` to the new concurrent orchestrator or expose compatible rendering helpers reused by it.
- Modify `src/news_brief/cli.py`: call the concurrent workflow by default and pass concurrency config.
- Modify `src/news_brief/config.py`: parse workflow concurrency defaults, with model concurrency defaulting to 6.
- Modify `README.md`: document new output layout.
- Add/modify tests in `tests/test_workflow_orchestrator.py`, `tests/test_workflow_steps.py`, `tests/test_config.py`.

## Task 1: Workflow Concurrency Config

**Files:**
- Modify: `src/news_brief/config.py`
- Modify: `tests/test_config.py`

- [ ] Add a failing test `test_parse_config_reads_workflow_concurrency_defaults` that parses `{ "rss_urls": ["https://feed.example/rss"] }` and asserts `config.workflow.rss_concurrency == 10`, `article_fetch_concurrency == 5`, `model_concurrency == 6`, `type_classification_concurrency == 2`, `type_synthesis_concurrency == 2`.
- [ ] Add a failing test `test_parse_config_reads_workflow_concurrency_overrides` that passes a `workflow` dict and asserts custom values are read.
- [ ] Add `WorkflowConfig` dataclass with those fields and defaults.
- [ ] Add `workflow: WorkflowConfig` to `AppConfig`.
- [ ] Parse `raw.get("workflow", {})` in `parse_config()` using a `_positive_int()` helper that falls back to defaults for missing or non-positive values.
- [ ] Run `python -m pytest tests/test_config.py -v` and verify it passes.

## Task 2: Source-Aware Models

**Files:**
- Modify: `src/news_brief/models.py`
- Modify: `tests/test_workflow_subcategories.py` or create `tests/test_workflow_types.py`

- [ ] Add tests for `ArticleTextRecord`, `TypeProposalRecord`, `TypePlanRecord`, `TypeCollectionRecord`, and `TypeFourDimRecord` serialization.
- [ ] Add `ArticleTextRecord(rss_id, article_id, article, default_category, text, detected_language, translated, fetch_error, extract_error, translation_error)` with `to_dict()`.
- [ ] Add `TypeProposalRecord(type_id, name, rationale, article_ids)` with `to_dict()`.
- [ ] Add `TypePlanRecord(types, ungrouped, validation_errors)` with `to_dict()`.
- [ ] Add `TypeCollectionRecord(type_id, name, rationale, articles, source_rss_ids, validation_errors)` with `to_dict()`.
- [ ] Add `TypeFourDimRecord(type_id, name, facts, background, impact, contradictions, source_links, error)` with `to_dict()`.
- [ ] Run focused serialization tests.

## Task 3: Per-RSS Concurrent Checkpoints

**Files:**
- Create: `src/news_brief/workflow/concurrent.py`
- Modify: `tests/test_workflow_orchestrator.py`

- [ ] Add an orchestrator test that runs with two fake RSS sources and asserts these files exist: `rss_tasks/rss_001_feed_items.json`, `rss_tasks/rss_001_articles_text.json`, `rss_tasks/rss_001_article_four_dims.json`, same for `rss_002`, plus `logs/rss_001.log` and `logs/model_calls.log`.
- [ ] Implement `run_concurrent_workflow(...)` with the same public inputs as `run_workflow()` plus concurrency integers.
- [ ] Generate stable `rss_id`s like `rss_001` after source selection.
- [ ] Create `rss_tasks` and `type_collections` directories in the run dir.
- [ ] Fetch feeds concurrently using `ThreadPoolExecutor(max_workers=rss_concurrency)`.
- [ ] Write one feed item JSON per RSS.
- [ ] Log RSS fetch start/done/error to `logs/rss_NNN.log` as JSONL.
- [ ] Keep `step1_rss_sources.json` and write aggregate `step2_feed_items.json` for compatibility.
- [ ] Run the new orchestrator test and verify the expected red-green behavior.

## Task 4: Text Extraction Without HTML Persistence

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Modify: `tests/test_workflow_orchestrator.py`

- [ ] Add a test that per-RSS `articles_text.json` contains `text` but does not contain `html` or `original_content`.
- [ ] In each RSS task, fetch article text with the existing `ArticleClient.fetch_text()` method. This method may fetch HTML internally, but the concurrent workflow never writes HTML to disk.
- [ ] Run article fetching concurrently with `article_fetch_concurrency`.
- [ ] Build `ArticleTextRecord` records with stable IDs like `rss001_a001`.
- [ ] If fetch fails, use `article.description or article.title` as `text`, record `fetch_error`, and continue.
- [ ] Translate English text if translator exists; record translation failure without aborting.
- [ ] Write `rss_NNN_articles_text.json` per RSS.
- [ ] Log article text extraction with `status`, `duration_ms`, and `text_chars`.

## Task 5: Concurrent Article Four-Dimension Generation

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Modify: `tests/test_workflow_orchestrator.py`

- [ ] Add a test asserting `rss_NNN_article_four_dims.json` contains `article_id`, `rss_id`, four dimensions, and model errors are retained as fallback records.
- [ ] Implement article analysis with a global `threading.Semaphore(model_concurrency)`.
- [ ] Use existing model client `summarize_article()` and existing parsing/fallback logic from `generate_article_four_dims()`.
- [ ] Log every model call to `logs/model_calls.log` with task, rss_id, article_id, model, status, duration_ms, input_chars, output_chars, and error.
- [ ] Keep failures as records; do not abort the RSS task.
- [ ] Write per-RSS `article_four_dims.json` and aggregate `step4_article_four_dims.json`.

## Task 6: Short Type Classification And Collections

**Files:**
- Create or modify: `src/news_brief/workflow/types.py`
- Modify: `src/news_brief/workflow/concurrent.py`
- Add: `tests/test_workflow_types.py`

- [ ] Add tests for parsing a strict JSON type plan with `types[].name`, `types[].rationale`, and `types[].article_ids`.
- [ ] Add validation tests rejecting unknown IDs, duplicate IDs, vague names, and names longer than 8 Chinese characters or 16 total characters.
- [ ] Implement prompt builder using article IDs, titles, source, RSS id, and article-level four dimensions, not full article text.
- [ ] Implement `parse_type_plan(raw)` and `validate_type_plan(records, plan)`.
- [ ] Implement fallback type names from existing `final_category` or short title tokens, capped and sanitized.
- [ ] In the concurrent orchestrator, write `step5_type_plan.json`, `step6_grouped_types.json`, and one `type_collections/type_XXX_name.json` file per type collection.
- [ ] Log type grouping events to `workflow.log` and type logs.

## Task 7: Concurrent Type Four-Dimension Synthesis

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Modify: `src/news_brief/openai_compatible_client.py`
- Add/modify: `tests/test_workflow_orchestrator.py`

- [ ] Add a method `summarize_type_collection(type_name, articles)` or use `chat()` with a type synthesis prompt.
- [ ] Add a test that multiple type collections produce `step7_type_four_dims.json` and logs `type_XXX_name.log`.
- [ ] Synthesize type collections concurrently with `type_synthesis_concurrency`, still behind `model_concurrency` semaphore.
- [ ] If type synthesis fails, emit a `TypeFourDimRecord` with `error` and conservative merged article-level dimensions.
- [ ] Log type synthesis start/done/error with article_count and duration.

## Task 8: Render New Final Brief And Wire CLI

**Files:**
- Modify: `src/news_brief/workflow/concurrent.py`
- Modify: `src/news_brief/workflow/orchestrator.py`
- Modify: `src/news_brief/cli.py`
- Modify: `README.md`
- Modify: `tests/test_workflow_orchestrator.py`
- Modify: `tests/test_run_news_brief_bat.py`

- [ ] Render final brief from `TypeFourDimRecord` with type names as the main `##` sections.
- [ ] Write `step8_brief.md`, `step8_brief.html`, plus `brief.md` and `brief.html` copies.
- [ ] Make `run_workflow()` delegate to `run_concurrent_workflow()` so CLI remains stable.
- [ ] Pass config workflow concurrency values from `cli.py`.
- [ ] Update README output layout and concurrency config example.
- [ ] Run `python -m pytest -v`.
- [ ] Search for old outputs that should no longer be primary, such as `step5_subcategory_plan` and `step7_subcategory_four_dims`, and update docs/tests where needed.
