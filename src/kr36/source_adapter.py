"""36Kr source adapter with conservative throttling and manual browser fallback."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import date
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urljoin

from utils.tools.content_models import ContentSourceAdapter
from utils.tools.content_models import RawArticleDetail, RawArticleRef, StandardArticle

KR36_PROJECT_ROOT = Path(__file__).resolve().parents[2]
KR36_COOKIES_FILE = KR36_PROJECT_ROOT / "config" / "kr36_cookies.json"
KR36_DEBUG_LOG_FILE = KR36_PROJECT_ROOT / "log.txt"
KR36_SLIDER_SOLVER_SCRIPT = KR36_PROJECT_ROOT / "scripts" / "kr36_risk_slider_solver.py"

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
    ("深氪", "深氪"),
    ("后浪白皮书", "后浪白皮书"),
    ("行业日报", "行业日报"),
    ("Long China 50", "Long China 50"),
    ("投资派", "投资派"),
    ("新青年观察", "新青年观察"),
    ("KRLab", "KRLab"),
    ("数智前瞻", "数智前瞻"),
)
KR36_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>/p/\d+|https?://36kr\.com/p/\d+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# 搜索页 CSR 渲染后，标题多在 p.title-wrapper > a（与纯 <a href=/p/> 并存）
KR36_SEARCH_TITLE_LINK_RE = re.compile(
    r'<p[^>]+class="[^"]*\btitle-wrapper\b[^"]*"[^>]*>\s*<a[^>]+href="(?P<href>/p/\d+)"[^>]*>(?P<title>.*?)</a>',
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
KR36_ACTIVITY_DATE_RANGE_RE = re.compile(r"(?P<start>\d{2}月\d{2}日)-(?P<end>\d{2}月\d{2}日)")
KR36_ACTIVITY_CITY_RE = re.compile(r"(北京|上海|深圳|杭州|广州|南京|苏州|成都|重庆|武汉|西安|厦门|天津|长沙|青岛)")
KR36_ACTIVITY_THEME_RE = re.compile(
    r"(?:主题|话题|专题|赛道|领域|方向)[：:]\s*([^\s<|，,。]{2,20})"
    r"|(?:关于|聚焦|围绕|探讨)\s*([^\s<|，,。]{2,20})"
)
KR36_RELATIVE_TIME_RE = re.compile(r"(刚刚|\d+\s*(分钟前|小时前|天前))")


def _normalize_kr36_risk_verification_playwright_mode(raw: object) -> str:
    """内置滑块脚本的 Playwright 形态：headless 或 agent-browser（有头，仍由脚本自动拖滑块）。"""
    s = str(raw or "headless").strip().lower().replace("_", "-")
    if s in ("agent-browser", "visible", "headed"):
        return "agent-browser"
    return "headless"


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
        self.deferred_retry_skip_blocked_ratio_percent = _positive_int_config(
            self._source_config.get("deferred_retry_skip_blocked_ratio_percent"),
            default=85,
        )
        self.deferred_retry_skip_blocked_ratio_percent = max(
            1,
            min(100, int(self.deferred_retry_skip_blocked_ratio_percent)),
        )
        if self.http_only_mode:
            self.risk_verification_command = ""
            self.risk_verification_auto_solver = False
            self.risk_verification_agent_browser_retry = False
            self.retry_on_risk_enabled = False
            self.browser_fallback_enabled = False
            self.search_listing_playwright_enabled = False
        self._last_request_at = 0.0
        self._last_request_by_url: dict[str, float] = {}
        self._last_risk_detected_at = 0.0
        self._risk_cooldown_until = 0.0

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        refs: list[RawArticleRef] = []
        deferred_pages: list[dict[str, str]] = []
        excluded_channels = {KR36_TOPICS_URL.rstrip("/"), KR36_ACTIVITY_URL.rstrip("/")}
        effective_channel_urls = tuple(
            url
            for url in self.channel_urls
            if str(url or "").strip().rstrip("/") not in excluded_channels
        )
        search_rounds = len(KR36_SEARCH_CATEGORY_KEYWORDS) if self.search_listing_enabled else 0
        rounds_total = 2 + search_rounds + len(effective_channel_urls)
        round_index = 0
        _append_kr36_debug_log(
            f"[kr36] listing_begin report_date={report_date.isoformat()} rounds_total={rounds_total} "
            f"candidate_limit={self.candidate_limit}"
        )

        # 1) 专题页：全量抓取，不受日期和 candidate_limit 约束。
        round_index += 1
        started_at = time.time()
        _append_kr36_debug_log(
            f"[kr36] round_start page={round_index} round={round_index}/{rounds_total} "
            f"stage=topics url={KR36_TOPICS_URL}"
        )
        _append_kr36_stage_log(action="进入", stage_name="专题页", data_count=0, total_refs=len(refs))
        topics_html = self._fetch_text(KR36_TOPICS_URL)
        if _looks_like_captcha_or_block(topics_html):
            deferred_pages.append({"stage": "topics", "category": "专题", "url": KR36_TOPICS_URL})
            _append_kr36_debug_log(
                f"[kr36] page_result page={round_index} stage=topics status=deferred_blocked "
                f"url={KR36_TOPICS_URL} fetched=0 total_refs={len(refs)}"
            )
            topics_html = ""
        topic_items = parse_topics_listing_html(
            topics_html,
            base_url=KR36_ROOT,
            listing_url=KR36_TOPICS_URL,
            report_date=report_date,
        )
        refs.extend(topic_items)
        _append_kr36_debug_log(
            f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} stage=topics status=ok "
            f"fetched={len(topic_items)} total_refs={len(refs)} elapsed_ms={int((time.time() - started_at) * 1000)}"
        )
        _append_kr36_stage_log(action="完成", stage_name="专题页", data_count=len(topic_items), total_refs=len(refs))
        if round_index < rounds_total:
            _append_kr36_debug_log(
                f"[kr36] round_next round={round_index + 1}/{rounds_total} stage=activity"
            )

        # 2) 活动页：全量抓取，不受日期和 candidate_limit 约束。
        round_index += 1
        started_at = time.time()
        _append_kr36_debug_log(
            f"[kr36] round_start page={round_index} round={round_index}/{rounds_total} "
            f"stage=activity url={KR36_ACTIVITY_URL}"
        )
        _append_kr36_stage_log(action="进入", stage_name="活动页", data_count=0, total_refs=len(refs))
        activity_html = self._fetch_text(KR36_ACTIVITY_URL)
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
        # 3) 指定栏目：每个资讯子类独立取搜索结果前 5 篇（可通过配置关闭）。
        info_counts: dict[str, int] = {}
        info_total_count = 0
        if self.search_listing_enabled:
            for category, keyword in KR36_SEARCH_CATEGORY_KEYWORDS:
                round_index += 1
                search_url = f"{KR36_SEARCH_BASE}{quote(keyword)}"
                started_at = time.time()
                _append_kr36_debug_log(
                    f"[kr36] round_start page={round_index} round={round_index}/{rounds_total} stage=search "
                    f"category={category} url={search_url}"
                )
                _append_kr36_stage_log(action="进入", stage_name=f"搜索页({category})", data_count=0, total_refs=len(refs))
                html = self._fetch_text(search_url)
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
                ):
                    if info_counts.get(category, 0) >= self.info_per_category_limit:
                        break
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
                        return refs
                _append_kr36_debug_log(
                    f"[kr36] round_end page={round_index} round={round_index}/{rounds_total} "
                    f"stage=search category={category} status=ok "
                    f"fetched={len(refs) - before_count} total_refs={len(refs)} elapsed_ms={int((time.time() - started_at) * 1000)}"
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

        # 4) 其余频道入口继续抓取。
        for channel_url in effective_channel_urls:
            round_index += 1
            started_at = time.time()
            _append_kr36_debug_log(
                f"[kr36] round_start page={round_index} round={round_index}/{rounds_total} "
                f"stage=channel url={channel_url}"
            )
            _append_kr36_stage_log(action="进入", stage_name="频道页", data_count=0, total_refs=len(refs))
            html = self._fetch_text(channel_url)
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
                    return refs
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
                        parse_topics_listing_html(
                            retry_html,
                            base_url=KR36_ROOT,
                            listing_url=retry_url,
                            report_date=report_date,
                        )
                    )
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
                    for item in parse_search_listing_html(
                        retry_html,
                        base_url=KR36_ROOT,
                        listing_url=retry_url,
                        report_date=report_date,
                        fixed_category=category or None,
                    ):
                        if category and info_counts.get(category, 0) >= self.info_per_category_limit:
                            break
                        refs.append(item)
                        if category:
                            info_counts[category] = info_counts.get(category, 0) + 1
                        info_total_count += 1
                        if info_total_count >= self.candidate_limit:
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
                if len(refs) >= self.candidate_limit or info_total_count >= self.candidate_limit:
                    _append_kr36_debug_log(
                        f"[kr36] deferred_retry_stopped_by_limit total_refs={len(refs)} info_total_count={info_total_count}"
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

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        # step4 会重新抓原文，这里仅保留 step1/2 所需的结构化元数据。
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
            "risk_verification_auto_solver": False,
            "risk_verification_wait_ms": 3000,  # 进入风控后、执行 verification 命令前的等待（毫秒）
            "risk_verification_playwright_mode": "headless",
            "risk_verification_agent_browser_retry": False,
            "request_interval_ms": 22000,
            "request_interval_jitter_ms": 3000,
            "request_interval_min_ms": 15000,
            "request_interval_max_ms": 30000,
            "request_interval_choices_ms": [],
            "same_url_cooldown_ms": 0,
            "info_per_category_limit": 5,
            "search_listing_enabled": True,
            "browser_fallback_enabled": False,
            "browser_fallback_headless": False,
            "browser_verification_timeout_ms": 180000,
            "browser_verification_poll_ms": 2000,
            "persist_browser_cookies": True,
            "search_listing_playwright_enabled": False,
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
            "deferred_retry_skip_blocked_ratio_percent": 85,
        }

    def _fetch_text(self, url: str) -> str:
        """
        默认仅 HTTP 抓取；命中风控时若配置了 risk_verification_command，或启用
        risk_verification_auto_solver（默认）且存在 scripts/kr36_risk_slider_solver.py，
        则先等待 risk_verification_wait_ms 再执行内置易盾滑块（Playwright + OpenCV，默认无头），随后再 curl；
        若主模式为 headless 且仍不可用，且 risk_verification_agent_browser_retry 为真，则同一脚本以
        agent-browser（有头 Chromium，仍自动拖滑块）再试一次。

        search/articles：在无头列表抓取仍异常（风控小页等）时，同样走上述滑块子进程后再无头重试。
        """
        self._wait_before_next_request(url)
        _append_kr36_debug_log(f"[kr36] fetch_start url={url}")
        html = self._curl_text(url)
        if _is_usable_html(html):
            fetch_method = "curl"
            if self._should_enrich_kr36_search_with_playwright(url, html):
                enriched = self._kr36_enrich_search_html_via_playwright_and_slider(url)
                if enriched:
                    html = enriched
                    fetch_method = "curl_playwright_search"
                    _append_kr36_debug_log(
                        f"[kr36] search_listing_csr_enriched url={url} html_length={len(html)}"
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
                f"has_verification_command={str(has_cmd).lower()}"
            )
            if has_cmd:
                wait_ms = max(0, int(self.risk_verification_wait_ms))
                print(
                    f"[kr36] 已进入风控页面：{url}\n"
                    f"[kr36] {wait_ms / 1000:.1f}s 后启动自动验证（自定义命令或内置易盾滑块）。"
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
                        _append_kr36_debug_log(
                            f"[kr36] fetch_ok method=verification_command url={url} html_length={len(html)}"
                        )
                        return html
                if self._should_retry_builtin_solver_agent_browser():
                    print(
                        "[kr36] 首次自动滑块未恢复可用页面，切换 agent-browser（可见 Chromium，仍由脚本自动拖动）重试…"
                    )
                    _append_kr36_debug_log(
                        f"[kr36] risk_verification_agent_browser_retry url={url} verify_ok={str(verify_ok).lower()}"
                    )
                    verify_ok2 = self._run_risk_verification(url, playwright_mode="agent-browser")
                    if verify_ok2:
                        self._human_pause(self.human_min_pause_ms, self.human_max_pause_ms)
                        self._wait_before_next_request(url)
                        html = self._curl_text(url)
                        if _is_usable_html(html):
                            _append_kr36_debug_log(
                                f"[kr36] fetch_ok method=verification_agent_browser url={url} "
                                f"html_length={len(html)}"
                            )
                            return html
            else:
                _append_kr36_debug_log(f"[kr36] risk_detected method=curl url={url} no_auto_verify_command=1")
                if self.http_only_mode and not self.retry_on_risk_enabled:
                    print(f"[kr36] 命中风控页，已记录并将在首轮结束后重试：{url}")
                    return html
                print(
                    "[kr36] 检测到风控页面，且未配置自动验证（risk_verification_command / "
                    "risk_verification_auto_solver + scripts/kr36_risk_slider_solver.py）。"
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
        return bool(self.risk_verification_auto_solver and KR36_SLIDER_SOLVER_SCRIPT.is_file())

    def _should_retry_builtin_solver_agent_browser(self) -> bool:
        """无头内置滑块已跑过仍失败时，是否再用同一脚本以 agent-browser（有头）自动重试。"""
        if not self.risk_verification_agent_browser_retry:
            return False
        if str(self.risk_verification_command or "").strip():
            return False
        if not (self.risk_verification_auto_solver and KR36_SLIDER_SOLVER_SCRIPT.is_file()):
            return False
        return self.risk_verification_playwright_mode == "headless"

    def _run_risk_verification(self, url: str, *, playwright_mode: str | None = None) -> bool:
        env = os.environ.copy()
        env["KR36_RISK_URL"] = url
        command = str(self.risk_verification_command or "").strip()
        if command:
            _append_kr36_debug_log(f"[kr36] verification_command_start url={url} command={command}")
            print(f"[kr36] 正在执行风控验证命令：{command}")
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    check=False,
                    env=env,
                )
            except Exception as error:
                _append_kr36_debug_log(f"[kr36] verification_command_error url={url} error={error}")
                print(f"[kr36] 风控验证命令异常：{error}")
                return False
        elif self.risk_verification_auto_solver and KR36_SLIDER_SOLVER_SCRIPT.is_file():
            mode = playwright_mode or self.risk_verification_playwright_mode
            argv = [sys.executable, str(KR36_SLIDER_SOLVER_SCRIPT)]
            if mode == "headless":
                argv.append("--headless")
            label = (
                "Playwright 无头 + OpenCV 自动拖动"
                if mode == "headless"
                else "agent-browser（有头 Chromium + OpenCV 自动拖动）"
            )
            _append_kr36_debug_log(
                f"[kr36] verification_command_start url={url} playwright_mode={mode} auto_solver_argv={argv!r}"
            )
            print(f"[kr36] 正在执行内置易盾滑块验证（{label}）：{' '.join(argv)}")
            try:
                completed = subprocess.run(argv, check=False, env=env)
            except Exception as error:
                _append_kr36_debug_log(f"[kr36] verification_command_error url={url} error={error}")
                print(f"[kr36] 风控验证命令异常：{error}")
                return False
        else:
            return False
        ok = completed.returncode == 0
        _append_kr36_debug_log(
            f"[kr36] verification_command_end url={url} return_code={completed.returncode} ok={str(ok).lower()}"
        )
        if not ok:
            if completed.returncode == 3:
                print(
                    "[kr36] 内置滑块脚本判定：当前页不是可自动完成的易盾拼图，或仍为拦截页（exit=3）。"
                )
            else:
                print(
                    f"[kr36] 风控验证失败（exit={completed.returncode}）。"
                    "请确认已安装 opencv-python-headless、playwright 且已执行 playwright install chromium。"
                )
        return ok

    def _visible_playwright_fetch_until_deadline(self, url: str, *, event_tag: str) -> str:
        """本机可见 Chromium：轮询直到 HTML 可用或超时；按配置写回 cookies。"""

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            _append_kr36_debug_log(
                f"[kr36] {event_tag}_unavailable missing_playwright "
                "hint='python -m playwright install chromium'"
            )
            return ""

        html = ""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context = browser.new_context(
                    locale="zh-CN",
                    user_agent=KR36_DEFAULT_USER_AGENT,
                    viewport={"width": 1440, "height": 1024},
                )
                _load_kr36_cookies_into_browser_context(context)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout_ms)
                page.wait_for_timeout(self.browser_wait_after_load_ms)

                deadline = time.time() + (self.browser_verification_timeout_ms / 1000)
                while time.time() < deadline:
                    html = page.content()
                    if _is_usable_html(html):
                        page.wait_for_timeout(self.browser_wait_after_load_ms)
                        html = page.content()
                        if self.persist_browser_cookies:
                            save_kr36_cookies(extract_kr36_cookie_values(context.cookies()))
                        _append_kr36_debug_log(
                            f"[kr36] {event_tag}_ok url={url} html_length={len(html)}"
                        )
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
        搜索页先无头渲染；若仍拿不到正常列表体（空、风控小页），则与 curl 风控一致：
        等待后执行内置滑块；主模式为 headless 且仍异常时，可再试 agent-browser（有头、仍自动拖滑块）。
        """
        enriched = self._playwright_kr36_search_listing_html(url)
        if enriched and _kr36_search_playwright_body_acceptable(enriched):
            return enriched
        if not self._has_risk_verification_action():
            _append_kr36_debug_log(
                f"[kr36] search_enrich_unacceptable_no_slider url={url} "
                f"html_length={len(enriched or '')}"
            )
            return ""
        _append_kr36_debug_log(f"[kr36] search_slider_bypass_start url={url}")
        print(
            "[kr36] 搜索无头页未拿到可用列表（可能为风控），"
            f"{self.risk_verification_wait_ms / 1000:.1f}s 后执行自动滑块并重试无头抓取…"
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
                    f"[kr36] search_slider_bypass_ok url={url} html_length={len(enriched2)}"
                )
                return enriched2
        else:
            _append_kr36_debug_log(f"[kr36] search_slider_bypass_solver_failed url={url}")
        if self._should_retry_builtin_solver_agent_browser():
            print(
                "[kr36] 搜索列表在自动滑块后仍异常，切换 agent-browser（可见 Chromium，仍由脚本自动拖动）重试…"
            )
            _append_kr36_debug_log(f"[kr36] search_agent_browser_retry url={url}")
            verify_ok2 = self._run_risk_verification(url, playwright_mode="agent-browser")
            if verify_ok2:
                self._human_pause(self.human_min_pause_ms, self.human_max_pause_ms)
                self._wait_before_next_request(url)
                enriched2 = self._playwright_kr36_search_listing_html(url)
                if enriched2 and _kr36_search_playwright_body_acceptable(enriched2):
                    _append_kr36_debug_log(
                        f"[kr36] search_agent_browser_ok url={url} html_length={len(enriched2)}"
                    )
                    return enriched2
        _append_kr36_debug_log(
            f"[kr36] search_slider_bypass_still_bad url={url} html_length={len(enriched2 or '')}"
        )
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
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
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
            "curl", "-sS", url,
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
) -> list[RawArticleRef]:
    """按 search/articles 结果抓取，调用方负责限制每个子类的条数。"""

    refs: list[RawArticleRef] = []
    seen_url: set[str] = set()

    def append_ref(href: str, title_raw: str) -> None:
        href = str(href or "").strip()
        title = clean_html_text(title_raw or "")
        if not href or not title:
            return
        url = href if href.startswith("http") else urljoin(base_url, href)
        if url in seen_url:
            return
        seen_url.add(url)
        refs.append(
            RawArticleRef(
                source_site="36kr",
                article_id=f"36kr:{url.rsplit('/', 1)[-1]}",
                title=title,
                url=url,
                published_at=report_date.isoformat(),
                channel=fixed_category,
                source_bucket=fixed_category,
                summary="",
                metadata={"listing_url": listing_url},
            )
        )

    for match in KR36_SEARCH_TITLE_LINK_RE.finditer(html):
        append_ref(match.group("href"), match.group("title"))
    for match in KR36_LINK_RE.finditer(html):
        append_ref(match.group("href"), match.group("title"))
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


