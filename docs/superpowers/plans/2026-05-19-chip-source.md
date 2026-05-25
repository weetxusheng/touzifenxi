# Chip 产业新闻源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 SEMI 中国 + 老姚吧 两站点作为 `chip` 单一聚合源，每日产出"产业新闻日报" Markdown + 邮件，按主题（新品发布 / 价格变动 / 产能 / 供需 / 节点突破）分桶。

**Architecture:** 沿用 InfoQ-lean 模板（`src/<source>/` 三文件 + 直接调 `utils/tools/facades/*`）。在 source 内部多一层 channel 分发：`ChipSourceAdapter` 路由到 `channels/semi.py` 与 `channels/laoyaoba.py`，每个 channel 实现 listing + article 抓取。Step 1-7 全部复用现有 facades；Step 5/6 注入 `CHIP_THEMES` 关键词主题白名单做跨 channel 主题分桶 + 标题去重。

**Tech Stack:** Python 3.11+，`httpx`（已在依赖中），`BeautifulSoup4` (`bs4`)，`pytest`，复用项目内 `utils.tools.facades.{content,content_analysis,intelligence}` / `utils.tools.llm.StructuredChatClient` / `c114.runtime.config.load_c114_runtime_config`（项目全局 runtime config 的事实命名）。

**Repo 状态备注：** `/root/workspace/shiqiang/touzifenxi` 当前不是 git 仓库根。每个 Task 末尾的 "Commit / Checkpoint" 步骤如 git 命令失败，可改为 `pytest tests/chip -v` 作为验证 checkpoint 替代。

**Spec：** `docs/superpowers/specs/2026-05-19-chip-source-design.md`

---

## File Structure

### Files to Create

```
src/chip/__init__.py
src/chip/settings.py
src/chip/cli.py
src/chip/source_adapter.py
src/chip/pipeline.py
src/chip/topic_grouping.py
src/chip/channels/__init__.py
src/chip/channels/spec.py
src/chip/channels/semi.py
src/chip/channels/laoyaoba.py
src/chip/channels/datetime_parse.py
src/utils/tools/research/chip_themes.py
src/utils/tools/research/__init__.py        # 如不存在则创建

tests/chip/__init__.py
tests/chip/conftest.py
tests/chip/fixtures/semi_home.html          # 92KB（从 /tmp 拷）
tests/chip/fixtures/semi_article_intel14a.html        # 11KB
tests/chip/fixtures/semi_column_hotnews.html          # 21KB
tests/chip/fixtures/laoyaoba_xinyaowen.html           # 84KB
tests/chip/fixtures/laoyaoba_article_icboard.html     # 32KB ("3小时前")
tests/chip/fixtures/laoyaoba_article_smic.html        # 32KB ("05-14 18:18")
tests/chip/fixtures/laoyaoba_article_legacy.html      # 52KB ("2019-09-26")
tests/chip/test_settings.py
tests/chip/test_datetime_parse.py
tests/chip/test_channels_spec.py
tests/chip/test_channels_semi.py
tests/chip/test_channels_laoyaoba.py
tests/chip/test_source_adapter.py
tests/chip/test_chip_themes.py
tests/chip/test_topic_grouping.py
tests/chip/test_pipeline.py
tests/chip/test_cli_smoke.py
tests/utils/test_email_render.py
```

### Files to Modify

```
src/utils/tools/output/email.py        # 参数化 footer_disclaimer
src/utils/cli.py                       # 新增 run-chip-daily-brief / send-chip-latest-brief-email
scripts/websearch.py                   # 按 --source 分发
pyproject.toml                         # 注册 chip_network pytest marker
```

### Responsibility Map

| 文件 | 单一职责 |
|------|---------|
| `src/chip/__init__.py` | 包入口，导出 `ChipSourceAdapter` |
| `src/chip/settings.py` | 路径解析、`.env.local` 读取（克隆自 infoq）|
| `src/chip/cli.py` | argparse + 步骤编排（克隆自 infoq/cli.py 改前缀）|
| `src/chip/source_adapter.py` | `ChipSourceAdapter`：channel 路由 + 同 channel 去重 + 异常隔离 |
| `src/chip/channels/spec.py` | `ChipChannelSpec` dataclass + `CHANNELS` 注册表 |
| `src/chip/channels/semi.py` | SEMI listing 解析 + article 解析 + 抓取节流 |
| `src/chip/channels/laoyaoba.py` | 老姚吧 listing 解析 + article 解析 + 节流 |
| `src/chip/channels/datetime_parse.py` | 三态时间解析（"3小时前" / "MM-DD HH:MM" / "YYYY-MM-DD"）|
| `src/chip/topic_grouping.py` | 按 CHIP_THEMES 做确定性主题分桶（替换 LLM 聚类）|
| `src/chip/pipeline.py` | 与 c114.pipeline 接口对齐：`run_chip_daily_brief` + `send_latest_chip_brief_email` |
| `utils/tools/research/chip_themes.py` | `CHIP_THEMES` 字典 + `classify_chip_themes` 多标签命中 |

---

## Task 1: Scaffold package and pytest infrastructure

**Files:**
- Create: `src/chip/__init__.py`
- Create: `src/chip/channels/__init__.py`
- Create: `tests/chip/__init__.py`
- Create: `tests/chip/conftest.py`
- Create: `tests/chip/fixtures/*.html`（从 `/tmp` 拷贝实测样本）
- Modify: `pyproject.toml` 注册 marker

- [ ] **Step 1: Create directories and empty package files**

```bash
cd /root/workspace/shiqiang/touzifenxi
mkdir -p src/chip/channels tests/chip/fixtures
touch src/chip/__init__.py src/chip/channels/__init__.py
touch tests/chip/__init__.py
```

- [ ] **Step 2: Write `src/chip/__init__.py`**

```python
"""Chip 产业新闻聚合源（SEMI 中国 + 老姚吧/集微网）。"""

from chip.source_adapter import ChipSourceAdapter

__all__ = ["ChipSourceAdapter"]
```

- [ ] **Step 3: Write `src/chip/channels/__init__.py`**

```python
"""Chip 源各 channel 实现。"""
```

- [ ] **Step 4: Copy实测 HTML fixtures from `/tmp/`**

```bash
cp /tmp/semi_home.html               tests/chip/fixtures/semi_home.html
cp /tmp/semi_n.html                  tests/chip/fixtures/semi_article_intel14a.html
cp /tmp/semi_col.html                tests/chip/fixtures/semi_column_hotnews.html
cp /tmp/lyb_xyw.html                 tests/chip/fixtures/laoyaoba_xinyaowen.html
cp /tmp/laoyaoba_article_icboard.html tests/chip/fixtures/laoyaoba_article_icboard.html
cp /tmp/laoyaoba_article_smic.html    tests/chip/fixtures/laoyaoba_article_smic.html
cp /tmp/laoyaoba_article_legacy.html  tests/chip/fixtures/laoyaoba_article_legacy.html
ls -la tests/chip/fixtures/
```

Expected output: 7 files, sizes roughly 11KB-92KB.

If any `/tmp` source is missing, re-fetch via `curl -sSL -A "Mozilla/5.0" -o tests/chip/fixtures/<name>.html <URL>` per spec §3.

- [ ] **Step 5: Write `tests/chip/conftest.py`**

```python
"""Chip 测试公用 fixtures。"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def semi_home_html() -> str:
    return _read("semi_home.html")


@pytest.fixture
def semi_article_html() -> str:
    return _read("semi_article_intel14a.html")


@pytest.fixture
def semi_column_html() -> str:
    return _read("semi_column_hotnews.html")


@pytest.fixture
def laoyaoba_xinyaowen_html() -> str:
    return _read("laoyaoba_xinyaowen.html")


@pytest.fixture
def laoyaoba_article_icboard_html() -> str:
    return _read("laoyaoba_article_icboard.html")


@pytest.fixture
def laoyaoba_article_smic_html() -> str:
    return _read("laoyaoba_article_smic.html")


@pytest.fixture
def laoyaoba_article_legacy_html() -> str:
    return _read("laoyaoba_article_legacy.html")
```

- [ ] **Step 6: Register `chip_network` pytest marker**

Edit `pyproject.toml`, in the `[tool.pytest.ini_options]` section, change the `markers` list from:

```toml
markers = [
  "c114_network: live fetch to c114 (requires C114_NETWORK_TEST=1)",
]
```

to:

```toml
markers = [
  "c114_network: live fetch to c114 (requires C114_NETWORK_TEST=1)",
  "chip_network: live fetch to chip sources (requires CHIP_NETWORK_TEST=1)",
]
```

- [ ] **Step 7: Verify pytest discovers the new package**

```bash
pytest tests/chip --collect-only -q
```

Expected: `no tests collected` (no test files yet), exit code 5 — that's fine for now. **Must not** show import errors.

- [ ] **Step 8: Checkpoint commit (skip if not in git repo)**

```bash
git add src/chip tests/chip pyproject.toml && \
git commit -m "feat(chip): scaffold package + fixtures + pytest marker" || \
echo "Not a git repo — skipping commit"
```

---

## Task 2: chip/settings.py

**Files:**
- Create: `src/chip/settings.py`

- [ ] **Step 1: Write failing test `tests/chip/test_settings.py`**

Create `tests/chip/test_settings.py`:

```python
from __future__ import annotations

from pathlib import Path

from chip.settings import AppPaths, ensure_directories, resolve_paths


def test_resolve_paths_returns_appPaths_dataclass():
    paths = resolve_paths()
    assert isinstance(paths, AppPaths)
    assert paths.data_dir.name == "data"
    assert paths.raw_dir == paths.data_dir / "raw"
    assert paths.reports_dir.name == "reports"


def test_ensure_directories_creates_all(tmp_path):
    paths = AppPaths(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        reports_dir=tmp_path / "reports",
        state_dir=tmp_path / "state",
    )
    ensure_directories(paths)
    assert (tmp_path / "data" / "raw").is_dir()
    assert (tmp_path / "reports").is_dir()
    assert (tmp_path / "state").is_dir()
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/chip/test_settings.py -v
```

Expected: `ModuleNotFoundError: No module named 'chip.settings'`

- [ ] **Step 3: Write `src/chip/settings.py`**

Clone `src/infoq/settings.py` verbatim — identical contract works for chip:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    reports_dir: Path
    state_dir: Path


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_local_env(project_root: Path) -> None:
    env_path = project_root / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip()


def resolve_paths() -> AppPaths:
    root = skill_root() / "output"
    load_local_env(Path.cwd().resolve())
    return AppPaths(
        project_root=root,
        data_dir=root / "data",
        raw_dir=root / "data" / "raw",
        processed_dir=root / "data" / "processed",
        reports_dir=root / "reports",
        state_dir=root / "state",
    )


def ensure_directories(paths: AppPaths) -> None:
    for path in (paths.data_dir, paths.raw_dir, paths.processed_dir, paths.reports_dir, paths.state_dir):
        path.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/chip/test_settings.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/settings.py tests/chip/test_settings.py && \
git commit -m "feat(chip): settings.py (clone of infoq)" || true
```

---

## Task 3: datetime_parse — 三态时间解析

老姚吧详情页的 `span.published-time` 有三种格式（实测样本均在 fixtures 中）：
- `"3小时前"`、`"30分钟前"`、`"2天前"` → 相对
- `"05-14 18:18"`、`"02-27 07:15"` → 当年 MM-DD HH:MM
- `"2019-09-26"`、`"2018-08-12"` → 跨年 YYYY-MM-DD

**Files:**
- Create: `src/chip/channels/datetime_parse.py`
- Create: `tests/chip/test_datetime_parse.py`

- [ ] **Step 1: Write failing tests `tests/chip/test_datetime_parse.py`**

```python
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest

from chip.channels.datetime_parse import parse_laoyaoba_published_time

SHANGHAI = timezone(timedelta(hours=8))


def _ref() -> datetime:
    return datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("3小时前", datetime(2026, 5, 19, 15, 0, 0, tzinfo=SHANGHAI)),
        ("30分钟前", datetime(2026, 5, 19, 17, 30, 0, tzinfo=SHANGHAI)),
        ("2天前", datetime(2026, 5, 17, 18, 0, 0, tzinfo=SHANGHAI)),
        ("昨天 09:15", datetime(2026, 5, 18, 9, 15, 0, tzinfo=SHANGHAI)),
    ],
)
def test_relative_time(text, expected):
    assert parse_laoyaoba_published_time(text, reference_now=_ref()) == expected


