# Reading Topic Event Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce single-event ownership and four-factor closure so any topic that borrows a factor from another event is deleted from the final reading brief.

**Architecture:** Keep the change inside the reading-topic formation layer. Add failing tests first, then introduce topic ownership heuristics in `reading_topics.py`, and finally verify the workflow still renders valid parent events while deleting invalid borrowed subtopics.

**Tech Stack:** Python, pytest, existing `news_brief.workflow.reading_topics` pipeline

---

### Task 1: Add Regression Tests For Borrowed-Factor Deletion

**Files:**
- Modify: `e:/AI/FeedCore/tests/test_reading_topics.py`
- Test: `e:/AI/FeedCore/tests/test_reading_topics.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reading_topic_groups_drop_topic_when_background_belongs_to_parent_event():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="人工智能与科技",
            facts=["英伟达正推动向中国销售H200人工智能芯片，但出货尚未启动。"],
            background=["此次访问正值华盛顿与北京试图维持脆弱贸易休战。"],
            impact=["美国企业高管随行旨在争取中国市场机会与监管审批。"],
            contradictions=["部分美国国会议员及科技业界反对英伟达向中国出口H200芯片。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="特朗普访华",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日至15日在北京与习近平会晤，议题包括贸易、台湾和人工智能竞争。"],
            background=["中美双方将讨论出口管制与高端芯片问题。"],
            impact=["外界关注双方是否讨论AI安全规范与军事应用风险。"],
            contradictions=["本次会谈能否达成贸易安排仍存在不确定性。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    by_category = {item.category_group: item for item in categories}

    assert "人工智能与科技" not in by_category
    assert "国际形式" in by_category
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reading_topics.py::test_reading_topic_groups_drop_topic_when_background_belongs_to_parent_event -q`

Expected: FAIL because the AI topic still survives today.

- [ ] **Step 3: Add one safety test for independent survival**

```python
def test_reading_topic_groups_keep_topic_when_four_factors_are_self_owned():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="人工智能与科技",
            facts=["英伟达正推动向中国销售H200人工智能芯片，但出货尚未启动。"],
            background=["美国对华高端芯片出口限制持续收紧，H200审批因此成为焦点。"],
            impact=["美国企业高管随行旨在争取中国市场机会与监管审批。"],
            contradictions=["部分美国国会议员及科技业界反对英伟达向中国出口H200芯片。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    tech = next(item for item in categories if item.category_group == "人工智能与科技")
    assert [topic.name for topic in tech.topics] == ["AI基础设施"]
```

- [ ] **Step 4: Run both tests and verify red**

Run: `pytest tests/test_reading_topics.py::test_reading_topic_groups_drop_topic_when_background_belongs_to_parent_event tests/test_reading_topics.py::test_reading_topic_groups_keep_topic_when_four_factors_are_self_owned -q`

Expected: first test fails; second may pass or fail, but at least one red case proves the new behavior is not implemented yet.

### Task 2: Implement Event Ownership And Closure Gate

**Files:**
- Modify: `e:/AI/FeedCore/src/news_brief/workflow/reading_topics.py`
- Test: `e:/AI/FeedCore/tests/test_reading_topics.py`

- [ ] **Step 1: Introduce topic-owned sentence classification**

```python
def _sentence_primary_event_score(text: str, rules: tuple[tuple[str, int], ...]) -> int:
    ...

def _is_borrowed_background_sentence(text: str, category: str, topic_name: str, record: TypeFourDimRecord) -> bool:
    ...
```

Implement them so background counts only when it supports the topic's own event rather than a stronger parent event.

- [ ] **Step 2: Tighten factor filtering to count only owned evidence**

```python
def _topic_filtered_values(...):
    ...
    if attr == "background" and _is_borrowed_background_sentence(...):
        continue
```

Do not remove direct chip-policy background that belongs to the AI issue itself.

- [ ] **Step 3: Make closure a hard gate**

