# Dynamic Subcategory Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add model-driven dynamic subcategory grouping and rename workflow checkpoints to explicit `step1_xx` through `step8_xx` outputs.

**Architecture:** Keep the current workflow layer, but split grouping into model proposal, validation, subcategory materialization, and rendering. The model sees only article-level four-dimension records, never full article bodies. Code validates every model assignment before final synthesis.

**Tech Stack:** Python 3.10, dataclasses, JSON/YAML-like checkpoint files, existing OpenAI-compatible client, pytest.

---

### File Structure

- Modify `src/news_brief/models.py`: add subcategory proposal/group/four-dimension records.
- Create `src/news_brief/workflow/subcategories.py`: model prompt builder, response parser, validation, fallback grouping.
- Modify `src/news_brief/workflow/steps.py`: expose subcategory planning/grouping/synthesis functions.
- Modify `src/news_brief/workflow/orchestrator.py`: write `step1_...` to `step8_...` files and convenience `brief.*` copies.
- Modify `src/news_brief/openai_compatible_client.py`: add `plan_subcategories()` or generic chat use for subcategory planning.
- Add tests in `tests/test_workflow_subcategories.py`.
- Update existing workflow tests to expect the new checkpoint names.

### Task 1: Subcategory Records

**Files:**
- Modify: `src/news_brief/models.py`
- Create: `tests/test_workflow_subcategories.py`

- [ ] Add tests for serializing a subcategory proposal and validated group.
- [ ] Add dataclasses:
  - `SubcategoryProposalRecord(parent_category, name, rationale, article_ids)`
  - `SubcategoryPlanRecord(parent_category, subcategories, ungrouped, validation_errors)`
  - `SubcategoryGroupRecord(parent_category, subcategory, rationale, articles)`
  - `SubcategoryFourDimRecord(parent_category, subcategory, facts, background, impact, contradictions, source_links)`
- [ ] Run focused tests for the new records.

### Task 2: Model Prompt And Parser

**Files:**
- Create: `src/news_brief/workflow/subcategories.py`
- Modify: `tests/test_workflow_subcategories.py`

- [ ] Add a test that `build_subcategory_prompt()` includes article IDs, titles, sources, and article-level dimensions, but not raw article content.
- [ ] Add a test that parses strict JSON with `parent_category`, `subcategories`, and `ungrouped`.
- [ ] Implement prompt builder and JSON parser.
- [ ] Reject unparsable model output by returning a plan with validation errors.

### Task 3: Validation And Fallback

**Files:**
- Modify: `src/news_brief/workflow/subcategories.py`
- Modify: `tests/test_workflow_subcategories.py`

- [ ] Add tests for unknown article IDs, duplicate article IDs, vague names, missing rationale, omitted articles, and invalid response fallback.
- [ ] Implement `validate_subcategory_plan(parent_category, articles, plan)`.
- [ ] Implement fallback one-article subcategories named from article titles.
- [ ] Ensure each article appears in exactly one validated group or explicit ungrouped fallback.

### Task 4: Step Functions

**Files:**
- Modify: `src/news_brief/workflow/steps.py`
- Modify: `tests/test_workflow_steps.py`

- [ ] Add tests for `plan_dynamic_subcategories()` using a fake model planner.
- [ ] Add tests for `group_validated_subcategories()`.
- [ ] Add tests for `generate_subcategory_four_dims()` merging only records inside the same validated subcategory.
- [ ] Implement the functions by delegating validation to `workflow/subcategories.py`.

### Task 5: Orchestrator Checkpoint Rename

**Files:**
- Modify: `src/news_brief/workflow/orchestrator.py`
- Modify: `tests/test_workflow_orchestrator.py`
- Modify: `tests/test_workflow_context.py` if needed

- [ ] Update tests to expect:
  - `step1_rss_sources.json`
  - `step2_feed_items.json`
  - `step3_article_contents.json`
  - `step4_article_four_dims.json`
  - `step5_subcategory_plan.json`
  - `step5_subcategory_plan.yaml`
  - `step6_grouped_subcategories.json`
  - `step7_subcategory_four_dims.json`
  - `step8_brief.md`
  - `step8_brief.html`
  - `brief.md`
  - `brief.html`
- [ ] Modify orchestrator to write the renamed files.
- [ ] Keep `brief.md` and `brief.html` as copies of step8 outputs.

### Task 6: Final Brief Rendering

**Files:**
- Modify: `src/news_brief/workflow/orchestrator.py`
- Modify: `tests/test_workflow_orchestrator.py`

- [ ] Add a test that the Markdown brief renders `## parent_category` and `### subcategory`.
- [ ] Ensure Step 7 four dimensions are rendered under the subcategory, not under article titles.
- [ ] Ensure no full original article body appears in any checkpoint.

### Task 7: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-05-12-news-brief-checkpoint-workflow-design.md` only if implementation differs from spec.

- [ ] Update README output list to the step1-step8 names.
- [ ] Run the full test suite.
- [ ] Search for old checkpoint names such as `step_1`, `step_2_5`, `step_4_grouped_topics`, and `step_5_topic_four_dims`.

