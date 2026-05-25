# News Brief Checkpoint Workflow Design

## Goal

Refactor the news brief pipeline into an engineering-friendly, traceable workflow. Each major step writes its inputs and outputs to disk so a run can be audited, debugged, and partially rerun without depending on the final brief alone.

The workflow should support quick validation with 10 RSS sources sampled across different categories, while keeping the full configured RSS list available for production runs.

## Output Layout

Each run writes to a direct child of the configured output directory:

```text
output/news_brief_YYYYMMDDHHMMSS/
  checkpoints/
  logs/
  step1_rss_sources.json
  step2_feed_items.json
  step3_article_contents.json
  step4_article_four_dims.json
  step5_subcategory_plan.json
  step5_subcategory_plan.yaml
  step6_grouped_subcategories.json
  step7_subcategory_four_dims.json
  step8_brief.md
  step8_brief.html
  brief.md
  brief.html
  execution_log.md
```

The project should not introduce an extra `output/runs/` layer.

## Architecture

Keep the current low-level modules for RSS parsing, article fetching, content cleaning, taxonomy, and LLM clients. Add a workflow layer above them:

- `workflow/run_context.py`: creates the run directory, writes checkpoints, and records execution metadata.
- `workflow/orchestrator.py`: coordinates the ordered step execution.
- `workflow/steps.py` or focused step modules: implement step-level operations with clear inputs and outputs.
- `models.py`: add structured records for intermediate workflow data.
- `cli.py`: call the orchestrator while preserving existing config-driven usage.

Each step should be small enough to test in isolation. The orchestrator should contain sequencing logic, not business logic.

## Data Flow

### step1_rss_sources: Select RSS Sources

Input: configured RSS sources.

Output: `step1_rss_sources.json`.

Each selected source should include:

- `url`
- `label` or query name when available
- `default_category`
- `selected_reason`
- `status`

For quick tests, select 10 RSS sources across different categories rather than taking the first 10 entries.

### step2_feed_items: Fetch RSS And Parse Feed Items

Input: selected RSS sources.

Output: `step2_feed_items.json`.

This step fetches RSS XML, parses article metadata, and records fetch or parse failures per source. It does not fetch article bodies.

Deduplication should happen after feed parsing. Skipped items should be retained with a skip reason where practical.

### step3_article_contents: Fetch Article Content And Translate English

Input: feed item records.

Output: `step3_article_contents.json`.

This step fetches article bodies and stores:

- article metadata
- detected language
- translation status
- fetch error, if any

English articles should be translated to Chinese before analysis, but full original text and translated full text should not be persisted in checkpoints. They may be kept in memory for downstream analysis.

### step4_article_four_dims: Generate Article-Level Four Dimensions

Input: article content records.

Output: `step4_article_four_dims.json`.

Each article receives four dimensions:

- facts
- background
- impact
- opposing views or data contradictions

This is article-level analysis. It should not merge multiple articles.

The article's final category uses a hybrid strategy: start with the RSS source's `default_category`, then allow title/body classification to override it when content clearly belongs elsewhere.

### step5_subcategory_plan: Model Dynamic Subcategory Plan

Input: article-level four-dimension records.

Output:

- `step5_subcategory_plan.json`
- `step5_subcategory_plan.yaml`

Group records by final broad category first. For each broad category, ask the configured model to propose precise dynamic subcategories and article assignments.

The model receives compact Step 4 records, not full article bodies. It must provide a rationale for every subcategory and must not invent facts.

The YAML output should be a lightweight review checklist, for example:

```yaml
report_date: '2026-04-25'
input_path: 'output/news_brief_YYYYMMDDHHMMSS/step4_article_four_dims.json'
categories:
  - topic: '光通信产业链公司业绩'
    items:
      - original_title: '亨通光电2026年第一季度营收大涨34.09% 扣非净利飙升107.68%'
        original_url: 'https://www.c114.com.cn/ftth/5472/a1309260.html'
        review_groups:
          strong:
          weak:
          drop:
          pending:
summary:
  strong: 0
  weak: 0
  drop: 0
  pending: 0
```

### step6_grouped_subcategories: Validate And Materialize Subcategories

Input: model subcategory plan.

Output: `step6_grouped_subcategories.json`.

Code validates model output before it can affect the brief:

- unknown article IDs are rejected;
- duplicated article assignments are resolved deterministically;
- vague subcategory names are rejected;
- missing articles are placed into `ungrouped`;
- invalid model output falls back to one-article subcategories.

### step7_subcategory_four_dims: Generate Subcategory-Level Four Dimensions

Input: validated grouped subcategories.

Output:

- `step7_subcategory_four_dims.json`

This step performs second-level synthesis. It should produce one integrated four-dimension brief per validated subcategory, grounded in article-level records and preserving source attribution.

Step 7 is not a simple concatenation of Step 4. It should reconcile repeated facts, note uncertainty, and preserve contradictions or data conflicts when present.

### step8_render_brief: Render Final Brief

Input: subcategory-level four dimensions.

Output:

- `step8_brief.md`
- `step8_brief.html`
- `brief.md`
- `brief.html`

The final brief should show broad categories as `##` sections and validated dynamic subcategories as `###` sections.

## Error Handling And Traceability

Each checkpoint should include enough metadata to understand what happened without reading console output:

- run id
- step name
- timestamps
- input counts
- output counts
- skipped counts
- per-item status
- error message where applicable

`execution_log.md` should summarize the run in human-readable form.

Failures in one source or article should not abort the entire run unless configuration explicitly requires fail-fast behavior.

## Testing Strategy

Retain core unit tests for parsing, fetching, validation, config, and rendering.

Add workflow tests that use fakes for RSS, article fetch, translation, and LLM clients. Tests should verify:

- every step writes the expected checkpoint
- each checkpoint is valid JSON with expected fields
- English content is translated before downstream analysis
- default source category can be overridden by article classification
- quick-test source selection uses 10 cross-category RSS sources
- final `brief.md` and `brief.html` are generated

When removing tests, delete only obsolete or duplicate demo-style tests. Do not delete coverage that protects production behavior.

## Non-Goals

- Do not introduce a heavyweight workflow engine.
- Do not require all steps to call external APIs in tests.
- Do not remove the existing CLI behavior without a compatibility path.
- Do not write final-only output that hides intermediate state.
