# Dajiala Wechat Batch Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that uses Dajiala公众号 API to batch export articles for one or more WeChat public accounts, including per-account and total cost summaries.

**Architecture:** Keep the tool small: one production script with a typed client, export service, cost tracker, and CLI entrypoint. Tests mock HTTP at the request layer so behavior is verified without consuming paid API calls.

**Tech Stack:** Python 3.10+, `requests`, `python-dotenv`, `pytest`.

---

### File Structure

- Create: `dajiala_wechat_export.py` - CLI, API client, article normalization, HTML export, cost summary.
- Create: `.env.example` - configuration template for API key and optional verify code.
- Create: `requirements.txt` - runtime and test dependencies.
- Create: `tests/test_dajiala_wechat_export.py` - request construction, batch parsing, export, and cost tests.

### Task 1: Tests For Batch Parsing And Cost Summary

- [ ] Write tests that prove comma/newline account input becomes an ordered list and cost rows aggregate by account and endpoint.
- [ ] Run `python -m pytest tests/test_dajiala_wechat_export.py -v` and confirm tests fail because the script does not exist.
- [ ] Implement `parse_accounts`, `CostTracker`, and endpoint price constants.
- [ ] Re-run tests and confirm they pass.

### Task 2: Tests For API Client Request Construction

- [ ] Add tests using a fake session to verify `post_history`, `post_condition`, `article_html`, and `get_remain_money` send the expected method, URL, and API key payload.
- [ ] Run the focused tests and confirm they fail because client methods are missing.
- [ ] Implement `DajialaClient` with injectable session and no real network calls in tests.
- [ ] Re-run tests and confirm they pass.

### Task 3: Tests For Batch Export Flow

- [ ] Add tests for latest mode and all mode using a fake client returning article lists and HTML.
- [ ] Confirm export writes one HTML file per article under `exports/<account>/`.
- [ ] Implement `BatchExporter` with safe filenames, deduplication by URL/title, and per-account summaries.
- [ ] Re-run all tests and confirm they pass.

### Task 4: CLI And Configuration

- [ ] Add CLI arguments: `--accounts`, `--accounts-file`, `--mode latest|all`, `--max-pages`, `--output-dir`, `--dry-run`, `--sleep`.
- [ ] Load `DAJIALA_API_KEY` and `DAJIALA_VERIFY_CODE` from `.env`.
- [ ] Add `.env.example` and `requirements.txt`.
- [ ] Run `python -m pytest -v`.
- [ ] Run `python dajiala_wechat_export.py --help` and confirm CLI help renders.

### Self-Review

- Spec coverage: batch抓取、最新/全部、HTML导出、调用计费、费用汇总都映射到任务。
- Placeholder scan: no placeholder tasks remain.
- Type consistency: client, exporter, and tests use the same function names.