def test_mm_dd_current_year():
    # 05-14 is before today (05-19) in 2026 → same year
    got = parse_laoyaoba_published_time("05-14 18:18", reference_now=_ref())
    assert got == datetime(2026, 5, 14, 18, 18, 0, tzinfo=SHANGHAI)


def test_mm_dd_cross_year():
    # 12-25 is after today (05-19) in 2026 → previous year 2025
    got = parse_laoyaoba_published_time("12-25 23:59", reference_now=_ref())
    assert got == datetime(2025, 12, 25, 23, 59, 0, tzinfo=SHANGHAI)


def test_yyyy_mm_dd():
    got = parse_laoyaoba_published_time("2019-09-26", reference_now=_ref())
    assert got == datetime(2019, 9, 26, 0, 0, 0, tzinfo=SHANGHAI)


def test_yyyy_mm_dd_hh_mm():
    got = parse_laoyaoba_published_time("2018-08-12 14:30", reference_now=_ref())
    assert got == datetime(2018, 8, 12, 14, 30, 0, tzinfo=SHANGHAI)


def test_unknown_returns_none():
    assert parse_laoyaoba_published_time("never", reference_now=_ref()) is None
    assert parse_laoyaoba_published_time("", reference_now=_ref()) is None
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_datetime_parse.py -v
```

Expected: `ModuleNotFoundError: No module named 'chip.channels.datetime_parse'`

- [ ] **Step 3: Write `src/chip/channels/datetime_parse.py`**

```python
"""老姚吧 published-time 多形态解析。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

SHANGHAI = timezone(timedelta(hours=8))

_REL_RE = re.compile(r"^(\d+)\s*(分钟|小时|天)前$")
_YESTERDAY_RE = re.compile(r"^昨天\s+(\d{1,2}):(\d{2})$")
_MM_DD_RE = re.compile(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$")
_YYYY_MM_DD_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$")


def parse_laoyaoba_published_time(
    text: str,
    *,
    reference_now: Optional[datetime] = None,
) -> Optional[datetime]:
    """把老姚吧 `<span class="published-time">` 文本解析为 Asia/Shanghai aware datetime。

    Returns None when the format is unrecognized — callers should fall back to
    HTTP Last-Modified header or crawl time.
    """

    s = (text or "").strip()
    if not s:
        return None

    now = reference_now or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)

    m = _REL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "分钟":
            return now - timedelta(minutes=n)
        if unit == "小时":
            return now - timedelta(hours=n)
        if unit == "天":
            return now - timedelta(days=n)

    m = _YESTERDAY_RE.match(s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        y = (now - timedelta(days=1)).date()
        return datetime(y.year, y.month, y.day, h, mi, 0, tzinfo=SHANGHAI)

    m = _YYYY_MM_DD_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h = int(m.group(4)) if m.group(4) else 0
        mi = int(m.group(5)) if m.group(5) else 0
        return datetime(y, mo, d, h, mi, 0, tzinfo=SHANGHAI)

    m = _MM_DD_RE.match(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        h = int(m.group(3)) if m.group(3) else 0
        mi = int(m.group(4)) if m.group(4) else 0
        year = now.year
        if (mo, d) > (now.month, now.day):
            year -= 1
        return datetime(year, mo, d, h, mi, 0, tzinfo=SHANGHAI)

    return None
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
pytest tests/chip/test_datetime_parse.py -v
```

Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/datetime_parse.py tests/chip/test_datetime_parse.py && \
git commit -m "feat(chip): datetime_parse for laoyaoba 3-format times" || true
```

---

## Task 4: ChipChannelSpec dataclass + registry

**Files:**
- Create: `src/chip/channels/spec.py`
- Create: `tests/chip/test_channels_spec.py`

- [ ] **Step 1: Write failing test `tests/chip/test_channels_spec.py`**

```python
from __future__ import annotations

from datetime import date

from chip.channels.spec import CHANNELS, ChipChannelSpec
from utils.tools.content_models import RawArticleDetail, RawArticleRef


def test_channels_registry_contains_two_channels():
    assert set(CHANNELS.keys()) == {"semi", "laoyaoba"}


def test_channel_spec_fields():
    for key, spec in CHANNELS.items():
        assert isinstance(spec, ChipChannelSpec)
        assert spec.key == key
        assert spec.name
        assert spec.listing_urls
        assert callable(spec.fetch_listing)
        assert callable(spec.fetch_article)


def test_channels_have_distinct_names():
    names = {spec.name for spec in CHANNELS.values()}
    assert len(names) == len(CHANNELS)
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_spec.py -v
```

Expected: `ModuleNotFoundError: No module named 'chip.channels.spec'`

- [ ] **Step 3: Write `src/chip/channels/spec.py`**

```python
"""ChipChannelSpec 定义 + CHANNELS 注册表。

实际 fetch_listing / fetch_article 实现见 channels/semi.py 与 channels/laoyaoba.py。
这里只是绑定 key → spec → callable 的路由表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from utils.tools.content_models import RawArticleDetail, RawArticleRef


@dataclass(frozen=True)
class ChipChannelSpec:
    """单个 channel 的元数据 + 抓取回调。"""

    key: str
    name: str
    listing_urls: tuple[str, ...]
    fetch_listing: Callable[["ChipChannelSpec", date], list[RawArticleRef]]
    fetch_article: Callable[["ChipChannelSpec", RawArticleRef], RawArticleDetail]


def _build_registry() -> dict[str, ChipChannelSpec]:
    # Import locally to avoid circular imports during test collection.
    from chip.channels import laoyaoba, semi

    return {
        "semi": ChipChannelSpec(
            key="semi",
            name="SEMI 中国",
            listing_urls=(
                "https://www.semi.org.cn/site/semi/",
                "https://www.semi.org.cn/site/semi/column/26595298402893836.html",
            ),
            fetch_listing=semi.fetch_listing,
            fetch_article=semi.fetch_article,
        ),
        "laoyaoba": ChipChannelSpec(
            key="laoyaoba",
            name="老姚吧",
            listing_urls=(
                "https://www.laoyaoba.com/xinyaowen",
                "https://www.laoyaoba.com/jwfocus",
            ),
            fetch_listing=laoyaoba.fetch_listing,
            fetch_article=laoyaoba.fetch_article,
        ),
    }


# Channels are registered at import time so callers can iterate `CHANNELS.values()`.
# Concrete `channels/semi.py` and `channels/laoyaoba.py` must each expose
# `fetch_listing(spec, report_date)` and `fetch_article(spec, ref)`.
CHANNELS: dict[str, ChipChannelSpec] = _build_registry()
```

> Note: at this step `chip.channels.semi` and `chip.channels.laoyaoba` don't exist yet, so the import inside `_build_registry()` will fail. We resolve this by stubbing both modules in the next step.

- [ ] **Step 4: Add minimal stub modules so the registry import works**

Create `src/chip/channels/semi.py`:

```python
"""SEMI 中国 channel 抓取（stub — 实现见 Task 5-6）。"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from utils.tools.content_models import RawArticleDetail, RawArticleRef

if TYPE_CHECKING:
    from chip.channels.spec import ChipChannelSpec


def fetch_listing(spec: "ChipChannelSpec", report_date: date) -> list[RawArticleRef]:
    raise NotImplementedError("SEMI fetch_listing not yet implemented")


def fetch_article(spec: "ChipChannelSpec", ref: RawArticleRef) -> RawArticleDetail:
    raise NotImplementedError("SEMI fetch_article not yet implemented")
```

Create `src/chip/channels/laoyaoba.py`:

```python
"""老姚吧 channel 抓取（stub — 实现见 Task 7-8）。"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from utils.tools.content_models import RawArticleDetail, RawArticleRef

if TYPE_CHECKING:
    from chip.channels.spec import ChipChannelSpec


def fetch_listing(spec: "ChipChannelSpec", report_date: date) -> list[RawArticleRef]:
    raise NotImplementedError("laoyaoba fetch_listing not yet implemented")


def fetch_article(spec: "ChipChannelSpec", ref: RawArticleRef) -> RawArticleDetail:
    raise NotImplementedError("laoyaoba fetch_article not yet implemented")
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
pytest tests/chip/test_channels_spec.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/chip/channels tests/chip/test_channels_spec.py && \
git commit -m "feat(chip): ChipChannelSpec registry + channel stubs" || true
```

---

## Task 5: SEMI channel — listing parser (pure HTML parsing, no network)

实测 selector：首页 `<a href="https://www.semi.org.cn/site/semi/article/<hex>.html">`，标题为 anchor 文本，**列表上无日期**。

**Files:**
- Create: `tests/chip/test_channels_semi.py` (extended in Task 6)
- Modify: `src/chip/channels/semi.py`

- [ ] **Step 1: Write failing test for `parse_listing_html`**

Create `tests/chip/test_channels_semi.py`:

```python
from __future__ import annotations

from chip.channels.semi import (
    SEMI_ARTICLE_URL_RE,
    parse_listing_html,
)


def test_parse_listing_homepage_yields_articles(semi_home_html):
    refs = parse_listing_html(semi_home_html, base_url="https://www.semi.org.cn/site/semi/")
    # Live homepage carried 30+ article links; tolerate 20 floor for future drift.
    assert len(refs) >= 20
    # All URLs must point to article detail
    for r in refs:
        assert SEMI_ARTICLE_URL_RE.search(r["url"])
        assert r["title"]
        assert r["channel"] == "semi"
        assert r["source_bucket"] == "semi"
    # Spot-check expected titles (from实测 snapshot)
    titles = " ".join(r["title"] for r in refs)
    assert "陈立武" in titles or "14A" in titles
    assert "SEMI报告" in titles or "硅晶圆" in titles


def test_parse_listing_dedupes_by_url(semi_home_html):
    refs = parse_listing_html(semi_home_html, base_url="https://www.semi.org.cn/site/semi/")
    urls = [r["url"] for r in refs]
    assert len(urls) == len(set(urls))


