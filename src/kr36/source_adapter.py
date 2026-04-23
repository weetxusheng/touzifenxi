"""36Kr source adapter with conservative throttling and manual browser fallback."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from datetime import datetime
from datetime import timedelta
from html import unescape
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote
from urllib.parse import urljoin
from urllib.parse import urlparse

from utils.tools.content_models import ContentSourceAdapter
from utils.tools.content_models import RawArticleDetail, RawArticleRef, StandardArticle

KR36_PROJECT_ROOT = Path(__file__).resolve().parents[2]
KR36_COOKIES_FILE = KR36_PROJECT_ROOT / "config" / "kr36_cookies.json"
KR36_DEBUG_LOG_FILE = KR36_PROJECT_ROOT / "log.txt"
_kr36_debug_log_path_override: Path | None = None

# 同进程内、跨 Kr36SourceAdapter 实例与 step4 旁路，共享「每 URL 风控恢复」计数（上限见 risk_max_recovery_rounds_per_url）。
_kr36_risk_recovery_lock = threading.Lock()
_kr36_risk_recovery_rounds: dict[str, int] = {}
# 并行 0.5 与主清单时，多实例可能同时写回 kr36_cookies.json。
_kr36_cookies_file_lock = threading.Lock()


def _kr36_norm_risk_url_key(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _kr36_risk_recovery_try_begin(url: str, max_rounds: int) -> bool:
    """
    准备发起一次 36kr 风控相关恢复（无痕刷新 / step4 滑块等）。
    max_rounds<=0 表示不限制；若该 URL 已在本次进程中累计达到 max_rounds 次，则返回 False 且**不**再发起恢复。
    返回 True 时会计数 +1（本次算一次恢复尝试）。
    """

    if max_rounds <= 0:
        return True
    key = _kr36_norm_risk_url_key(url)
    with _kr36_risk_recovery_lock:
        n = _kr36_risk_recovery_rounds.get(key, 0)
        if n >= max_rounds:
            return False
        _kr36_risk_recovery_rounds[key] = n + 1
    return True


def _kr36_risk_recovery_reset(url: str) -> None:
    """该 URL 已成功拿到正常页时调用，清掉累计，后续可再恢复。"""
    key = _kr36_norm_risk_url_key(url)
    with _kr36_risk_recovery_lock:
        _kr36_risk_recovery_rounds.pop(key, None)


KR36_ROOT = "https://36kr.com"
KR36_DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
KR36_DEFAULT_CHANNEL_URLS: tuple[str, ...] = ()
# 专题和活动单独抓取，不受 candidate_limit 和日期过滤约束。
KR36_TOPICS_URL = "https://36kr.com/topics/"
KR36_ACTIVITY_URL = "https://36kr.com/activity"
# 仅首页需要相对时间过滤。
KR36_RELATIVE_TIME_REQUIRED_LISTING_PREFIXES = (
)
KR36_RELATIVE_TIME_REQUIRED_EXACT_URLS = frozenset(
    {"https://36kr.com", "https://36kr.com/"}
)
KR36_SEARCH_BASE = "https://36kr.com/search/articles/"
KR36_SEARCH_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("36氪独家", "36氪独家"),
    # ("深氪", "深氪"),
    # ("后浪白皮书", "后浪白皮书"),
    # ("行业日报", "行业日报"),
    # ("Long China 50", "Long China 50"),
    # ("投资派", "投资派"),
    # ("新青年观察", "新青年观察"),
    # ("KRLab", "KRLab"),
    # ("AI协同创新中心", "AI协同创新中心"),
    # ("数智前瞻", "数智前瞻"),
)
KR36_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>(?:/p/\d+(?:\?[^"]*)?)|(?:https?://(?:www\.)?36kr\.com/p/\d+(?:\?[^"]*)?))"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# 搜索页 CSR 渲染后，标题多在 p.title-wrapper > a（与纯 <a href=/p/> 并存）
KR36_SEARCH_TITLE_LINK_RE = re.compile(
    r'<p[^>]+class="[^"]*\btitle-wrapper\b[^"]*"[^>]*>\s*<a[^>]+href="(?P<href>(?:/p/\d+(?:\?[^"]*)?)|(?:https?://(?:www\.)?36kr\.com/p/\d+(?:\?[^"]*)?))"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
KR36_SEARCH_ARTICLE_ITEM_RE = re.compile(
    r'<li[^>]+class="[^"]*\bsearch-result-list-item-article\b[^"]*"[^>]*>(?P<content>.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)
KR36_ANCHOR_WITH_HREF_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"#]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
KR36_TOPIC_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>/topics/\d+|https?://36kr\.com/topics/\d+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
KR36_ACTIVITY_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>/activity/[^"#?]+|https?://36kr\.com/activity/[^"#?]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# 活动卡片：36Kr 活动页使用 class="activity-item" 的链接，href 指向站外地址
KR36_ACTIVITY_CARD_RE = re.compile(
    r'<a[^>]+class="[^"]*\bactivity-item\b[^"]*"[^>]*href="(?P<href>[^"#][^"]*)"[^>]*>(?P<content>.*?)</a>'
    r'|<a[^>]+href="(?P<href2>[^"#][^"]*)"[^>]*class="[^"]*\bactivity-item\b[^"]*"[^>]*>(?P<content2>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
KR36_ACTIVITY_TITLE_RE = re.compile(
    r'class="[^"]*\b(?:item-title|item-name|event-name|kr-shadow-content|activity-name)\b[^"]*"[^>]*>'
    r'\s*(?:<[^>]+>)*\s*(?P<t>[^<]{3,80})',
    re.IGNORECASE,
)
KR36_ACTIVITY_DESC_RE = re.compile(
    r'class="[^"]*\b(?:item-introduce|item-intro|event-introduce|activity-introduce)\b[^"]*"[^>]*>'
    r"\s*(?P<d>.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
KR36_TAG_RE = re.compile(r"<[^>]+>")
KR36_WHITESPACE_RE = re.compile(r"\s+")
KR36_AD_HINTS = ("广告", "推广", "赞助", "品牌合作")
KR36_ALLOWED_CATEGORIES = (
    "专题",
    "活动",
    "36氪独家",
    "深氪",
    "后浪白皮书",
    "行业日报",
    "Long China 50",
    "投资派",
    "新青年观察",
    "KRLab",
    "AI协同创新中心",
    "数智前瞻",
)
KR36_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("36氪独家", ("36氪独家", "36kr独家", "独家")),
    ("深氪", ("深氪",)),
    ("后浪白皮书", ("后浪白皮书", "后浪研究", "后浪研究所")),
    ("行业日报", ("行业日报", "日报")),
    ("Long China 50", ("long china 50", "longchina50", "long china")),
    ("投资派", ("投资派",)),
    ("新青年观察", ("新青年观察",)),
    ("KRLab", ("krlab", "krlab")),
    ("AI协同创新中心", ("ai协同创新中心", "ai协同", "协同创新中心")),
    ("数智前瞻", ("数智前瞻",)),
    ("专题", ("专题", "/topics/", "/topic/")),
)
KR36_ACTIVITY_STATUS_ORDER = {
    "未开始": 0,
    "报名中": 1,
    "活动中": 2,
    "已结束": 3,
}
KR36_ACTIVITY_STATUS_RE = re.compile(r"(未开始|报名中|活动中|已结束)")
# 与页面文案一致；活动中≈进行中场，未开始/报名中≈待开始/报名期
KR36_ACTIVITY_LISTING_KEPT_STATUSES: frozenset[str] = frozenset(
    ("未开始", "报名中", "活动中")
)
KR36_ACTIVITY_DATE_RANGE_RE = re.compile(r"(?P<start>\d{2}月\d{2}日)-(?P<end>\d{2}月\d{2}日)")
KR36_ACTIVITY_CITY_RE = re.compile(r"(北京|上海|深圳|杭州|广州|南京|苏州|成都|重庆|武汉|西安|厦门|天津|长沙|青岛)")
KR36_ACTIVITY_THEME_RE = re.compile(
    r"(?:主题|话题|专题|赛道|领域|方向)[：:]\s*([^\s<|，,。]{2,20})"
    r"|(?:关于|聚焦|围绕|探讨)\s*([^\s<|，,。]{2,20})"
)
KR36_RELATIVE_TIME_RE = re.compile(r"(刚刚|\d+\s*(分钟前|小时前|天前))")
# 搜索结果条目内的时间文案：相对时间 / YYYY-MM-DD / MM-DD / X月X日
# 搜索结果条目内的时间文案：相对时间 / YYYY-MM-DD / MM-DD / X月X日
KR36_SEARCH_ITEM_TIME_RE = re.compile(
    r"(?:"
    r"刚刚"
    r"|\d+\s*(?:分钟前|小时前|天前)"
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}月\d{1,2}日"
    r")",
    re.IGNORECASE,
)
# HTML 属性里的 datetime（ISO 8601）：datetime="2026-04-18" / datetime="2026-04-18T..."
KR36_SEARCH_ITEM_DATETIME_ATTR_RE = re.compile(
    r'datetime=["\'](\d{4}-\d{2}-\d{2})',
    re.IGNORECASE,
)
# data 属性里的 13 位 Unix 毫秒时间戳：data-time="1745000000000"
KR36_SEARCH_ITEM_TIMESTAMP_RE = re.compile(
    r"""(?:data-time|data-pub-time|data-create-time|publishedAt|pubdate)\s*=\s*["'](\d{10,13})["']""",
    re.IGNORECASE,
)

KR36_TOPIC_FOCUS_KEYWORDS: tuple[str, ...] = ("本周有大事", "36氪编辑精选")
KR36_TOPIC_SECTION_RE = re.compile(
    r'<ul[^>]+class="[^"]*\bkr-substance-(?:post|station\d*video)\b[^"]*"[^>]*>(?P<content>.*?)</ul>',
    re.IGNORECASE | re.DOTALL,
)
KR36_TOPIC_LIST_ITEM_RE = re.compile(
    r'<li[^>]+class="[^"]*\blist-item\b[^"]*"[^>]*>(?P<content>.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)
KR36_TOPIC_ITEM_DATE_RE = re.compile(
    r"(?P<date>\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}日)",
    re.IGNORECASE,
)
KR36_VIDEO_FILE_LINK_RE = re.compile(
    r"https?://[^\"'\\\s<>]+?\.(?:mp4|m3u8)(?:\?[^\"'\\\s<>]*)?",
    re.IGNORECASE,
)
KR36_VIDEO_FILE_LINK_ESCAPED_RE = re.compile(
    r"https?:\\\\/\\\\/[^\"'\\\s<>]+?\.(?:mp4|m3u8)(?:\\\\/[^\"'\\\s<>]*)?",
    re.IGNORECASE,
)
# 线上同时出现 video.36krcdn.com（单数）与 videos.36krcdn.com（复数）两种主机名
KR36_VIDEO_CDN_LINK_RE = re.compile(
    r"https?://video(?:s)?\.36krcdn\.com/[^\s\"'<>]+",
    re.IGNORECASE,
)
# video 页：不强制 src 以 http 开头，允许 // 协议相对、无引号、无 scheme 的裸域等
KR36_VIDEO_TAG_SRC_RE = re.compile(
    r"<video[^>]+?src\s*=\s*"
    r'(?:"(?P<vd>[^"]*)"|' + r"'(?P<vs>[^']*)'|"
    r"(?P<vb>[^\s>]+))",
    re.IGNORECASE,
)
KR36_BARE_CDN_HOST_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9.-]*\.)+[a-zA-Z]{2,}/).+"
)
KR36_VIDEO_ID_RE = re.compile(r"/video/(?P<id>\d+)")


def resolve_kr36_video_detail_page_url(item_url: str) -> str:
    """
    专题链路第三层：必须是 PC 视频详情页，才能拿到播放器 DOM（含 <video src>）与 initialState。
    支持 m.36kr.com/video/{id} 等入口，统一归一到 https://36kr.com/video/{id}。
    """
    video_id = _extract_kr36_video_id(item_url)
    if not video_id:
        return ""
    return f"{KR36_ROOT}/video/{video_id}"


def deduce_topic_item_kind_from_36kr_item_url(item_url: str) -> str:
    """
    与 parse_topic_detail_html 写入 metadata.topic_item_kind 的规则一致，优先看 URL 路径形态：
    含 ``/video/`` → ``video``；含 ``/p/`` → ``article``；其它返回空（由调用方用 metadata 兜底）。
    """
    u = (item_url or "").lower()
    if "/video/" in u:
        return "video"
    if "/p/" in u:
        return "article"
    return ""


def effective_topic_item_kind_for_download(item: RawArticleRef) -> str:
    """
    先按 URL 判断类型，与 topic_item_kind 同规则；仅当无法从链推断时再用 metadata。
    """
    d = deduce_topic_item_kind_from_36kr_item_url(item.url)
    if d:
        return d
    return str((item.metadata or {}).get("topic_item_kind") or "").strip().lower()


def _normalize_kr36_risk_verification_playwright_mode(raw: object) -> str:
    """内置滑块脚本的 Playwright 形态：默认有头 agent-browser；可设 headless。"""
    s = str(raw or "agent-browser").strip().lower().replace("_", "-")
    if s in ("agent-browser", "visible", "headed"):
        return "agent-browser"
    return "headless"


def _default_kr36_playwright_chromium_channel() -> str:
    """未配置时按平台用系统已安装的浏览器（Playwright `channel=msedge|chrome`）。"""
    if sys.platform == "win32":
        return "msedge"
    if sys.platform == "darwin":
        return "chrome"
    return ""


def _normalize_kr36_playwright_chromium_channel(raw: object) -> str:
    """
    空 / auto = 与「默认」一致（Windows 为 Edge，macOS 为 Chrome，其它为内置 Chromium）；
    bundled / none = 显式用 Playwright 自带 Chromium。
    """
    s = str(raw or "").strip().lower()
    if s in ("", "auto", "default", "system"):
        return _default_kr36_playwright_chromium_channel()
    if s in ("bundled", "chromium", "playwright", "none", "off"):
        return ""
    return str(raw or "").strip()


def _find_windows_chrome_executable() -> str:
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    )
    for exe in candidates:
        if os.path.exists(exe):
            return exe
    return ""


def _open_external_windows_chrome_incognito(url: str) -> bool:
    chrome_exe = _find_windows_chrome_executable()
    if not chrome_exe:
        return False
    try:
        subprocess.Popen([chrome_exe, "--incognito", url])
        return True
    except Exception:
        return False


def _kr36_slider_drag(page: object, sx: float, sy: float, drag_px: float) -> None:
    """Human-like mouse drag from (sx,sy) by drag_px pixels rightward."""
    page.mouse.move(sx, sy)
    time.sleep(random.uniform(0.40, 0.70))
    page.mouse.down()
    time.sleep(random.uniform(0.20, 0.40))
    steps = random.randint(40, 55)
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3.0 - 2.0 * t)  # smooth-step
        page.mouse.move(
            sx + drag_px * ease + random.uniform(-0.5, 0.5),
            sy + random.uniform(-0.8, 0.8),
        )
        time.sleep(random.uniform(0.018, 0.035))  # slower so the drag is visible
    time.sleep(random.uniform(0.15, 0.30))
    page.mouse.up()
    time.sleep(random.uniform(1.0, 1.8))


def _kr36_try_solve_slider_captcha(page: object) -> bool:
    """
    Detect and solve 36kr slider-puzzle captcha.
    Delegates to src.kr36.slider_captcha.solve_slider_captcha which uses
    TM_CCOEFF_NORMED + alpha-mask template matching for precise hole location.
    Returns True if a drag attempt was made.
    """
    try:
        from .slider_captcha import solve_slider_captcha
        result = solve_slider_captcha(page)
        _append_kr36_debug_log(
            f"[kr36] slider_captcha_solve_result attempted={str(result).lower()}"
        )
        return result
    except Exception as exc:
        _append_kr36_debug_log(f"[kr36] slider_captcha_solve_error err={exc}")
        print(f"[kr36] slider_captcha_solve_error: {exc}")
        return False


