# Concurrent RSS Task Workflow Design

## Goal

Refactor the news brief workflow so large article sets are processed as independent RSS-source tasks first, then merged into short, model-identified type collections, and finally synthesized by type using the four required dimensions.

The workflow must support concurrent fetching and concurrent integration while preserving detailed traceability through per-RSS, per-type, and model-call logs.

## Core Decisions

- RSS sources are task boundaries, not final reading categories.
- HTML is never persisted. It is fetched in memory and immediately converted to text.
- Extracted text is persisted per RSS source for traceability.
- Article-level four-dimension results are persisted per RSS source.
- Type classification happens after all per-RSS article-level outputs are available.
- Type names are model-generated but must be short, concrete, and easy to scan.
- Same-type article collections are written to JSON before type-level synthesis.
- Type-level synthesis is based only on the articles inside that type collection.

## Output Layout

Each run writes directly under `output/<run_id>/`:

```text
output/news_brief_YYYYMMDDHHMMSS/
  step1_rss_sources.json
  rss_tasks/
    rss_001_feed_items.json
    rss_001_articles_text.json
    rss_001_article_four_dims.json
    rss_002_feed_items.json
    rss_002_articles_text.json
    rss_002_article_four_dims.json
  type_collections/
    type_001_AI芯片.json
    type_002_算力基建.json
    type_003_美联储.json
  logs/
    workflow.log
    model_calls.log
    rss_001.log
    rss_002.log
    type_001_AI芯片.log
  step5_type_plan.json
  step6_grouped_types.json
  step7_type_four_dims.json
  step8_brief.md
  step8_brief.html
  brief.md
  brief.html
  execution_log.md
```

`brief.md` and `brief.html` remain convenience copies of the Step 8 outputs.

## Workflow

### step1_rss_sources

Select configured RSS sources. Quick runs should select roughly 10 sources across broad categories.

Output: `step1_rss_sources.json`.

Each source has:

- `rss_id`, for example `rss_001`
- `url`
- `label`
- `default_category`
- `selected_reason`
- `status`

### step2_fetch_feeds_concurrently

Fetch the selected RSS feeds concurrently.

Output per RSS:

```text
rss_tasks/rss_001_feed_items.json
```

This step parses article metadata only. It does not fetch article HTML, extract text, translate, or call the model.

Each feed task log records:

- RSS fetch start/end
- status
- duration
- parsed article count
- parse or fetch errors

### step3_fetch_and_extract_text_by_rss

For each RSS source, fetch article HTML concurrently and immediately extract readable text in memory. HTML must not be written to disk.

Output per RSS:

```text
rss_tasks/rss_001_articles_text.json
```

Each article record stores:

- `article_id`, for example `rss001_a003`
- article metadata
- `text`
- detected language
- translated text if translation is enabled and needed
- fetch/extract/translation status
- error messages if any

If text extraction fails, use RSS description/title as fallback where possible and mark the error.

### step4_article_four_dims_by_rss

Generate article-level four dimensions for every extracted article. This step may run concurrently across RSS sources and across articles, but all model calls share a global model concurrency limit.

Output per RSS:

```text
rss_tasks/rss_001_article_four_dims.json
```

A single article model failure must not abort the whole RSS task. The article should be retained with a model error marker and a review-needed four-dimension fallback.

### step5_type_classification

Merge all per-RSS article-level outputs and ask the model to identify short, readable types.

For large article sets, this step should work in batches:

1. Generate candidate type assignments from batches of article-level records.
2. Merge similar type names.
3. Validate type names and assignments in code.

Output:

```text
step5_type_plan.json
```

Type name rules:

- Short and scannable, ideally 2-8 Chinese characters.
- Concrete, such as `AI芯片`, `算力基建`, `模型发布`, `美联储`, `加密资产`, `地缘冲突`.
- Reject vague names such as `科技新闻`, `财经动态`, `综合观察`, `其他热点`.
- Do not use long headline-style names.
- Every assignment must reference existing `article_id`s.
- Each article should have one primary type.

### step6_grouped_types

Materialize same-type article collections from `step5_type_plan.json`.

Output:

```text
step6_grouped_types.json

type_collections/type_001_AI芯片.json
type_collections/type_002_算力基建.json
```

Each type collection JSON contains:

- type id and name
- rationale
- source RSS IDs
- article count
- article-level four-dimension records
- validation errors, if any

This checkpoint is required before type-level synthesis. It is the audit point proving which articles were grouped together.

### step7_type_four_dims

For each type collection, run type-level four-dimension synthesis concurrently, again behind the global model concurrency limit.

Output:

```text
step7_type_four_dims.json
```

Each type-level summary must use only the articles inside that type collection. It must not borrow facts from other types.

The four dimensions are:

- facts
- background
- impact
- opposing views / data contradictions

If the type collection does not support a dimension, say so explicitly rather than inventing content.

### step8_render_brief

Render final Markdown and HTML.

Output:

```text
step8_brief.md
step8_brief.html
brief.md
brief.html
```

The final brief is organized by short type names, with clear four-dimension sections under each type.

## Concurrency Model

The workflow should use bounded concurrency. Default values:

```yaml
workflow:
  rss_concurrency: 10
  article_fetch_concurrency: 5
  article_analysis_concurrency: 3
  type_classification_concurrency: 2
  type_synthesis_concurrency: 2
```

Rules:

- RSS feed fetching can run with high concurrency.
- Article HTML fetching can run with moderate concurrency.
- Text extraction is local and can run alongside fetch completion.
- Model calls must use a global semaphore so article analysis, type classification, and type synthesis do not overload the provider.
- A failed RSS task should not abort other RSS tasks.
- A failed article should not abort its RSS task.
- A failed type synthesis should not abort other types.

## Logging

Logs must be detailed enough to debug a large run without reading console output.

### workflow.log

Human-readable step-level log:

```text
2026-05-13T09:00:00Z | step2_fetch_feeds | input=10 | output=10 | skipped=0 | completed concurrent RSS fetch
```

### rss_NNN.log

JSONL per RSS task:

```json
{"time":"...","rss_id":"rss_001","event":"rss_fetch_start","url":"..."}
{"time":"...","rss_id":"rss_001","event":"rss_fetch_done","status":"ok","duration_ms":731,"article_count":12}
{"time":"...","rss_id":"rss_001","event":"article_text_extracted","article_id":"rss001_a003","url":"...","status":"ok","duration_ms":1284,"text_chars":5230}
```

### model_calls.log

JSONL model-call audit log. Never log API keys or full prompts.

```json
{"time":"...","task":"article_four_dims","rss_id":"rss_001","article_id":"rss001_a003","model":"deepseek-v4-pro","status":"ok","duration_ms":8421,"input_chars":6230,"output_chars":910,"error":null}
```

### type logs

Each type synthesis gets a type log:

```json
{"time":"...","task":"type_synthesis","type_id":"type_001","type":"AI芯片","article_count":8,"status":"ok","duration_ms":12030,"error":null}
```

## Error Handling

- HTML fetch failure: record error and fallback to RSS description/title if available.
- Text extraction failure: record error and fallback when possible.
- Translation failure: record error and keep original text for analysis.
- Article model failure: keep article with review-needed fallback four dimensions.
- Type classification failure: fallback to conservative one-article or rule-derived short types.
- Type synthesis failure: keep type collection and mark synthesis error in Step 7.

## Non-Goals

- Do not persist HTML.
- Do not introduce a heavyweight external workflow engine.
- Do not make RSS source names final reading categories.
- Do not generate long type names.
- Do not let one failed RSS, article, or type abort the entire run.
