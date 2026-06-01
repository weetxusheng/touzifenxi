# Dynamic Subcategory Briefing Design

## Goal

Improve reading experience by splitting each broad category into precise, data-grounded subcategories before generating topic-level four-dimension briefs.

Subcategories should be dynamically identified by the configured model from the current run's article data. The code must validate model output and prevent unsupported grouping, vague labels, or invented claims.

## Current Gap

The workflow currently assigns each article to a broad category, then groups articles with a simple title-token key. This is not precise enough:

- related articles can be split into several repeated sections;
- unrelated articles with shared generic terms can be merged;
- final four-dimension synthesis can become too broad and easier to overstate.

## Proposed Flow

Insert explicit model-assisted subcategory steps between article-level four dimensions and final synthesis:

```text
step1_rss_sources: select RSS sources
step2_feed_items: fetch RSS and parse feed items
step3_article_contents: fetch article content and translate English in memory
step4_article_four_dims: generate article-level four dimensions
step5_subcategory_plan: model proposes dynamic subcategories inside each broad category
step6_grouped_subcategories: code validates and materializes grouped subcategories
step7_subcategory_four_dims: generate four dimensions per validated subcategory
step8_render_brief: render final Markdown and HTML
```

Every user-visible output file should start with the step prefix so the run directory reads like an execution trace.

## Model Input

The model should not receive full article bodies for subcategory planning. It should receive compact, traceable records from Step 3:

```yaml
parent_category: 人工智能与科技
articles:
  - id: A1
    title: ...
    source: ...
    url: ...
    facts:
    background:
    impact:
    contradictions:
```

This keeps grouping grounded in already extracted evidence and avoids re-reading raw content.

## Required Model Output

The model must output strict JSON or YAML that can be parsed into:

```yaml
parent_category: 人工智能与科技
subcategories:
  - name: AI芯片与算力基础设施
    rationale: 多篇文章共同涉及 GPU、数据中心、算力投资或芯片供应链。
    article_ids: [A1, A3, A7]
ungrouped:
  - article_id: A9
    reason: 与其它文章缺少共同实体或共同议题。
```

Each subcategory name should be short, specific, and readable. Vague names such as “科技新闻”, “财经动态”, “国际观察”, or “其它热点” are invalid.

## Validation Rules

Code must validate the model output before using it:

- Every referenced `article_id` must exist in the parent category input.
- Each article can appear in at most one subcategory.
- Empty subcategories are discarded.
- Subcategories without a rationale are invalid.
- Vague subcategory names are invalid.
- If a model omits an article, the code places it in `ungrouped`.
- If the model duplicates an article across subcategories, keep the first valid assignment and move later duplicates to `ungrouped` with a reason.
- If the whole model response is invalid or unparsable, fall back to one-article subcategories named from article titles.

## Synthesis Rules

`step7_subcategory_four_dims` must generate four dimensions only from articles assigned to the same validated subcategory:

- Do not borrow facts from sibling subcategories.
- Do not infer trend claims from a single article.
- Preserve contradictions and uncertainty from article-level records.
- If a dimension has no support in the subcategory records, say it is not supported rather than inventing content.

## Checkpoint Outputs

Add or revise workflow outputs:

```text
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
```

`step5_subcategory_plan.*` stores model proposals and validation results. `step6_grouped_subcategories.json` stores the final validated grouping used by Step 7.

`brief.md` and `brief.html` can remain as convenience copies of `step8_brief.md` and `step8_brief.html`.

## Final Brief Structure

```markdown
## 人工智能与科技

### AI芯片与算力基础设施

#### 事实
- ...

#### 背景
- ...

#### 产生的影响
- ...

#### 反面观点 / 数据矛盾点
- ...
```

The `##` level remains the broad category. The `###` level becomes the validated dynamic subcategory.

## Non-Goals

- Do not introduce a fixed subcategory taxonomy.
- Do not persist full article bodies.
- Do not allow model-only claims without article-level evidence.
- Do not force every article into a multi-article group.