def test_parse_listing_column_page(semi_column_html):
    refs = parse_listing_html(semi_column_html, base_url="https://www.semi.org.cn/site/semi/")
    assert len(refs) >= 8
    assert all(SEMI_ARTICLE_URL_RE.search(r["url"]) for r in refs)
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_semi.py -v
```

Expected: `ImportError: cannot import name 'parse_listing_html' from 'chip.channels.semi'`

- [ ] **Step 3: Implement `parse_listing_html` in `src/chip/channels/semi.py`**

Replace the file contents with:

```python
"""SEMI 中国 channel 抓取。

实测结构：
- 首页/栏目页是服务端渲染，<a href="https://www.semi.org.cn/site/semi/article/<32hex>.html">
- 列表上没有日期，需要进详情页才能拿到 publish_date
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Iterable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from utils.tools.content_models import RawArticleDetail, RawArticleRef

if TYPE_CHECKING:
    from chip.channels.spec import ChipChannelSpec

SEMI_BASE = "https://www.semi.org.cn"
SEMI_LISTING_URLS = (
    f"{SEMI_BASE}/site/semi/",
    f"{SEMI_BASE}/site/semi/column/26595298402893836.html",
)
SEMI_ARTICLE_URL_RE = re.compile(r"^https?://www\.semi\.org\.cn/site/semi/article/[a-f0-9]{32}\.html$")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REQUEST_TIMEOUT = 20.0
_THROTTLE_SECONDS = 1.0


def parse_listing_html(html: str, *, base_url: str) -> list[dict[str, Any]]:
    """Extract article refs from a SEMI listing HTML page.

    Returns plain dicts (not RawArticleRef) so the function is purely about
    HTML structure; the channel-level `fetch_listing` will adapt to
    RawArticleRef and request details.
    """

    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        if not href or "${" in href:  # template placeholders in some areas
            continue
        url = urljoin(base_url, href)
        if not SEMI_ARTICLE_URL_RE.match(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        text = " ".join(anchor.stripped_strings)
        if not text:
            continue
        refs.append(
            {
                "url": url,
                "title": text,
                "channel": "semi",
                "source_bucket": "semi",
                "article_id": _extract_article_id(url),
            }
        )
    return refs


def _extract_article_id(url: str) -> str:
    match = re.search(r"/article/([a-f0-9]{32})\.html", url)
    return match.group(1) if match else url
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/chip/test_channels_semi.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/semi.py tests/chip/test_channels_semi.py && \
git commit -m "feat(chip/semi): listing HTML parser" || true
```

---

## Task 6: SEMI channel — article detail parser

实测 selector：
- 标题：`h2.col-lg-8`
- 摘要：`div.ct-p.ct-bzjy`
- 正文：`div.single-post-content`
- 日期：`<span>` 文本匹配 `^20\d{2}-\d{1,2}-\d{1,2}$`，位置在 `"来源："` 之后

**Files:**
- Modify: `src/chip/channels/semi.py`
- Modify: `tests/chip/test_channels_semi.py`

- [ ] **Step 1: Append failing tests to `tests/chip/test_channels_semi.py`**

```python
from datetime import date

from chip.channels.semi import parse_article_html


def test_parse_article_extracts_fields(semi_article_html):
    parsed = parse_article_html(
        semi_article_html,
        url="https://www.semi.org.cn/site/semi/article/a77885fcd495465e97cbfd36b5a17381.html",
    )
    assert "英特尔" in parsed["title"]
    assert "14A" in parsed["title"]
    assert parsed["published_date"] == date(2026, 5, 20)
    assert "综合报道" in parsed["source_org"] or parsed["source_org"]
    assert "14A" in parsed["summary"] or "陈立武" in parsed["summary"]
    assert len(parsed["content_text"]) > 200
    assert "陈立武" in parsed["content_text"]


def test_parse_article_handles_missing_summary(semi_article_html):
    parsed = parse_article_html(
        semi_article_html,
        url="https://www.semi.org.cn/site/semi/article/test.html",
    )
    # Should not raise even if fields are absent; all string fields are str.
    assert isinstance(parsed["title"], str)
    assert isinstance(parsed["summary"], str)
    assert isinstance(parsed["content_text"], str)
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_semi.py::test_parse_article_extracts_fields -v
```

Expected: `ImportError: cannot import name 'parse_article_html'`

- [ ] **Step 3: Append `parse_article_html` to `src/chip/channels/semi.py`**

Append (after the `_extract_article_id` function):

```python
_DATE_SPAN_RE = re.compile(r"^(20\d{2})-(\d{1,2})-(\d{1,2})$")


def parse_article_html(html: str, *, url: str) -> dict[str, Any]:
    """Extract title / date / summary / source / body from a SEMI article page."""

    soup = BeautifulSoup(html, "html.parser")

    title_node = soup.select_one("h2.col-lg-8")
    title = title_node.get_text(strip=True) if title_node else ""

    summary_node = soup.select_one("div.ct-p.ct-bzjy")
    summary = summary_node.get_text(strip=True) if summary_node else ""
    if not summary:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            summary = (meta.get("content") or "").strip()

    body_node = soup.select_one("div.single-post-content")
    content_text = body_node.get_text("\n", strip=True) if body_node else ""

    published_date = _extract_publish_date(soup)
    source_org = _extract_source_org(soup)

    return {
        "url": url,
        "title": title,
        "summary": summary,
        "content_text": content_text,
        "published_date": published_date,
        "source_org": source_org,
        "article_id": _extract_article_id(url),
    }


def _extract_publish_date(soup: BeautifulSoup) -> date | None:
    # The date appears as a bare <span>YYYY-MM-DD</span> near "来源："
    for span in soup.find_all("span"):
        text = (span.get_text(strip=True) or "").strip()
        match = _DATE_SPAN_RE.match(text)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            try:
                return date(year, month, day)
            except ValueError:
                continue
    return None


def _extract_source_org(soup: BeautifulSoup) -> str:
    # 结构：<span>来源：<span>来源名</span></span>
    for span in soup.find_all("span"):
        text = (span.get_text(strip=True) or "")
        if text.startswith("来源："):
            after = text[len("来源："):].strip()
            if after:
                return after
            inner = span.find("span")
            if inner:
                return (inner.get_text(strip=True) or "").strip()
    return ""
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/chip/test_channels_semi.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/semi.py tests/chip/test_channels_semi.py && \
git commit -m "feat(chip/semi): article detail parser" || true
```

---

## Task 7: SEMI channel — wire up fetch_listing / fetch_article with HTTP

These functions are not unit-tested with real network (network tests run with `CHIP_NETWORK_TEST=1` later). The unit test verifies they use `parse_*` correctly with an injectable `fetch_html` callable.

**Files:**
- Modify: `src/chip/channels/semi.py`
- Modify: `tests/chip/test_channels_semi.py`

- [ ] **Step 1: Append failing test**

Append to `tests/chip/test_channels_semi.py`:

```python
from chip.channels.semi import fetch_article, fetch_listing
from chip.channels.spec import CHANNELS
from utils.tools.content_models import RawArticleRef


class _FakeFetcher:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        return self.mapping[url]


def test_fetch_listing_uses_listing_urls_and_returns_refs(semi_home_html, semi_column_html, semi_article_html):
    fetcher = _FakeFetcher(
        {
            "https://www.semi.org.cn/site/semi/": semi_home_html,
            "https://www.semi.org.cn/site/semi/column/26595298402893836.html": semi_column_html,
            # any article URL → semi_article_html (used to feed published_date)
        }
    )
    # For listing test, we don't need detail fetcher; pass a stub that returns empty html
    # so adapter skips items with missing date.
    refs = fetch_listing(
        CHANNELS["semi"],
        date(2026, 5, 20),
        http_get=fetcher,
        detail_get=lambda url: semi_article_html,  # all dates → 2026-05-20
    )
    assert len(refs) >= 20
    assert all(isinstance(r, RawArticleRef) for r in refs)
    assert all(r.channel == "semi" and r.source_bucket == "semi" for r in refs)
    assert any(r.published_at.startswith("2026-05-20") for r in refs)


def test_fetch_article_returns_RawArticleDetail(semi_article_html):
    ref = RawArticleRef(
        source_site="chip",
        article_id="a77885fcd495465e97cbfd36b5a17381",
        title="placeholder",
        url="https://www.semi.org.cn/site/semi/article/a77885fcd495465e97cbfd36b5a17381.html",
        channel="semi",
        source_bucket="semi",
    )
    detail = fetch_article(
        CHANNELS["semi"],
        ref,
        http_get=lambda url: semi_article_html,
    )
    assert detail.title.startswith("英特尔") or "14A" in detail.title
    assert detail.published_at.startswith("2026-05-20")
    assert detail.content_text
    assert detail.channel == "semi"
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_semi.py -v
```

Expected: ImportError for `fetch_listing` / `fetch_article` (they're stubs raising NotImplementedError).

- [ ] **Step 3: Replace stub `fetch_listing` and `fetch_article` in `src/chip/channels/semi.py`**

Replace the entire `fetch_listing` and `fetch_article` functions with:

```python
def _default_http_get(url: str) -> str:
    with httpx.Client(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": _DEFAULT_UA})
        response.raise_for_status()
        return response.text


def fetch_listing(
    spec: "ChipChannelSpec",
    report_date: date,
    *,
    http_get=None,
    detail_get=None,
) -> list[RawArticleRef]:
    """Fetch SEMI listing pages and filter to articles published on report_date.

    Because SEMI listing pages do not carry publish dates, we must request each
    article detail to learn its date. Articles whose date != report_date are
    dropped here.
    """

    get_listing = http_get or _default_http_get
    get_detail = detail_get or _default_http_get

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for listing_url in spec.listing_urls:
        try:
            html = get_listing(listing_url)
        except Exception:
            continue
        time.sleep(_THROTTLE_SECONDS)
        for item in parse_listing_html(html, base_url=listing_url):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            candidates.append(item)

    refs: list[RawArticleRef] = []
    for cand in candidates:
        try:
            article_html = get_detail(cand["url"])
        except Exception:
            continue
        time.sleep(_THROTTLE_SECONDS)
        parsed = parse_article_html(article_html, url=cand["url"])
        if parsed["published_date"] != report_date:
            continue
        refs.append(
            RawArticleRef(
                source_site="chip",
                article_id=parsed["article_id"],
                title=parsed["title"] or cand["title"],
                url=cand["url"],
                published_at=parsed["published_date"].isoformat(),
                channel="semi",
                source_bucket="semi",
                summary=parsed["summary"],
                metadata={
                    "source_org": parsed["source_org"],
                    "content_text": parsed["content_text"],
                    "listing_title": cand["title"],
                },
            )
        )
    return refs


def fetch_article(
    spec: "ChipChannelSpec",
    ref: RawArticleRef,
    *,
    http_get=None,
) -> RawArticleDetail:
    """Fetch and parse one SEMI article."""

    get_html = http_get or _default_http_get
    html = get_html(ref.url)
    parsed = parse_article_html(html, url=ref.url)

    return RawArticleDetail(
        source_site="chip",
        article_id=parsed["article_id"],
        title=parsed["title"] or ref.title,
        url=ref.url,
        published_at=parsed["published_date"].isoformat() if parsed["published_date"] else ref.published_at,
        author=parsed["source_org"],
        channel="semi",
        source_bucket="semi",
        tags=[],
        summary=parsed["summary"] or ref.summary,
        content_text=parsed["content_text"],
        metadata={
            "source_org": parsed["source_org"],
            "listing_title": ref.title,
        },
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/chip/test_channels_semi.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/semi.py tests/chip/test_channels_semi.py && \
git commit -m "feat(chip/semi): fetch_listing + fetch_article with detail-date filter" || true
```

---

## Task 8: laoyaoba channel — listing parser

实测：`<a href="/n/<id>">` 是文章链接；列表上有相对/绝对时间；要排除版权声明/关于我们等静态页 id。

**Files:**
- Create: `tests/chip/test_channels_laoyaoba.py`
- Modify: `src/chip/channels/laoyaoba.py`

- [ ] **Step 1: Write failing test**

Create `tests/chip/test_channels_laoyaoba.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from chip.channels.laoyaoba import (
    EXCLUDED_LAOYAOBA_IDS,
    parse_listing_html,
)

SHANGHAI = timezone(timedelta(hours=8))


def test_parse_listing_extracts_articles(laoyaoba_xinyaowen_html):
    items = parse_listing_html(
        laoyaoba_xinyaowen_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    # Live snapshot had 67 /n/* links incl. static pages; after exclusion expect >= 40.
    assert len(items) >= 30
    for it in items:
        assert it["url"].startswith("https://www.laoyaoba.com/n/")
        assert it["channel"] == "laoyaoba"
        assert it["source_bucket"] == "laoyaoba"
        assert it["article_id"].isdigit()
        # Exclude IDs that are well-known static pages
        assert int(it["article_id"]) not in EXCLUDED_LAOYAOBA_IDS


def test_parse_listing_includes_published_at_when_present(laoyaoba_xinyaowen_html):
    items = parse_listing_html(
        laoyaoba_xinyaowen_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    with_time = [i for i in items if i.get("published_at")]
    assert len(with_time) >= 10  # most entries have a timestamp


def test_parse_listing_excludes_short_titles(laoyaoba_xinyaowen_html):
    items = parse_listing_html(
        laoyaoba_xinyaowen_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    for it in items:
        # All retained titles should have at least 6 chars of substance.
        assert len(it["title"]) >= 6


def test_excluded_ids_constant_contents():
    # Sanity: must include the known static-page IDs.
    assert 729927 in EXCLUDED_LAOYAOBA_IDS  # 版权声明
    assert 683317 in EXCLUDED_LAOYAOBA_IDS  # 关于我们
    assert 683318 in EXCLUDED_LAOYAOBA_IDS  # 联系我们
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_laoyaoba.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement listing parser in `src/chip/channels/laoyaoba.py`**

Replace the stub file contents with:

```python
"""老姚吧（爱集微 / ijiwei）channel 抓取。

实测：
- 桌面站 https://www.laoyaoba.com/ 是 SSR；移动站是 SPA + /api（被 robots 禁）
- 文章 URL /n/<numeric-id>
- 列表上携带相对/绝对时间（"3小时前" / "05-14 18:18"）
- 详情页 selector：h1.media-title / span.published-time / div.media-source / div.media-article-content
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from chip.channels.datetime_parse import SHANGHAI, parse_laoyaoba_published_time
from utils.tools.content_models import RawArticleDetail, RawArticleRef

if TYPE_CHECKING:
    from chip.channels.spec import ChipChannelSpec

LAOYAOBA_BASE = "https://www.laoyaoba.com"
LAOYAOBA_LISTING_URLS = (
    f"{LAOYAOBA_BASE}/xinyaowen",
    f"{LAOYAOBA_BASE}/jwfocus",
)

# IDs that 解析到的但实际是静态页（版权声明 / 关于我们 等），抓取层硬过滤。
EXCLUDED_LAOYAOBA_IDS: frozenset[int] = frozenset({683317, 683318, 729927})

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_ARTICLE_HREF_RE = re.compile(r"^/n/(\d+)/?$")
# 列表 anchor 文本里时间可能裹在前后；用宽松匹配抽取
_TIME_HINTS = (
    re.compile(r"(\d+\s*(?:分钟|小时|天)前)"),
    re.compile(r"(昨天\s+\d{1,2}:\d{2})"),
    re.compile(r"(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})"),
    re.compile(r"(20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)"),
)
_REQUEST_TIMEOUT = 20.0
_THROTTLE_SECONDS = 1.0
_MIN_TITLE_LEN = 6