class Kr36SourceAdapter(ContentSourceAdapter):
    """36Kr 抓取入口，默认聚焦资讯/专题/活动页并排除快讯。"""

    source_site = "36kr"
    DEFAULT_CANDIDATE_LIMIT = 120

    def __init__(self, source_config: dict[str, object] | None = None) -> None:
        self._source_config = dict(source_config or {})
        configured_urls = self._source_config.get("channel_urls")
        if isinstance(configured_urls, list) and configured_urls:
            self.channel_urls = tuple(str(item).strip() for item in configured_urls if str(item).strip())
        else:
            self.channel_urls = KR36_DEFAULT_CHANNEL_URLS
        self.candidate_limit = _positive_int_config(
            self._source_config.get("candidate_limit"),
            default=self.DEFAULT_CANDIDATE_LIMIT,
        )
        self.browser_timeout_ms = _positive_int_config(
            self._source_config.get("browser_timeout_ms"),
            default=30000,
        )
        self.browser_wait_after_load_ms = _positive_int_config(
            self._source_config.get("browser_wait_after_load_ms"),
            default=1800,
        )
        self.human_min_pause_ms = _positive_int_config(
            self._source_config.get("human_min_pause_ms"),
            default=250,
        )
        self.human_max_pause_ms = _positive_int_config(
            self._source_config.get("human_max_pause_ms"),
            default=850,
        )
        self.human_scroll_steps = _positive_int_config(
            self._source_config.get("human_scroll_steps"),
            default=4,
        )
        self.risk_retry_count = _positive_int_config(
            self._source_config.get("risk_retry_count"),
            default=2,
        )
        self.retry_on_risk_enabled = _bool_config(
            self._source_config.get("retry_on_risk_enabled"),
            default=False,
        )
        self.risk_retry_wait_ms = _positive_int_config(
            self._source_config.get("risk_retry_wait_ms"),
            default=1200,
        )
        self.risk_verification_command = str(self._source_config.get("risk_verification_command") or "").strip()
        self.risk_incognito_cookie_refresh_enabled = _bool_config(
            self._source_config.get("risk_incognito_cookie_refresh_enabled"),
            default=False,
        )
        self.http_only_mode = _bool_config(
            self._source_config.get("http_only_mode"),
            default=True,
        )
        self.risk_verification_auto_solver = _bool_config(
            self._source_config.get("risk_verification_auto_solver"),
            default=True,
        )
        self.risk_verification_wait_ms = _positive_int_config(
            self._source_config.get("risk_verification_wait_ms"),
            default=3000,
        )
        self.risk_verification_playwright_mode = _normalize_kr36_risk_verification_playwright_mode(
            self._source_config.get("risk_verification_playwright_mode")
        )
        if "risk_verification_agent_browser_retry" in self._source_config:
            self.risk_verification_agent_browser_retry = _bool_config(
                self._source_config.get("risk_verification_agent_browser_retry"),
                default=True,
            )
        elif "risk_manual_browser_enabled" in self._source_config:
            # 旧键曾表示「可见浏览器兜底」；现改为无头失败后同一脚本的有头自动重试
            self.risk_verification_agent_browser_retry = _bool_config(
                self._source_config.get("risk_manual_browser_enabled"),
                default=True,
            )
        else:
            self.risk_verification_agent_browser_retry = True
        self.request_interval_ms = _positive_int_config(
            self._source_config.get("request_interval_ms"),
            default=22000,
        )
        self.request_interval_jitter_ms = _positive_int_config(
            self._source_config.get("request_interval_jitter_ms"),
            default=3000,
        )
        self.request_interval_min_ms = _positive_int_config(
            self._source_config.get("request_interval_min_ms"),
            default=15000,
        )
        self.request_interval_max_ms = _positive_int_config(
            self._source_config.get("request_interval_max_ms"),
            default=30000,
        )
        self.request_interval_choices_ms = _int_list_config(
            self._source_config.get("request_interval_choices_ms"),
            default=[15000, 20000, 25000],
        )
        self.same_url_cooldown_ms = _positive_int_config(
            self._source_config.get("same_url_cooldown_ms"),
            default=0,
        )
        self.info_per_category_limit = _positive_int_config(
            self._source_config.get("info_per_category_limit"),
            default=5,
        )
        self.search_listing_enabled = _bool_config(
            self._source_config.get("search_listing_enabled"),
            default=True,
        )
        self.listing_topics_only = _bool_config(
            self._source_config.get("listing_topics_only"),
            default=False,
        )
        self.listing_parallel_topic_and_main = _bool_config(
            self._source_config.get("listing_parallel_topic_and_main"),
            default=True,
        )
        self.playwright_chromium_channel = _normalize_kr36_playwright_chromium_channel(
            self._source_config.get("playwright_chromium_channel")
            or self._source_config.get("playwright_channel")
        )
        self.browser_fallback_enabled = _bool_config(
            self._source_config.get("browser_fallback_enabled"),
            default=False,
        )
        self.browser_fallback_headless = _bool_config(
            self._source_config.get("browser_fallback_headless"),
            default=False,
        )
        self.browser_verification_timeout_ms = _positive_int_config(
            self._source_config.get("browser_verification_timeout_ms"),
            default=180000,
        )
        self.browser_verification_poll_ms = _positive_int_config(
            self._source_config.get("browser_verification_poll_ms"),
            default=2000,
        )
        # 滑块/风控通过后、关闭 Playwright 前有头浏览器前再停留的毫秒；无头模式不驻留。0=仍立即关窗。默认 180s。
        self.risk_post_success_browser_dwell_ms = _positive_int_config(
            self._source_config.get("risk_post_success_browser_dwell_ms"),
            default=180000,
        )
        # 已检测到滑块/风控路径时：等待「弹窗出现 + 图 load」的最长毫秒（由 playwright_slider 内监听实现，非每页盲等）
        self.step4_captcha_aware_pre_wait_ms = _positive_int_config(
            self._source_config.get("step4_captcha_aware_pre_wait_ms"),
            default=20000,
        )
        self.step4_min_dwell_before_ok_ms = _positive_int_config(
            self._source_config.get("step4_min_dwell_before_ok_ms"),
            default=2500,
        )
        self.step4_post_heuristic_min_age_ms = _positive_int_config(
            self._source_config.get("step4_post_heuristic_min_age_ms"),
            default=4500,
        )
        self.persist_browser_cookies = _bool_config(
            self._source_config.get("persist_browser_cookies"),
            default=True,
        )
        self.search_listing_playwright_enabled = _bool_config(
            self._source_config.get("search_listing_playwright_enabled"),
            default=True,
        )
        self.search_playwright_pre_stagger_min_ms = _positive_int_config(
            self._source_config.get("search_playwright_pre_stagger_min_ms"),
            default=1500,
        )
        self.search_playwright_pre_stagger_max_ms = _positive_int_config(
            self._source_config.get("search_playwright_pre_stagger_max_ms"),
            default=4500,
        )
        self.search_playwright_max_attempts = _positive_int_config(
            self._source_config.get("search_playwright_max_attempts"),
            default=2,
        )
        self.deferred_retry_attempts = _positive_int_config(
            self._source_config.get("deferred_retry_attempts"),
            default=3,
        )
        self.deferred_retry_min_interval_ms = _positive_int_config(
            self._source_config.get("deferred_retry_min_interval_ms"),
            default=30000,
        )
        self.deferred_retry_max_interval_ms = _positive_int_config(
            self._source_config.get("deferred_retry_max_interval_ms"),
            default=60000,
        )
        self.deferred_retry_backoff_step_ms = _positive_int_config(
            self._source_config.get("deferred_retry_backoff_step_ms"),
            default=15000,
        )
        self.deferred_retry_wait_jitter_ms = _positive_int_config(
            self._source_config.get("deferred_retry_wait_jitter_ms"),
            default=5000,
        )
        self.risk_cooldown_after_block_ms = _positive_int_config(
            self._source_config.get("risk_cooldown_after_block_ms"),
            default=90000,
        )
        self.risk_cooldown_jitter_ms = _positive_int_config(
            self._source_config.get("risk_cooldown_jitter_ms"),
            default=5000,
        )
        self.risk_max_recovery_rounds_per_url = _positive_int_config(
            self._source_config.get("risk_max_recovery_rounds_per_url"),
            default=3,
        )
        self.deferred_retry_skip_blocked_ratio_percent = _positive_int_config(
            self._source_config.get("deferred_retry_skip_blocked_ratio_percent"),
            default=85,
        )
        self.deferred_retry_skip_blocked_ratio_percent = max(
            1,
            min(100, int(self.deferred_retry_skip_blocked_ratio_percent)),
        )
        self.topic_deep_fetch_enabled = _bool_config(
            self._source_config.get("topic_deep_fetch_enabled"),
            default=True,
        )
        self.topic_focus_limit = _positive_int_config(
            self._source_config.get("topic_focus_limit"),
            default=2,
        )
        self.topic_focus_keywords = tuple(
            _str_list_config(
                self._source_config.get("topic_focus_keywords"),
                default=list(KR36_TOPIC_FOCUS_KEYWORDS),
            )
        )
        # 专题二级（专题详情内视频/文）：时间窗；一级仍只按 topic_focus_keywords 选栏。
        # weekday_split=周一至周四抓上周、周五至周日抓本周；兼容仅配置 topic_previous_week_only 的旧项。
        self.topic_subitem_date_mode = _kr36_topic_subitem_date_mode_from_config(
            dict(self._source_config) if self._source_config else {}
        )
        self.topic_item_download_enabled = _bool_config(
            self._source_config.get("topic_item_download_enabled"),
            default=True,
        )
        self.topic_video_download_enabled = _bool_config(
            self._source_config.get("topic_video_download_enabled"),
            default=True,
        )
        self.topic_article_download_enabled = _bool_config(
            self._source_config.get("topic_article_download_enabled"),
            default=True,
        )
        topic_download_dir_value = str(self._source_config.get("topic_download_dir") or "").strip()
        self.topic_download_dir = Path(topic_download_dir_value) if topic_download_dir_value else Path.cwd()
        self.topic_video_curl_max_time_seconds = _positive_int_config(
            self._source_config.get("topic_video_curl_max_time_seconds"),
            default=600,
        )
        self.topic_extract_audio_enabled = _bool_config(
            self._source_config.get("topic_extract_audio_enabled"),
            default=True,
        )
        self.topic_asr_enabled = _bool_config(
            self._source_config.get("topic_asr_enabled"),
            default=True,
        )
        self.topic_asr_max_audio_mb = _positive_int_config(
            self._source_config.get("topic_asr_max_audio_mb"),
            default=95,
        )
        self.topic_asr_timeout_seconds = _positive_int_config(
            self._source_config.get("topic_asr_timeout_seconds"),
            default=300,
        )
        if self.http_only_mode:
            self.retry_on_risk_enabled = False
            self.browser_fallback_enabled = False
        self._last_request_at = 0.0
        self._last_request_by_url: dict[str, float] = {}
        self._last_risk_detected_at = 0.0
        self._risk_cooldown_until = 0.0

    def _playwright_chromium_launch_kwargs(
        self, *, headless: bool, incognito: bool = False
    ) -> dict[str, object]:
        if incognito:
            args: list[str] = [
                "--incognito",
                "--disable-blink-features=AutomationControlled",
            ]
        else:
            args = ["--disable-blink-features=AutomationControlled"]
        out: dict[str, object] = {"headless": headless, "args": args}
        ch = (self.playwright_chromium_channel or "").strip()
        if ch:
            out["channel"] = ch
        return out

    def _playwright_launch_chromium(self, playwright: object, lkw: dict[str, object]) -> object:
        try:
            return playwright.chromium.launch(**lkw)  # type: ignore[no-untyped-call,union-attr]
        except Exception as error:
            if lkw.get("channel"):
                lkw2 = {k: v for k, v in lkw.items() if k != "channel"}
                _append_kr36_debug_log(
                    f"[kr36] playwright_chromium_channel_launch_failed err={error!r} "
                    f"channel={lkw.get('channel')!r} retry=bundled_chromium"
                )
                return playwright.chromium.launch(**lkw2)  # type: ignore[no-untyped-call,union-attr]
            raise

    def _run_listing_step05_topics(
        self,
        deferred_pages: list[dict[str, str]],
        report_date: date,
        *,
        rounds_total: int,
    ) -> list[RawArticleRef]:
        """步骤 0.5：/topics/ 与专题详情（与主清单可并行时跑独立线程）。"""
        round_index = 1
        started_at = time.time()
        _append_kr36_debug_log(
            f"[kr36] step0.5_topic_round_start page={round_index} round={round_index}/{rounds_total} "
            f"stage=topics url={KR36_TOPICS_URL}"
        )
        _append_kr36_stage_log(action="进入", stage_name="专题页(0.5步)", data_count=0, total_refs=0)
        topics_html = self._fetch_text(KR36_TOPICS_URL)
        topic_items: list[RawArticleRef] = []
        topics_html = self._maybe_recover_html_with_step4(
            KR36_TOPICS_URL, topics_html, log_phase="step0.5-topics"
        )
        if _looks_like_captcha_or_block(topics_html):
            deferred_pages.append({"stage": "topics", "category": "专题", "url": KR36_TOPICS_URL})
            _append_kr36_debug_log(
                f"[kr36] page_result page={round_index} stage=topics status=deferred_blocked "
                f"url={KR36_TOPICS_URL} fetched=0 total_refs=0"
            )
        if not _looks_like_captcha_or_block(topics_html):
            topic_items = self._collect_topic_items_from_topics_html(
                topics_html,
                report_date=report_date,
                deferred_pages=deferred_pages,
            )
        _append_kr36_debug_log(
            f"[kr36] step0.5_topic_round_end page={round_index} round={round_index}/{rounds_total} stage=topics status=ok "
            f"fetched={len(topic_items)} total_topic_refs={len(topic_items)} "
            f"elapsed_ms={int((time.time() - started_at) * 1000)}"
        )
        _append_kr36_stage_log(
            action="完成", stage_name="专题页(0.5步)", data_count=len(topic_items), total_refs=len(topic_items)
        )
        return topic_items

    def _run_listing_non_topic_stages(
        self,
        deferred_pages: list[dict[str, str]],
        report_date: date,
        rounds_total: int,
        effective_channel_urls: tuple[str, ...],
    ) -> tuple[list[RawArticleRef], dict[str, int], int]:
        """步骤 1 清单主链：活动 + 搜索 + 频道（不含步骤 0.5 专题）。"""
        refs: list[RawArticleRef] = []
        round_index = 1
        if round_index < rounds_total:
            _append_kr36_debug_log(
                f"[kr36] step1_listing_next round={round_index + 1}/{rounds_total} stage=activity"
            )

        # 2) 活动页：全量抓取，不受日期和 candidate_limit 约束。
        round_index += 1
        started_at = time.time()
        _append_kr36_debug_log(
            f"[kr36] step1_main_round_start page={round_index} round={round_index}/{rounds_total} "
            f"stage=activity url={KR36_ACTIVITY_URL}"
        )
        _append_kr36_stage_log(action="进入", stage_name="活动页", data_count=0, total_refs=len(refs))
        activity_html = self._fetch_text(KR36_ACTIVITY_URL)
        activity_html = self._maybe_recover_html_with_step4(
            KR36_ACTIVITY_URL, activity_html, log_phase="step1-activity"
        )
        if _looks_like_captcha_or_block(activity_html):
            deferred_pages.append({"stage": "activity", "category": "活动", "url": KR36_ACTIVITY_URL})
            _append_kr36_debug_log(
                f"[kr36] page_result page={round_index} stage=activity status=deferred_blocked "
                f"url={KR36_ACTIVITY_URL} fetched=0 total_refs={len(refs)}"
            )
            activity_html = ""
        activity_items = parse_activity_listing_html(
            activity_html,
            base_url=KR36_ROOT,
            listing_url=KR36_ACTIVITY_URL,
            report_date=report_date,
        )
        refs.extend(activity_items)
        _append_kr36_debug_log(
            f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} stage=activity status=ok "
            f"fetched={len(activity_items)} total_refs={len(refs)} elapsed_ms={int((time.time() - started_at) * 1000)}"
        )
        _append_kr36_stage_log(action="完成", stage_name="活动页", data_count=len(activity_items), total_refs=len(refs))
        if round_index < rounds_total:
            _append_kr36_debug_log(
                f"[kr36] round_next round={round_index + 1}/{rounds_total} stage=search"
            )

        info_categories = {name for name, _ in KR36_SEARCH_CATEGORY_KEYWORDS}
        info_counts: dict[str, int] = {}
        info_total_count = 0
        # 搜索分类日期窗：上周一 → 今天，覆盖完整上周 + 本周已发内容。
        _sdw = resolve_kr36_search_category_date_window(report_date)
        effective_window: tuple[date, date] = _sdw
        if self.search_listing_enabled:
            for category, keyword in KR36_SEARCH_CATEGORY_KEYWORDS:
                round_index += 1
                search_url = f"{KR36_SEARCH_BASE}{quote(keyword)}"
                started_at = time.time()
                _append_kr36_debug_log(
                    f"[kr36] round_start page={round_index} round={round_index}/{rounds_total} stage=search "
                    f"category={category} url={search_url} "
                    f"date_window={effective_window[0].isoformat()}~{effective_window[1].isoformat()}"
                )
                _append_kr36_stage_log(action="进入", stage_name=f"搜索页({category})", data_count=0, total_refs=len(refs))
                html = self._fetch_text(search_url)
                html = self._maybe_recover_html_with_step4(
                    search_url, html, log_phase="step1-search"
                )
                if _looks_like_captcha_or_block(html):
                    deferred_pages.append({"stage": "search", "category": category, "url": search_url})
                    _append_kr36_debug_log(
                        f"[kr36] page_result page={round_index} stage=search category={category} "
                        f"status=deferred_blocked url={search_url} fetched=0 total_refs={len(refs)}"
                    )
                    html = ""
                before_count = len(refs)
                for item in parse_search_listing_html(
                    html,
                    base_url=KR36_ROOT,
                    listing_url=search_url,
                    report_date=report_date,
                    fixed_category=category,
                    date_window=effective_window,
                ):
                    refs.append(item)
                    info_counts[category] = info_counts.get(category, 0) + 1
                    info_total_count += 1
                    if info_total_count >= self.candidate_limit:
                        _append_kr36_debug_log(
                            f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} "
                            f"stage=search category={category} status=limit_reached "
                            f"fetched={len(refs) - before_count} total_refs={len(refs)} "
                            f"elapsed_ms={int((time.time() - started_at) * 1000)} limit_reached=true"
                        )
                        _append_kr36_stage_log(
                            action="完成",
                            stage_name=f"搜索页({category})",
                            data_count=len(refs) - before_count,
                            total_refs=len(refs),
                        )
                        _append_kr36_debug_log(
                            f"[kr36] listing_end report_date={report_date.isoformat()} total_refs={len(refs)} "
                            f"stopped_by=candidate_limit"
                        )
                        return refs, info_counts, info_total_count
                _append_kr36_debug_log(
                    f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} "
                    f"stage=search category={category} status=ok "
                    f"fetched={len(refs) - before_count} total_refs={len(refs)} elapsed_ms={int((time.time() - started_at) * 1000)}"
                )
                fetched_count = len(refs) - before_count
                if fetched_count <= 0 and html:
                    _append_kr36_debug_log(
                        f"[kr36] search_zero_result category={category} html_length={len(html)} "
                        f"has_list_main={str('kr-search-result-list-main' in html).lower()} "
                        f"has_search_item={str('search-result-list-item-article' in html).lower()} "
                        f"has_p_link={str('/p/' in html).lower()} "
                        f"risk_like={str(_looks_like_captcha_or_block(html)).lower()}"
                    )
                    print(
                        f"[kr36] search_zero_result category={category} html_length={len(html)} "
                        f"has_list_main={str('kr-search-result-list-main' in html).lower()} "
                        f"has_search_item={str('search-result-list-item-article' in html).lower()} "
                        f"risk_like={str(_looks_like_captcha_or_block(html)).lower()}"
                    )
                _append_kr36_stage_log(
                    action="完成",
                    stage_name=f"搜索页({category})",
                    data_count=len(refs) - before_count,
                    total_refs=len(refs),
                )
                if round_index < rounds_total:
                    _append_kr36_debug_log(
                        f"[kr36] round_next round={round_index + 1}/{rounds_total} stage=search_or_channel"
                    )
        else:
            _append_kr36_debug_log("[kr36] search_stage_skipped reason=search_listing_enabled_false")

        for channel_url in effective_channel_urls:
            round_index += 1
            started_at = time.time()
            _append_kr36_debug_log(
                f"[kr36] round_start page={round_index} round={round_index}/{rounds_total} "
                f"stage=channel url={channel_url}"
            )
            _append_kr36_stage_log(action="进入", stage_name="频道页", data_count=0, total_refs=len(refs))
            html = self._fetch_text(channel_url)
            html = self._maybe_recover_html_with_step4(
                channel_url, html, log_phase="step1-channel"
            )
            if _looks_like_captcha_or_block(html):
                deferred_pages.append({"stage": "channel", "category": "channel", "url": channel_url})
                _append_kr36_debug_log(
                    f"[kr36] page_result page={round_index} stage=channel status=deferred_blocked "
                    f"url={channel_url} fetched=0 total_refs={len(refs)}"
                )
                html = ""
            before_count = len(refs)
            for item in parse_listing_html(
                html,
                base_url=KR36_ROOT,
                listing_url=channel_url,
                report_date=report_date,
                require_relative_time=requires_relative_time_filter(channel_url),
                fixed_category=infer_kr36_info_channel_from_listing_url(channel_url),
            ):
                if item.source_bucket in info_categories:
                    if info_counts.get(item.source_bucket, 0) >= self.info_per_category_limit:
                        continue
                    info_counts[item.source_bucket] = info_counts.get(item.source_bucket, 0) + 1
                refs.append(item)
                if len(refs) >= self.candidate_limit:
                    _append_kr36_debug_log(
                        f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} "
                        f"stage=channel status=limit_reached "
                        f"url={channel_url} fetched={len(refs) - before_count} total_refs={len(refs)} "
                        f"elapsed_ms={int((time.time() - started_at) * 1000)} limit_reached=true"
                    )
                    _append_kr36_stage_log(
                        action="完成",
                        stage_name="频道页",
                        data_count=len(refs) - before_count,
                        total_refs=len(refs),
                    )
                    _append_kr36_debug_log(
                        f"[kr36] listing_end report_date={report_date.isoformat()} total_refs={len(refs)} "
                        f"stopped_by=candidate_limit"
                    )
                    return refs, info_counts, info_total_count
            _append_kr36_debug_log(
                f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} stage=channel status=ok "
                f"url={channel_url} fetched={len(refs) - before_count} total_refs={len(refs)} "
                f"elapsed_ms={int((time.time() - started_at) * 1000)}"
            )
            _append_kr36_stage_log(
                action="完成",
                stage_name="频道页",
                data_count=len(refs) - before_count,
                total_refs=len(refs),
            )
            if round_index < rounds_total:
                _append_kr36_debug_log(
                    f"[kr36] round_next round={round_index + 1}/{rounds_total} stage=channel"
                )
        return refs, info_counts, info_total_count

    def _fetch_listing_parallel(self, report_date: date) -> list[RawArticleRef]:
        excluded_channels = {KR36_TOPICS_URL.rstrip("/"), KR36_ACTIVITY_URL.rstrip("/")}
        effective_channel_urls = tuple(
            url
            for url in self.channel_urls
            if str(url or "").strip().rstrip("/") not in excluded_channels
        )
        search_rounds = len(KR36_SEARCH_CATEGORY_KEYWORDS) if self.search_listing_enabled else 0
        rounds_total = 2 + search_rounds + len(effective_channel_urls)
        _append_kr36_debug_log(
            f"[kr36] listing_begin report_date={report_date.isoformat()} rounds_total={rounds_total} "
            f"candidate_limit={self.candidate_limit} parallel=true step0.5_plus_step1"
        )
        d_topic: list[dict[str, str]] = []
        d_main: list[dict[str, str]] = []
        cfg = dict(self._source_config)
        with ThreadPoolExecutor(max_workers=2) as ex:
            ft = ex.submit(
                lambda: Kr36SourceAdapter(cfg)._run_listing_step05_topics(
                    d_topic, report_date, rounds_total=rounds_total
                )
            )
            fm = ex.submit(
                lambda: Kr36SourceAdapter(cfg)._run_listing_non_topic_stages(
                    d_main, report_date, rounds_total, effective_channel_urls
                )
            )
            topic_items = ft.result()
            main_refs, info_counts, info_total_count = fm.result()
        deferred_pages: list[dict[str, str]] = d_topic + d_main
        merged = list(topic_items)
        merged.extend(main_refs)
        n0 = len(merged)
        if n0 > self.candidate_limit:
            merged = merged[: self.candidate_limit]
            _append_kr36_debug_log(
                f"[kr36] listing_parallel_merged_trim before={n0} after={len(merged)} limit={self.candidate_limit}"
            )
        return self._deferred_listing_round(
            merged,
            deferred_pages,
            report_date=report_date,
            rounds_total=rounds_total,
            info_counts=info_counts,
            info_total_count=info_total_count,
        )

    def _deferred_listing_round(
        self,
        refs: list[RawArticleRef],
        deferred_pages: list[dict[str, str]],
        *,
        report_date: date,
        rounds_total: int,
        info_counts: dict[str, int],
        info_total_count: int,
    ) -> list[RawArticleRef]:
        info_total = int(info_total_count)
        info_categories = {name for name, _ in KR36_SEARCH_CATEGORY_KEYWORDS}
        if deferred_pages:
            blocked_ratio_percent = int((len(deferred_pages) * 100) / max(1, rounds_total))
            if blocked_ratio_percent >= self.deferred_retry_skip_blocked_ratio_percent:
                _append_kr36_debug_log(
                    f"[kr36] deferred_retry_round_skipped total={len(deferred_pages)} "
                    f"blocked_ratio_percent={blocked_ratio_percent} "
                    f"threshold_percent={self.deferred_retry_skip_blocked_ratio_percent}"
                )
                _append_kr36_debug_log(
                    f"[kr36] listing_end report_date={report_date.isoformat()} total_refs={len(refs)} "
                    "stopped_by=deferred_retry_skipped_high_block_ratio"
                )
                return refs
            retry_attempts = max(1, int(self.deferred_retry_attempts))
            _append_kr36_debug_log(
                f"[kr36] deferred_retry_round_start total={len(deferred_pages)} attempts_per_page={retry_attempts}"
            )
            retry_success_count = 0
            retry_still_blocked_count = 0
            for retry_index, task in enumerate(deferred_pages, start=1):
                stage = str(task.get("stage") or "")
                category = str(task.get("category") or "")
                retry_url = str(task.get("url") or "")
                if not retry_url:
                    continue
                retry_html = ""
                passed = False
                for attempt in range(1, retry_attempts + 1):
                    if attempt > 1:
                        base_min_ms = min(self.deferred_retry_min_interval_ms, self.deferred_retry_max_interval_ms)
                        base_max_ms = max(self.deferred_retry_min_interval_ms, self.deferred_retry_max_interval_ms)
                        base_wait_ms = random.randint(base_min_ms, base_max_ms)
                        # 第 2/3 次尝试前递增等待，避免同一链接被连续命中风控。
                        backoff_wait_ms = max(0, attempt - 2) * self.deferred_retry_backoff_step_ms
                        retry_jitter_ms = (
                            random.randint(-self.deferred_retry_wait_jitter_ms, self.deferred_retry_wait_jitter_ms)
                            if self.deferred_retry_wait_jitter_ms > 0
                            else 0
                        )
                        wait_ms = max(0, base_wait_ms + backoff_wait_ms + retry_jitter_ms)
                        _append_kr36_debug_log(
                            f"[kr36] deferred_retry_pre_attempt_wait retry_page={retry_index}/{len(deferred_pages)} "
                            f"attempt={attempt}/{retry_attempts} wait_ms={wait_ms} "
                            f"base_wait_ms={base_wait_ms} backoff_wait_ms={backoff_wait_ms} jitter_ms={retry_jitter_ms}"
                        )
                        time.sleep(wait_ms / 1000)
                    _append_kr36_debug_log(
                        f"[kr36] deferred_retry_start retry_page={retry_index}/{len(deferred_pages)} "
                        f"attempt={attempt}/{retry_attempts} stage={stage} category={category} url={retry_url}"
                    )
                    retry_html = self._fetch_text(retry_url)
                    retry_html = self._maybe_recover_html_with_step4(
                        retry_url, retry_html, log_phase="deferred-retry"
                    )
                    if _looks_like_captcha_or_block(retry_html):
                        _append_kr36_debug_log(
                            f"[kr36] deferred_retry_attempt_blocked retry_page={retry_index}/{len(deferred_pages)} "
                            f"attempt={attempt}/{retry_attempts} stage={stage} category={category} url={retry_url}"
                        )
                        continue
                    passed = True
                    break
                if not passed:
                    retry_still_blocked_count += 1
                    _append_kr36_debug_log(
                        f"[kr36] deferred_retry_result retry_page={retry_index}/{len(deferred_pages)} "
                        f"stage={stage} category={category} status=still_blocked "
                        f"attempted={retry_attempts} url={retry_url} fetched=0 total_refs={len(refs)}"
                    )
                    continue
                before_count = len(refs)
                if stage == "topics":
                    refs.extend(
                        self._collect_topic_items_from_topics_html(
                            retry_html,
                            report_date=report_date,
                            deferred_pages=None,
                        )
                    )
                elif stage == "topic_detail":
                    topic_title = str(task.get("topic_title") or "")
                    window_start, window_end = resolve_kr36_topic_subitem_date_window(
                        report_date, mode=self.topic_subitem_date_mode
                    )
                    topic_items_retry = parse_topic_detail_html(
                        retry_html,
                        base_url=KR36_ROOT,
                        listing_url=retry_url,
                        report_date=report_date,
                        topic_title=topic_title,
                        window_start=window_start,
                        window_end=window_end,
                    )
                    refs.extend(topic_items_retry)
                    self._download_topic_items(topic_items_retry, report_date=report_date)
                elif stage == "activity":
                    refs.extend(
                        parse_activity_listing_html(
                            retry_html,
                            base_url=KR36_ROOT,
                            listing_url=retry_url,
                            report_date=report_date,
                        )
                    )
                elif stage == "search":
                    retry_dw: tuple[date, date] = resolve_kr36_search_category_date_window(report_date)
                    for item in parse_search_listing_html(
                        retry_html,
                        base_url=KR36_ROOT,
                        listing_url=retry_url,
                        report_date=report_date,
                        fixed_category=category or "",
                        date_window=retry_dw,
                    ):
                        refs.append(item)
                        if category:
                            info_counts[category] = info_counts.get(category, 0) + 1
                        info_total += 1
                        if info_total >= self.candidate_limit:
                            break
                else:
                    for item in parse_listing_html(
                        retry_html,
                        base_url=KR36_ROOT,
                        listing_url=retry_url,
                        report_date=report_date,
                        require_relative_time=requires_relative_time_filter(retry_url),
                        fixed_category=infer_kr36_info_channel_from_listing_url(retry_url),
                    ):
                        if item.source_bucket in info_categories:
                            if info_counts.get(item.source_bucket, 0) >= self.info_per_category_limit:
                                continue
                            info_counts[item.source_bucket] = info_counts.get(item.source_bucket, 0) + 1
                        refs.append(item)
                        if len(refs) >= self.candidate_limit:
                            break
                _append_kr36_debug_log(
                    f"[kr36] deferred_retry_result retry_page={retry_index}/{len(deferred_pages)} "
                    f"stage={stage} category={category} status=ok url={retry_url} "
                    f"fetched={len(refs) - before_count} total_refs={len(refs)}"
                )
                retry_success_count += 1
                _append_kr36_debug_log(
                    f"[kr36] deferred_retry_done stage={stage} category={category} url={retry_url} "
                    f"fetched={len(refs) - before_count} total_refs={len(refs)}"
                )
                if len(refs) >= self.candidate_limit or info_total >= self.candidate_limit:
                    _append_kr36_debug_log(
                        f"[kr36] deferred_retry_stopped_by_limit total_refs={len(refs)} info_total_count={info_total}"
                    )
                    break
            _append_kr36_debug_log(
                f"[kr36] deferred_retry_round_end total_refs={len(refs)} "
                f"retried={len(deferred_pages)} retry_success={retry_success_count} "
                f"retry_still_blocked={retry_still_blocked_count}"
            )

        _append_kr36_debug_log(
            f"[kr36] listing_end report_date={report_date.isoformat()} total_refs={len(refs)} stopped_by=normal_complete"
        )
        return refs

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        return self._fetch_listing_sequential(report_date)

    def _fetch_listing_sequential(self, report_date: date) -> list[RawArticleRef]:
        deferred_pages: list[dict[str, str]] = []
        excluded_channels = {KR36_TOPICS_URL.rstrip("/"), KR36_ACTIVITY_URL.rstrip("/")}
        effective_channel_urls = tuple(
            url
            for url in self.channel_urls
            if str(url or "").strip().rstrip("/") not in excluded_channels
        )
        search_rounds = len(KR36_SEARCH_CATEGORY_KEYWORDS) if self.search_listing_enabled else 0
        if self.listing_topics_only:
            rounds_total = 1
        else:
            rounds_total = 2 + search_rounds + len(effective_channel_urls)
        _append_kr36_debug_log(
            f"[kr36] listing_begin report_date={report_date.isoformat()} rounds_total={rounds_total} "
            f"candidate_limit={self.candidate_limit} mode=sequential step0.5_then_step1"
        )
        topic_items = self._run_listing_step05_topics(
            deferred_pages, report_date, rounds_total=rounds_total
        )
        if self.listing_topics_only:
            _append_kr36_debug_log("[kr36] listing_topics_only=true 跳过活动/搜索/频道")
            return self._deferred_listing_round(
                list(topic_items),
                deferred_pages,
                report_date=report_date,
                rounds_total=rounds_total,
                info_counts={},
                info_total_count=0,
            )
        main_refs, info_counts, info_total_count = self._run_listing_non_topic_stages(
            deferred_pages, report_date, rounds_total, effective_channel_urls
        )
        merged = list(topic_items)
        merged.extend(main_refs)
        n0 = len(merged)
        if n0 > self.candidate_limit:
            merged = merged[: self.candidate_limit]
            _append_kr36_debug_log(
                f"[kr36] listing_sequential_merged_trim before={n0} after={len(merged)} limit={self.candidate_limit}"
            )
        return self._deferred_listing_round(
            merged,
            deferred_pages,
            report_date=report_date,
            rounds_total=rounds_total,
            info_counts=info_counts,
            info_total_count=info_total_count,
        )

    def _collect_topic_items_from_topics_html(
        self,
        topics_html: str,
        *,
        report_date: date,
        deferred_pages: list[dict[str, str]] | None,
    ) -> list[RawArticleRef]:
        """① /topics/ 列表 → ② 聚焦专题详情页解析条目；③ 视频在 _download_topic_item_asset 打开 /video/{id} 再下 CDN。
        五段语义见 ``topic_focus_step15``（Step 1.5）。"""
        from . import topic_focus_step15 as t15

        focus_topics, topic_refs = t15.step1_parse_topics_listing_and_select_focus(
            topics_html,
            report_date=report_date,
            focus_keywords=self.topic_focus_keywords,
            focus_limit=self.topic_focus_limit,
        )
        if not focus_topics:
            return topic_refs[: self.topic_focus_limit]
        if not self.topic_deep_fetch_enabled:
            return focus_topics

        window_start, window_end = t15.topic_time_window(
            report_date, mode=self.topic_subitem_date_mode
        )

        collected: list[RawArticleRef] = []
        for topic_ref in focus_topics:
            topic_html = self._fetch_text(topic_ref.url)
            topic_html = self._maybe_recover_html_with_step4(
                topic_ref.url, topic_html, log_phase="topic-detail"
            )
            # 专题详情页是 CSR 渲染：curl 只拿到 ~1.5k 的空壳（不含 36kr.com 文本），
            # _kr36_likely_36kr_csr_risk_listing_shell 无法识别它（会被"缺少域名字符串"过滤）。
            # 直接按体积判断：有内容的专题详情页必然 >> 5k bytes。
            if (
                not _looks_like_captcha_or_block(topic_html)
                and _is_usable_html(topic_html)
                and len(topic_html) < 10000
                and not self.http_only_mode
            ):
                _append_kr36_debug_log(
                    f"[kr36] topic_detail_csr_shell url={topic_ref.url} html_length={len(topic_html)} → playwright"
                )
                from .risk_step4_tool import Kr36RiskStep4Tool
                enriched = Kr36RiskStep4Tool(self).fetch(
                    topic_ref.url, log_phase="topic-detail-csr"
                )
                if enriched and not _looks_like_captcha_or_block(enriched) and len(enriched) > len(topic_html):
                    topic_html = enriched
            if _looks_like_captcha_or_block(topic_html):
                if deferred_pages is not None:
                    deferred_pages.append(
                        {
                            "stage": "topic_detail",
                            "category": "专题",
                            "url": topic_ref.url,
                            "topic_title": topic_ref.title,
                        }
                    )
                continue
            topic_items = t15.step2_parse_topic_items_from_detail_html(
                topic_html,
                report_date=report_date,
                topic_title=topic_ref.title,
                listing_url=topic_ref.url,
                window_start=window_start,
                window_end=window_end,
            )
            collected.extend(topic_items)
            self._download_topic_items(topic_items, report_date=report_date)

        if collected:
            return collected
        return focus_topics

    def _download_topic_items(self, items: list[RawArticleRef], *, report_date: date) -> None:
        if not self.topic_item_download_enabled or not items:
            return
        for item in items:
            item_kind = effective_topic_item_kind_for_download(item)
            if not item_kind:
                continue
            if item_kind == "video" and not self.topic_video_download_enabled:
                continue
            if item_kind == "article" and not self.topic_article_download_enabled:
                continue
            try:
                self._download_topic_item_asset(item, report_date=report_date)
            except Exception as error:
                _append_kr36_debug_log(
                    f"[kr36] topic_download_error url={item.url} kind={item_kind} error={error}"
                )

    def _download_topic_item_asset(self, item: RawArticleRef, *, report_date: date) -> None:
        metadata = item.metadata or {}
        item_kind = effective_topic_item_kind_for_download(item)
        topic_title = str(metadata.get("topic_title") or metadata.get("topic_url") or "topic")
        item_date = str(metadata.get("topic_item_date") or report_date.isoformat())
        if not item_kind:
            return
        raw_tag_kind = str(metadata.get("topic_item_kind") or "").strip().lower()
        if item_kind == "video" and raw_tag_kind and raw_tag_kind != "video":
            _append_kr36_debug_log(
                f"[kr36] topic_item_kind_resolved url={item.url!r} "
                f"metadata_topic_item_kind={raw_tag_kind!r} -> video (from path)"
            )
        elif item_kind == "article" and raw_tag_kind and raw_tag_kind not in ("article", ""):
            _append_kr36_debug_log(
                f"[kr36] topic_item_kind_resolved url={item.url!r} "
                f"metadata_topic_item_kind={raw_tag_kind!r} -> article (from path)"
            )

        download_root = (self.topic_download_dir / "kr36_topic_downloads").resolve()
        day_dir = (
            download_root
            / _safe_path_component(topic_title)
            / item_date
            / ("videos" if item_kind == "video" else "articles")
        )
        day_dir.mkdir(parents=True, exist_ok=True)
        basename = _safe_filename(f"{item.title}_{item.article_id}")

        # 三层：① /topics/ 列表 → ② 专题详情 → ③ https://36kr.com/video/{id} 播放页（仅在此页解析 src / initialState 并下载）
        if item_kind == "video":
            video_page_url = resolve_kr36_video_detail_page_url(item.url)
            if not video_page_url:
                _append_kr36_debug_log(f"[kr36] topic_video_skip_no_video_id listing_url={item.url!r}")
                return
        else:
            video_page_url = item.url

        if item_kind == "video":
            try:
                html = self._fetch_text(video_page_url)
            except Exception as error:
                _append_kr36_debug_log(
                    f"[kr36] topic_video_initial_fetch_exception page={video_page_url!r} err={error!r}"
                )
                html = self._kr36_topic_video_page_recover_with_playwright(
                    video_page_url, log_reason="initial_curl_exception"
                )
        else:
            html = self._fetch_text(video_page_url)
        if item_kind == "video" and _looks_like_captcha_or_block(html):
            # 视频页命中风控时，先走滑块，再回到 /video/{id} 重抓真实播放页。
            html = self._kr36_topic_video_page_recover_with_playwright(
                video_page_url, log_reason="captcha_or_block"
            )
        if item_kind == "video" and html:
            from . import topic_focus_step15 as t15

            if not t15.step5_list_video_cdn_urls_from_subpage_html(html):
                # /video/ 首屏常为 CSR 壳：curl 无 CDN 且未必带「人机验证」文案；仅判 captcha 会漏掉，
                # 导致只落 html、不跑下载与 ASR。小体积页或验证壳脚本页在开启 auto_solver 且非 http_only 时补一轮 step4。
                looks_risk_like = _looks_like_captcha_or_block(html)
                interstitial = _kr36_search_html_has_risk_interstitial(html)
                blob_len = len(html or "")
                small_shell = blob_len < 22000
                can_browser = not self.http_only_mode and self.risk_verification_auto_solver
                should_recover = can_browser and (
                    looks_risk_like or interstitial or small_shell
                )
                if should_recover:
                    reason_parts: list[str] = []
                    if looks_risk_like:
                        reason_parts.append("captcha_copy")
                    if interstitial:
                        reason_parts.append("risk_interstitial")
                    if small_shell:
                        reason_parts.append(f"small_shell_len={blob_len}")
                    _append_kr36_debug_log(
                        f"[kr36] topic_video_no_cdn_in_html page={video_page_url!r} "
                        f"try_playwright reason={','.join(reason_parts)}"
                    )
                    html = self._kr36_topic_video_page_recover_with_playwright(
                        video_page_url, log_reason="no_cdn_in_html"
                    )
                else:
                    _append_kr36_debug_log(
                        f"[kr36] topic_video_no_cdn_in_html len={blob_len} page={video_page_url!r} "
                        f"skip_playwright http_only={str(self.http_only_mode).lower()} "
                        f"auto_solver={str(self.risk_verification_auto_solver).lower()} "
                        f"looks_risk_like={str(looks_risk_like).lower()} "
                        f"interstitial={str(interstitial).lower()} small_shell={str(small_shell).lower()}"
                    )
        if not html:
            return

        if item_kind == "video":
            from . import topic_media
            from . import volc_speech as kr36_volc_speech

            cookie_str = ""
            effective_cookies = load_kr36_cookies()
            if effective_cookies:
                cookie_str = "; ".join(f"{k}={v}" for k, v in effective_cookies.items())

            from . import topic_focus_step15 as t15

            cdn_urls = list(t15.step5_list_video_cdn_urls_from_subpage_html(html))
            cdn_prev = cdn_urls[0][:120] if cdn_urls else "-"
            _append_kr36_debug_log(
                f"[kr36] topic_video_cdn_extract count={len(cdn_urls)} "
                f"html_len={len(html)} preview={cdn_prev!r} page={video_page_url!r}"
            )

            video_saved: Path | None = None
            for video_url in cdn_urls:
                ext = _guess_file_extension_from_url(video_url, default=".mp4")
                if ".m3u8" in ext.lower() or ".m3u8" in video_url.lower():
                    output_path = day_dir / f"{basename}.mp4"
                else:
                    output_path = day_dir / f"{basename}{ext}"
                ok, dl_err = t15.step5_download_cdn_url_to_path(
                    video_url,
                    output_path,
                    video_page_referer=video_page_url,
                    cookie_header=cookie_str,
                    user_agent=KR36_DEFAULT_USER_AGENT,
                    curl_max_time_seconds=self.topic_video_curl_max_time_seconds,
                )
                if ok:
                    video_saved = output_path
                    _append_kr36_debug_log(
                        f"[kr36] topic_video_download_ok cdn={video_url[:160]!r} local={video_saved}"
                    )
                    break
                _append_kr36_debug_log(
                    f"[kr36] topic_video_download_failed url={video_url} page={video_page_url} err={dl_err}"
                )

            if not video_saved:
                _append_kr36_debug_log(
                    f"[kr36] topic_video_fallback_html_only cdn_tried={len(cdn_urls)} "
                    f"page={video_page_url!r} html_len={len(html)}"
                )
                fallback_path = day_dir / f"{basename}.html"
                fallback_path.write_text(html, encoding="utf-8")
                return

            creds = kr36_volc_speech.resolve_volc_speech_credentials(config=self._source_config)
            mp3_path = day_dir / f"{basename}.asr.mp3"
            transcript_path = day_dir / f"{basename}.transcript.txt"
            if self.topic_extract_audio_enabled and topic_media.ffmpeg_executable():
                if not t15.step6a_extract_mp3_for_asr(video_saved, mp3_path):
                    _append_kr36_debug_log(f"[kr36] topic_extract_audio_failed video={video_saved}")
                elif self.topic_asr_enabled and not creds:
                    _append_kr36_debug_log(
                        f"[kr36] topic_asr_skip_no_credentials audio={mp3_path} "
                        "hint=set sources.kr36 volc_speech_api_key (or app_key+access_key) in config/runtime.local.json"
                    )
                elif self.topic_asr_enabled and creds:
                    max_bytes = max(1, self.topic_asr_max_audio_mb) * 1024 * 1024
                    if mp3_path.stat().st_size > max_bytes:
                        _append_kr36_debug_log(
                            f"[kr36] topic_asr_skip_too_large path={mp3_path} "
                            f"bytes={mp3_path.stat().st_size}"
                        )
                    else:
                        try:
                            code, nchars = t15.step6b_transcribe_mp3_to_transcript_files(
                                mp3_path,
                                transcript_path=transcript_path,
                                asr_json_path=day_dir / f"{basename}.asr.json",
                                video_path_for_json=video_saved,
                                config=self._source_config,
                                timeout_seconds=float(self.topic_asr_timeout_seconds),
                            )
                        except Exception as exc:
                            _append_kr36_debug_log(
                                f"[kr36] topic_asr_error video={video_saved} err={exc}"
                            )
                        else:
                            _append_kr36_debug_log(
                                f"[kr36] topic_asr_ok chars={nchars} transcript={transcript_path} "
                                f"code={code} role={t15.TOPIC_ITEM_FULLTEXT_ROLE_ASR}"
                            )
            elif self.topic_asr_enabled and creds:
                _append_kr36_debug_log(
                    f"[kr36] topic_asr_skip_extract_disabled_or_no_ffmpeg video={video_saved}"
                )
            elif self.topic_asr_enabled and not creds and (
                not self.topic_extract_audio_enabled or not topic_media.ffmpeg_executable()
            ):
                _append_kr36_debug_log(
                    f"[kr36] topic_asr_skip_no_credentials video={video_saved} "
                    "hint=set sources.kr36.volc_speech_api_key; need ffmpeg+extract for flash ASR"
                )
            return

        output_path = day_dir / f"{basename}.html"
        output_path.write_text(html, encoding="utf-8")

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        # step4 会重新抓原文；专题视频的全文在 topic_downloads 下 *.transcript.txt（见 topic-focus-step15.md），不必再对 /video/ 当文章 HTML 取全文。
        return RawArticleDetail(
            source_site=self.source_site,
            article_id=ref.article_id,
            title=ref.title,
            url=ref.url,
            published_at=ref.published_at,
            author="",
            channel=ref.channel,
            source_bucket=ref.source_bucket,
            tags=[],
            summary=ref.summary,
            content_text="",
            metadata=dict(ref.metadata),
        )

    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle:
        metadata = dict(raw.metadata)
        metadata.setdefault("channel_key", normalize_channel_key(raw.channel or "资讯"))
        metadata.setdefault("channel_name", raw.channel or "资讯")
        metadata.setdefault("channel_url", KR36_ROOT)
        keywords = [raw.channel] if raw.channel else []
        return StandardArticle(
            source_site=self.source_site,
            source_bucket=raw.source_bucket or raw.channel or "资讯",
            channel=raw.channel or raw.source_bucket or "资讯",
            article_id=raw.article_id,
            title=raw.title,
            url=raw.url,
            published_at=raw.published_at,
            author=raw.author,
            tags=list(raw.tags or []),
            keywords=keywords,
            summary=raw.summary,
            content_text=raw.content_text,
            metadata=metadata,
        )

    def default_source_config(self) -> dict[str, object]:
        return {
            "channel_urls": list(KR36_DEFAULT_CHANNEL_URLS),
            "candidate_limit": self.DEFAULT_CANDIDATE_LIMIT,
            "browser_timeout_ms": 30000,
            "browser_wait_after_load_ms": 1800,
            "human_min_pause_ms": 250,
            "human_max_pause_ms": 850,
            "human_scroll_steps": 4,
            "risk_retry_count": 2,
            "retry_on_risk_enabled": False,
            "risk_retry_wait_ms": 1200,
            "http_only_mode": True,
            "risk_verification_command": "",
            "risk_incognito_cookie_refresh_enabled": False,
            "risk_verification_auto_solver": False,
            "risk_verification_wait_ms": 3000,  # 进入风控后、执行 verification 命令前的等待（毫秒）
            "risk_verification_playwright_mode": "agent-browser",
            "risk_verification_agent_browser_retry": False,
            "request_interval_ms": 22000,
            "request_interval_jitter_ms": 3000,
            "request_interval_min_ms": 15000,
            "request_interval_max_ms": 30000,
            "request_interval_choices_ms": [],
            "same_url_cooldown_ms": 0,
            "info_per_category_limit": 5,
            "search_listing_enabled": True,
            "listing_topics_only": False,
            "listing_parallel_topic_and_main": False,
            "playwright_chromium_channel": "auto",
            "browser_fallback_enabled": False,
            "browser_fallback_headless": False,
            "browser_verification_timeout_ms": 180000,
            "browser_verification_poll_ms": 2000,
            "risk_post_success_browser_dwell_ms": 180000,
            "persist_browser_cookies": True,
            "search_listing_playwright_enabled": True,
            "search_playwright_pre_stagger_min_ms": 1500,
            "search_playwright_pre_stagger_max_ms": 4500,
            "search_playwright_max_attempts": 2,
            "deferred_retry_attempts": 3,
            "deferred_retry_min_interval_ms": 30000,
            "deferred_retry_max_interval_ms": 60000,
            "deferred_retry_backoff_step_ms": 15000,
            "deferred_retry_wait_jitter_ms": 5000,
            "risk_cooldown_after_block_ms": 90000,
            "risk_cooldown_jitter_ms": 5000,
            "risk_max_recovery_rounds_per_url": 3,
            "deferred_retry_skip_blocked_ratio_percent": 85,
            "topic_deep_fetch_enabled": True,
            "topic_focus_limit": 2,
            "topic_focus_keywords": list(KR36_TOPIC_FOCUS_KEYWORDS),
            "topic_subitem_date_mode": "weekday_split",
            "topic_item_download_enabled": True,
            "topic_video_download_enabled": True,
            "topic_article_download_enabled": True,
            "topic_download_dir": ".",
            "topic_video_curl_max_time_seconds": 600,
            "topic_extract_audio_enabled": True,
            "topic_asr_enabled": True,
            "topic_asr_max_audio_mb": 95,
            "topic_asr_timeout_seconds": 300,
            "volc_speech_api_key": "",
            "volc_speech_uid": "",
        }

    def _fetch_text(self, url: str) -> str:
        """
        默认仅 HTTP 抓取；
        首屏为验证码/风控时，**先**走与联调工具相同的
        ``step4_fetch_html_via_playwright_slider``（自动滑块，见 ``_maybe_recover_html_with_step4``）；
        仍失败时再执行：1) ``risk_verification_command`` 2) 无痕 Cookie 刷新；最后 curl 重试/浏览器兜底。

        ``http_only_mode=true`` 时跳过 Playwright，仅走上述无浏览器分支。
        """
        self._wait_before_next_request(url)
        _append_kr36_debug_log(f"[kr36] fetch_start url={url}")
        html = self._curl_text(url)
        if _is_usable_html(html):
            _kr36_risk_recovery_reset(url)
            fetch_method = "curl"
            if self._should_enrich_kr36_search_with_playwright(url, html):
                enriched = self._kr36_enrich_search_html_via_playwright_and_slider(url)
                if enriched:
                    html = enriched
                    fetch_method = "curl_playwright_search"
                    _append_kr36_debug_log(
                        f"[kr36] search_listing_csr_enriched url={url} html_length={len(html)}"
                    )
            elif self._should_enrich_kr36_topics_or_activity_with_playwright(url, html):
                enriched = self._kr36_enrich_topics_activity_html_via_playwright_and_slider(url)
                if enriched:
                    html = enriched
                    fetch_method = "curl_playwright_topics_activity"
                    _append_kr36_debug_log(
                        f"[kr36] topics_activity_csr_enriched url={url} html_length={len(html)}"
                    )
            _append_kr36_debug_log(
                f"[kr36] fetch_ok method={fetch_method} url={url} html_length={len(html)}"
            )
            return html
        if _looks_like_captcha_or_block(html):
            self._last_risk_detected_at = time.time()
            cooldown_jitter = (
                random.randint(-self.risk_cooldown_jitter_ms, self.risk_cooldown_jitter_ms)
                if self.risk_cooldown_jitter_ms > 0
                else 0
            )
            cooldown_ms = max(0, self.risk_cooldown_after_block_ms + cooldown_jitter)
            self._risk_cooldown_until = time.time() + (cooldown_ms / 1000)
            _append_kr36_debug_log(
                f"[kr36] risk_cooldown_set url={url} cooldown_ms={cooldown_ms} "
                f"base_ms={self.risk_cooldown_after_block_ms} jitter_ms={cooldown_jitter}"
            )
            has_cmd = self._has_risk_verification_action()
            _append_kr36_debug_log(
                f"[kr36] risk_entered method=curl url={url} html_length={len(html or '')} "
                f"has_cookie_refresh_action={str(has_cmd).lower()}"
            )
            print(
                f"[kr36] risk_detected url={url} html_length={len(html or '')} "
                f"has_cookie_refresh_action={str(has_cmd).lower()}"
            )
            # 与 run_kr36_auto_captcha_test 同路径，优先于无痕/Cookie 命令
            html = self._maybe_recover_html_with_step4(url, html, log_phase="fetch_text_captcha")
            if not _looks_like_captcha_or_block(html):
                _kr36_risk_recovery_reset(url)
                _append_kr36_debug_log(
                    f"[kr36] fetch_ok method=step4_risk_recovery url={url} html_length={len(html)}"
                )
                return html
            if has_cmd:
                wait_ms = max(0, int(self.risk_verification_wait_ms))
                print(
                    f"[kr36] 已进入风控页面：{url}\n"
                    f"[kr36] {wait_ms / 1000:.1f}s 后启动无痕 Cookie 刷新（或执行自定义命令）。"
                )
                _append_kr36_debug_log(
                    f"[kr36] risk_verification_delay_before_command wait_ms={wait_ms} url={url}"
                )
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000)
                _append_kr36_debug_log(f"[kr36] risk_verification_invoking_command url={url}")
                verify_ok = self._run_risk_verification(url)
                if verify_ok:
                    self._human_pause(self.human_min_pause_ms, self.human_max_pause_ms)
                    self._wait_before_next_request(url)
                    html = self._curl_text(url)
                    if _is_usable_html(html):
                        _kr36_risk_recovery_reset(url)
                        _append_kr36_debug_log(
                            f"[kr36] fetch_ok method=risk_cookie_refresh url={url} html_length={len(html)}"
                        )
                        return html
            else:
                _append_kr36_debug_log(f"[kr36] risk_detected method=curl url={url} no_cookie_refresh_action=1")
                if self.http_only_mode and not self.retry_on_risk_enabled:
                    print(f"[kr36] 命中风控页，已记录并将在首轮结束后重试：{url}")
                    return html
                print(
                    "[kr36] 检测到风控页面，且未配置自动刷新 Cookie（risk_verification_command / "
                    "risk_incognito_cookie_refresh_enabled）。"
                    f"请配置后重试：{url}"
                )
            if not self.retry_on_risk_enabled:
                return html
            html = self._curl_retry_on_risk(url)
            if _is_usable_html(html):
                _append_kr36_debug_log(f"[kr36] fetch_ok method=curl_retry url={url} html_length={len(html)}")
                return html
            browser_html = self._browser_assisted_fetch(url)
            if _is_usable_html(browser_html):
                _append_kr36_debug_log(
                    f"[kr36] fetch_ok method=browser_fallback url={url} html_length={len(browser_html)}"
                )
                return browser_html
        _append_kr36_debug_log(f"[kr36] fetch_failed url={url} html_length={len(html or '')}")
        return html

    def _wait_before_next_request(self, url: str) -> None:
        """限制全局频率与同 URL 冷却时间，避免高频触发风控。"""
        min_ms = min(self.request_interval_min_ms, self.request_interval_max_ms)
        max_ms = max(self.request_interval_min_ms, self.request_interval_max_ms)
        if min_ms > 0 and max_ms > 0:
            target_interval_ms = random.randint(min_ms, max_ms)
        elif self.request_interval_choices_ms:
            target_interval_ms = random.choice(self.request_interval_choices_ms)
        else:
            target_interval_ms = self.request_interval_ms
            if self.request_interval_jitter_ms > 0:
                target_interval_ms += random.randint(-self.request_interval_jitter_ms, self.request_interval_jitter_ms)
            target_interval_ms = max(1000, target_interval_ms)

        normalized_url = str(url or "").strip()
        now = time.time()
        elapsed_ms = int((now - self._last_request_at) * 1000)
        global_wait_ms = max(0, target_interval_ms - elapsed_ms)

        url_wait_ms = 0
        if normalized_url and self.same_url_cooldown_ms > 0:
            last_url_request_at = self._last_request_by_url.get(normalized_url, 0.0)
            elapsed_url_ms = int((now - last_url_request_at) * 1000)
            url_wait_ms = max(0, self.same_url_cooldown_ms - elapsed_url_ms)

        risk_wait_ms = 0
        if self._risk_cooldown_until > now:
            risk_wait_ms = int((self._risk_cooldown_until - now) * 1000)

        wait_ms = max(global_wait_ms, url_wait_ms, risk_wait_ms)
        if wait_ms > 0:
            _append_kr36_debug_log(
                f"[kr36] throttle_wait url={normalized_url or '-'} wait_ms={wait_ms} "
                f"global_wait_ms={global_wait_ms} same_url_wait_ms={url_wait_ms} risk_wait_ms={risk_wait_ms}"
            )
            time.sleep(wait_ms / 1000)

        request_at = time.time()
        self._last_request_at = request_at
        if normalized_url:
            self._last_request_by_url[normalized_url] = request_at

    def _curl_retry_on_risk(self, url: str) -> str:
        html = ""
        for attempt in range(1, max(1, self.risk_retry_count) + 1):
            _append_kr36_debug_log(
                f"[kr36] curl_retry_start url={url} attempt={attempt}/{max(1, self.risk_retry_count)}"
            )
            self._human_pause(self.risk_retry_wait_ms, self.risk_retry_wait_ms + 900)
            self._wait_before_next_request(url)
            html = self._curl_text(url)
            if _is_usable_html(html):
                _append_kr36_debug_log(
                    f"[kr36] curl_retry_ok url={url} attempt={attempt} html_length={len(html)}"
                )
                return html
        _append_kr36_debug_log(f"[kr36] curl_retry_failed url={url} html_length={len(html or '')}")
        return html

    def _has_risk_verification_action(self) -> bool:
        if str(self.risk_verification_command or "").strip():
            return True
        return bool(self.risk_incognito_cookie_refresh_enabled)

    def _should_retry_builtin_solver_agent_browser(self) -> bool:
        """保留历史接口，当前风控恢复策略不再走二次滑块重试。"""
        return False

    def _run_risk_verification(self, url: str, *, playwright_mode: str | None = None) -> bool:
        _ = playwright_mode
        max_rr = int(getattr(self, "risk_max_recovery_rounds_per_url", 3) or 3)
        if not _kr36_risk_recovery_try_begin(url, max_rr):
            _append_kr36_debug_log(
                f"[kr36] risk_verification_skipped_limit url={url} max_rounds={max_rr}"
            )
            print(
                f"[kr36] 该 URL 风控恢复已达上限（{max_rr} 次），跳过 Cookie 验证/刷新：{url}"
            )
            return False
        env = os.environ.copy()
        env["KR36_RISK_URL"] = url
        command = str(self.risk_verification_command or "").strip()
        if command:
            _append_kr36_debug_log(f"[kr36] verification_command_start url={url} command={command}")
            print(f"[kr36] 正在执行 Cookie 刷新命令：{command}")
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    check=False,
                    env=env,
                )
            except Exception as error:
                _append_kr36_debug_log(f"[kr36] verification_command_error url={url} error={error}")
                print(f"[kr36] Cookie 刷新命令异常：{error}")
                return False
        else:
            return self._run_incognito_cookie_refresh(url)
        ok = completed.returncode == 0
        _append_kr36_debug_log(
            f"[kr36] verification_command_end url={url} return_code={completed.returncode} ok={str(ok).lower()}"
        )
        if not ok:
            print(f"[kr36] Cookie 刷新命令执行失败（exit={completed.returncode}）。")
        return ok

    def _run_incognito_cookie_refresh(self, url: str) -> bool:
        if not self.risk_incognito_cookie_refresh_enabled:
            return False
        before = load_kr36_cookies()
        before_sv = before.get("s_v_web_id", "")
        before_sensors = before.get("sensorsdata2015jssdkcross", "")
        print(f"[kr36] risk_cookie_refresh before s_v_web_id={before_sv}")
        print(f"[kr36] risk_cookie_refresh before sensorsdata2015jssdkcross={before_sensors}")
        previous_persist_flag = self.persist_browser_cookies
        self.persist_browser_cookies = True
        try:
            html = self._visible_playwright_fetch_until_deadline(
                url,
                event_tag="risk_cookie_refresh",
                incognito_mode=True,
                load_existing_cookies=False,
            )
        finally:
            self.persist_browser_cookies = previous_persist_flag

        after = load_kr36_cookies()
        after_sv = after.get("s_v_web_id", "")
        after_sensors = after.get("sensorsdata2015jssdkcross", "")
        changed = after != before
        key_changed = (before_sv != after_sv) or (before_sensors != after_sensors)
        has_required_fields = bool(after_sv and after_sensors)
        html_usable = _is_usable_html(html)
        ok = bool(changed or key_changed or has_required_fields or html_usable)
        _append_kr36_debug_log(
            f"[kr36] risk_cookie_refresh_end url={url} ok={str(ok).lower()} "
            f"changed={str(changed).lower()} key_changed={str(key_changed).lower()} "
            f"has_required_fields={str(has_required_fields).lower()} html_usable={str(html_usable).lower()}"
        )
        print(
            f"[kr36] risk_cookie_refresh ok={str(ok).lower()} changed={str(changed).lower()} "
            f"key_changed={str(key_changed).lower()} has_required_fields={str(has_required_fields).lower()}"
        )
        print(f"[kr36] risk_cookie_refresh after s_v_web_id={after_sv}")
        print(f"[kr36] risk_cookie_refresh after sensorsdata2015jssdkcross={after_sensors}")
        return ok

    def _step4_playwright_allows_return_html(
        self, html: str, *, age_s: float, after_slider_attempt: bool
    ) -> bool:
        """
        关窗前约束：防「首屏壳子 + 宽松启发」在验证/滑块未出现时秒关。

        - after_slider_attempt: 本页已跑过 _kr36_try_solve_slider_captcha 且返回 True
         （内部已再等 load/多段 wait），可放宽，只要当前 HTML 仍像正页即关。
        - 未跑滑块：须满 ``step4_min_dwell_before_ok_ms``；若仅靠
          ``_kr36_step4_post_slider_page_looks_resolved`` 而 ``_is_usable_html`` 为假，
          须再满 ``step4_post_heuristic_min_age_ms``。
        """
        strict = _is_usable_html(html)
        post = _kr36_step4_post_slider_page_looks_resolved(html)
        if not strict and not post:
            return False
        # 未拖滑块：2k~15k 的 36kr CSR 首屏在验证码进 DOM 前 _is_usable_html 会为真，禁止关窗
        if (
            not after_slider_attempt
            and strict
            and (not post)
            and _kr36_likely_36kr_csr_risk_listing_shell(html)
        ):
            return False
        if after_slider_attempt:
            # 滑块解决后，仅凭 <html> 标签（strict）不足以判断页面已就绪——
            # 验证通过后有一段跳转/重渲染过程，CSR 首屏（<5k）仍可通过 strict。
            # 必须额外满足 post（页面实质内容已渲染，len >= 4000 且含 36kr.com）。
            if post:
                return True
            # strict=True 但 post=False：页面尺寸可用但内容未就绪，继续等待
            return False
        if age_s * 1000.0 < float(self.step4_min_dwell_before_ok_ms):
            return False
        if post and not strict and age_s * 1000.0 < float(
            self.step4_post_heuristic_min_age_ms
        ):
            return False
        return True

    def _playwright_dwell_before_close(
        self, page: object, *, headless: bool, log_tag: str = ""
    ) -> None:
        """
        有头/可见浏览器：滑块或页面可用即将关窗时，再停留若干秒，避免「刚过风控就关窗」无法确认。
        无头不等待；dwell_ms=0 不等待（恢复旧行为）。
        """
        ms = max(0, int(self.risk_post_success_browser_dwell_ms))
        if ms <= 0 or headless:
            return
        tag = f"{log_tag} " if log_tag else ""
        _append_kr36_debug_log(f"[kr36] {tag}risk_post_success_dwell start_ms={ms}")
        print(
            f"[kr36] 验证或当前页已就绪，浏览器将再停留约 {ms / 1000:.0f} 秒后自动关闭；"
            f"需要可提前关窗（若已开 persist，Cookie 应已写回）。"
        )
        try:
            page.wait_for_timeout(ms)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            _append_kr36_debug_log(f"[kr36] {tag}risk_post_success_dwell_error err={exc!r}")

    def step4_fetch_html_via_playwright_slider(
        self, url: str, *, log_phase: str = "step4"
    ) -> str:
        """遇 36kr 风控/滑块时，用浏览器打开并自动滑块，写回 cookie 后返回 HTML。实现见 `risk_step4_tool` / `playwright_slider_session`。"""

        from .risk_step4_tool import Kr36RiskStep4Tool

        return Kr36RiskStep4Tool(self).fetch(url, log_phase=log_phase)

    def _maybe_recover_html_with_step4(
        self, url: str, html: str, *, log_phase: str
    ) -> str:
        """
        首屏为验证码/风控文案时，与 ``scripts/kr36/run_kr36_auto_captcha_test`` 同一路径：
        ``Kr36RiskStep4Tool``（内部 ``playwright_slider_session`` + 自动滑块），并写回 cookie。

        ``http_only_mode`` 为真时不启动 Playwright，原样返回 ``html``。
        """
        from .risk_step4_tool import Kr36RiskStep4Tool

        return Kr36RiskStep4Tool(self).maybe_recover(url, html, log_phase=log_phase)

    def _kr36_topic_video_page_recover_with_playwright(self, video_page_url: str, *, log_reason: str) -> str:
        """视频专题：Playwright+自动滑块后，优先用 curl 再拉（cookie 已落盘/刷新）。"""
        from .risk_step4_tool import Kr36RiskStep4Tool

        _append_kr36_debug_log(
            f"[kr36] topic_video_playwright_recover reason={log_reason} page={video_page_url!r}"
        )
        try:
            solved_html = Kr36RiskStep4Tool(self).fetch(
                video_page_url, log_phase="step4-video"
            )
        except Exception as error:  # 防御：与 step4 内部日志互补
            _append_kr36_debug_log(
                f"[kr36] topic_video_playwright_slider_exception page={video_page_url!r} err={error!r}"
            )
            return ""
        retried_html = ""
        try:
            retried_html = self._fetch_text(video_page_url)
        except Exception as retry_error:
            _append_kr36_debug_log(
                f"[kr36] topic_video_post_playwright_curl_exception page={video_page_url!r} err={retry_error!r}"
            )
        if not _looks_like_captcha_or_block(retried_html) and retried_html:
            return retried_html
        return solved_html

    def _visible_playwright_fetch_until_deadline(
        self,
        url: str,
        *,
        event_tag: str,
        incognito_mode: bool = False,
        load_existing_cookies: bool = True,
    ) -> str:
        """本机可见浏览器：轮询直到 HTML 可用或超时；按配置写回 cookies。"""

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            _append_kr36_debug_log(
                f"[kr36] {event_tag}_unavailable missing_playwright "
                "hint='python -m playwright install msedge' 或 install chrome，或设 playwright_chromium_channel=bundled + install chromium"
            )
            return ""

        html = ""
        try:
            with sync_playwright() as playwright:
                launch_kwargs = self._playwright_chromium_launch_kwargs(
                    headless=False, incognito=incognito_mode
                )
                try:
                    browser = self._playwright_launch_chromium(playwright, launch_kwargs)
                except Exception as launch_error:
                    if not incognito_mode:
                        raise
                    opened = _open_external_windows_chrome_incognito(url)
                    _append_kr36_debug_log(
                        f"[kr36] {event_tag}_incognito_launch_failed url={url} error={launch_error} "
                        f"external_incognito_opened={str(opened).lower()}"
                    )
                    if opened:
                        print(f"[kr36] 已兜底打开外部 Chrome 无痕窗口，请手动完成验证：{url}")
                    return ""

                context = browser.new_context(
                    locale="zh-CN",
                    user_agent=KR36_DEFAULT_USER_AGENT,
                    viewport={"width": 1440, "height": 1024},
                )
                if load_existing_cookies:
                    _load_kr36_cookies_into_browser_context(context)
                if incognito_mode:
                    print(f"[kr36] incognito_browser_opened event={event_tag} url={url}")
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout_ms)
                page.wait_for_timeout(self.browser_wait_after_load_ms)

                deadline = time.time() + (self.browser_verification_timeout_ms / 1000)
                while time.time() < deadline:
                    try:
                        html = page.content()
                    except Exception:
                        html = ""
                    if _is_usable_html(html) and not _kr36_likely_36kr_csr_risk_listing_shell(
                        html
                    ):
                        page.wait_for_timeout(self.browser_wait_after_load_ms)
                        try:
                            html = page.content()
                        except Exception:
                            pass
                        if self.persist_browser_cookies:
                            save_kr36_cookies(extract_kr36_cookie_values(context.cookies()))
                        _append_kr36_debug_log(
                            f"[kr36] {event_tag}_ok url={url} html_length={len(html)}"
                        )
                        context.close()
                        browser.close()
                        return html
                    # 检测到拼图/滑块验证码时自动尝试拖拽
                    if _looks_like_captcha_or_block(html):
                        solved = _kr36_try_solve_slider_captcha(page)
                        _append_kr36_debug_log(
                            f"[kr36] {event_tag}_slider_solve_attempt url={url} attempted={str(solved).lower()}"
                        )
                        if solved:
                            # 拖拽已发出，等 36kr 服务端校验后页面跳转完成
                            try:
                                page.wait_for_load_state(
                                    "domcontentloaded", timeout=10000
                                )
                            except Exception:
                                pass
                            page.wait_for_timeout(3000)
                            try:
                                html = page.content()
                            except Exception:
                                html = ""

                            # 如果仍在验证页（滑块通过但页面未跳转），导航到首页
                            # 让 Sensors Analytics JS 初始化并写入 sensorsdata2015jssdkcross
                            if not _is_usable_html(html):
                                _append_kr36_debug_log(
                                    f"[kr36] {event_tag}_slider_post_navigate url={url} reason=html_not_usable"
                                )
                                print(
                                    f"[kr36] 滑块已拖拽，但页面仍为验证页；"
                                    f"正在跳转首页以完成 cookie 初始化…"
                                )
                                try:
                                    page.goto(
                                        "https://36kr.com/",
                                        wait_until="domcontentloaded",
                                        timeout=20000,
                                    )
                                    # 等待 Sensors Analytics SDK 写入 sensorsdata2015jssdkcross
                                    page.wait_for_timeout(5000)
                                    html = page.content()
                                except Exception as _nav_err:
                                    _append_kr36_debug_log(
                                        f"[kr36] {event_tag}_slider_post_navigate_error url={url} err={_nav_err}"
                                    )

                            if self.persist_browser_cookies:
                                new_cookies = extract_kr36_cookie_values(
                                    context.cookies()
                                )
                                save_kr36_cookies(new_cookies)
                                _append_kr36_debug_log(
                                    f"[kr36] {event_tag}_cookies_saved_after_slider url={url} "
                                    f"has_sv={bool(new_cookies.get('s_v_web_id'))} "
                                    f"has_sensors={bool(new_cookies.get('sensorsdata2015jssdkcross'))}"
                                )
                                print(
                                    f"[kr36] cookie 已保存（验证码通过后）"
                                    f" 共 {len(new_cookies)} 项："
                                )
                                for k, v in new_cookies.items():
                                    print(
                                        f"[kr36]   {k} = "
                                        f"{v[:80]}{'...' if len(v) > 80 else ''}"
                                    )
                            _append_kr36_debug_log(
                                f"[kr36] {event_tag}_slider_done url={url} "
                                f"html_usable={str(_is_usable_html(html)).lower()}"
                            )
                            print(f"[kr36] 验证通过，浏览器立即关闭 event={event_tag}")
                            context.close()
                            browser.close()
                            return html
                    page.wait_for_timeout(self.browser_verification_poll_ms)

                html = page.content()
                if self.persist_browser_cookies:
                    save_kr36_cookies(extract_kr36_cookie_values(context.cookies()))
                _append_kr36_debug_log(
                    f"[kr36] {event_tag}_timeout url={url} html_length={len(html)}"
                )
                context.close()
                browser.close()
                return html
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            _append_kr36_debug_log(f"[kr36] {event_tag}_error url={url} error={error}")
        except Exception as error:
            _append_kr36_debug_log(f"[kr36] {event_tag}_unexpected_error url={url} error={error}")
        return html

    def _browser_assisted_fetch(self, url: str) -> str:
        """Open a visible browser so the user can clear a challenge manually."""

        if not self.browser_fallback_enabled:
            _append_kr36_debug_log(f"[kr36] browser_fallback_disabled url={url}")
            return ""
        if self.browser_fallback_headless:
            _append_kr36_debug_log(f"[kr36] browser_fallback_skipped_headless url={url}")
            return ""

        timeout_seconds = max(1, self.browser_verification_timeout_ms // 1000)
        print(
            f"[kr36] 检测到风控或验证码页面，正在打开浏览器。"
            f"请在 {timeout_seconds} 秒内手动完成验证，然后等待页面恢复。"
        )
        _append_kr36_debug_log(
            f"[kr36] browser_fallback_start url={url} timeout_ms={self.browser_verification_timeout_ms}"
        )
        return self._visible_playwright_fetch_until_deadline(url, event_tag="browser_fallback")

    def _should_enrich_kr36_search_with_playwright(self, url: str, html: str) -> bool:
        """search/articles 首屏常为 CSR 壳，curl 无 /p/ 链接时需无头渲染后再解析。"""
        if not self.search_listing_playwright_enabled:
            return False
        if not _is_kr36_search_articles_url(url):
            return False
        if _looks_like_captcha_or_block(html):
            return False
        if _kr36_html_contains_search_article_links(html):
            return False
        return True

    def _should_enrich_kr36_topics_or_activity_with_playwright(self, url: str, html: str) -> bool:
        """/topics/、/activity 列表页同搜索页，curl 常只有 ~2k CSR 壳，无头渲染后才有可解析链接。"""
        if not self.search_listing_playwright_enabled:
            return False
        if not (
            _kr36_topics_listing_index_url(url)
            or _kr36_activity_listing_index_url(url)
        ):
            return False
        if _looks_like_captcha_or_block(html):
            return False
        blob = html or ""
        if _kr36_topics_listing_index_url(url) and _kr36_listing_html_has_parsable_topic_links(
            blob
        ):
            return False
        if _kr36_activity_listing_index_url(url) and _kr36_listing_html_has_parsable_activity_items(
            blob
        ):
            return False
        return True

    def _playwright_kr36_search_listing_html(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _append_kr36_debug_log("[kr36] search_playwright_skip reason=missing_playwright")
            return ""
        attempts = max(1, int(self.search_playwright_max_attempts))
        lo = min(self.search_playwright_pre_stagger_min_ms, self.search_playwright_pre_stagger_max_ms)
        hi = max(self.search_playwright_pre_stagger_min_ms, self.search_playwright_pre_stagger_max_ms)
        timeout_ms = max(35000, int(self.browser_timeout_ms))
        _append_kr36_debug_log(
            f"[kr36] search_playwright_start url={url} timeout_ms={timeout_ms} max_attempts={attempts}"
        )
        best = ""
        for attempt in range(1, attempts + 1):
            if attempt == 1 and hi > 0:
                time.sleep(random.randint(max(0, lo), hi) / 1000)
            elif attempt > 1:
                self._human_pause(4500, 9500)
            chunk = self._playwright_kr36_search_listing_html_once(url, timeout_ms=timeout_ms)
            if chunk:
                best = chunk
            if chunk and _kr36_search_playwright_body_acceptable(chunk):
                if _kr36_html_contains_search_article_links(chunk):
                    _append_kr36_debug_log(
                        f"[kr36] search_playwright_ok attempt={attempt} url={url} html_length={len(chunk)}"
                    )
                elif _kr36_search_html_likely_zero_hits_message(chunk):
                    _append_kr36_debug_log(
                        f"[kr36] search_playwright_likely_zero_hits attempt={attempt} "
                        f"url={url} html_length={len(chunk)}"
                    )
                else:
                    _append_kr36_debug_log(
                        f"[kr36] search_playwright_acceptable_no_links attempt={attempt} "
                        f"url={url} html_length={len(chunk)}"
                    )
                return chunk
            _append_kr36_debug_log(
                f"[kr36] search_playwright_suspicious_body attempt={attempt}/{attempts} "
                f"url={url} html_length={len(chunk or '')} hint=risk_or_transient"
            )
        return best

    def _kr36_enrich_search_html_via_playwright_and_slider(self, url: str) -> str:
        """
        搜索页用 Playwright 拉列表，与 ``risk_verification_playwright_mode`` 一致（有头/无头）。

        当 ``risk_verification_auto_solver=True``（且非 ``http_only_mode``）时，跳过轻量拉页，
        **直接**走与联调脚本（``run_kr36_auto_captcha_test``）相同的
        ``Kr36RiskStep4Tool.fetch``（完整滑块会话），保证行为完全统一。
        """
        if not self.http_only_mode and self.risk_verification_auto_solver:
            from .risk_step4_tool import Kr36RiskStep4Tool

            _append_kr36_debug_log(
                f"[kr36] search_enrich_direct_step4 reason=auto_solver url={url}"
            )
            print("[kr36] 搜索列表：auto_solver=true，直接走 step4（与联调脚本相同路径）")
            return Kr36RiskStep4Tool(self).fetch(url, log_phase="search-listing-step4")

        enriched = self._playwright_kr36_search_listing_html(url)
        if enriched and _kr36_search_playwright_body_acceptable(enriched):
            return enriched
        step4_html = self._kr36_try_step4_after_search_listing_failed(
            url, enriched or ""
        )
        if step4_html:
            return step4_html
        if not self._has_risk_verification_action():
            _append_kr36_debug_log(
                f"[kr36] search_enrich_unacceptable_no_cookie_refresh url={url} "
                f"html_length={len(enriched or '')}"
            )
            return ""
        _append_kr36_debug_log(f"[kr36] search_cookie_refresh_start url={url}")
        print(
            "[kr36] 搜索 Playwright 未拿到可用列表（可能为风控），"
            f"{self.risk_verification_wait_ms / 1000:.1f}s 后执行 Cookie 刷新并再试同一浏览器路径…"
        )
        wait_ms = max(0, int(self.risk_verification_wait_ms))
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        verify_ok = self._run_risk_verification(url)
        enriched2 = ""
        if verify_ok:
            self._human_pause(self.human_min_pause_ms, self.human_max_pause_ms)
            self._wait_before_next_request(url)
            enriched2 = self._playwright_kr36_search_listing_html(url)
            if enriched2 and _kr36_search_playwright_body_acceptable(enriched2):
                _append_kr36_debug_log(
                    f"[kr36] search_cookie_refresh_ok url={url} html_length={len(enriched2)}"
                )
                return enriched2
        else:
            _append_kr36_debug_log(f"[kr36] search_cookie_refresh_failed url={url}")
        _append_kr36_debug_log(
            f"[kr36] search_cookie_refresh_still_bad url={url} html_length={len(enriched2 or '')}"
        )
        if not (enriched2 and _kr36_search_playwright_body_acceptable(enriched2)):
            late4s = self._kr36_try_step4_after_search_listing_failed(
                url, enriched2 or ""
            )
            if late4s:
                return late4s
        return enriched2

    def _playwright_kr36_search_listing_html_once(self, url: str, *, timeout_ms: int) -> str:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        try:
            with sync_playwright() as playwright:
                listing_headless = self.risk_verification_playwright_mode == "headless"
                lkw = self._playwright_chromium_launch_kwargs(
                    headless=listing_headless, incognito=False
                )
                browser = self._playwright_launch_chromium(playwright, lkw)
                context = browser.new_context(
                    locale="zh-CN",
                    user_agent=KR36_DEFAULT_USER_AGENT,
                    viewport={"width": 1440, "height": 1024},
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                )
                _load_kr36_cookies_into_browser_context(context)
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until="load", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(500)
                main_ok = False
                try:
                    page.wait_for_selector("ul.kr-search-result-list-main", timeout=22000)
                    main_ok = True
                except PlaywrightTimeoutError:
                    _append_kr36_debug_log(
                        f"[kr36] search_playwright_selector_timeout url={url} "
                        "selector=ul.kr-search-result-list-main"
                    )
                page.wait_for_timeout(2200)
                if main_ok:
                    try:
                        page.wait_for_selector("li.search-result-list-item-article", timeout=14000)
                    except PlaywrightTimeoutError:
                        _append_kr36_debug_log(
                            f"[kr36] search_playwright_no_article_items url={url} "
                            "hint=possible_empty_or_slow"
                        )
                else:
                    try:
                        page.wait_for_selector("li.search-result-list-item-article", timeout=16000)
                    except PlaywrightTimeoutError:
                        _append_kr36_debug_log(
                            f"[kr36] search_playwright_selector_timeout url={url} "
                            "selector=li.search-result-list-item-article"
                        )
                try:
                    page.mouse.wheel(0, 600)
                except Exception:
                    pass
                page.wait_for_timeout(800)
                page.wait_for_timeout(self.browser_wait_after_load_ms)
                html = page.content()
                if self.persist_browser_cookies:
                    save_kr36_cookies(extract_kr36_cookie_values(context.cookies()))
                context.close()
                browser.close()
                _append_kr36_debug_log(f"[kr36] search_playwright_once_end url={url} html_length={len(html)}")
                return html
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            _append_kr36_debug_log(f"[kr36] search_playwright_error url={url} error={error}")
        except Exception as error:
            _append_kr36_debug_log(f"[kr36] search_playwright_unexpected url={url} error={error}")
        return ""

    def _kr36_try_step4_after_topics_activity_listing_failed(
        self, url: str, page_kind: str, listing_html: str
    ) -> str:
        """
        轻量 ``_playwright_kr36_topics_activity_listing_html_once`` 无滑块循环，遇风控/验证码时往往仍拿不到列表体。
        在 ``http_only_mode`` 为 false 时，于此前提下改走与 ``_maybe_recover_html_with_step4``/正文相同的
        ``Kr36RiskStep4Tool``（全量自动滑块会话），避免活动/专题只「等无痕」而不触发滑块。

        触发：HTML 已像验证码/风控、或含字节系风险壳、或 ``risk_verification_auto_solver`` 为 true
        （任意轻量拉页失败也尝试 step4，用于小壳/难判页面）。
        """
        if self.http_only_mode:
            return ""
        blob = listing_html or ""
        try_step4 = _looks_like_captcha_or_block(blob)
        if not try_step4 and blob:
            try_step4 = _kr36_search_html_has_risk_interstitial(blob)
        if not try_step4 and self.risk_verification_auto_solver:
            try_step4 = True
        if not try_step4:
            return ""
        from .risk_step4_tool import Kr36RiskStep4Tool

        _append_kr36_debug_log(
            f"[kr36] topics_activity_step4_after_listing url={url} page_kind={page_kind} "
            f"auto_solver={str(self.risk_verification_auto_solver).lower()}"
        )
        print(
            "[kr36] 专题/活动轻量拉页未拿到可解析列表；"
            "改走与正文相同的 step4（Playwright+自动滑块）…"
        )
        try:
            step4_html = Kr36RiskStep4Tool(self).fetch(
                url, log_phase="topics-activity-step4"
            )
        except Exception as error:  # noqa: BLE001
            _append_kr36_debug_log(
                f"[kr36] topics_activity_step4_fetch_exception url={url} err={error!r}"
            )
            return ""
        if step4_html and _kr36_topics_activity_playwright_body_acceptable(
            step4_html, page_kind=page_kind
        ):
            _append_kr36_debug_log(
                f"[kr36] topics_activity_step4_ok url={url} html_length={len(step4_html)}"
            )
            return step4_html
        if step4_html and (not _looks_like_captcha_or_block(step4_html)) and len(step4_html) >= 15000:
            return step4_html
        return ""

    def _kr36_try_step4_after_search_listing_failed(
        self, url: str, listing_html: str
    ) -> str:
        """
        搜索页轻量 Playwright 同专题/活动：无全量滑块；失败时接 ``Kr36RiskStep4Tool`` 与逻辑见
        ``_kr36_try_step4_after_topics_activity_listing_failed``。
        """
        if self.http_only_mode:
            return ""
        blob = listing_html or ""
        try_step4 = _looks_like_captcha_or_block(blob)
        if not try_step4 and blob:
            try_step4 = _kr36_search_html_has_risk_interstitial(blob)
        if not try_step4 and self.risk_verification_auto_solver:
            try_step4 = True
        if not try_step4:
            return ""
        from .risk_step4_tool import Kr36RiskStep4Tool

        _append_kr36_debug_log(
            f"[kr36] search_step4_after_listing url={url} "
            f"auto_solver={str(self.risk_verification_auto_solver).lower()}"
        )
        print(
            "[kr36] 搜索轻量拉页未拿到可用列表；"
            "改走与正文相同的 step4（Playwright+自动滑块）…"
        )
        try:
            step4_html = Kr36RiskStep4Tool(self).fetch(
                url, log_phase="search-listing-step4"
            )
        except Exception as error:  # noqa: BLE001
            _append_kr36_debug_log(
                f"[kr36] search_step4_fetch_exception url={url} err={error!r}"
            )
            return ""
        if step4_html and _kr36_search_playwright_body_acceptable(step4_html):
            _append_kr36_debug_log(
                f"[kr36] search_step4_ok url={url} html_length={len(step4_html)}"
            )
            return step4_html
        if step4_html and (not _looks_like_captcha_or_block(step4_html)) and len(step4_html) >= 20000:
            return step4_html
        return ""

    def _kr36_enrich_topics_activity_html_via_playwright_and_slider(self, url: str) -> str:
        """
        专题/活动列表：Playwright 拉列表（有头/无头同 ``risk_verification_playwright_mode``）+ 可选 Cookie 刷新。

        当 ``risk_verification_auto_solver=True``（且非 ``http_only_mode``）时，跳过轻量拉页，
        **直接**走与联调脚本（``run_kr36_auto_captcha_test``）相同的
        ``Kr36RiskStep4Tool.fetch``（完整滑块会话），保证行为完全统一。
        """
        page_kind = _kr36_topics_activity_page_kind(url)
        if not page_kind:
            return ""
        if not self.http_only_mode and self.risk_verification_auto_solver:
            from .risk_step4_tool import Kr36RiskStep4Tool

            _append_kr36_debug_log(
                f"[kr36] topics_activity_enrich_direct_step4 reason=auto_solver url={url} page_kind={page_kind}"
            )
            print(
                f"[kr36] 专题/活动列表（{page_kind}）：auto_solver=true，"
                "直接走 step4（与联调脚本相同路径）"
            )
            return Kr36RiskStep4Tool(self).fetch(url, log_phase=f"topics-activity-step4-{page_kind}")

        enriched = self._playwright_kr36_topics_activity_listing_html(url, page_kind=page_kind)
        if enriched and _kr36_topics_activity_playwright_body_acceptable(enriched, page_kind=page_kind):
            return enriched
        step4_html = self._kr36_try_step4_after_topics_activity_listing_failed(
            url, page_kind, enriched or ""
        )
        if step4_html:
            return step4_html
        if not self._has_risk_verification_action():
            _append_kr36_debug_log(
                f"[kr36] topics_activity_enrich_unacceptable_no_cookie_refresh url={url} "
                f"html_length={len(enriched or '')}"
            )
            return ""
        _append_kr36_debug_log(f"[kr36] topics_activity_cookie_refresh_start url={url}")
        print(
            "[kr36] 专题/活动列表 Playwright 未拿到可用内容（可能为风控），"
            f"{self.risk_verification_wait_ms / 1000:.1f}s 后执行 Cookie 刷新并再试同一浏览器路径…"
        )
        wait_ms = max(0, int(self.risk_verification_wait_ms))
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        verify_ok = self._run_risk_verification(url)
        enriched2 = ""
        if verify_ok:
            self._human_pause(self.human_min_pause_ms, self.human_max_pause_ms)
            self._wait_before_next_request(url)
            enriched2 = self._playwright_kr36_topics_activity_listing_html(url, page_kind=page_kind)
            if enriched2 and _kr36_topics_activity_playwright_body_acceptable(
                enriched2, page_kind=page_kind
            ):
                _append_kr36_debug_log(
                    f"[kr36] topics_activity_cookie_refresh_ok url={url} html_length={len(enriched2)}"
                )
                return enriched2
        else:
            _append_kr36_debug_log(f"[kr36] topics_activity_cookie_refresh_failed url={url}")
        _append_kr36_debug_log(
            f"[kr36] topics_activity_cookie_refresh_still_bad url={url} html_length={len(enriched2 or '')}"
        )
        if not (
            enriched2
            and _kr36_topics_activity_playwright_body_acceptable(enriched2, page_kind=page_kind)
        ):
            late4 = self._kr36_try_step4_after_topics_activity_listing_failed(
                url, page_kind, enriched2 or ""
            )
            if late4:
                return late4
        return enriched2

    def _playwright_kr36_topics_activity_listing_html(self, url: str, *, page_kind: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _append_kr36_debug_log(
                "[kr36] topics_activity_playwright_skip reason=missing_playwright"
            )
            return ""
        attempts = max(1, int(self.search_playwright_max_attempts))
        lo = min(self.search_playwright_pre_stagger_min_ms, self.search_playwright_pre_stagger_max_ms)
        hi = max(self.search_playwright_pre_stagger_min_ms, self.search_playwright_pre_stagger_max_ms)
        timeout_ms = max(35000, int(self.browser_timeout_ms))
        _append_kr36_debug_log(
            f"[kr36] topics_activity_playwright_start url={url} kind={page_kind} "
            f"timeout_ms={timeout_ms} max_attempts={attempts}"
        )
        best = ""
        for attempt in range(1, attempts + 1):
            if attempt == 1 and hi > 0:
                time.sleep(random.randint(max(0, lo), hi) / 1000)
            elif attempt > 1:
                self._human_pause(4500, 9500)
            chunk = self._playwright_kr36_topics_activity_listing_html_once(
                url, timeout_ms=timeout_ms, page_kind=page_kind
            )
            if chunk:
                best = chunk
            if chunk and _kr36_topics_activity_playwright_body_acceptable(chunk, page_kind=page_kind):
                _append_kr36_debug_log(
                    f"[kr36] topics_activity_playwright_ok attempt={attempt} url={url} "
                    f"html_length={len(chunk)}"
                )
                return chunk
            _append_kr36_debug_log(
                f"[kr36] topics_activity_playwright_suspicious_body attempt={attempt}/{attempts} "
                f"url={url} html_length={len(chunk or '')} hint=risk_or_transient"
            )
        return best

    def _playwright_kr36_topics_activity_listing_html_once(
        self, url: str, *, timeout_ms: int, page_kind: str
    ) -> str:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        try:
            with sync_playwright() as playwright:
                listing_headless = self.risk_verification_playwright_mode == "headless"
                lkw = self._playwright_chromium_launch_kwargs(
                    headless=listing_headless, incognito=False
                )
                browser = self._playwright_launch_chromium(playwright, lkw)
                context = browser.new_context(
                    locale="zh-CN",
                    user_agent=KR36_DEFAULT_USER_AGENT,
                    viewport={"width": 1440, "height": 1024},
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                )
                _load_kr36_cookies_into_browser_context(context)
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until="load", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(500)
                if page_kind == "topics":
                    try:
                        page.wait_for_selector('a[href^="/topics/"]', timeout=22000)
                    except PlaywrightTimeoutError:
                        _append_kr36_debug_log(
                            f"[kr36] topics_activity_playwright_selector_timeout url={url} "
                            "selector=a[href^=\"/topics/\"]"
                        )
                elif page_kind == "activity":
                    try:
                        page.wait_for_selector(
                            ".activity-item, a[href*='/activity/']", timeout=22000
                        )
                    except PlaywrightTimeoutError:
                        _append_kr36_debug_log(
                            f"[kr36] topics_activity_playwright_selector_timeout url={url} "
                            "selector=.activity-item|a[href*='/activity/']"
                        )
                try:
                    page.mouse.wheel(0, 800)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                page.wait_for_timeout(self.browser_wait_after_load_ms)
                html = page.content()
                if self.persist_browser_cookies:
                    save_kr36_cookies(extract_kr36_cookie_values(context.cookies()))
                context.close()
                browser.close()
                _append_kr36_debug_log(
                    f"[kr36] topics_activity_playwright_once_end url={url} html_length={len(html)}"
                )
                return html
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            _append_kr36_debug_log(
                f"[kr36] topics_activity_playwright_error url={url} error={error}"
            )
        except Exception as error:
            _append_kr36_debug_log(
                f"[kr36] topics_activity_playwright_unexpected url={url} error={error}"
            )
        return ""

    def _human_pause(self, low_ms: int | None = None, high_ms: int | None = None) -> None:
        low = int(low_ms if low_ms is not None else self.human_min_pause_ms)
        high = int(high_ms if high_ms is not None else self.human_max_pause_ms)
        if low > high:
            low, high = high, low
        time.sleep(random.uniform(low / 1000, high / 1000))

    def _curl_text(self, url: str, cookies: dict[str, str] | None = None) -> str:
        effective_cookies = cookies if cookies is not None else load_kr36_cookies()
        cookie_str = "; ".join(f"{k}={v}" for k, v in effective_cookies.items()) if effective_cookies else ""
        command = [
            "curl", "-sS", "--ssl-no-revoke", url,
            "-H", f"user-agent: {KR36_DEFAULT_USER_AGENT}",
            "-H", f"referer: {KR36_ROOT}/",
            "-H", "accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "accept-language: zh-CN,zh;q=0.9",
        ]
        if cookie_str:
            command += ["-H", f"cookie: {cookie_str}"]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout or ""


def _is_kr36_search_articles_url(url: str) -> bool:
    u = (url or "").lower()
    return "36kr.com" in u and "/search/articles/" in u


def _is_kr36_topic_detail_url(url: str) -> bool:
    """专题详情页 https://36kr.com/topics/{id}（纯数字 id，区别于 /topics/ 列表）。"""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return False
    host = p.netloc.lower().split(":", 1)[0]
    if host not in ("36kr.com", "www.36kr.com"):
        return False
    parts = (p.path or "").rstrip("/").split("/")
    # /topics/3770543132934659 → ['', 'topics', '3770543132934659']
    return len(parts) == 3 and parts[1] == "topics" and parts[2].isdigit()


def _kr36_topics_listing_index_url(url: str) -> bool:
    """仅专题列表 https://36kr.com/topics/，不含 /topics/{id} 详情。"""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return False
    host = p.netloc.lower().split(":", 1)[0]
    if host not in ("36kr.com", "www.36kr.com"):
        return False
    path = (p.path or "/").rstrip("/")
    return path == "/topics"


def _kr36_activity_listing_index_url(url: str) -> bool:
    """仅活动列表 https://36kr.com/activity 。"""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return False
    host = p.netloc.lower().split(":", 1)[0]
    if host not in ("36kr.com", "www.36kr.com"):
        return False
    path = (p.path or "/").rstrip("/")
    return path == "/activity"


def _kr36_topics_activity_page_kind(url: str) -> str:
    if _kr36_topics_listing_index_url(url):
        return "topics"
    if _kr36_activity_listing_index_url(url):
        return "activity"
    return ""


def _kr36_listing_html_has_parsable_topic_links(html: str) -> bool:
    return bool(KR36_TOPIC_LINK_RE.search(html or ""))


def _kr36_listing_html_has_parsable_activity_items(html: str) -> bool:
    blob = html or ""
    if KR36_ACTIVITY_CARD_RE.search(blob):
        return True
    return bool(KR36_ACTIVITY_LINK_RE.search(blob))


def _kr36_topics_activity_playwright_body_acceptable(html: str, *, page_kind: str) -> bool:
    """专题/活动列表：已能正则解析、或大页非风控壳，则视为可交给 parse_*_listing_html。"""
    blob = html or ""
    if not blob.strip():
        return False
    if _kr36_search_html_has_risk_interstitial(blob):
        return False
    if page_kind == "topics" and _kr36_listing_html_has_parsable_topic_links(blob):
        return True
    if page_kind == "activity" and _kr36_listing_html_has_parsable_activity_items(blob):
        return True
    if len(blob) >= 20000:
        return True
    return False


def _kr36_html_contains_search_article_links(html: str) -> bool:
    """CSR 前后：标题区或通用 /p/ 链接任一出现即视为已有可解析列表。"""
    blob = html or ""
    if KR36_SEARCH_TITLE_LINK_RE.search(blob):
        return True
    return bool(KR36_LINK_RE.search(blob))


def _kr36_search_html_has_risk_interstitial(html: str) -> bool:
    """字节系验证壳等：体积极小且带验证码脚本，并非「无搜索结果」。"""
    blob = html or ""
    if len(blob) > 12000:
        return False
    return "TTGCaptcha" in blob or "sec_sdk_build" in blob or "captcha/index.js" in blob


def _kr36_search_playwright_body_acceptable(html: str) -> bool:
    """可用于解析或可信「零条」判断：大页、或已含列表容器且非风控壳。"""
    blob = html or ""
    if not blob.strip():
        return False
    if _kr36_search_html_has_risk_interstitial(blob):
        return False
    if _kr36_html_contains_search_article_links(blob):
        return True
    if len(blob) >= 40000:
        return True
    if "kr-search-result-list-main" in blob and len(blob) >= 18000:
        return True
    return False


def _kr36_search_html_likely_zero_hits_message(html: str) -> bool:
    """列表区已渲染但文案提示无结果（与风控小页区分）。"""
    blob = html or ""
    if "kr-search-result-list-main" not in blob:
        return False
    hints = ("暂无", "没有相关", "未找到相关", "共 0 条", "共0条")
    return any(h in blob for h in hints)


def parse_listing_html(
    html: str,
    *,
    base_url: str,
    listing_url: str,
    report_date: date,
    require_relative_time: bool = False,
    fixed_category: str | None = None,
) -> list[RawArticleRef]:
    lowered_listing = listing_url.lower()
    if "/activity" in lowered_listing:
        return parse_activity_listing_html(html, base_url=base_url, listing_url=listing_url, report_date=report_date)

    refs: list[RawArticleRef] = []
    matches = list(KR36_LINK_RE.finditer(html))
    for index, match in enumerate(matches):
        href = str(match.group("href") or "").strip()
        title = clean_html_text(match.group("title") or "")
        if not href or not title:
            continue
        url = href if href.startswith("http") else urljoin(base_url, href)
        if require_relative_time:
            context = html[max(0, match.start() - 200): min(len(html), match.end() + 200)]
            if KR36_RELATIVE_TIME_RE.search(clean_html_text(context)) is None:
                continue
        category = fixed_category or infer_kr36_bucket(url, title)
        refs.append(
            RawArticleRef(
                source_site="36kr",
                article_id=f"36kr:{url.rsplit('/', 1)[-1]}",
                title=title,
                url=url,
                published_at=report_date.isoformat(),
                channel=category or "资讯",
                source_bucket=category or "资讯",
                summary="",
                metadata={"listing_url": listing_url},
            )
        )
    return refs


def parse_search_listing_html(
    html: str,
    *,
    base_url: str,
    listing_url: str,
    report_date: date,
    fixed_category: str,
    date_window: tuple[date, date] | None = None,
) -> list[RawArticleRef]:
    """
    按 search/articles 结果抓取。

    当 ``date_window`` 非 None 时，对每条条目尝试解析发布日期，只保留落在
    ``[date_window[0], date_window[1]]`` 区间内的结果；日期解析失败时**排除**该条目
    （防止无法确认日期的旧文章混入）。调用方不再需要以 ``info_per_category_limit`` 截断。
    """
    refs: list[RawArticleRef] = []
    seen_url: set[str] = set()

    def normalize_search_article_href(href: str) -> str:
        raw = str(href or "").strip()
        if not raw:
            return ""
        lowered = raw.lower()
        if lowered.startswith(("javascript:", "mailto:", "#")):
            return ""
        if raw.startswith("//"):
            raw = f"https:{raw}"
        url = raw if raw.startswith("http") else urljoin(base_url, raw)
        url = url.split("#", 1)[0]
        lowered_url = url.lower()
        if "36kr.com" not in lowered_url:
            return ""
        if "/p/" not in lowered_url:
            return ""
        return url

    def extract_best_anchor_from_item(item_html: str) -> tuple[str, str] | None:
        best: tuple[int, str, str] | None = None
        for anchor in KR36_ANCHOR_WITH_HREF_RE.finditer(item_html):
            href = str(anchor.group("href") or "").strip()
            anchor_html = str(anchor.group(0) or "")
            title = clean_html_text(anchor.group("title") or "")
            if not title:
                title_attr = re.search(r'\btitle="([^"]+)"', anchor_html, flags=re.IGNORECASE)
                if title_attr:
                    title = clean_html_text(title_attr.group(1))
            url = normalize_search_article_href(href)
            if not url or not title:
                continue

            context = item_html[max(0, anchor.start() - 200): min(len(item_html), anchor.end() + 200)].lower()
            score = 0
            if "/p/" in href.lower():
                score += 5
            if "article-item-title" in context:
                score += 3
            if "title-wrapper" in context:
                score += 2
            if len(title) >= 8:
                score += 1
            if any(ad_hint in title for ad_hint in KR36_AD_HINTS):
                score -= 3

            candidate = (score, url, title)
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            return None
        return best[1], best[2]

    def _in_date_window(item_html: str) -> bool:
        """
        日期窗口校验。

        - 无 date_window：直接通过。
        - 解析到日期：判断是否在 ``[date_window[0], date_window[1]]`` 内。
        - **解析失败**：记录调试日志并**排除**（不再默认放行），
          避免因日期格式未识别而把窗口外的旧文章混入结果。
        """
        if date_window is None:
            return True
        pub = _infer_search_result_published_date(item_html, report_date)
        if pub is None:
            _append_kr36_debug_log(
                f"[kr36] search_date_parse_failed fixed_category={fixed_category} "
                f"window={date_window[0].isoformat()}~{date_window[1].isoformat()} "
                f"item_html_len={len(item_html)} action=exclude"
            )
            return False
        in_win = date_window[0] <= pub <= date_window[1]
        if not in_win:
            _append_kr36_debug_log(
                f"[kr36] search_date_filtered fixed_category={fixed_category} "
                f"pub={pub.isoformat()} window={date_window[0].isoformat()}~{date_window[1].isoformat()}"
            )
        return in_win

    def append_ref(href: str, title_raw: str, item_html: str = "") -> None:
        title = clean_html_text(title_raw or "")
        url = normalize_search_article_href(href)
        if not url or not title:
            return
        if url in seen_url:
            return
        if not _in_date_window(item_html):
            return
        seen_url.add(url)
        pub = _infer_search_result_published_date(item_html, report_date) if item_html else None
        refs.append(
            RawArticleRef(
                source_site="36kr",
                article_id=f"36kr:{url.rsplit('/', 1)[-1]}",
                title=title,
                url=url,
                published_at=(pub or report_date).isoformat(),
                channel=fixed_category,
                source_bucket=fixed_category,
                summary="",
                metadata={"listing_url": listing_url},
            )
        )

    # 优先按用户给出的 DOM 结构解析：li.search-result-list-item-article
    for item_match in KR36_SEARCH_ARTICLE_ITEM_RE.finditer(html):
        item_html = str(item_match.group("content") or "")
        extracted = extract_best_anchor_from_item(item_html)
        if extracted is None:
            continue
        url, title = extracted
        append_ref(url, title, item_html)

    # 回退：仅当主路径（li.search-result-list-item-article）完全没有匹配时才运行。
    # 取匹配位置前后各 600 字符作为上下文，供 _in_date_window 尝试解析日期。
    # 若主路径已产出结果，不再运行回退，以免相邻条目的日期互相污染。
    if not refs:
        _ctx = 600
        for match in KR36_SEARCH_TITLE_LINK_RE.finditer(html):
            ctx_start = max(0, match.start() - _ctx)
            ctx_end = min(len(html), match.end() + _ctx)
            append_ref(match.group("href"), match.group("title"), html[ctx_start:ctx_end])
        for match in KR36_LINK_RE.finditer(html):
            ctx_start = max(0, match.start() - _ctx)
            ctx_end = min(len(html), match.end() + _ctx)
            append_ref(match.group("href"), match.group("title"), html[ctx_start:ctx_end])
    return refs


def parse_topics_listing_html(
    html: str,
    *,
    base_url: str,
    listing_url: str,
    report_date: date,
) -> list[RawArticleRef]:
    """专题页全量抓取，不按日期过滤，抓取所有可见专题。"""
    seen: set[str] = set()
    refs: list[RawArticleRef] = []
    for match in KR36_TOPIC_LINK_RE.finditer(html):
        href = str(match.group("href") or "").strip()
        title = clean_html_text(match.group("title") or "")
        if not href or not title:
            continue
        url = href if href.startswith("http") else urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        topic_id = url.rsplit("/", 1)[-1]
        refs.append(
            RawArticleRef(
                source_site="36kr",
                article_id=f"36kr:topic:{topic_id}",
                title=title,
                url=url,
                published_at=report_date.isoformat(),
                channel="专题",
                source_bucket="专题",
                summary="",
                metadata={"listing_url": listing_url},
            )
        )
    return refs


def select_focus_topics(
    topic_refs: list[RawArticleRef],
    *,
    focus_keywords: tuple[str, ...],
    limit: int,
) -> list[RawArticleRef]:
    if not topic_refs:
        return []
    normalized_keywords = tuple(re.sub(r"\s+", "", token.lower()) for token in focus_keywords if token.strip())
    selected: list[RawArticleRef] = []
    seen_url: set[str] = set()
    if normalized_keywords:
        for ref in topic_refs:
            compact_title = re.sub(r"\s+", "", ref.title.lower())
            if not any(keyword in compact_title for keyword in normalized_keywords):
                continue
            if ref.url in seen_url:
                continue
            seen_url.add(ref.url)
            selected.append(ref)
            if len(selected) >= max(1, limit):
                return selected
    if selected:
        return selected
    return topic_refs[: max(1, limit)]


def resolve_previous_week_window(report_date: date) -> tuple[date, date]:
    current_week_monday = report_date - timedelta(days=report_date.weekday())
    previous_week_monday = current_week_monday - timedelta(days=7)
    previous_week_sunday = current_week_monday - timedelta(days=1)
    return previous_week_monday, previous_week_sunday


def resolve_kr36_search_category_date_window(report_date: date) -> tuple[date, date]:
    """搜索分类专用日期窗。

    - 周四及之前（Mon~Thu, weekday 0-3）：上周五 → 上周日（周末精选）。
    - 周五及之后（Fri~Sun, weekday 4-6）：本周一 → 今天。
    """
    if report_date.weekday() <= 3:
        # 上周日 = 今天往前推 weekday+1 天；上周五 = 上周日再前 2 天
        last_sunday = report_date - timedelta(days=report_date.weekday() + 1)
        last_friday = last_sunday - timedelta(days=2)
        return last_friday, last_sunday
    # 周五及以后：本周一至今天
    current_week_monday = report_date - timedelta(days=report_date.weekday())
    return current_week_monday, report_date


def resolve_current_week_window(report_date: date) -> tuple[date, date]:
    """``report_date`` 所在自然周：周一 00:00 起算至周日（闭区间，按日历日比较）。"""
    monday = report_date - timedelta(days=report_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _kr36_topic_subitem_date_mode_from_config(sc: dict[str, object]) -> str:
    """
    专题二级时间策略：weekday_split | previous_week | none；未写新键时兼容 ``topic_previous_week_only``。
    """
    m = str(sc.get("topic_subitem_date_mode") or sc.get("topic_subitem_time_mode") or "").strip()
    if m:
        return m.lower()
    if "topic_previous_week_only" in sc:
        v = sc.get("topic_previous_week_only")
        if v is None:
            return "previous_week"
        if isinstance(v, bool):
            return "previous_week" if v else "none"
        s = str(v).strip().lower()
        if s in ("0", "false", "no", "off", "none"):
            return "none"
        return "previous_week"
    return "weekday_split"


def resolve_kr36_topic_subitem_date_window(
    report_date: date, *, mode: str
) -> tuple[date | None, date | None]:
    """
    专题详情页子项（二级）的日期窗。

    - ``weekday_split``（默认）：周一至周四为「自然周上的上周一～周日」；周五至周日为「本周一～周日」。
    - ``previous_week``：始终为上周窗（与历史 ``topic_previous_week_only=true`` 一致）。
    - ``none``：不按时窗筛子项（与 ``topic_previous_week_only=false`` 一致）。活动页不走本函数。

    一级专题仍仅由 ``topic_focus_keywords`` 在 /topics/ 列表上筛选，不按时窗。
    """
    m = (mode or "").strip().lower()
    if m in ("", "weekday_split", "weekday", "default", "auto"):
        if report_date.weekday() <= 3:  # Mon=0 .. Thu=3
            s, e = resolve_previous_week_window(report_date)
            return s, e
        s, e = resolve_current_week_window(report_date)
        return s, e
    if m in ("previous_week", "last_week", "true", "1", "on", "yes", "old"):
        s, e = resolve_previous_week_window(report_date)
        return s, e
    if m in ("none", "unfiltered", "off", "false", "0", "no", "all"):
        return None, None
    s, e = resolve_previous_week_window(report_date)
    s2, e2 = resolve_current_week_window(report_date)
    return (s, e) if report_date.weekday() <= 3 else (s2, e2)


def parse_topic_detail_html(
    html: str,
    *,
    base_url: str,
    listing_url: str,
    report_date: date,
    topic_title: str,
    window_start: date | None,
    window_end: date | None,
) -> list[RawArticleRef]:
    refs: list[RawArticleRef] = []
    seen_urls: set[str] = set()
    topic_id = listing_url.rstrip("/").rsplit("/", 1)[-1]
    for section_match in KR36_TOPIC_SECTION_RE.finditer(html):
        section_html = str(section_match.group("content") or "")
        for item_match in KR36_TOPIC_LIST_ITEM_RE.finditer(section_html):
            item_html = str(item_match.group("content") or "")
            best_url = ""
            best_title = ""
            best_score = -10
            for anchor in KR36_ANCHOR_WITH_HREF_RE.finditer(item_html):
                href = str(anchor.group("href") or "").strip()
                if not href:
                    continue
                title = clean_html_text(anchor.group("title") or "")
                normalized_url = normalize_topic_item_url(href=href, base_url=base_url)
                if not normalized_url or not title:
                    continue
                lowered_url = normalized_url.lower()
                if "/video/" not in lowered_url and "/p/" not in lowered_url:
                    continue
                score = 0
                if "/video/" in lowered_url or "/p/" in lowered_url:
                    score += 4
                if "item-title" in anchor.group(0):
                    score += 2
                if len(title) >= 8:
                    score += 1
                if score > best_score:
                    best_score = score
                    best_url = normalized_url
                    best_title = title
            if not best_url or best_url in seen_urls:
                continue
            published_day = infer_topic_item_date(item_html, report_date=report_date)
            if window_start is not None and window_end is not None:
                if published_day is None:
                    continue
                if published_day < window_start or published_day > window_end:
                    continue
            seen_urls.add(best_url)
            item_kind = deduce_topic_item_kind_from_36kr_item_url(best_url)
            if not item_kind:
                item_kind = "article"
            item_id = best_url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
            published_at = (published_day or report_date).isoformat()
            refs.append(
                RawArticleRef(
                    source_site="36kr",
                    article_id=f"36kr:topic:{item_kind}:{item_id}",
                    title=best_title,
                    url=best_url,
                    published_at=published_at,
                    channel="专题",
                    source_bucket="专题",
                    summary="",
                    metadata={
                        "listing_url": listing_url,
                        "topic_url": listing_url,
                        "topic_id": topic_id,
                        "topic_title": topic_title,
                        "topic_item_kind": item_kind,
                        "topic_item_date": published_at,
                    },
                )
            )
    return refs


def normalize_topic_item_url(*, href: str, base_url: str) -> str:
    raw = str(href or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith(("javascript:", "mailto:", "#")):
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    url = raw if raw.startswith("http") else urljoin(base_url, raw)
    if "36kr.com" not in url.lower():
        return ""
    return url.split("#", 1)[0]


def infer_topic_item_date(value: str, *, report_date: date) -> date | None:
    plain = clean_html_text(value)
    match = KR36_TOPIC_ITEM_DATE_RE.search(plain)
    if not match:
        return None
    raw_date = str(match.group("date") or "").strip()
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            continue
    md = re.fullmatch(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日", raw_date)
    if not md:
        return None
    month = int(md.group("month"))
    day = int(md.group("day"))
    try:
        candidate = date(report_date.year, month, day)
    except ValueError:
        return None
    if candidate > report_date + timedelta(days=2):
        try:
            candidate = date(report_date.year - 1, month, day)
        except ValueError:
            return None
    return candidate


def _infer_search_result_published_date(item_html: str, report_date: date) -> date | None:
    """
    从搜索结果条目 HTML（原始标签未剥离）中解析发布日期。

    优先级：
    1. HTML 属性：``datetime="YYYY-MM-DD"`` / ``data-time="<13位时间戳>"``
    2. 可见文本：相对时间（刚刚/X分钟前/X小时前/X天前）、YYYY-MM-DD、MM-DD、X月X日

    无法解析时返回 ``None``。
    """
    raw = str(item_html or "")

    # ── 1. datetime 属性（ISO 8601）────────────────────────────────────
    for m in KR36_SEARCH_ITEM_DATETIME_ATTR_RE.finditer(raw):
        val = m.group(1).strip()
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

    # ── 2. data-time / data-pub-time 等 13 位毫秒时间戳 ────────────────
    for m in KR36_SEARCH_ITEM_TIMESTAMP_RE.finditer(raw):
        ts_str = m.group(1).strip()
        try:
            ts = int(ts_str)
            # 13 位毫秒 → 秒
            if ts > 9_999_999_999:
                ts //= 1000
            from datetime import timezone as _tz
            return datetime.fromtimestamp(ts, tz=_tz.utc).date()
        except (ValueError, OSError, OverflowError):
            continue

    # ── 3. 可见文本 ─────────────────────────────────────────────────────
    plain = clean_html_text(raw)
    for m in KR36_SEARCH_ITEM_TIME_RE.finditer(plain):
        text = m.group(0).strip()
        # 相对时间：X天前
        rel_day = re.fullmatch(r"(\d+)\s*天前", text)
        if rel_day:
            try:
                return report_date - timedelta(days=int(rel_day.group(1)))
            except OverflowError:
                return report_date
        # 相对时间：刚刚 / X分钟前 / X小时前
        if re.fullmatch(r"刚刚|\d+\s*(?:分钟前|小时前)", text):
            return report_date
        # 绝对日期：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        # MM-DD 或 MM/DD（无年份）
        md = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", text)
        if md:
            month, day = int(md.group(1)), int(md.group(2))
            try:
                candidate = date(report_date.year, month, day)
                if candidate > report_date + timedelta(days=1):
                    candidate = date(report_date.year - 1, month, day)
                return candidate
            except ValueError:
                continue
        # X月X日
        md2 = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", text)
        if md2:
            month, day = int(md2.group(1)), int(md2.group(2))
            try:
                candidate = date(report_date.year, month, day)
                if candidate > report_date + timedelta(days=1):
                    candidate = date(report_date.year - 1, month, day)
                return candidate
            except ValueError:
                continue
    return None


def _stream_url_prefer_https(url: str) -> str:
    """同页内统一用 https 直链（CDN 多支持 https，避免混用 http）。"""
    s = (url or "").strip()
    if s.startswith("http://"):
        return "https://" + s[7:]
    return s


def _normalize_video_tag_src_to_https(value: str) -> str:
    """
    从 <video src> 抓到的原始串归一成 https 绝对地址：支持 // 协议相对、http 升级、
    站内以 / 开头的路径、无 scheme 的 `videos.36krcdn.com/...` 等（不强制先出现 https?://）。
    无法识别则返回空串。
    """
    s = unescape((value or "").strip())
    if not s or s.lower() in ("#", "about:blank", "javascript:", "javascript:;"):
        return ""
    if s.startswith("https://"):
        return s
    if s.startswith("//") and not s.startswith("///"):
        s = "https:" + s
    elif s.startswith("http://"):
        s = "https://" + s[7:]
    elif s.startswith("/") and not s.startswith("//"):
        s = urljoin(f"{KR36_ROOT}/", s.lstrip("/"))
    elif KR36_BARE_CDN_HOST_RE.match(s):
        s = "https://" + s
    else:
        return ""
    if s.startswith("https://"):
        return s
    if s.startswith("http://"):
        return "https://" + s[7:]
    return ""


def extract_video_media_url(html: str) -> str:
    candidates = extract_video_media_urls(html)
    return candidates[0] if candidates else ""


def extract_video_media_urls(html: str) -> list[str]:
    blob = html or ""
    candidates: list[str] = []

    # 第三层 /video/{id} 页：优先 <video> 的 src（格式放宽，再统一为 https）
    for tag_match in KR36_VIDEO_TAG_SRC_RE.finditer(blob):
        raw = (tag_match.group("vd") or tag_match.group("vs") or tag_match.group("vb") or "").strip()
        src = _normalize_video_tag_src_to_https(raw)
        if not src:
            continue
        candidates.append(_stream_url_prefer_https(src))

    state_payload = _extract_window_initial_state_json(blob)
    if state_payload:
        try:
            parsed = json.loads(state_payload)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            video_detail = parsed.get("videoDetail")
            if isinstance(video_detail, dict):
                data = video_detail.get("data")
                if isinstance(data, dict):
                    # 36kr 视频详情页会在 initialState.videoDetail.data.url* 中给出可下载流地址。
                    # 按清晰度从低到高尝试，优先较小文件以提高直链下载成功率。
                    url_keys = [k for k in data if str(k).startswith("url")]
                    rank = {"url": 0, "url256": 1, "url384": 2, "url720": 3, "url1080": 4}

                    def _url_key_order(k: object) -> tuple[int, str]:
                        s = str(k)
                        return (rank.get(s, 50), s)

                    for key in sorted(url_keys, key=_url_key_order):
                        stream_url = str(data.get(key) or "").strip()
                        if stream_url.startswith("http"):
                            candidates.append(_stream_url_prefer_https(stream_url))
    candidates.extend(KR36_VIDEO_CDN_LINK_RE.findall(blob))
    candidates.extend(KR36_VIDEO_FILE_LINK_RE.findall(blob))
    candidates.extend(match.replace("\\/", "/") for match in KR36_VIDEO_FILE_LINK_ESCAPED_RE.findall(blob))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _stream_url_prefer_https(str(candidate or "").strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _extract_window_initial_state_json(html: str) -> str:
    """取 window.initialState= 赋值；多段时优先含 videoDetail 的脚本（视频页）。"""
    marker = "window.initialState="
    blob = html or ""
    payloads: list[str] = []
    start = 0
    while True:
        idx = blob.find(marker, start)
        if idx < 0:
            break
        payload = blob[idx + len(marker) :]
        end = payload.find("</script>")
        if end >= 0:
            payload = payload[:end]
        payload = payload.strip()
        if payload.endswith(";"):
            payload = payload[:-1].rstrip()
        if payload:
            payloads.append(payload)
        start = idx + len(marker)
    if not payloads:
        return ""
    for payload in reversed(payloads):
        if "videoDetail" in payload:
            return payload
    return payloads[-1]


def _extract_kr36_video_id(url: str) -> str:
    match = KR36_VIDEO_ID_RE.search(url or "")
    if not match:
        return ""
    return str(match.group("id") or "").strip()


def _guess_file_extension_from_url(url: str, *, default: str) -> str:
    lowered = str(url or "").lower()
    if ".m3u8" in lowered:
        return ".m3u8"
    if ".mp4" in lowered:
        return ".mp4"
    if "video_mp4" in lowered or "36krcdn.com" in lowered:
        return ".mp4"
    return default


def _safe_path_component(value: str, *, fallback: str = "topic") -> str:
    token = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    token = token.strip(" .")
    if not token:
        token = fallback
    return token[:80]


def _safe_filename(value: str, *, fallback: str = "item") -> str:
    token = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    token = re.sub(r"\s+", "_", token)
    token = token.strip("._")
    if not token:
        token = fallback
    return token[:120]


def _extract_activity_title(content: str) -> str:
    """从活动卡片内层 HTML 提取名称。"""
    m = KR36_ACTIVITY_TITLE_RE.search(content)
    if m:
        t = clean_html_text(m.group("t"))
        if len(t) >= 3:
            return t[:80]
    plain = clean_html_text(content)
    for sep in ("未开始", "报名中", "活动中", "已结束", "月", "地点", "时间", "主题"):
        idx = plain.find(sep)
        if 3 < idx < 80:
            plain = plain[:idx]
            break
    return plain.strip()[:80]


def _include_kr36_activity_in_crawl_list(status: str) -> bool:
    """
    活动列表：仅「未开始、报名中、活动中」进 step1；「已结束」排除。
    与页面 class 上四类状态字一致；未解析到状态（空串）时保留，避免误丢。
    """
    s = (status or "").strip()
    if not s:
        return True
    if s == "已结束":
        return False
    return s in KR36_ACTIVITY_LISTING_KEPT_STATUSES


def parse_activity_listing_html(
    html: str,
    *,
    base_url: str,
    listing_url: str,
    report_date: date,
) -> list[RawArticleRef]:
    """活动页抓取；已结束场不进入清单。优先匹配 class=activity-item 卡片。"""

    refs: list[RawArticleRef] = []
    seen_urls: set[str] = set()

    # 优先：activity-item class 卡片（href 可指向站外）
    for match in KR36_ACTIVITY_CARD_RE.finditer(html):
        href = str(match.group("href") or match.group("href2") or "").strip()
        content = str(match.group("content") or match.group("content2") or "")
        if not href:
            continue
        title = _extract_activity_title(content)
        if not title:
            title = href.rsplit("/", 1)[-1][:60] or "活动"
        url = href if href.startswith("http") else urljoin(base_url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        context = content

        status = infer_activity_status(context)
        date_range = infer_activity_date_range(context)
        city = infer_activity_city(context)
        theme = infer_activity_theme(context, title)
        description = infer_activity_description(context)
        start_label = build_activity_start_label(status, date_range, report_date)

        summary_parts: list[str] = []
        if description:
            summary_parts.append(description)
        if date_range:
            summary_parts.append(f"时间: {date_range}")
        if city:
            summary_parts.append(f"地点: {city}")
        if theme:
            summary_parts.append(f"主题: {theme}")
        if start_label:
            summary_parts.append(start_label)

        if not _include_kr36_activity_in_crawl_list(status):
            continue
        refs.append(RawArticleRef(
            source_site="36kr",
            article_id=f"36kr:activity:{url.rsplit('/', 1)[-1]}",
            title=title,
            url=url,
            published_at=report_date.isoformat(),
            channel="活动",
            source_bucket="活动",
            summary=" | ".join(summary_parts),
            metadata={
                "listing_url": listing_url,
                "activity_status": status,
                "activity_time_range": date_range,
                "activity_city": city,
                "activity_theme": theme,
                "activity_description": description,
                "start_label": start_label,
            },
        ))

    # 兜底：旧路径（href=/activity/slug 或 36kr.com/activity/slug）
    for match in KR36_ACTIVITY_LINK_RE.finditer(html):
        href = str(match.group("href") or "").strip()
        title = clean_html_text(match.group("title") or "")
        if not href or not title:
            continue

        url = href if href.startswith("http") else urljoin(base_url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        context = html[max(0, match.start() - 400): min(len(html), match.end() + 400)]
        status = infer_activity_status(context)
        date_range = infer_activity_date_range(context)
        city = infer_activity_city(context)
        theme = infer_activity_theme(context, title)
        description = infer_activity_description(context)
        start_label = build_activity_start_label(status, date_range, report_date)

        summary_parts: list[str] = []
        if description:
            summary_parts.append(description)
        if date_range:
            summary_parts.append(f"时间: {date_range}")
        if city:
            summary_parts.append(f"地点: {city}")
        if theme:
            summary_parts.append(f"主题: {theme}")
        if start_label:
            summary_parts.append(start_label)

        if not _include_kr36_activity_in_crawl_list(status):
            continue
        ref = RawArticleRef(
            source_site="36kr",
            article_id=f"36kr:activity:{url.rsplit('/', 1)[-1]}",
            title=title,
            url=url,
            published_at=report_date.isoformat(),
            channel="活动",
            source_bucket="活动",
            summary=" | ".join(summary_parts),
            metadata={
                "listing_url": listing_url,
                "activity_status": status,
                "activity_time_range": date_range,
                "activity_city": city,
                "activity_theme": theme,
                "activity_description": description,
                "start_label": start_label,
            },
        )
        refs.append(ref)

    return refs


def clean_html_text(value: str) -> str:
    text = KR36_TAG_RE.sub(" ", value)
    text = unescape(text)
    return KR36_WHITESPACE_RE.sub(" ", text).strip()


def infer_kr36_bucket(url: str, title: str) -> str | None:
    lowered = f"{url} {title}".lower()
    if "/topics/" in lowered or "/topic/" in lowered:
        return "专题"
    if "/activity" in lowered:
        return "活动"
    for category, keywords in KR36_CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None


def infer_kr36_info_channel_from_listing_url(listing_url: str) -> str | None:
    normalized = str(listing_url or "").strip().lower()
    mapping: tuple[tuple[str, str], ...] = (
        ("/information/latest/", "最新"),
        ("/information/finance/", "财经"),
        ("/information/auto/", "汽车"),
        ("/information/technology/", "科技"),
        ("/information/venturecapital/", "创投"),
    )
    for token, label in mapping:
        if token in normalized:
            return label
    return None


def requires_relative_time_filter(listing_url: str) -> bool:
    normalized = str(listing_url or "").strip()
    if normalized.rstrip("/") in {u.rstrip("/") for u in KR36_RELATIVE_TIME_REQUIRED_EXACT_URLS}:
        return True
    normalized_lower = normalized.lower()
    return any(normalized_lower.startswith(prefix) for prefix in KR36_RELATIVE_TIME_REQUIRED_LISTING_PREFIXES)


def infer_activity_status(context: str) -> str:
    match = KR36_ACTIVITY_STATUS_RE.search(context)
    if not match:
        return ""
    return match.group(1)


def infer_activity_date_range(context: str) -> str:
    match = KR36_ACTIVITY_DATE_RANGE_RE.search(context)
    if not match:
        return ""
    return f"{match.group('start')}-{match.group('end')}"


def infer_activity_city(context: str) -> str:
    match = KR36_ACTIVITY_CITY_RE.search(context)
    if not match:
        return ""
    return match.group(1)


def infer_activity_theme(context: str, title: str) -> str:
    plain = clean_html_text(context)
    m = KR36_ACTIVITY_THEME_RE.search(plain)
    if m:
        return (m.group(1) or m.group(2) or "").strip()
    return ""


def infer_activity_description(context: str) -> str:
    """活动卡片描述：优先取 item-introduce 等介绍区文本。"""
    m = KR36_ACTIVITY_DESC_RE.search(context or "")
    if not m:
        return ""
    return clean_html_text(m.group("d") or "")


def build_activity_start_label(status: str, date_range: str, report_date: date) -> str:
    """返回人类可读的开始时间标签，如"5天后开始"/"今天开始"/"进行中"/"已结束"。"""
    if status == "已结束":
        return "已结束"
    if status == "活动中":
        return "进行中"
    if not date_range:
        return status
    start_text = date_range.split("-", 1)[0]
    start_day = parse_activity_month_day(start_text, report_date.year)
    if start_day is None:
        return status
    delta_days = (start_day - report_date).days
    if delta_days < 0:
        return "进行中"
    if delta_days == 0:
        return "今天开始"
    return f"{delta_days}天后开始"


def parse_activity_month_day(value: str, year: int) -> date | None:
    text = value.strip()
    match = re.fullmatch(r"(?P<month>\d{2})月(?P<day>\d{2})日", text)
    if not match:
        return None
    try:
        return date(year, int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None


def normalize_channel_key(value: str) -> str:
    token = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    token = token.strip("-")
    return token or "general"


def _positive_int_config(value: Any, *, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def load_kr36_cookies() -> dict[str, str]:
    """从 config/kr36_cookies.json 加载 Cookie，文件不存在时返回空字典。"""
    with _kr36_cookies_file_lock:
        if not KR36_COOKIES_FILE.exists():
            return {}
        try:
            raw = json.loads(KR36_COOKIES_FILE.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in raw.items() if v}
        except Exception:
            return {}


def save_kr36_cookies(cookies: dict[str, str]) -> None:
    """Persist refreshed browser cookies for later curl-based requests."""

    if not cookies:
        return
    with _kr36_cookies_file_lock:
        KR36_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        KR36_COOKIES_FILE.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def extract_kr36_cookie_values(cookie_items: list[dict[str, Any]]) -> dict[str, str]:
    """Keep only 36Kr cookies in the name/value format used by curl."""

    extracted: dict[str, str] = {}
    for item in cookie_items:
        domain = str(item.get("domain") or "").lower()
        if "36kr.com" not in domain:
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        if name and value:
            extracted[name] = value
    return extracted


def _load_kr36_cookies_into_browser_context(context: Any) -> None:
    cookies = load_kr36_cookies()
    if not cookies:
        return
    context.add_cookies(
        [
            {
                "name": name,
                "value": value,
                "domain": ".36kr.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
            }
            for name, value in cookies.items()
        ]
    )


def _looks_like_captcha_or_block(html: str) -> bool:
    """
    仅扫前 4k，用于快速识别风控/滑块**文案**页。

    注意：不要再用英文单词 ``verify`` 或子串 ``captcha`` 单独匹配，否则
    正文/头里 recaptcha、JSON 里的 "verify" 会长期把页面判为「仍在验证」，
    Step4 Playwright 会空转直到 ``browser_verification_timeout_ms`` 才关浏览器。
    """
    sample = (html or "")[:4000]
    lowered = sample.lower()
    if not lowered:
        return True
    # 用中文提示与验证码 DOM 类名等较稳定片段；见 slider_captcha 中 captcha-verify-image
    risk_tokens = (
        "captcha-verify",
        "verifycenter",
        "人机验证",
        "请完成验证",
        "完成验证后继续",
        "访问受限",
        "异常流量",
        "security check",
        "\u5b8c\u6210\u9a8c\u8bc1\u540e\u7ee7\u7eed",  # 与「完成验证后继续」同义，保留
        "\u62d6\u52a8\u5b8c\u6210\u4e0a\u65b9\u62fc\u56fe",  # 拖动完成上方拼图
        "\u6309\u4f4f\u5de6\u8fb9\u6309\u94ae\u62d6\u52a8",  # 按住左边按钮拖动
    )
    return any(token in lowered for token in risk_tokens)


def _kr36_likely_36kr_csr_risk_listing_shell(html: str) -> bool:
    """
    CSR 首屏小壳：验证码文案/DOM 尚未进 HTML 时，_is_usable_html 也会为真，不能据此关窗。

    若已满足 step4 的「正页/列表已展开」启发（post），或篇幅明显已非壳子，则不算壳。
    """
    if not html:
        return False
    hlen = len(html)
    if hlen < 500 or hlen > 15000:
        return False
    if _kr36_step4_post_slider_page_looks_resolved(html):
        return False
    top = (html or "")[:8000].lower()
    if "36kr.com" not in top and "36氪" not in (html or "")[:3000]:
        return False
    return True


def _kr36_step4_post_slider_page_looks_resolved(html: str) -> bool:
    """
    仅用于 step4 Playwright 滑块链：正页 <head> 里常有 verify/recaptcha 子串，若只靠
    _is_usable_html 会长时间判失败；在「首屏已无人机提示 + 体量为正常列表/文章」时判为
    已通过，以便尽快关浏览器。其它抓取路径仍用 _is_usable_html。
    """
    if not html or len(html) < 4000:
        return False
    top = html[:10000]
    for needle in (
        "人机验证",
        "请完成验证",
        "访问受限",
        "异常流量",
        "按住左边按钮",
        "拖动完成上方拼图",
    ):
        if needle in top:
            return False
    if "captcha-verify-image" in top:
        return False
    lo = html.lower()
    if "<html" not in lo:
        return False
    if "36kr.com" in lo or "36氪" in top:
        return True
    if "kr-search-result" in lo or "search-result-list-item" in lo:
        return True
    return len(html) > 25000


def _is_usable_html(html: str) -> bool:
    if not html:
        return False
    lowered = html.lower()
    if "<html" not in lowered:
        return False
    return not _looks_like_captcha_or_block(html)


def _kr36_listing_requires_step4_recovery(url: str, html: str) -> bool:
    """
    列表页被拦或仅返回 CSR 壳时，``_looks_like_captcha_or_block`` 常为假（验证码文案未进首屏 HTML）。
    此时与显式风控页一样应走 ``Kr36RiskStep4Tool.fetch``（滑块 + 回写 Cookie）。
    """
    blob = html or ""
    if not blob.strip():
        return False
    if _looks_like_captcha_or_block(blob):
        return False
    if not _is_usable_html(blob):
        return False
    if _kr36_activity_listing_index_url(url):
        return not _kr36_listing_html_has_parsable_activity_items(blob)
    if _kr36_topics_listing_index_url(url):
        return not _kr36_listing_html_has_parsable_topic_links(blob)
    if _is_kr36_search_articles_url(url):
        if _kr36_html_contains_search_article_links(blob):
            return False
        if _kr36_search_html_likely_zero_hits_message(blob):
            return False
        return True
    return False


def _kr36_needs_playwright_slider_recovery(url: str, html: str) -> bool:
    """
    统一「需走 Playwright + 自动滑块」判定：显式验证码文案、字节系验证壳脚本、
    或专题/活动/搜索列表 curl 仅为 CSR 壳（与 ``maybe_recover`` 触发条件一致）。
    """
    blob = html or ""
    if not blob.strip():
        return False
    if _looks_like_captcha_or_block(blob):
        return True
    if _kr36_search_html_has_risk_interstitial(blob):
        return True
    return _kr36_listing_requires_step4_recovery(url, blob)


def _int_list_config(value: Any, *, default: list[int]) -> list[int]:
    if not isinstance(value, list):
        return list(default)
    result: list[int] = []
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.append(parsed)
    return result or list(default)


def _str_list_config(value: Any, *, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            result.append(token)
    return result or list(default)


def _bool_config(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


@contextmanager
def kr36_debug_log_file(path: Path) -> Iterator[None]:
    """本次抓取把 [kr36] 调试日志写入 path（如 kr36_report/.../kr36_fetch.log），结束后恢复。"""
    global _kr36_debug_log_path_override
    prev = _kr36_debug_log_path_override
    _kr36_debug_log_path_override = path.resolve()
    try:
        yield
    finally:
        _kr36_debug_log_path_override = prev


def _append_kr36_debug_log(message: str) -> None:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} {message}"
        log_file = _resolve_kr36_debug_log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as stream:
            stream.write(f"{line}\n")
        echo_stdout = str(os.getenv("KR36_LOG_ECHO_STDOUT", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if echo_stdout:
            print(line)
    except Exception:
        # 调试日志不影响主流程。
        return


def _append_kr36_stage_log(*, action: str, stage_name: str, data_count: int, total_refs: int) -> None:
    """写入面向人工排查的中文阶段日志。"""
    _append_kr36_debug_log(
        f"[kr36] {action}{stage_name}，数据 {data_count} 条，累计 {total_refs} 条"
    )


def _resolve_kr36_debug_log_file() -> Path:
    custom_log_file = os.getenv("KR36_LOG_FILE", "").strip()
    if custom_log_file:
        return Path(custom_log_file)
    if _kr36_debug_log_path_override is not None:
        return _kr36_debug_log_path_override
    return KR36_DEBUG_LOG_FILE

