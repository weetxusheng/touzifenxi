# Buy-Side Research Score Gate Design

## Goal

Add a buy-side fund researcher / fund manager quality gate after article text extraction and basic quality filtering, before article-level four-dimension analysis. The gate should keep only articles with clear investment research value and filter or mark low-value articles with traceable reasons.

## Why This Is Needed

The current workflow already removes obvious bad text such as empty pages, verification pages, very short snippets, and extraction failures. That still leaves many articles that are technically readable but not useful for an investment-oriented brief: political color without asset implications, soft commentary, repeated news, low-information summaries, or stories with no impact on macro, industries, companies, policy, or market pricing.

The new gate is not a general news-quality score. It is a buy-side research usefulness score. It should answer: "Would a fund researcher or portfolio manager want this article in a research brief?"

## Placement

The gate runs here:

```text
step1_rss_sources
step2_fetch_feeds
step3_fetch_and_extract_text
basic_quality_filter
step4_research_scores
filtered_by_research_score
step5_article_four_dims
step6_type_classification
step7_grouped_types
step8_type_four_dims
step9_reading_topic_groups
step10_brief
```

Rationale:

- Before text extraction is too early because title and RSS summary are not enough.
- After four-dimension generation is too late because low-value articles already consumed model budget.
- After basic quality filtering is ideal because only readable text reaches the research scorer.

## Scoring Identity

The model must act as:

```text
You are a buy-side fund researcher / portfolio manager assistant.
Your task is to judge whether this article is useful for investment research.
You do not reward popularity, sensational headlines, political entertainment, or generic news value.
You only reward information that can help form views on macro, policy, industry cycles, company fundamentals, valuation, supply-demand, risk appetite, or asset prices.
```

## Input

Each scoring call receives one article:

```json
{
  "article_id": "rss001_a003",
  "title": "...",
  "source": "...",
  "published_at": "...",
  "rss_default_category": "...",
  "text_excerpt": "正文前 5000-8000 字，优先保留开头和含数字/政策/公司名段落"
}
```

Do not send API keys, file paths, or previous model conclusions. The scorer should work from article metadata and extracted text only.

## Output

The model must return strict JSON:

```json
{
  "score": 72,
  "decision": "keep",
  "reason": "文章披露具体订单变化和潜在产业链迁移，对半导体供应链判断有研究价值。",
  "investment_relevance": 24,
  "information_increment": 18,
  "decision_value": 15,
  "verifiability": 12,
  "noise_penalty": 3,
  "evidence": [
    "特斯拉AI6芯片订单可能从三星/台积电转移至英特尔",
    "计划2026年12月流片"
  ],
  "tags": ["半导体", "供应链", "公司基本面"]
}
```

Allowed decisions:

- `keep`: `score >= 60` and evidence is non-empty.
- `drop`: `score < 60` or article has weak/no investment value.
- `pending`: model cannot reliably judge, output is malformed, or evidence does not support score.

Pending articles do not enter four-dimension analysis by default.

## Scoring Rubric

100 points total:

- Investment relevance, 30 points: connection to macro, policy, rates, FX, commodities, industry cycles, company fundamentals, valuation, risk appetite, asset prices, or supply chains.
- Information increment, 25 points: new data, policy, order, earnings, financing, regulation, supply-demand change, guidance, transaction, or credible event update.
- Decision value, 20 points: helps judge allocation, industry strength, earnings direction, competitive position, market expectation, or risk exposure.
- Verifiability, 15 points: clear subject, time, numbers, source, event, and concrete facts.
- Noise penalty, 10 points: subtract for clickbait, repeated summaries, commentary without facts, incomplete body, advertising, sensational framing, or no investment relevance.

The final score should not exceed the sum of the first four dimensions minus the noise penalty.

## Hard Validation Rules

Code validates model output before accepting it:

- Score must be an integer from 0 to 100.
- Component scores must be within their allowed ranges.
- `decision` must be one of `keep`, `drop`, `pending`.
- If `score >= 60` but `evidence` is empty, downgrade to `pending`.
- If `decision = keep` but `score < 60`, downgrade to `drop`.
- If `reason` is vague, for example "值得关注", "有一定影响", "信息较重要", downgrade to `pending`.
- If scoring JSON is invalid, create a `pending` record with error details.

## Prompt Guardrails

The prompt must explicitly forbid:

- Scoring based on headline heat alone.
- Rewarding ideological alignment or political drama.
- Adding facts not found in the article text.
- Writing generic reasons like "值得关注".
- Keeping articles without evidence.

The prompt must require:

- Cite 1-3 evidence snippets from the article.
- Explain keep/drop in one concrete sentence.
- Prefer conservative scoring when unsure.
- Drop readable but low-research-value articles.

## Checkpoint Files

Add these files:

```text
step4_research_scores.json
filtered_by_research_score.json
```

`step4_research_scores.json` contains all scored articles, including keep/drop/pending.

`filtered_by_research_score.json` contains articles excluded before four-dimension analysis, with score, decision, reason, evidence, and validation errors.

Existing step numbering after this gate should be updated or clearly documented. If renumbering is too disruptive, preserve current filenames and add the research score files between `step3_article_contents.json` and `step4_article_four_dims.json`.

## Logs

Write model call logs:

```json
{
  "task": "research_score",
  "rss_id": "rss_001",
  "article_id": "rss001_a003",
  "status": "ok",
  "duration_ms": 8421,
  "input_chars": 6200,
  "output_chars": 800,
  "score": 72,
  "decision": "keep",
  "error": null
}
```

RSS logs should also record whether each article was kept, dropped, or pending.

## Concurrency

The scorer uses the existing global model semaphore. It should run concurrently across articles but must share the same model concurrency limit as article analysis and category synthesis. This prevents runaway model calls.

## Acceptance Criteria

- Articles below 60 are not passed to article-level four-dimension generation.
- Articles with score 60+ but empty evidence are not passed through.
- Filtered articles are traceable in `filtered_by_research_score.json`.
- Logs show scoring duration and decision.
- The scorer prompt is buy-side investment oriented, not general news oriented.
- Existing low-quality text filtering still runs before scoring.
