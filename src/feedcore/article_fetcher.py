from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

from .content_scrubber import scrub_extracted_noise


class ArticleFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserPage:
    url: str
    html: str
    pdf: bytes | None = None


class BrowserFetcher(Protocol):
    def fetch(self, url: str, timeout: int, save_pdf: bool) -> BrowserPage:
        pass


class ArticleClient:
    def __init__(
        self,
        timeout: int = 45,
        user_agent: str = "FeedCoreNewsBrief/0.1",
        browser_enabled: bool = False,
        document_dir: Path | None = None,
        save_html: bool = True,
        save_pdf: bool = False,
        browser_timeout: int = 30,
        session: object | None = None,
        browser_fetcher: BrowserFetcher | None = None,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.browser_enabled = browser_enabled
        self.document_dir = document_dir
        self.save_html = save_html
        self.save_pdf = save_pdf
        self.browser_timeout = browser_timeout
        self.session = session
        self.browser_fetcher = browser_fetcher or PlaywrightBrowserFetcher(user_agent=user_agent)

    def fetch_text(self, url: str) -> str:
        """先 HTTP；失败或正文为空时在 ``browser_enabled`` 下用 Playwright 再抓（Google News 链接常如此）。"""
        http_error: Exception | None = None
        try:
            response = self._http_get(url)
            response.raise_for_status()
            text = extract_article_text(response.text, url=url)
            if text:
                return text
        except Exception as exc:
            http_error = exc

        if self.browser_enabled:
            try:
                browser_text = self._fetch_with_browser(url)
                if browser_text:
                    return browser_text
            except Exception as browser_exc:
                if http_error:
                    raise ArticleFetchError(
                        f"No article text extracted from {url}; HTTP error: {http_error}; browser: {browser_exc}"
                    ) from browser_exc
                raise ArticleFetchError(f"No article text extracted from {url}; browser fallback failed: {browser_exc}") from browser_exc

        if http_error:
            raise ArticleFetchError(f"No article text extracted from {url}; HTTP failed: {http_error}") from http_error
        raise ArticleFetchError(f"No article text extracted from {url}")

    def _http_get(self, url: str):
        if self.session is not None:
            return self.session.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )

        import requests

        return requests.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )

    def _fetch_with_browser(self, url: str) -> str:
        try:
            page = self.browser_fetcher.fetch(url, timeout=self.browser_timeout, save_pdf=self.save_pdf)
        except Exception as exc:
            raise ArticleFetchError(f"No article text extracted from {url}; browser fallback failed: {exc}") from exc

        self._save_browser_documents(source_url=url, page=page)
        return extract_article_text(page.html, url=page.url)

    def _save_browser_documents(self, source_url: str, page: BrowserPage) -> None:
        if self.document_dir is None:
            return

        self.document_dir.mkdir(parents=True, exist_ok=True)
        stem = _document_stem(source_url)
        if self.save_html:
            (self.document_dir / f"{stem}.html").write_text(page.html, encoding="utf-8")
        if self.save_pdf and page.pdf:
            (self.document_dir / f"{stem}.pdf").write_bytes(page.pdf)


class PlaywrightBrowserFetcher:
    """Loads pages with Playwright.

    Many sites never reach ``networkidle`` (ads, analytics, WebSockets). Waiting the full
    user ``timeout`` for ``networkidle`` can stall for minutes. We cap that wait and the
    initial navigation so a single slow URL cannot block the whole pipeline for absurdly long.
    """

    # networkidle often never fires on news sites; never spend more than this on it.
    _NETWORK_IDLE_MAX_MS = 15_000
    # Hard cap for goto even if config mistakenly sets browser.timeout very large (seconds).
    _GOTO_MAX_MS = 180_000

    def __init__(self, user_agent: str = "FeedCoreNewsBrief/0.1") -> None:
        self.user_agent = user_agent

    def fetch(self, url: str, timeout: int, save_pdf: bool) -> BrowserPage:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ArticleFetchError(
                "Playwright is not installed. Run: python -m pip install -e ."
            ) from exc

        timeout_ms = timeout * 1000
        goto_timeout_ms = min(timeout_ms, self._GOTO_MAX_MS)
        networkidle_timeout_ms = min(timeout_ms, self._NETWORK_IDLE_MAX_MS)
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page(user_agent=self.user_agent)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout_ms)
                page.wait_for_load_state("networkidle", timeout=networkidle_timeout_ms)
            except Exception:
                # Many news sites keep connections open; current DOM is still useful.
                pass
            html = page.content()
            final_url = page.url
            pdf = page.pdf(format="A4") if save_pdf else None
            browser.close()
        return BrowserPage(url=final_url, html=html, pdf=pdf)


def _launch_browser(playwright):
    try:
        return playwright.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return playwright.chromium.launch(headless=True)


def extract_article_text(html: str, url: str | None = None) -> str:
    trafilatura_text = _extract_with_trafilatura(html, url)
    if trafilatura_text:
        return scrub_extracted_noise(trafilatura_text)

    soup_text = _extract_with_bs4(html)
    if soup_text:
        return scrub_extracted_noise(soup_text)

    return scrub_extracted_noise(_extract_with_stdlib(html))


def _extract_with_trafilatura(html: str, url: str | None) -> str:
    try:
        import trafilatura
    except ImportError:
        return ""

    extracted = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
    return _normalize_text(extracted or "")


def _extract_with_bs4(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    root = soup.find("article") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    return _normalize_text("\n\n".join(p for p in paragraphs if p))


def _extract_with_stdlib(html: str) -> str:
    parser = _ParagraphParser()
    parser.feed(html)
    return _normalize_text("\n\n".join(parser.paragraphs))


def _normalize_text(value: str) -> str:
    value = unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _document_stem(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"article_{digest}"


class _ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self._in_paragraph = False
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer"}:
            self._skip_depth += 1
        if tag == "p" and self._skip_depth == 0:
            self._in_paragraph = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "p" and self._in_paragraph:
            paragraph = _normalize_text(" ".join(self._parts))
            if paragraph:
                self.paragraphs.append(paragraph)
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_paragraph and self._skip_depth == 0:
            self._parts.append(data)
