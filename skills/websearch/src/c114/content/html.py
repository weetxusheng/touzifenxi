"""Step 4 HTML 清洗与正文提取。

本模块只负责把原始 HTML 解码、抽取可见文本并做站点特化清洗。
它不负责网络请求、checkpoint 或运行目录判断。
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from html.parser import HTMLParser

from ..search.workflow import extract_domain, unquote_yaml_scalar


class VisibleTextParser(HTMLParser):
    """从 HTML 中抽取可见正文文本的简易解析器。"""

    def __init__(self) -> None:
        """初始化正文提取解析器的内部状态。"""
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """遇到脚本或样式标签时进入跳过状态。"""
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """脚本或样式标签结束时退出跳过状态。"""
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        """收集非脚本区域的可见文本。"""
        if self._skip_depth:
            return
        text = compact_text(data, limit=10_000)
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        """返回清洗后的正文文本。"""
        return compact_text(" ".join(self._parts), limit=20_000)

def decode_html(payload: bytes, charset: str | None) -> str:
    """按多种常见编码顺序解码 HTML。"""
    candidates = [charset, "utf-8", "gb18030", "gbk", "latin-1"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return payload.decode(candidate)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def extract_html_title(html: str) -> str:
    """从 HTML 中提取页面标题。"""
    title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if not title_match:
        return ""
    return compact_text(title_match.group(1), limit=200)


def extract_html_summary(html: str) -> str:
    """从 HTML 的 meta 信息中提取摘要。"""
    for pattern in (
        r'<meta[^>]+name="description"[^>]+content="([^"]*)"',
        r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
    ):
        match = re.search(pattern, html, re.I)
        if match:
            return compact_text(unquote_yaml_scalar(match.group(1)), limit=400)
    return ""


def extract_content_text(url: str, html: str) -> str:
    """Route HTML extraction through site-specific logic when available."""
    domain = extract_domain(url)
    if domain == "www.c114.com.cn" or domain.endswith(".c114.com.cn"):
        specialized = extract_c114_article_text(html)
        if specialized:
            return specialized
    return extract_visible_text(html)


def extract_c114_article_text(html: str) -> str:
    """Extract正文 from C114 pages using the article container before generic fallback."""
    match = re.search(
        r'<div class="article_text">\s*<div class="text" id="text1">\s*(.*?)\s*</div>\s*</div>',
        html,
        re.I | re.S,
    )
    if not match:
        return ""
    body_html = match.group(1)
    body_html = re.sub(r"<script.*?</script>", " ", body_html, flags=re.I | re.S)
    body_html = re.sub(r"<style.*?</style>", " ", body_html, flags=re.I | re.S)
    body_html = re.sub(r"<[^>]+>", " ", body_html)
    text = compact_text(unquote_yaml_scalar(body_html), limit=12000)
    text = strip_c114_shell_sections(text)
    return compact_text(text, limit=12000)


def strip_c114_shell_sections(text: str) -> str:
    """去掉 C114 页面外壳、版权和分享等非正文片段。"""
    stop_markers = [
        "免责声明",
        "相关链接",
        "热门文章",
        "最新视频",
        "为您推荐",
        "C114简介",
        "联系我们",
        "网站地图",
        "Copyright",
        "举报电话",
        "用户注销",
    ]
    cleaned = text
    for marker in stop_markers:
        index = cleaned.find(marker)
        if index != -1:
            cleaned = cleaned[:index]
    return cleaned


def extract_visible_text(html: str) -> str:
    """使用通用可见文本解析器提取正文。"""
    cleaned = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    parser = VisibleTextParser()
    parser.feed(cleaned)
    return parser.get_text()


def normalize_compare_text(value: str) -> str:
    """归一化标题文本，便于做相似度比较。"""
    lowered = value.lower()
    return re.sub(r"[\W_]+", "", lowered)


def title_similarity(left: str, right: str) -> float:
    """计算两个标题的相似度。"""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_aliyun_published_at(value: str) -> str:
    """把阿里云返回的发布时间归一化为 YYYY-MM-DD。"""

    if not value:
        return ""
    text = value.strip()
    if len(text) >= 10:
        return text[:10]
    return text


def published_date_close(reference: str, candidate: str, max_days: int = 3) -> bool:
    """判断两个日期是否在允许误差范围内。"""

    if not reference or not candidate:
        return False
    try:
        reference_day = date.fromisoformat(reference[:10])
        candidate_day = date.fromisoformat(candidate[:10])
    except ValueError:
        return False
    return abs((reference_day - candidate_day).days) <= max_days

def compact_text(value: str, limit: int) -> str:
    """压缩空白并截断过长文本。"""
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit].strip()
