# News RSS Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that reads configured Google News RSS feeds, extracts article metadata, fetches article details, and uses one OpenAI-compatible model configuration to produce Chinese translations and briefs.

**Architecture:** The CLI loads YAML config, parses RSS items into `Article` records, fetches linked pages with a reusable HTTP client, extracts readable content, then sends content to the configured OpenAI-compatible chat endpoint. Outputs are written as JSON for machine use and Markdown for reading.

**Tech Stack:** Python 3.10+, `requests`, `feedparser`, `beautifulsoup4`, `trafilatura`, `PyYAML`.

---

### Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `config.example.yaml`
- Create: `src/news_brief/__init__.py`

- [ ] Create packaging metadata with dependencies and console script.
- [ ] Add a sample configuration with multiple RSS feed URLs and model timeout defaults.
- [ ] Document environment variables `MODEL`, `BASE_URL`, and `API_KEY`.

### Task 2: RSS Parsing

**Files:**
- Create: `tests/test_rss.py`
- Create: `src/news_brief/models.py`
- Create: `src/news_brief/rss.py`

- [ ] Write a failing test that parses RSS entries and extracts `title`, `link`, `pubDate`, `description`, and `source`.
- [ ] Implement `Article` and `parse_feed`.

### Task 3: Article Fetching

**Files:**
- Create: `tests/test_article_fetcher.py`
- Create: `src/news_brief/article_fetcher.py`

- [ ] Write a failing test that extracts main text from HTML.
- [ ] Implement extraction with `trafilatura`, falling back to BeautifulSoup paragraph text.

### Task 4: OpenAI-Compatible Client

**Files:**
- Create: `tests/test_openai_compatible_client.py`
- Create: `src/news_brief/openai_compatible_client.py`

- [ ] Write a failing test for parsing an OpenAI-compatible chat response.
- [ ] Implement the model client using `Authorization: Bearer <key>`.

### Task 5: Pipeline and CLI

**Files:**
- Create: `tests/test_pipeline.py`
- Create: `src/news_brief/config.py`
- Create: `src/news_brief/pipeline.py`
- Create: `src/news_brief/cli.py`

- [ ] Write a failing test for pipeline orchestration using fake RSS, fetch, and model dependencies.
- [ ] Implement config loading, output writing, and CLI arguments.