def parse_activity_listing_html(
    html: str,
    *,
    base_url: str,
    listing_url: str,
    report_date: date,
) -> list[RawArticleRef]:
    """活动页全量抓取，不按发布时间过滤。优先匹配 class=activity-item 卡片。"""

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
        start_label = build_activity_start_label(status, date_range, report_date)

        summary_parts: list[str] = []
        if date_range:
            summary_parts.append(f"时间: {date_range}")
        if city:
            summary_parts.append(f"地点: {city}")
        if theme:
            summary_parts.append(f"主题: {theme}")
        if start_label:
            summary_parts.append(start_label)

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
        start_label = build_activity_start_label(status, date_range, report_date)

        summary_parts: list[str] = []
        if date_range:
            summary_parts.append(f"时间: {date_range}")
        if city:
            summary_parts.append(f"地点: {city}")
        if theme:
            summary_parts.append(f"主题: {theme}")
        if start_label:
            summary_parts.append(start_label)

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
        return "已结束"
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
    lowered = (html or "")[:4000].lower()
    if not lowered:
        return True
    risk_tokens = (
        "captcha",
        "verify",
        "人机验证",
        "请完成验证",
        "访问受限",
        "异常流量",
        "security check",
        "\u5b8c\u6210\u9a8c\u8bc1\u540e\u7ee7\u7eed",
        "\u62d6\u52a8\u5b8c\u6210\u4e0a\u65b9\u62fc\u56fe",
        "\u6309\u4f4f\u5de6\u8fb9\u6309\u94ae\u62d6\u52a8",
    )
    return any(token in lowered for token in risk_tokens)


def _is_usable_html(html: str) -> bool:
    if not html:
        return False
    lowered = html.lower()
    if "<html" not in lowered:
        return False
    return not _looks_like_captcha_or_block(html)


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
    return KR36_DEBUG_LOG_FILE
