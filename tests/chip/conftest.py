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
