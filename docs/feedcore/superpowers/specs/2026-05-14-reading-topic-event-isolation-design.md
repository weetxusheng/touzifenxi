# Reading Topic Event Isolation Design

## Goal

Tighten the final reading-topic brief so every rendered topic is a complete event rather than a partial slice borrowed from a larger event.

The business rule is strict:

- every rendered topic must have a complete four-factor chain;
- all four factors must be supported by evidence that belongs to that same topic;
- if any factor must be borrowed from another event, the topic is invalid and must disappear completely from the final brief.

This design is intended to prevent outputs such as an AI topic that only exists because it borrows background from a broader `特朗普访华` event.

## Current Problem

The current pipeline first maps type-level records into fixed category groups and topic buckets, then filters individual sentences per factor. That approach can produce a topic that looks complete on the page but is not actually self-contained:

- the `facts` may be about H200 chip sales;
- the `impact` may be about executives seeking approvals;
- the `background` may only make sense because the article is really about the Trump China visit;
- the final output still renders the AI topic as though it were an independent event.

This creates two business problems:

1. the user sees duplicated event fragments across multiple topics;
2. a topic can survive even though its four factors do not belong to one coherent event.

## Decision

Use `event ownership + four-factor closure` as the governing rule for final topic generation.

In practice:

1. each type-level record is first treated as evidence for one primary event;
2. candidate topics are allowed only when their own evidence can close all four factors;
3. if a candidate topic needs another event's sentence to close a missing factor, it is removed rather than downgraded or hidden;
4. sub-issues that cannot close independently do not appear anywhere as standalone topics.

## Definitions

### Primary Event

The dominant real-world event represented by a record or small cluster of records.

Examples:

- `特朗普访华并与习近平会谈`
- `英伟达推动向中国销售H200芯片`
- `美国上诉法院推翻非法越境者不得保释政策`

The primary event is not the same as a broad domain label such as `人工智能与科技` or `国际形式`.

### Topic Ownership

A candidate topic owns a sentence only when that sentence directly advances the topic's own event chain rather than merely providing context for a different event.

### Four-Factor Closure

A topic is closed only when `事实 / 背景 / 影响 / 反面观点` can all be supported from evidence owned by that topic.

Closure is a hard gate. There is no partial pass.

## Business Rules

### 1. Single-Event Ownership By Default

Each source record should default to one primary event.

The system should not freely split one record into multiple standalone topics just because multiple domains are mentioned in the same article. Mentioning AI inside a state visit article does not make AI an independent event.

### 2. Topic Validity Requires Full Closure

A candidate topic is valid only if all four factors are present after filtering and each factor is supported by owned evidence.

If any factor is missing, generic, or only derivable from another event, the topic is invalid.

Invalid topics are dropped completely from:

- `step9_reading_topic_groups.json`
- `step10_brief.md`
- `step10_brief.html`
- final `brief.md`
- final `brief.html`

### 3. No Borrowed Background

Background is not a filler field.

If a topic's background is actually the setup for another event, that background cannot be reused to keep the topic alive. This is the core rule behind removing the current `AI基础设施` example when its background is really part of `特朗普访华`.

### 4. No Placeholder Survival

Topics must not survive behind placeholders such as `未形成可追溯要点。`

A placeholder is acceptable only as a rendering fallback for an otherwise valid topic during intermediate development, not as production evidence that a topic exists.

### 5. No Standalone Sub-Issue Without Independent Evidence

A sub-issue may be mentioned inside a larger event summary, but it must not become its own topic unless it independently closes all four factors.

The user has explicitly chosen the stricter rule: if independent closure is impossible, the sub-issue should disappear completely rather than remain visible as a secondary topic.

## Proposed Pipeline Changes

### Stage A: Determine Primary Event Strength

Before final topic rendering, score each type-level record for its strongest event identity using:

- type name;
- fact sentences;
- background sentences;
- impact sentences;
- contradiction sentences;
- event-specific keyword weights.

This score should distinguish between:

- a main event anchor, such as `访华 / 会晤 / 峰会 / 战争 / 法院裁决`;
- a sub-issue anchor, such as `AI / 芯片 / 审批 / 市场准入`.

If the record contains both, the system should choose the stronger primary event unless the sub-issue itself has full four-factor evidence.

### Stage B: Build Candidate Topics Conservatively

Candidate topics can still be formed from the existing fixed category and topic taxonomy, but they must be treated as provisional.

At this stage, the system may group records into buckets such as:

- `中美与国际冲突`
- `美国治理动态`
- `AI基础设施`
- `模型与产品应用`