```python
def _topic_meets_quality_bar(category: str, topic_name: str, records: list[TypeFourDimRecord]) -> bool:
    facts = _topic_filtered_values(...)
    background = _topic_filtered_values(...)
    impact = _topic_filtered_values(...)
    contradictions = _topic_filtered_values(...)
    return bool(facts and background and impact and contradictions)
```

Keep the implementation minimal, but ensure the gate reflects the business rule: no topic survives with a borrowed or empty factor.

- [ ] **Step 4: Run the focused tests to verify green**

Run: `pytest tests/test_reading_topics.py::test_reading_topic_groups_drop_topic_when_background_belongs_to_parent_event tests/test_reading_topics.py::test_reading_topic_groups_keep_topic_when_four_factors_are_self_owned -q`

Expected: PASS

### Task 3: Protect Existing Reading-Topic Behavior

**Files:**
- Modify: `e:/AI/FeedCore/tests/test_reading_topics.py` only if assertions need to be updated for the stricter rule
- Test: `e:/AI/FeedCore/tests/test_reading_topics.py`

- [ ] **Step 1: Re-run current reading-topic regression cases**

Run: `pytest tests/test_reading_topics.py -q`

Expected: All reading-topic tests pass, including:
- valid international main events still render;
- weak AI topics are dropped;
- independent AI topics still survive;
- report title and numbered sections still render.

- [ ] **Step 2: Make minimal test updates if old assertions relied on placeholder survival**

```python
assert "未形成可追溯要点。" not in markdown
```

Only change tests where the new business rule intentionally invalidates prior output.

- [ ] **Step 3: Re-run `tests/test_reading_topics.py -q` until green**

Run: `pytest tests/test_reading_topics.py -q`

Expected: PASS

### Task 4: Verify Workflow-Level Brief Rendering

**Files:**
- Test: `e:/AI/FeedCore/tests/test_workflow_orchestrator.py`
- Modify: `e:/AI/FeedCore/src/news_brief/workflow/concurrent.py` only if workflow checkpoints require a small guardrail update

- [ ] **Step 1: Run the workflow regressions that exercise final brief rendering**

Run: `pytest tests/test_workflow_orchestrator.py::test_run_workflow_writes_all_step_files_under_output_run_id tests/test_workflow_orchestrator.py::test_category_group_prefers_type_and_evidence_over_final_category -q`

Expected: PASS or a focused failure caused by the new closure rule.

- [ ] **Step 2: If needed, keep valid parent-event output while allowing borrowed subtopics to disappear**

```python
brief_md.write_text(step10_md.read_text(encoding="utf-8"), encoding="utf-8")
```

No renderer-specific workaround should resurrect invalid topics.

- [ ] **Step 3: Run the full related suite**

Run: `pytest tests/test_reading_topics.py tests/test_markdown_link_rendering.py tests/test_workflow_orchestrator.py tests/test_research_gate.py tests/test_workflow_types.py tests/test_brief_validation.py -q`

Expected: PASS

### Task 5: Refresh The Sample Output For Manual Review

**Files:**
- Modify generated outputs under: `e:/AI/FeedCore/output/news_brief_20260514133718/`

- [ ] **Step 1: Rebuild reading-topic output from step7 artifacts**

Run:

```bash
python <temp rerender script that:
  - loads output/news_brief_20260514133718/step7_type_four_dims.json
  - rebuilds reading categories with build_reading_topic_groups(...)
  - rewrites step9_reading_topic_groups.json
  - rewrites step10_brief.md/html
  - rewrites final brief.md/html
>
```

Expected: the invalid borrowed AI topic disappears from the final sample output.

- [ ] **Step 2: Spot-check the resulting markdown**

Run: `Get-Content -Encoding utf8 output\\news_brief_20260514133718\\brief.md | Select-Object -First 120`

Expected: the parent international event remains; the borrowed standalone AI topic is gone if it cannot close independently.

## Self-Review

- Spec coverage: this plan covers event ownership, four-factor closure, deletion of borrowed subtopics, preservation of valid parent events, and manual rerender of the sample output.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: all tasks target the existing `build_reading_topic_groups` / `_topic_filtered_values` / `_topic_meets_quality_bar` flow and keep tests centered on those interfaces.
