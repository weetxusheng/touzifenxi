"""微信文章页离线查看：将懒加载/隐藏的正文媒体还原为可直连 URL。"""

from __future__ import annotations

import html
import os
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

# 与 parse.images.IMAGE_SRC_PRIORITY 一致，避免 dajiala → parse 依赖
IMAGE_URL_ATTRS = ("data-src", "data-original", "data-src-url", "src")
VIDEO_URL_ATTRS = ("data-src", "data-cover", "data-poster", "poster", "src")
AUDIO_URL_ATTRS = ("data-src", "src", "voice-src", "data-voice-src")

_PLACEHOLDER_SRC_RE = re.compile(
    r"^(?:data:image/|about:blank|javascript:|\s*$)",
    re.IGNORECASE,
)
_STYLE_HIDE_RE = re.compile(
    r"(visibility\s*:\s*hidden|opacity\s*:\s*0)\s*;?",
    re.IGNORECASE,
)
_CONTENT_ROOT_SELECTORS = ("#js_content", ".rich_media_content")


def weixin_offline_normalize_enabled() -> bool:
    v = os.environ.get("GZH_WEIXIN_OFFLINE_NORMALIZE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _absolutize_url(url: str, *, base: str = "https://mp.weixin.qq.com/") -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("/"):
        return urljoin(base, u)
    return u


def _pick_url(tag: Tag, attr_names: tuple[str, ...]) -> str:
    for name in attr_names:
        v = tag.get(name)
        if not v:
            continue
        s = str(v).strip()
        if not s or _PLACEHOLDER_SRC_RE.match(s):
            continue
        if s.startswith("data:"):
            continue
        return _absolutize_url(s)
    return ""


def _src_is_missing_or_placeholder(src: str | None) -> bool:
    s = (src or "").strip()
    return not s or bool(_PLACEHOLDER_SRC_RE.match(s))


def _strip_hide_style(style: str) -> str:
    cleaned = _STYLE_HIDE_RE.sub("", style or "")
    return re.sub(r";\s*;", ";", cleaned).strip(" ;")


def _unhide_content_roots(soup: BeautifulSoup) -> None:
    for sel in _CONTENT_ROOT_SELECTORS:
        for node in soup.select(sel):
            if not isinstance(node, Tag):
                continue
            style = _strip_hide_style(node.get("style") or "")
            extra = "visibility: visible !important; opacity: 1 !important;"
            node["style"] = f"{style}; {extra}".strip("; ") if style else extra


def _normalize_img(tag: Tag) -> None:
    url = _pick_url(tag, IMAGE_URL_ATTRS)
    if not url:
        return
    if _src_is_missing_or_placeholder(tag.get("src")):
        tag["src"] = url
    if not tag.get("data-gzh-original-src"):
        picked = tag.get("data-src") or tag.get("src") or url
        tag["data-gzh-original-src"] = _absolutize_url(str(picked))


def _normalize_video_like(tag: Tag) -> None:
    url = _pick_url(tag, VIDEO_URL_ATTRS)
    if not url:
        return
    if tag.name == "video":
        if _src_is_missing_or_placeholder(tag.get("src")):
            tag["src"] = url
        if not tag.get("poster"):
            poster = _pick_url(tag, ("data-poster", "poster"))
            if poster:
                tag["poster"] = poster
    elif tag.name == "iframe" and _src_is_missing_or_placeholder(tag.get("src")):
        tag["src"] = url


def _normalize_audio_like(tag: Tag) -> None:
    url = _pick_url(tag, AUDIO_URL_ATTRS)
    if not url:
        return
    if tag.name == "audio" and _src_is_missing_or_placeholder(tag.get("src")):
        tag["src"] = url
    elif tag.name == "mpvoice" and url:
        audio = soup_new_audio(tag, url)
        if audio is not None:
            tag.replace_with(audio)


def soup_new_audio(mpvoice_tag: Tag, url: str) -> Tag | None:
    audio = BeautifulSoup(
        f'<audio controls preload="none" src="{html.escape(url, quote=True)}"></audio>',
        "html.parser",
    ).audio
    if audio is None:
        return None
    name = mpvoice_tag.get("name") or mpvoice_tag.get("aria-label")
    if name:
        audio["title"] = str(name)[:200]
    return audio


def _inject_offline_css(soup: BeautifulSoup) -> None:
    css = (
        "#js_content,.rich_media_content{visibility:visible!important;opacity:1!important}"
        " .rich_media_content img,.js_content img{max-width:100%;height:auto}"
    )
    style = soup.new_tag("style", id="gzh-pipeline-offline-media")
    style.string = css
    head = soup.find("head")
    if head:
        head.append(style)
    elif soup.body:
        soup.body.insert(0, style)


def _content_roots(soup: BeautifulSoup) -> list[Tag]:
    roots: list[Tag] = []
    for sel in _CONTENT_ROOT_SELECTORS:
        roots.extend(soup.select(sel))
    if roots:
        return roots
    if soup.body:
        return [soup.body]
    return [soup]


def normalize_weixin_media_html(page_html: str) -> str:
    """
    将微信页/正文片段中的图片、音视频标签改为可离线浏览器直接加载的 ``src``。

    - 正文根节点去掉 ``visibility:hidden`` / ``opacity:0``
    - ``img``：``data-src`` 等写入 ``src``
    - ``video`` / ``iframe`` / ``audio`` / ``mpvoice``：尽量写入可播放地址
    """
    if not (page_html or "").strip():
        return page_html or ""
    if not weixin_offline_normalize_enabled():
        return page_html

    soup = BeautifulSoup(page_html, "html.parser")
    _unhide_content_roots(soup)

    for root in _content_roots(soup):
        for img in root.find_all("img"):
            _normalize_img(img)
        for tag in root.find_all(["video", "iframe", "audio", "mpvoice"]):
            if tag.name in ("video", "iframe"):
                _normalize_video_like(tag)
            else:
                _normalize_audio_like(tag)

    if soup.find("html"):
        _inject_offline_css(soup)
        return str(soup)
    return str(soup)