No bucket is guaranteed to survive.

### Stage C: Sentence Ownership Filtering

For each factor sentence in a candidate topic, classify it as:

- `owned`: directly supports this topic's event;
- `shared-context`: mostly sets up another event;
- `generic`: too vague to support topic validity;
- `fragment`: not semantically complete.

Only `owned` sentences count toward closure.

Examples:

- `特朗普总统于当地时间5月13日晚抵达北京。`
  - should count toward the state-visit event;
  - should not count toward `AI基础设施`.

- `此次访问正值华盛顿与北京试图维持脆弱贸易休战。`
  - if used under the visit event, it can be owned background;
  - if used under an AI chip topic, it is borrowed background and should not count.

- `部分美国国会议员及科技业界反对英伟达向中国出口H200芯片。`
  - can count as owned contradiction for an H200 topic because it directly addresses that issue.

### Stage D: Four-Factor Closure Validation

After ownership filtering, validate each candidate topic.

A topic passes only if:

- `facts` has at least one owned sentence;
- `background` has at least one owned sentence;
- `impact` has at least one owned sentence;
- `contradictions` has at least one owned sentence.

Additionally, the set of owned sentences must point to one coherent event chain rather than four unrelated sentences that merely share keywords.

### Stage E: Dominance Check Against Parent Event

If a candidate topic fails closure but its evidence clearly belongs to a stronger primary event, the weaker topic is removed and the stronger one remains eligible.

This is not a merge step that creates a new child topic. It is a deletion rule for non-independent subtopics.

## Heuristic Strategy

The implementation should use deterministic heuristics grounded in the available data rather than freeform model judgment.

Recommended signals:

- named actors: `特朗普`, `习近平`, `黄仁勋`, `英伟达`, `美联储`;
- event verbs: `会晤`, `访华`, `裁决`, `销售`, `审批`, `推出`, `制裁`;
- issue nouns: `芯片`, `贸易`, `关税`, `停火`, `军控`, `移民`;
- causal framing: `导致`, `引发`, `推动`, `旨在`, `可能影响`;
- contradiction framing: `反对`, `未确认`, `仍不确定`, `存在分歧`.

The key distinction is not simply whether a sentence contains AI or trade tokens. It is whether the sentence advances the candidate topic's own event.

## Example: Current Trump Visit And H200 Case

### Input Situation

The article contains:

- Trump arriving in Beijing;
- Nvidia pushing H200 sales to China;
- executives seeking market access and approvals;
- the broader context of a fragile US-China trade truce.

### Expected Outcome

If the H200 topic cannot produce its own independent background that is about the H200 issue rather than about the state visit, the H200 topic fails closure and is deleted.

The result should be:

- the `AI基础设施` topic does not appear at all;
- the `特朗普访华` or `中美与国际冲突` event remains if it closes independently;
- no AI topic survives with borrowed visit context.

## Rendering Rules

The renderer should assume upstream validity is already enforced.

Therefore:

- it should not render placeholder factor lines for invalid topics;
- it should not preserve topics with empty factor lists;
- it should only render topics that passed closure validation.

This keeps the final brief aligned with the user's reading expectation: every visible topic is complete and worth reading.

## Testing Strategy

Add or update tests for the following cases:

1. `borrowed background deletes topic`
   - a candidate AI topic has valid fact, impact, contradiction, but its only background belongs to a visit event;
   - expected: AI topic is absent.

2. `shared article but independent topic survives`
   - one article mentions a visit and an AI issue;
   - the AI issue also has its own background and contradiction tied directly to chips or approvals;
   - expected: AI topic may survive only if all four factors are independently owned.

3. `main event survives after subtopic deletion`
   - removing the borrowed subtopic does not accidentally delete the valid main event.

4. `no placeholder-based survival`
   - a topic with any empty factor should not render with `未形成可追溯要点。` as a production fallback.

5. `no duplicate standalone topics from one incomplete event`
   - the same article should not generate multiple final topics when only one of them is actually closed.

## Non-Goals

- Do not use a second-pass generative model to invent missing factors.
- Do not keep weak topics merely because they contain high-interest keywords such as `AI` or `特朗普`.
- Do not preserve a topic for category coverage if it fails closure.
- Do not merge unrelated events solely to avoid empty output.

## Implementation Notes

This design should be implemented inside the reading-topic formation layer rather than only in the HTML renderer.

The likely change points are:

- topic assignment;
- sentence ownership filtering;
- topic quality validation;
- final rendering guardrails.

The output checkpoints should continue to make the decision traceable so the user can inspect why a topic was dropped.