def parse_listing_html(
    html: str,
    *,
    reference_now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract article candidates from a laoyaoba listing page."""

    reference = reference_now or datetime.now(SHANGHAI)
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        m = _ARTICLE_HREF_RE.match(href)
        if not m:
            continue
        article_id = m.group(1)
        if int(article_id) in EXCLUDED_LAOYAOBA_IDS:
            continue
        url = urljoin(LAOYAOBA_BASE + "/", href)
        if url in seen:
            continue
        anchor_text = " ".join(anchor.stripped_strings)
        if len(anchor_text) < _MIN_TITLE_LEN:
            continue

        title, time_text = _split_title_and_time(anchor_text)
        if len(title) < _MIN_TITLE_LEN:
            continue
        published_at = ""
        if time_text:
            parsed = parse_laoyaoba_published_time(time_text, reference_now=reference)
            if parsed is not None:
                published_at = parsed.isoformat()

        seen.add(url)
        items.append(
            {
                "url": url,
                "title": title,
                "article_id": article_id,
                "channel": "laoyaoba",
                "source_bucket": "laoyaoba",
                "published_at": published_at,
            }
        )
    return items


def _split_title_and_time(text: str) -> tuple[str, str]:
    """Pull a time substring out of the anchor text, return (title, time_text)."""

    for rx in _TIME_HINTS:
        m = rx.search(text)
        if m:
            time_text = m.group(1)
            title = (text[: m.start()] + text[m.end():]).strip()
            return title, time_text
    return text.strip(), ""
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/chip/test_channels_laoyaoba.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/laoyaoba.py tests/chip/test_channels_laoyaoba.py && \
git commit -m "feat(chip/laoyaoba): listing parser with time split + excluded IDs" || true
```

---

## Task 9: laoyaoba channel — article detail parser

实测三种 fixture：icboard / smic / legacy 分别覆盖三种 published-time 格式。

**Files:**
- Modify: `src/chip/channels/laoyaoba.py`
- Modify: `tests/chip/test_channels_laoyaoba.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/chip/test_channels_laoyaoba.py`:

```python
from chip.channels.laoyaoba import parse_article_html


def test_parse_article_icboard_relative_time(laoyaoba_article_icboard_html):
    parsed = parse_article_html(
        laoyaoba_article_icboard_html,
        url="https://www.laoyaoba.com/n/1038379",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "IC载板" in parsed["title"]
    # "3小时前" relative to 18:00 => 15:00
    assert parsed["published_at"].startswith("2026-05-19T15:00")
    assert parsed["author"] == "爱集微"
    assert "海门" in parsed["source_org"]
    assert "海门" in parsed["tags"]
    assert "IC载板" in parsed["content_text"]
    assert "summary" in parsed and parsed["summary"]


def test_parse_article_smic_mmdd_format(laoyaoba_article_smic_html):
    parsed = parse_article_html(
        laoyaoba_article_smic_html,
        url="https://www.laoyaoba.com/n/1035371",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "中芯国际" in parsed["title"]
    # "05-14 18:18" current year 2026
    assert parsed["published_at"].startswith("2026-05-14T18:18")
    assert "中芯国际" in parsed["tags"] or "第一季度财报" in parsed["tags"]
    # SMIC article has no author-item (external aggregated)
    assert isinstance(parsed["author"], str)


def test_parse_article_legacy_yyyy_format(laoyaoba_article_legacy_html):
    parsed = parse_article_html(
        laoyaoba_article_legacy_html,
        url="https://www.laoyaoba.com/n/729927",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "版权声明" in parsed["title"]
    assert parsed["published_at"].startswith("2019-09-26")


def test_parse_article_with_missing_time_returns_empty_string(laoyaoba_article_icboard_html):
    # Manually break the time tag by replacing the span text → still works
    broken = laoyaoba_article_icboard_html.replace("3小时前", "wat?")
    parsed = parse_article_html(
        broken,
        url="https://www.laoyaoba.com/n/1038379",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert parsed["published_at"] == ""
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_laoyaoba.py -v
```

Expected: ImportError for `parse_article_html`.

- [ ] **Step 3: Append `parse_article_html` to `src/chip/channels/laoyaoba.py`**

```python
def parse_article_html(
    html: str,
    *,
    url: str,
    reference_now: datetime | None = None,
) -> dict[str, Any]:
    """Extract title / time / author / source / tags / body from a laoyaoba article page."""

    reference = reference_now or datetime.now(SHANGHAI)
    soup = BeautifulSoup(html, "html.parser")

    title_node = soup.select_one("h1.media-title")
    title = title_node.get_text(strip=True) if title_node else ""

    time_node = soup.select_one("span.published-time")
    time_text = time_node.get_text(strip=True) if time_node else ""
    published_at = ""
    if time_text:
        parsed_dt = parse_laoyaoba_published_time(time_text, reference_now=reference)
        if parsed_dt is not None:
            published_at = parsed_dt.isoformat()

    author_node = soup.select_one("a.author-item")
    author = author_node.get_text(strip=True) if author_node else ""

    source_org = ""
    media_source = soup.select_one("div.media-source")
    if media_source:
        first_span = media_source.find("span", recursive=False)
        if first_span:
            text = first_span.get_text(strip=True)
            if text.startswith("来源："):
                source_org = text[len("来源："):].strip()

    tags: list[str] = []
    if media_source:
        for tag_span in media_source.select("span.media-tag-item"):
            tag_text = tag_span.get_text(strip=True)
            # tag_text 形如 "#海门#"
            cleaned = tag_text.strip("#").strip()
            if cleaned:
                tags.append(cleaned)

    body_node = soup.select_one("div.media-article-content")
    content_text = body_node.get_text("\n", strip=True) if body_node else ""

    summary = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        summary = (meta_desc.get("content") or "").strip()

    article_id_match = re.search(r"/n/(\d+)", url)
    article_id = article_id_match.group(1) if article_id_match else url

    return {
        "url": url,
        "article_id": article_id,
        "title": title,
        "published_at": published_at,
        "author": author,
        "source_org": source_org,
        "tags": tags,
        "summary": summary,
        "content_text": content_text,
    }
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/chip/test_channels_laoyaoba.py -v
```

Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/laoyaoba.py tests/chip/test_channels_laoyaoba.py && \
git commit -m "feat(chip/laoyaoba): article detail parser covering 3 time formats" || true
```

---

## Task 10: laoyaoba channel — fetch_listing / fetch_article wiring

**Files:**
- Modify: `src/chip/channels/laoyaoba.py`
- Modify: `tests/chip/test_channels_laoyaoba.py`

- [ ] **Step 1: Append failing test**

Append to `tests/chip/test_channels_laoyaoba.py`:

```python
from chip.channels.laoyaoba import fetch_article, fetch_listing
from chip.channels.spec import CHANNELS
from utils.tools.content_models import RawArticleRef


def test_fetch_listing_filters_by_date(laoyaoba_xinyaowen_html, laoyaoba_article_smic_html):
    refs = fetch_listing(
        CHANNELS["laoyaoba"],
        date(2026, 5, 14),
        http_get=lambda url: laoyaoba_xinyaowen_html,
        detail_get=lambda url: laoyaoba_article_smic_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    # Some items in fixture have listing time on 05-14
    assert all(r.channel == "laoyaoba" for r in refs)
    assert all(r.published_at.startswith("2026-05-14") for r in refs)


def test_fetch_listing_uses_listing_time_when_available(laoyaoba_xinyaowen_html):
    # detail_get not called for items whose listing time already matches report_date
    detail_calls = []

    def detail_get(url):
        detail_calls.append(url)
        return ""  # would fail parse, but we shouldn't reach here

    refs = fetch_listing(
        CHANNELS["laoyaoba"],
        date(2026, 5, 19),  # "今日"
        http_get=lambda url: laoyaoba_xinyaowen_html,
        detail_get=detail_get,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    # 列表里 "3小时前 / 4小时前" 的条目 published_at 已经是 2026-05-19，
    # 不需要为它们再 detail_get
    assert all(r.published_at.startswith("2026-05-19") for r in refs)


def test_fetch_article_returns_RawArticleDetail(laoyaoba_article_icboard_html):
    ref = RawArticleRef(
        source_site="chip",
        article_id="1038379",
        title="placeholder",
        url="https://www.laoyaoba.com/n/1038379",
        channel="laoyaoba",
        source_bucket="laoyaoba",
    )
    detail = fetch_article(
        CHANNELS["laoyaoba"],
        ref,
        http_get=lambda url: laoyaoba_article_icboard_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "IC载板" in detail.title
    assert detail.channel == "laoyaoba"
    assert "海门" in detail.tags
    assert detail.content_text
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_channels_laoyaoba.py -v
```

Expected: NotImplementedError / ImportError on `fetch_listing`/`fetch_article`.

- [ ] **Step 3: Implement fetch_listing / fetch_article**

Replace the stub `fetch_listing` and `fetch_article` at the bottom of `src/chip/channels/laoyaoba.py`:

```python
def _default_http_get(url: str) -> str:
    with httpx.Client(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": _DEFAULT_UA})
        response.raise_for_status()
        return response.text


def fetch_listing(
    spec: "ChipChannelSpec",
    report_date: date,
    *,
    http_get=None,
    detail_get=None,
    reference_now: datetime | None = None,
) -> list[RawArticleRef]:
    """Fetch laoyaoba listing pages, filter to report_date.

    - If a listing item already carries a published_at on report_date, no detail
      request is needed (saves ~50% requests).
    - For items without a listing time, request detail and try to parse.
    - Discard items whose final time != report_date.
    """

    get_listing = http_get or _default_http_get
    get_detail = detail_get or _default_http_get
    reference = reference_now or datetime.now(SHANGHAI)
    report_iso = report_date.isoformat()

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for listing_url in spec.listing_urls:
        try:
            html = get_listing(listing_url)
        except Exception:
            continue
        time.sleep(_THROTTLE_SECONDS)
        for item in parse_listing_html(html, reference_now=reference):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            candidates.append(item)

    refs: list[RawArticleRef] = []
    for cand in candidates:
        published_at = cand.get("published_at") or ""
        needs_detail = not published_at.startswith(report_iso)
        detail_parsed: dict[str, Any] | None = None
        if needs_detail:
            # Listing-time mismatch (or absent) → check detail. We do NOT skip
            # outside-window items here without checking, because listing time
            # might be just minutes off the date boundary.
            try:
                detail_html = get_detail(cand["url"])
            except Exception:
                continue
            time.sleep(_THROTTLE_SECONDS)
            detail_parsed = parse_article_html(
                detail_html,
                url=cand["url"],
                reference_now=reference,
            )
            published_at = detail_parsed.get("published_at") or ""
            if not published_at.startswith(report_iso):
                continue
        # Build RawArticleRef. Hydrate from detail if we already fetched it.
        summary = ""
        content_text = ""
        tags: list[str] = []
        source_org = ""
        if detail_parsed is not None:
            summary = detail_parsed.get("summary") or ""
            content_text = detail_parsed.get("content_text") or ""
            tags = detail_parsed.get("tags") or []
            source_org = detail_parsed.get("source_org") or ""
        refs.append(
            RawArticleRef(
                source_site="chip",
                article_id=cand["article_id"],
                title=cand["title"],
                url=cand["url"],
                published_at=published_at,
                channel="laoyaoba",
                source_bucket="laoyaoba",
                summary=summary,
                metadata={
                    "source_org": source_org,
                    "content_text": content_text,
                    "tags": tags,
                    "listing_title": cand["title"],
                },
            )
        )
    return refs


def fetch_article(
    spec: "ChipChannelSpec",
    ref: RawArticleRef,
    *,
    http_get=None,
    reference_now: datetime | None = None,
) -> RawArticleDetail:
    """Fetch and parse one laoyaoba article."""

    get_html = http_get or _default_http_get
    html = get_html(ref.url)
    parsed = parse_article_html(html, url=ref.url, reference_now=reference_now)

    return RawArticleDetail(
        source_site="chip",
        article_id=parsed["article_id"],
        title=parsed["title"] or ref.title,
        url=ref.url,
        published_at=parsed["published_at"] or ref.published_at,
        author=parsed["author"],
        channel="laoyaoba",
        source_bucket="laoyaoba",
        tags=parsed["tags"],
        summary=parsed["summary"] or ref.summary,
        content_text=parsed["content_text"],
        metadata={
            "source_org": parsed["source_org"],
            "listing_title": ref.title,
            "tags": parsed["tags"],
        },
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/chip/test_channels_laoyaoba.py -v
```

Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/channels/laoyaoba.py tests/chip/test_channels_laoyaoba.py && \
git commit -m "feat(chip/laoyaoba): fetch_listing + fetch_article with detail fallback" || true
```

---

## Task 11: chip_themes — 主题白名单 + 多标签分类

**Files:**
- Create: `src/utils/tools/research/__init__.py`（如不存在）
- Create: `src/utils/tools/research/chip_themes.py`
- Create: `tests/chip/test_chip_themes.py`

- [ ] **Step 1: Verify research dir state**

```bash
ls -la src/utils/tools/research/ 2>/dev/null || echo "MISSING"
```

If missing, create with `mkdir -p src/utils/tools/research && touch src/utils/tools/research/__init__.py`.

- [ ] **Step 2: Write failing tests `tests/chip/test_chip_themes.py`**

```python
from __future__ import annotations

from utils.tools.content_models import StandardArticle
from utils.tools.research.chip_themes import CHIP_THEMES, classify_chip_themes


def _article(title: str, summary: str = "", content: str = "") -> StandardArticle:
    return StandardArticle(
        source_site="chip",
        source_bucket="semi",
        channel="semi",
        article_id="x",
        title=title,
        url="https://example.com/x",
        published_at="2026-05-19",
        author="",
        tags=[],
        keywords=[],
        summary=summary,
        content_text=content,
    )


def test_chip_themes_has_five_buckets():
    expected = {"新品发布", "价格变动", "产能/扩产", "供需/缺货", "技术节点突破"}
    assert set(CHIP_THEMES.keys()) == expected


def test_classify_product_launch():
    a = _article(title="格罗方德推出用于CPO的硅光子共封装先进光引擎方案")
    assert "新品发布" in classify_chip_themes(a)


def test_classify_price_change():
    a = _article(title="3月DRAM合约价环比上涨7%")
    assert "价格变动" in classify_chip_themes(a)


def test_classify_capacity():
    a = _article(title="年产360万平方米IC载板智能工厂封顶")
    assert "产能/扩产" in classify_chip_themes(a)


def test_classify_supply():
    a = _article(title="HBM供应紧张，下游加大下单")
    assert "供需/缺货" in classify_chip_themes(a)


def test_classify_node():
    a = _article(title="英特尔14A制程2029量产 18A工艺良率回升")
    assert "技术节点突破" in classify_chip_themes(a)


def test_classify_multilabel():
    # 跨主题：新品 + 节点
    a = _article(title="台积电推出2nm新工艺 良率达到目标")
    themes = classify_chip_themes(a)
    assert "新品发布" in themes
    assert "技术节点突破" in themes


def test_no_match_returns_empty():
    a = _article(title="美国就业数据创新高")
    assert classify_chip_themes(a) == []
```

- [ ] **Step 3: Run, verify fail**

```bash
pytest tests/chip/test_chip_themes.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement `src/utils/tools/research/chip_themes.py`**

```python
"""芯片产业主题白名单 + 关键词命中分类。

设计要点：
- 每个主题对应一组关键词；命中 ≥1 个即归入该主题
- 多标签：一篇文章可命中多个主题
- 关键词在 title + summary + content_text 三处任一命中即算命中
- 字典顺序固定，输出按字典顺序的稳定列表
"""

from __future__ import annotations

from collections.abc import Mapping

from utils.tools.content_models import StandardArticle

CHIP_THEMES: dict[str, tuple[str, ...]] = {
    "新品发布": ("流片", "量产", "首发", "发布会", "新一代", "推出", "上市"),
    "价格变动": ("涨价", "调价", "提价", "降价", "合同价", "长协价", "报价"),
    "产能/扩产": ("扩产", "新厂", "投产", "产能", "建厂", "封顶", "增资"),
    "供需/缺货": ("缺货", "短缺", "供应紧张", "去库存", "下单", "订单"),
    "技术节点突破": ("nm", "EUV", "GAA", "良率", "工艺", "制程"),
}


def classify_chip_themes(
    article: StandardArticle,
    *,
    themes: Mapping[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Return all themes whose keyword set hits the article text.

    Search space: title + summary + content_text concatenated, case-insensitive
    for ASCII (Chinese keywords are case-stable so this only matters for "nm"/"EUV").
    """

    catalog = themes if themes is not None else CHIP_THEMES
    haystack = " ".join((article.title, article.summary, article.content_text)).lower()
    hits: list[str] = []
    for theme, keywords in catalog.items():
        for kw in keywords:
            if kw.lower() in haystack:
                hits.append(theme)
                break
    return hits
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/chip/test_chip_themes.py -v
```

Expected: 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/utils/tools/research/chip_themes.py src/utils/tools/research/__init__.py tests/chip/test_chip_themes.py && \
git commit -m "feat(chip): theme classifier with 5 buckets" || true
```

---

## Task 12: ChipSourceAdapter — channel 路由 + 同 channel 去重 + 跨 channel 标题去重

**Files:**
- Create: `src/chip/source_adapter.py`
- Create: `tests/chip/test_source_adapter.py`

- [ ] **Step 1: Write failing test**

Create `tests/chip/test_source_adapter.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from chip.channels.spec import ChipChannelSpec
from chip.source_adapter import ChipSourceAdapter, cross_channel_dedupe
from utils.tools.content_models import RawArticleDetail, RawArticleRef


def _make_ref(channel: str, article_id: str, title: str, url: str = "") -> RawArticleRef:
    return RawArticleRef(
        source_site="chip",
        article_id=article_id,
        title=title,
        url=url or f"https://example.com/{channel}/{article_id}",
        published_at="2026-05-19",
        channel=channel,
        source_bucket=channel,
    )


def _spec(key: str, listing_refs: list[RawArticleRef], detail: RawArticleDetail | None = None):
    def fl(spec, report_date):
        return listing_refs
    def fa(spec, ref):
        if detail is None:
            raise RuntimeError("no detail configured")
        return detail
    return ChipChannelSpec(
        key=key,
        name=key.upper(),
        listing_urls=(),
        fetch_listing=fl,
        fetch_article=fa,
    )


def test_adapter_routes_to_each_channel(monkeypatch):
    semi_refs = [_make_ref("semi", "a1", "AAA")]
    laoyaoba_refs = [_make_ref("laoyaoba", "b1", "BBB")]
    fake_channels = {
        "semi": _spec("semi", semi_refs),
        "laoyaoba": _spec("laoyaoba", laoyaoba_refs),
    }
    monkeypatch.setattr("chip.source_adapter.CHANNELS", fake_channels)
    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(date(2026, 5, 19))
    assert len(refs) == 2
    assert {r.channel for r in refs} == {"semi", "laoyaoba"}


def test_adapter_dedupes_within_channel(monkeypatch):
    dup = _make_ref("semi", "a1", "AAA", url="https://example.com/semi/a1")
    refs_with_dup = [dup, dup]
    fake_channels = {"semi": _spec("semi", refs_with_dup), "laoyaoba": _spec("laoyaoba", [])}
    monkeypatch.setattr("chip.source_adapter.CHANNELS", fake_channels)
    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(date(2026, 5, 19))
    assert len(refs) == 1


def test_adapter_continues_when_one_channel_fails(monkeypatch):
    def failing(spec, report_date):
        raise RuntimeError("network down")
    good = _spec("laoyaoba", [_make_ref("laoyaoba", "b1", "BBB")])
    bad = ChipChannelSpec(key="semi", name="SEMI", listing_urls=(), fetch_listing=failing, fetch_article=lambda *a: None)
    monkeypatch.setattr("chip.source_adapter.CHANNELS", {"semi": bad, "laoyaoba": good})
    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(date(2026, 5, 19))
    assert len(refs) == 1
    assert refs[0].channel == "laoyaoba"
    assert "semi" in adapter.failed_channels


def test_adapter_fetch_article_routes_to_channel(monkeypatch):
    detail = RawArticleDetail(
        source_site="chip", article_id="b1", title="BBB",
        url="https://example.com/laoyaoba/b1", published_at="2026-05-19",
        author="", channel="laoyaoba", source_bucket="laoyaoba",
        tags=[], summary="", content_text="body",
    )
    ch = _spec("laoyaoba", [], detail=detail)
    monkeypatch.setattr("chip.source_adapter.CHANNELS", {"laoyaoba": ch, "semi": _spec("semi", [])})
    adapter = ChipSourceAdapter()
    ref = _make_ref("laoyaoba", "b1", "BBB")
    got = adapter.fetch_article(ref)
    assert got is detail


def test_cross_channel_dedupe_picks_semi_when_titles_similar():
    semi_ref = _make_ref("semi", "a1", "格罗方德推出CPO硅光子方案")
    lyb_ref = _make_ref("laoyaoba", "b1", "格罗方德推出 CPO 硅光子方案")
    kept = cross_channel_dedupe([lyb_ref, semi_ref], similarity_threshold=0.6)
    assert len(kept) == 1
    assert kept[0].channel == "semi"
    assert lyb_ref.url in kept[0].metadata.get("alt_urls", [])


def test_cross_channel_dedupe_keeps_distinct_titles():
    a = _make_ref("semi", "a1", "中芯国际发布Q1财报")
    b = _make_ref("laoyaoba", "b1", "特斯拉放弃印度建厂")
    kept = cross_channel_dedupe([a, b], similarity_threshold=0.6)
    assert len(kept) == 2
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_source_adapter.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `src/chip/source_adapter.py`**

```python
"""ChipSourceAdapter：把 chip channels 路由到 ContentSourceAdapter 协议。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

from chip.channels.spec import CHANNELS
from utils.tools.content_models import (
    ContentSourceAdapter,
    RawArticleDetail,
    RawArticleRef,
    StandardArticle,
)

logger = logging.getLogger(__name__)


class ChipSourceAdapter(ContentSourceAdapter):
    """聚合 SEMI + 老姚吧 channel 的 chip 单源适配器。"""

    source_site = "chip"

    def __init__(self) -> None:
        self.failed_channels: set[str] = set()

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        refs: list[RawArticleRef] = []
        for channel_key, spec in CHANNELS.items():
            try:
                channel_refs = spec.fetch_listing(spec, report_date)
            except Exception as exc:
                logger.warning("chip channel listing failed: %s: %s", channel_key, exc)
                self.failed_channels.add(channel_key)
                continue
            refs.extend(channel_refs)
        return _dedupe_within_channel(refs)

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        spec = CHANNELS[ref.channel]
        return spec.fetch_article(spec, ref)

    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle:
        keywords = [str(k) for k in raw.metadata.get("keywords") or []]
        return StandardArticle(
            source_site=self.source_site,
            source_bucket=raw.source_bucket or raw.channel,
            channel=raw.channel,
            article_id=raw.article_id,
            title=raw.title,
            url=raw.url,
            published_at=raw.published_at,
            author=raw.author,
            tags=list(raw.tags),
            keywords=keywords,
            summary=raw.summary,
            content_text=raw.content_text,
            metadata=dict(raw.metadata),
        )


def _dedupe_within_channel(refs: Iterable[RawArticleRef]) -> list[RawArticleRef]:
    seen: set[tuple[str, str]] = set()
    out: list[RawArticleRef] = []
    for ref in refs:
        key = (ref.channel, ref.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def cross_channel_dedupe(
    refs: list[RawArticleRef],
    *,
    similarity_threshold: float = 0.6,
) -> list[RawArticleRef]:
    """Drop near-duplicate titles across channels; keep SEMI when both present.

    Uses 2-gram Jaccard similarity over normalized titles. Surviving ref carries
    the dropped sibling's URL in `metadata['alt_urls']`.
    """

    # Sort so SEMI comes first; the first survivor wins.
    priority = {"semi": 0, "laoyaoba": 1}
    ordered = sorted(refs, key=lambda r: (priority.get(r.channel, 99), r.article_id))

    survivors: list[RawArticleRef] = []
    for ref in ordered:
        norm = _normalize_title(ref.title)
        merged = False
        for i, kept in enumerate(survivors):
            if _jaccard_2gram(norm, _normalize_title(kept.title)) >= similarity_threshold:
                alt = list(kept.metadata.get("alt_urls") or [])
                alt.append(ref.url)
                new_meta = dict(kept.metadata)
                new_meta["alt_urls"] = alt
                survivors[i] = _replace_metadata(kept, new_meta)
                merged = True
                break
        if not merged:
            survivors.append(ref)
    return survivors


def _replace_metadata(ref: RawArticleRef, metadata: dict[str, object]) -> RawArticleRef:
    return RawArticleRef(
        source_site=ref.source_site,
        article_id=ref.article_id,
        title=ref.title,
        url=ref.url,
        published_at=ref.published_at,
        channel=ref.channel,
        source_bucket=ref.source_bucket,
        summary=ref.summary,
        metadata=metadata,
    )


def _normalize_title(text: str) -> str:
    """Strip punctuation/whitespace, lowercase ASCII, full→half width."""

    if not text:
        return ""
    # Full-width to half-width digits and letters
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif ch in "　 \t\n\r":
            continue
        elif ch in "，。！？：；、,.!?:;\"'()（）「」【】":
            continue
        else:
            out.append(ch.lower())
    return "".join(out)


def _jaccard_2gram(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    def grams(s: str) -> set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}
    sa, sb = grams(a), grams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/chip/test_source_adapter.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/source_adapter.py tests/chip/test_source_adapter.py && \
git commit -m "feat(chip): ChipSourceAdapter + cross-channel title dedupe" || true
```

---

## Task 12.5: chip topic grouping — replace LLM clustering with CHIP_THEMES bucketing

InfoQ 在 Step 1.5 调 `auto_group_analysis_topics`（LLM 把文章聚类成主题）。chip 要求按 `CHIP_THEMES` 5 个固定桶分组，纯关键词命中即可，不必走 LLM。

**Files:**
- Create: `src/chip/topic_grouping.py`
- Create: `tests/chip/test_topic_grouping.py`

- [ ] **Step 1: Write failing test `tests/chip/test_topic_grouping.py`**

```python
from __future__ import annotations

from utils.tools.facades.intelligence import ArticleAnalysis, TopicBrief
from chip.topic_grouping import group_analyses_by_chip_themes


def _analysis(title: str, summary: str = "", content: str = "") -> ArticleAnalysis:
    # ArticleAnalysis fields are populated by analyze_daily_articles; we mock
    # only what group_analyses_by_chip_themes touches.
    return ArticleAnalysis(
        article_id=title[:8],
        title=title,
        url=f"https://example.com/{title[:8]}",
        published_at="2026-05-19",
        channel="semi",
        source_bucket="semi",
        summary=summary,
        keywords=[],
        topic="",
        core_signal=content[:60] if content else summary[:60],
        action_hint="",
        entities=[],
        raw_text=" ".join((title, summary, content)),
    )


def test_groups_into_chip_themes():
    analyses = [
        _analysis("台积电2nm新工艺良率提升"),
        _analysis("DRAM合约价上涨7%"),
        _analysis("中微四川公司增资10亿元"),
        _analysis("HBM供应紧张"),
        _analysis("通用商业新闻无关半导体"),
    ]
    new_analyses, briefs = group_analyses_by_chip_themes(analyses)
    topics = {a.topic for a in new_analyses}
    assert "技术节点突破" in topics
    assert "价格变动" in topics
    assert "产能/扩产" in topics
    assert "供需/缺货" in topics
    assert "其他动态" in topics
    assert len(briefs) >= 5


def test_briefs_carry_article_ids():
    analyses = [_analysis("英特尔14A制程量产")]
    _, briefs = group_analyses_by_chip_themes(analyses)
    node_brief = next(b for b in briefs if b.topic == "技术节点突破")
    assert analyses[0].article_id in node_brief.article_ids
```

- [ ] **Step 2: Verify the ArticleAnalysis dataclass signature**

```bash
grep -n "class ArticleAnalysis\|@dataclass" src/utils/tools/facades/intelligence.py | head -5
python -c "from utils.tools.facades.intelligence import ArticleAnalysis, TopicBrief; import dataclasses; print([f.name for f in dataclasses.fields(ArticleAnalysis)]); print([f.name for f in dataclasses.fields(TopicBrief)])"
```

If the field names differ from those used in the test fixture above, adjust the test's `_analysis()` factory to match (only the fields you actually set must exist; missing fields with no defaults will TypeError).

- [ ] **Step 3: Run, verify fail**

```bash
pytest tests/chip/test_topic_grouping.py -v
```

Expected: ImportError for `chip.topic_grouping`.

- [ ] **Step 4: Implement `src/chip/topic_grouping.py`**

```python
"""Deterministic chip-theme topic grouping (replaces LLM clustering for chip)."""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Iterable

from utils.tools.content_models import StandardArticle
from utils.tools.facades.intelligence import ArticleAnalysis, TopicBrief
from utils.tools.research.chip_themes import CHIP_THEMES, classify_chip_themes

OTHER_TOPIC = "其他动态"


def group_analyses_by_chip_themes(
    analyses: Iterable[ArticleAnalysis],
) -> tuple[list[ArticleAnalysis], list[TopicBrief]]:
    """Assign each ArticleAnalysis to its first-matched chip theme; build TopicBriefs.

    Returns (new_analyses_with_topic_set, topic_briefs). New analyses are dataclass
    replacements with the `.topic` field filled in.
    """

    grouped: dict[str, list[ArticleAnalysis]] = defaultdict(list)
    new_analyses: list[ArticleAnalysis] = []
    theme_order = list(CHIP_THEMES.keys()) + [OTHER_TOPIC]

    for analysis in analyses:
        themes = _classify_from_analysis(analysis)
        topic = themes[0] if themes else OTHER_TOPIC
        new = dataclasses.replace(analysis, topic=topic)
        new_analyses.append(new)
        grouped[topic].append(new)

    briefs: list[TopicBrief] = []
    for topic in theme_order:
        bucket = grouped.get(topic) or []
        if not bucket:
            continue
        briefs.append(
            TopicBrief(
                topic=topic,
                summary=f"{topic}：{len(bucket)} 条信号",
                article_ids=[a.article_id for a in bucket],
            )
        )
    return new_analyses, briefs


def _classify_from_analysis(analysis: ArticleAnalysis) -> list[str]:
    """Adapt ArticleAnalysis to StandardArticle just enough to reuse classify_chip_themes."""

    fake = StandardArticle(
        source_site="chip",
        source_bucket=getattr(analysis, "source_bucket", "") or "",
        channel=getattr(analysis, "channel", "") or "",
        article_id=analysis.article_id,
        title=analysis.title,
        url=analysis.url,
        published_at=analysis.published_at,
        author="",
        tags=[],
        keywords=list(analysis.keywords) if analysis.keywords else [],
        summary=analysis.summary,
        content_text=(analysis.raw_text or analysis.core_signal or ""),
    )
    return classify_chip_themes(fake)
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/chip/test_topic_grouping.py -v
```

Expected: 2 tests pass. If field-name mismatches arose in Step 2, fix the dataclass-replace targets here too.

- [ ] **Step 6: Commit**

`chip/cli.py` doesn't exist yet — its wiring to this grouping function happens during Task 13's manual touch-ups. For now just commit the module:

```bash
git add src/chip/topic_grouping.py tests/chip/test_topic_grouping.py && \
git commit -m "feat(chip): CHIP_THEMES-driven topic grouping module" || true
```

---

## Task 13: chip/cli.py — clone of infoq/cli.py with chip prefixes

InfoQ 的 cli.py 是 397 行，结构上 chip 与之 95% 对称。本任务先用机械替换生成 chip/cli.py，再做 4 处人工修正。

**Files:**
- Create: `src/chip/cli.py`

- [ ] **Step 1: Clone infoq cli.py to chip and mechanically rename prefixes**

```bash
cd /root/workspace/shiqiang/touzifenxi
sed \
  -e 's/from infoq\.source_adapter import InfoQSourceAdapter/from chip.source_adapter import ChipSourceAdapter/g' \
  -e 's/InfoQSourceAdapter/ChipSourceAdapter/g' \
  -e 's/from \.settings/from chip.settings/g' \
  -e 's/InfoQ/Chip/g' \
  -e 's/infoq_/chip_/g' \
  -e 's/"infoq"/"chip"/g' \
  -e "s/'infoq'/'chip'/g" \
  -e 's/--source infoq/--source chip/g' \
  -e 's/infoq-hot-topics/chip-hot-topics/g' \
  -e 's/Run the InfoQ daily hot-topics skill\./Run the Chip industry-news daily skill./g' \
  src/infoq/cli.py > src/chip/cli.py
wc -l src/chip/cli.py
```

Expected output: ~397 lines.

- [ ] **Step 2: Diff against original to verify the substitutions**

```bash
diff <(sed 's/infoq/chip/g; s/InfoQ/Chip/g' src/infoq/cli.py) src/chip/cli.py | head -20
```

Expected: small or empty diff (only the 4 manual touchups in Step 3 should remain).

- [ ] **Step 3: Manual touch-ups in `src/chip/cli.py`**

Four edits required after the sed pass:

(a) **Replace the InfoQ-specific hot-topics fetcher**. Find the function near line 299 named `fetch_and_materialize_chip_articles` (post-sed name). Replace its body so it uses `ChipSourceAdapter` consistently:

Find:
```python
def fetch_and_materialize_chip_articles(
    target_date: date,
    paths: AppPaths,
    *,
    run_dir: Path | None = None,
) -> dict[str, object]:
```

Replace the entire body (everything until the next `def` at module level) with:

```python
def fetch_and_materialize_chip_articles(
    target_date: date,
    paths: AppPaths,
    *,
    run_dir: Path | None = None,
) -> dict[str, object]:
    """Step 0: fetch listing + per-article details via ChipSourceAdapter, persist raw JSON/CSV."""

    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(target_date)
    standards: list[StandardArticle] = []
    for ref in refs:
        try:
            detail = adapter.fetch_article(ref)
        except Exception:
            continue
        standards.append(adapter.normalize_article(detail))

    raw_json_path = (run_dir or paths.raw_dir) / raw_json_name(target_date)
    raw_json_path.parent.mkdir(parents=True, exist_ok=True)
    payload_articles = [asdict(s) for s in standards]
    raw_json_path.write_text(
        json.dumps(
            {
                "report_date": target_date.isoformat(),
                "by_channel": {
                    "semi": [a for a in payload_articles if a["channel"] == "semi"],
                    "laoyaoba": [a for a in payload_articles if a["channel"] == "laoyaoba"],
                },
                "articles": payload_articles,
                "failed_channels": sorted(adapter.failed_channels),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    raw_csv_path = (run_dir or paths.raw_dir) / RAW_CSV_NAME
    write_step1_csv(raw_csv_path, standards, report_date=target_date.isoformat())
    return {
        "articles": payload_articles,
        "raw_json_path": str(raw_json_path),
        "raw_csv_path": str(raw_csv_path),
        "failed_channels": sorted(adapter.failed_channels),
    }
```

(b) **Re-confirm imports near top of `src/chip/cli.py`**. The sed pass should have produced:

```python
from chip.source_adapter import ChipSourceAdapter
from chip.settings import AppPaths, ensure_directories, resolve_paths
from utils.tools.content_models import StandardArticle
```

If the InfoQ-specific helper `rehydrate_original_contents` references `InfoQSourceAdapter` anywhere it wasn't caught by sed, fix it. Run:

```bash
grep -n "InfoQ\|infoq" src/chip/cli.py
```

Expected: zero matches. If non-zero, hand-edit each line.

(c) **Update `RAW_JSON_PREFIX` block** — sed has already changed all PREFIX constants. Confirm:

```bash
grep -E "^(RUN_DIR_PREFIX|RAW_JSON_PREFIX|RAW_CSV_NAME|STEP_[0-9]+|LAYER_ISSUES_PREFIX|SEARCH_TRACE_PREFIX)" src/chip/cli.py
```

Expected: all use `chip_*` prefixes.

(d) **Verify checkpoint prefix usage**. Search for `checkpoint_prefix="`:

```bash
grep -n 'checkpoint_prefix=' src/chip/cli.py
```

All occurrences should pass `checkpoint_prefix="chip"`. The sed pass handles this via `'infoq' → 'chip'` substitution.

(e) **Wire CHIP_THEMES topic grouping** — replace LLM clustering call (per Task 12.5).

Find the block in `src/chip/cli.py` that allocates the step_1_5 checkpoint store and calls `auto_group_analysis_topics`:

```python
topic_grouping_checkpoint_store = StepCheckpointStore.load_or_create(
    checkpoint_path=checkpoint_path_for_step(
        output_path=analysis_output,
        step_name="step_1_5",
        report_date=report_date_text,
        prefix="chip",
    ),
    step_name="step_1_5",
    report_date=report_date_text,
    input_path=raw_csv_path,
    output_path=analysis_output,
)
analyses, briefs = auto_group_analysis_topics(
    analyses,
    llm_client,
    report_date=report_date_text,
    source_site="chip",
    checkpoint_store=topic_grouping_checkpoint_store,
)
```

Replace the entire block with:

```python
from chip.topic_grouping import group_analyses_by_chip_themes
analyses, briefs = group_analyses_by_chip_themes(analyses)
```

(The import can also be moved to the top of the file if preferred; inline import keeps the diff minimal here.)

After this edit, also drop the no-longer-needed import of `auto_group_analysis_topics` from the top of `src/chip/cli.py` (if it's not used elsewhere):

```bash
grep -n "auto_group_analysis_topics" src/chip/cli.py
```

If only the import remains, remove the line.

- [ ] **Step 4: Add chip-specific helper imports if any are missing**

Check `src/chip/cli.py` head imports include:

```python
from utils.tools.output.briefing import write_step1_csv
```

This should be present from the sed pass. Verify:

```bash
grep "write_step1_csv" src/chip/cli.py
```

- [ ] **Step 5: Smoke test the CLI imports and argparse**

Create `tests/chip/test_cli_smoke.py`:

```python
from __future__ import annotations

import pytest


def test_cli_module_imports():
    # Plain import smoke — surfaces any syntax / NameError post-sed
    import chip.cli  # noqa: F401


def test_build_parser_lists_expected_subcommands():
    from chip.cli import build_parser
    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    names = set(subparsers_action.choices.keys())
    assert "chip-hot-topics" in names
    assert "run" in names


def test_cli_help_does_not_crash():
    from chip.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
```

Run:

```bash
pytest tests/chip/test_cli_smoke.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/chip/cli.py tests/chip/test_cli_smoke.py && \
git commit -m "feat(chip): cli (clone of infoq/cli.py + ChipSourceAdapter wiring)" || true
```

---

## Task 14: Parameterize render_c114_brief_email footer

让 `render_c114_brief_email` 接收 `footer_disclaimer=` 参数（默认 c114 常量），这样 chip 可以传自己的 footer 文案，未来 infoq/kr36 也能复用同一个函数。

**Files:**
- Modify: `src/utils/tools/output/email.py`
- Create: `tests/utils/test_email_render.py`

- [ ] **Step 1: Locate the function and its current signature**

```bash
grep -n "def render_c114_brief_email\|C114_EMAIL_FOOTER_DISCLAIMER" src/utils/tools/output/email.py
```

Expected: shows the function and the constant.

- [ ] **Step 2: Write failing test**

Create `tests/utils/__init__.py` if missing:

```bash
mkdir -p tests/utils && touch tests/utils/__init__.py
```

Create `tests/utils/test_email_render.py`:

```python
from __future__ import annotations

from utils.tools.output.email import render_c114_brief_email

SAMPLE_MD = """# 测试简报

## 🆕 新品发布
- 【SEMI】XX 公司新品 (link)

## 💰 价格变动
- 【SEMI】合约价上涨 (link)
"""


def test_default_footer_is_c114():
    rendered = render_c114_brief_email(SAMPLE_MD)
    assert "c114" in rendered.html.lower() or "通信" in rendered.html


def test_custom_footer_passes_through():
    rendered = render_c114_brief_email(
        SAMPLE_MD,
        footer_disclaimer="本简报由 chip 流水线生成，仅供研究参考。",
    )
    assert "chip 流水线" in rendered.html
```

- [ ] **Step 3: Run, verify fail**

```bash
pytest tests/utils/test_email_render.py -v
```

Expected: `test_custom_footer_passes_through` fails — current function doesn't accept `footer_disclaimer`.

- [ ] **Step 4: Edit `render_c114_brief_email` signature**

In `src/utils/tools/output/email.py`, find the function:

```python
def render_c114_brief_email(markdown_text: str) -> RenderedEmail:
```

Change to:

```python
def render_c114_brief_email(
    markdown_text: str,
    *,
    footer_disclaimer: str = C114_EMAIL_FOOTER_DISCLAIMER,
) -> RenderedEmail:
```

Then in the function body, replace **all** occurrences of the bareword `C114_EMAIL_FOOTER_DISCLAIMER` (there are two — in the text rendering call and the HTML rendering call) with `footer_disclaimer`:

```python
# Before:
footer_disclaimer=C114_EMAIL_FOOTER_DISCLAIMER,
# After:
footer_disclaimer=footer_disclaimer,
```

(Verify with `grep -n "C114_EMAIL_FOOTER_DISCLAIMER" src/utils/tools/output/email.py` — only the constant *definition* and the **default value** in the signature should remain.)

- [ ] **Step 5: Run tests**

```bash
pytest tests/utils/test_email_render.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Sanity-check existing c114 callers still work**

```bash
pytest tests -k "email" -v --co
pytest tests -k "c114" -v --co 2>&1 | head -20
```

(Collect-only just confirms no import breakage.)

- [ ] **Step 7: Commit**

```bash
git add src/utils/tools/output/email.py tests/utils/test_email_render.py tests/utils/__init__.py && \
git commit -m "refactor(email): parameterize footer_disclaimer for cross-source reuse" || true
```

---

## Task 14.5: chip/pipeline.py — c114-aligned daily-brief + email entrypoints

`src/utils/cli.py` 已有 `run-c114-daily-brief` 调用 `c114.pipeline.run_c114_daily_brief(project_root, report_date, recipients, send_mail) → C114DailyBriefRunResult` 的接口。chip 需要相同形态的入口函数才能用同样的 wiring pattern。

**实测发现：**
- 实际邮件发送函数：`utils.tools.output.email.send_email(*, recipient_emails, subject, body_text, body_html, attachments=(), config=None)` —— **`docs/channels.md` 提到的 `src/touzifenxi/channels/email.py` 在当前仓库不存在**，spec 中的"复用 channels.email" 路径要修正为复用 `utils.tools.output.email.send_email`
- c114 pipeline 用 `dataclass ChipDailyBriefRunResult` 类型聚合 success/run_dir/step6_path/failure_step 等信息

**Files:**
- Create: `src/chip/pipeline.py`
- Create: `tests/chip/test_pipeline.py`

- [ ] **Step 1: Write failing test `tests/chip/test_pipeline.py`**

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from chip.pipeline import (
    ChipDailyBriefRunResult,
    run_chip_daily_brief,
    send_latest_chip_brief_email,
    shanghai_yesterday,
)


def test_shanghai_yesterday_returns_date():
    d = shanghai_yesterday()
    assert isinstance(d, date)


def test_run_chip_daily_brief_failure_path_returns_result(tmp_path, monkeypatch):
    """If the underlying chip.cli step fails, run_chip_daily_brief surfaces it."""

    def boom(*args, **kwargs):
        raise RuntimeError("fixtures missing")

    monkeypatch.setattr("chip.cli.run_with_args", boom)

    result = run_chip_daily_brief(
        project_root=tmp_path,
        report_date=date(2026, 5, 19),
        recipients=["test@example.com"],
        send_mail=False,
    )
    assert isinstance(result, ChipDailyBriefRunResult)
    assert result.succeeded is False
    assert "fixtures missing" in result.failure_reason


def test_send_latest_chip_brief_email_missing_step6(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = send_latest_chip_brief_email(
        project_root=tmp_path,
        recipients=["test@example.com"],
        report_date=date(2026, 5, 19),
        require_modified_not_before=None,
        step6_md_path=None,
    )
    assert result.succeeded is False
    assert "step6" in result.error_detail.lower() or "未找到" in result.error_detail
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/chip/test_pipeline.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `src/chip/pipeline.py`**

```python
"""Chip 每日简报 pipeline 入口（与 src/c114/pipeline.py 接口对齐，供 src/utils/cli.py 调用）。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_SHANGHAI = timezone(timedelta(hours=8))

_CHIP_BRIEF_FOOTER = (
    "本简报由 chip（SEMI 中国 + 老姚吧）产业新闻流水线生成，仅供内部研究参考。"
)


def shanghai_yesterday() -> date_cls:
    return (datetime.now(_SHANGHAI) - timedelta(days=1)).date()


@dataclass
class ChipDailyBriefRunResult:
    succeeded: bool
    failure_step: str = ""
    failure_reason: str = ""
    run_dir: str = ""
    step6_path: str = ""
    log_path: str = ""


@dataclass
class ChipEmailSendResult:
    succeeded: bool
    step6_path: str = ""
    error_detail: str = ""


def run_chip_daily_brief(
    *,
    project_root: Path,
    report_date: date_cls,
    recipients: list[str],
    send_mail: bool,
) -> ChipDailyBriefRunResult:
    """Run chip pipeline for `report_date`, optionally email the brief."""

    import chip.cli as chip_cli
    from chip.settings import resolve_paths

    paths = resolve_paths()
    chip_args = argparse.Namespace(command="run", date=report_date.isoformat())
    try:
        chip_cli.run_with_args(chip_args, paths=paths)
    except Exception as exc:
        return ChipDailyBriefRunResult(
            succeeded=False,
            failure_step="chip.cli.run",
            failure_reason=str(exc),
        )

    step6_path = _locate_step6_markdown(paths.reports_dir, report_date)
    if step6_path is None:
        return ChipDailyBriefRunResult(
            succeeded=False,
            failure_step="locate_step6",
            failure_reason=f"no chip_step_6_brief_{report_date.strftime('%Y%m%d')}.md under {paths.reports_dir}",
        )

    if send_mail:
        send_result = send_latest_chip_brief_email(
            project_root=project_root,
            recipients=recipients,
            report_date=report_date,
            require_modified_not_before=None,
            step6_md_path=step6_path,
        )
        if not send_result.succeeded:
            return ChipDailyBriefRunResult(
                succeeded=False,
                failure_step="email",
                failure_reason=send_result.error_detail,
                run_dir=str(step6_path.parent),
                step6_path=str(step6_path),
            )

    return ChipDailyBriefRunResult(
        succeeded=True,
        run_dir=str(step6_path.parent),
        step6_path=str(step6_path),
    )


def send_latest_chip_brief_email(
    *,
    project_root: Path,
    recipients: list[str],
    report_date: Optional[date_cls],
    require_modified_not_before: Optional[datetime],
    step6_md_path: Optional[Path],
) -> ChipEmailSendResult:
    """Find latest chip step6 markdown and email it.

    Either `step6_md_path` is provided directly, or `report_date` is used to
    locate the most recent file matching `chip_step_6_brief_YYYYMMDD.md`.
    """

    from chip.settings import resolve_paths
    from utils.tools.output.email import render_c114_brief_email, send_email

    if step6_md_path is None:
        if report_date is None:
            return ChipEmailSendResult(succeeded=False, error_detail="report_date required when step6_md_path absent")
        paths = resolve_paths()
        step6_md_path = _locate_step6_markdown(paths.reports_dir, report_date)
    if step6_md_path is None or not step6_md_path.exists():
        return ChipEmailSendResult(succeeded=False, error_detail="未找到 chip step6 markdown 文件")

    if require_modified_not_before is not None:
        mtime = datetime.fromtimestamp(step6_md_path.stat().st_mtime, _SHANGHAI)
        if mtime < require_modified_not_before:
            return ChipEmailSendResult(
                succeeded=False,
                step6_path=str(step6_md_path),
                error_detail=f"step6 mtime {mtime} 早于门控 {require_modified_not_before}",
            )

    md_text = step6_md_path.read_text(encoding="utf-8")
    rendered = render_c114_brief_email(md_text, footer_disclaimer=_CHIP_BRIEF_FOOTER)
    try:
        send_email(
            recipient_emails=recipients,
            subject=rendered.subject,
            body_text=rendered.text,
            body_html=rendered.html,
        )
    except Exception as exc:
        return ChipEmailSendResult(
            succeeded=False,
            step6_path=str(step6_md_path),
            error_detail=f"send_email 失败: {exc}",
        )

    return ChipEmailSendResult(succeeded=True, step6_path=str(step6_md_path))


def _locate_step6_markdown(reports_dir: Path, report_date: date_cls) -> Optional[Path]:
    date_token = report_date.strftime("%Y%m%d")
    candidates: list[Path] = []
    candidates.extend(reports_dir.glob(f"*/chip_step_6_brief_{date_token}.md"))
    candidates.extend(reports_dir.glob(f"*/*/chip_step_6_brief_{date_token}.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/chip/test_pipeline.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/chip/pipeline.py tests/chip/test_pipeline.py && \
git commit -m "feat(chip): pipeline.py with c114-aligned run/send entrypoints" || true
```

---

## Task 15: src/utils/cli.py — add `run-chip-daily-brief` + `send-chip-latest-brief-email`

**Files:**
- Modify: `src/utils/cli.py`

- [ ] **Step 1: Locate existing `run-c114-daily-brief` command**

```bash
grep -n "run-c114-daily-brief\|send-c114-latest-brief-email\|def run_c114_daily_brief" src/utils/cli.py | head -10
```

This gives the anchors for inserting the chip equivalents next to them.

- [ ] **Step 2: Inspect handler structure for run-c114-daily-brief**

```bash
sed -n "/run-c114-daily-brief/,/^def \|^    parser\\.add\\|^if __name/p" src/utils/cli.py | head -60
```

Note the handler function name (likely `run_c114_daily_brief`) and the subparser registration block.

- [ ] **Step 3: Add subparsers (mirror c114 block structure)**

Find the existing `c114_runner_parser = subparsers.add_parser("run-c114-daily-brief", ...)` and `send_c114_latest_parser` blocks in `src/utils/cli.py`. Insert the chip equivalents right after `send_c114_latest_parser`'s last `.add_argument(...)` call:

```python
chip_runner_parser = subparsers.add_parser(
    "run-chip-daily-brief",
    help="以项目内统一 runner 运行 chip 产业新闻当日简报（SEMI + 老姚吧），并在成功后发送邮件。",
)
chip_runner_parser.add_argument(
    "--date",
    default=None,
    help="可选日期，格式 YYYY-MM-DD；不传时使用 Asia/Shanghai 的 T-1 日期。",
)
chip_runner_parser.add_argument(
    "--no-email",
    action="store_true",
    help="仅跑流水线并生成本地 Step6 markdown，不发送邮件。",
)
chip_runner_parser.add_argument(
    "--to",
    nargs="+",
    default=["zx944532395@sina.com"],
    help="邮件收件人列表。",
)

send_chip_latest_parser = subparsers.add_parser(
    "send-chip-latest-brief-email",
    help="仅根据 chip Step6 简报渲染并发送邮件，不重新跑流水线。",
)
send_chip_latest_parser.add_argument(
    "--to",
    nargs="+",
    default=["zx944532395@sina.com"],
    help="邮件收件人列表。",
)
send_chip_latest_parser.add_argument(
    "--report-date",
    default=None,
    help="指定统计日 YYYY-MM-DD；只发送该日的 chip_step_6_brief_YYYYMMDD.md。缺省时取 T-1。",
)
```

- [ ] **Step 4: Add command dispatch branches**

Find the existing `if args.command == "run-c114-daily-brief":` block in `src/utils/cli.py` (around line 449). After the `send-c114-latest-brief-email` branch ends, add:

```python
if args.command == "run-chip-daily-brief":
    from datetime import date as date_cls

    from chip.pipeline import run_chip_daily_brief, shanghai_yesterday

    report_date = date_cls.fromisoformat(args.date) if args.date else shanghai_yesterday()
    result = run_chip_daily_brief(
        project_root=paths.project_root,
        report_date=report_date,
        recipients=args.to,
        send_mail=not args.no_email,
    )
    if result.succeeded:
        print(f"运行目录: {result.run_dir}")
        print(f"Step 6 文件: {result.step6_path}")
        if args.no_email:
            print("已跳过邮件发送（--no-email）。")
        else:
            print(f"邮件发送完成: {', '.join(args.to)}")
        return
    print(f"失败步骤: {result.failure_step}")
    print(f"失败原因: {result.failure_reason}")
    if result.run_dir:
        print(f"运行目录: {result.run_dir}")
    raise SystemExit(1)

if args.command == "send-chip-latest-brief-email":
    from datetime import date as date_cls

    from chip.pipeline import send_latest_chip_brief_email, shanghai_yesterday

    report_date = date_cls.fromisoformat(args.report_date) if args.report_date else shanghai_yesterday()
    result = send_latest_chip_brief_email(
        project_root=paths.project_root,
        recipients=args.to,
        report_date=report_date,
        require_modified_not_before=None,
        step6_md_path=None,
    )
    if not result.succeeded:
        print(result.error_detail or "发送失败。")
        raise SystemExit(1)
    print(f"Step 6 文件: {result.step6_path}")
    print(f"邮件发送完成: {', '.join(args.to)}")
    return
```

- [ ] **Step 5: Smoke test the new subcommands**

```bash
touzifenxi run-chip-daily-brief --help 2>&1 | head -10
touzifenxi send-chip-latest-brief-email --help 2>&1 | head -10
```

Expected: both produce help text mentioning `--date`/`--no-email`/`--to`/`--report-date` as appropriate.

If the `touzifenxi` entrypoint isn't on PATH, run via module instead:

```bash
PYTHONPATH=src python -m utils.cli run-chip-daily-brief --help
```

- [ ] **Step 6: Commit**

```bash
git add src/utils/cli.py && \
git commit -m "feat(cli): wire run-chip-daily-brief + send-chip-latest-brief-email" || true
```

---

## Task 16: scripts/websearch.py — `--source` dispatch

**Files:**
- Modify: `scripts/websearch.py`

- [ ] **Step 1: Read current contents**

```bash
cat scripts/websearch.py
```

Confirms it currently hard-imports `c114.cli`.

- [ ] **Step 2: Rewrite to dispatch on `--source`**

Replace the entire contents with:

```python
from __future__ import annotations

import argparse
import sys
from importlib import import_module
from pathlib import Path

_SOURCES = {
    "c114": "c114.cli",
    "infoq": "infoq.cli",
    "chip": "chip.cli",
}


def bootstrap() -> None:
    project_root = Path(__file__).resolve().parents[1]
    scripts_dir = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != scripts_dir]
    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def main() -> None:
    bootstrap()
    from utils.tools.runtime.win_stdio import ensure_utf8_stdio
    ensure_utf8_stdio()

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", choices=list(_SOURCES.keys()), default="c114")
    args, remainder = parser.parse_known_args()
    target_module = import_module(_SOURCES[args.source])
    # Re-inject remainder so the target CLI sees its own args.
    sys.argv = [sys.argv[0]] + remainder
    target_module.main()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke test dispatch**

```bash
python scripts/websearch.py --source chip --help 2>&1 | head -10
```

Expected: shows the chip CLI help (subcommands `chip-hot-topics`, `run`, etc.).

```bash
python scripts/websearch.py --source c114 --help 2>&1 | head -5
```

Expected: shows c114 CLI help (backward-compat).

- [ ] **Step 4: Commit**

```bash
git add scripts/websearch.py && \
git commit -m "feat(websearch): dispatch on --source {c114|infoq|chip}" || true
```

---

## Task 17: Integration smoke — wire everything and run against a single fixture day

**Files:**
- Modify: `tests/chip/test_cli_smoke.py`（追加端到端 Step 0 测试）

- [ ] **Step 1: Append end-to-end Step 0 test**

```python
from __future__ import annotations

import json
from datetime import date

import pytest


def test_step0_writes_raw_json_and_csv(
    tmp_path,
    semi_home_html,
    semi_article_html,
    semi_column_html,
    laoyaoba_xinyaowen_html,
    laoyaoba_article_smic_html,
    monkeypatch,
):
    from chip import cli as chip_cli
    from chip.channels import laoyaoba as lyb_chan
    from chip.channels import semi as semi_chan
    from chip.settings import AppPaths

    # Build an AppPaths under tmp_path
    paths = AppPaths(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        reports_dir=tmp_path / "reports",
        state_dir=tmp_path / "state",
    )
    for p in (paths.raw_dir, paths.reports_dir, paths.state_dir, paths.processed_dir):
        p.mkdir(parents=True, exist_ok=True)

    # Intercept network. Simulate report_date = 2026-05-20 for SEMI (the article date),
    # and 2026-05-14 for laoyaoba (SMIC article date).
    target = date(2026, 5, 20)

    def semi_get(url: str) -> str:
        if "column" in url:
            return semi_column_html
        if "/article/" in url:
            return semi_article_html
        return semi_home_html

    def lyb_get(url: str) -> str:
        if "/n/" in url:
            return laoyaoba_article_smic_html  # MM-DD: 05-14, will NOT match 05-20
        return laoyaoba_xinyaowen_html

    monkeypatch.setattr(semi_chan, "_default_http_get", semi_get)
    monkeypatch.setattr(lyb_chan, "_default_http_get", lyb_get)

    payload = chip_cli.fetch_and_materialize_chip_articles(target, paths)
    raw_json = json.loads((paths.raw_dir / chip_cli.raw_json_name(target)).read_text(encoding="utf-8"))

    assert raw_json["report_date"] == "2026-05-20"
    assert isinstance(raw_json["articles"], list)
    # Must have at least the SEMI sample article (intel14a, 2026-05-20)
    semi_titles = [a["title"] for a in raw_json["articles"] if a["channel"] == "semi"]
    assert any("14A" in t or "陈立武" in t for t in semi_titles)
```

- [ ] **Step 2: Run the integration test**

```bash
pytest tests/chip/test_cli_smoke.py::test_step0_writes_raw_json_and_csv -v
```

Expected: PASS. If the cli function name `fetch_and_materialize_chip_articles` differs from what Task 13 wrote, adjust the import path.

- [ ] **Step 3: Run the full chip test suite**

```bash
pytest tests/chip -v
```

Expected: all tests pass.

- [ ] **Step 4: Ruff sanity**

```bash
ruff check src/chip src/utils/tools/research/chip_themes.py src/utils/tools/output/email.py src/utils/cli.py scripts/websearch.py
```

Expected: clean. Fix any reported issues.

- [ ] **Step 5: Commit**

```bash
git add tests/chip/test_cli_smoke.py && \
git commit -m "test(chip): end-to-end Step 0 smoke against HTML fixtures" || true
```

---

## Task 18: Live-network sanity test (gated, manual)

This is a single optional task to confirm the live抓取 still works against the real sites. It costs ~30s of HTTP requests, so it's gated behind `CHIP_NETWORK_TEST=1`.

**Files:**
- Append to: `tests/chip/test_channels_semi.py`
- Append to: `tests/chip/test_channels_laoyaoba.py`

- [ ] **Step 1: Append live SEMI test**

```python
import os

import pytest


@pytest.mark.chip_network
@pytest.mark.skipif(os.environ.get("CHIP_NETWORK_TEST") != "1", reason="set CHIP_NETWORK_TEST=1 to enable live fetch")
def test_live_semi_listing_returns_something():
    from chip.channels.semi import _default_http_get, parse_listing_html

    html = _default_http_get("https://www.semi.org.cn/site/semi/")
    refs = parse_listing_html(html, base_url="https://www.semi.org.cn/site/semi/")
    assert len(refs) >= 10
```

- [ ] **Step 2: Append live laoyaoba test**

```python
import os

import pytest


@pytest.mark.chip_network
@pytest.mark.skipif(os.environ.get("CHIP_NETWORK_TEST") != "1", reason="set CHIP_NETWORK_TEST=1 to enable live fetch")
def test_live_laoyaoba_listing_returns_something():
    from datetime import datetime, timedelta, timezone
    from chip.channels.laoyaoba import _default_http_get, parse_listing_html

    html = _default_http_get("https://www.laoyaoba.com/xinyaowen")
    items = parse_listing_html(html, reference_now=datetime.now(timezone(timedelta(hours=8))))
    assert len(items) >= 10
```

- [ ] **Step 3: Run live tests (with permission)**

```bash
CHIP_NETWORK_TEST=1 pytest tests/chip -m chip_network -v
```

Expected: 2 tests pass; sites return >= 10 article refs each.

If either site is rate-limiting or returning unexpected HTML, capture a fresh fixture (`curl ... -o tests/chip/fixtures/<file>.html`) and the unit tests will surface the breakage.

- [ ] **Step 4: Commit**

```bash
git add tests/chip/test_channels_semi.py tests/chip/test_channels_laoyaoba.py && \
git commit -m "test(chip): live network smoke (gated by CHIP_NETWORK_TEST=1)" || true
```

---

## Verification Checklist (final, run before declaring done)

- [ ] `pytest tests/chip -v` — all green (excluding `chip_network` marker)
- [ ] `ruff check src/chip src/utils/tools/research/chip_themes.py` — clean
- [ ] `touzifenxi run-chip-daily-brief --help` — shows new command
- [ ] `python scripts/websearch.py --source chip --help` — shows chip CLI subcommands
- [ ] `python scripts/websearch.py --source c114 --help` — still works (backward compat)
- [ ] Optional: `CHIP_NETWORK_TEST=1 pytest tests/chip -m chip_network -v` — live抓取 sanity
- [ ] Smoke run: `touzifenxi run-chip-daily-brief --date <yesterday>` end-to-end produces `output/reports/<chip_search_*>/chip_step_6_brief_*.md`
