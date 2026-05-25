from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree

from .models import Article


def parse_feed(xml_text: str, feed_url: str) -> list[Article]:
    try:
        return _parse_with_feedparser(xml_text, feed_url)
    except ImportError:
        return _parse_with_element_tree(xml_text, feed_url)


def _parse_with_feedparser(xml_text: str, feed_url: str) -> list[Article]:
    import feedparser

    parsed = feedparser.parse(xml_text)
    last_build_date = _clean(
        getattr(parsed.feed, "lastbuilddate", "")
        or getattr(parsed.feed, "published", "")
        or getattr(parsed.feed, "updated", "")
    )
    articles: list[Article] = []
    for entry in parsed.entries:
        source = ""
        if getattr(entry, "source", None):
            source = getattr(entry.source, "title", "") or getattr(entry.source, "href", "")

        articles.append(
            Article(
                title=_clean(getattr(entry, "title", "")),
                link=_clean(getattr(entry, "link", "")),
                pub_date=_clean(getattr(entry, "published", "") or getattr(entry, "updated", "")),
                description=_clean(getattr(entry, "summary", "")),
                source=_clean(source),
                feed_url=feed_url,
            )
        )
    return _filter_t_minus_one_window(articles, last_build_date)


def _parse_with_element_tree(xml_text: str, feed_url: str) -> list[Article]:
    root = ElementTree.fromstring(xml_text)
    channel = root.find(".//channel")
    last_build_date = ""
    if channel is not None:
        last_build_date = _clean(_find_text(channel, "lastBuildDate"))
    articles: list[Article] = []
    for item in root.findall(".//item"):
        source_element = item.find("source")
        articles.append(
            Article(
                title=_clean(_find_text(item, "title")),
                link=_clean(_find_text(item, "link")),
                pub_date=_clean(_find_text(item, "pubDate")),
                description=_clean(_find_text(item, "description")),
                source=_clean(source_element.text if source_element is not None else ""),
                feed_url=feed_url,
            )
        )
    return _filter_t_minus_one_window(articles, last_build_date)


def _find_text(item: ElementTree.Element, tag: str) -> str:
    element = item.find(tag)
    return element.text or "" if element is not None else ""


def _clean(value: object) -> str:
    return " ".join(unescape(str(value or "")).split())


def _filter_t_minus_one_window(articles: list[Article], last_build_date: str) -> list[Article]:
    upper = _parse_rfc_datetime(last_build_date)
    if upper is None:
        return articles

    lower = upper - timedelta(days=1)
    filtered: list[Article] = []
    for article in articles:
        published_at = _parse_rfc_datetime(article.pub_date)
        if published_at is None:
            continue
        if lower <= published_at <= upper:
            filtered.append(article)
    return filtered


def _parse_rfc_datetime(value: str) -> datetime | None:
    value = _clean(value)
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
