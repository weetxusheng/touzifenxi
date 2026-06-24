from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests

from gzh_pipeline.audit.trace import TraceRecorder, monotonic_ms
from gzh_pipeline.constants import BASE_URL, ENDPOINT_PRICES


@dataclass
class CostTracker:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def record(self, account: str, endpoint: str, count: int = 1) -> None:
        price = ENDPOINT_PRICES.get(endpoint, 0.0)
        self.rows.append(
            {
                "account": account,
                "endpoint": endpoint,
                "count": count,
                "unit_price": price,
                "estimated_cost": round(price * count, 4),
            }
        )

    def summary(self) -> dict[str, Any]:
        accounts: dict[str, dict[str, Any]] = {}
        total_calls = 0
        total_cost = 0.0
        for row in self.rows:
            account = row["account"]
            count = row["count"]
            cost = row["estimated_cost"]
            total_calls += count
            total_cost += cost
            account_summary = accounts.setdefault(account, {"calls": 0, "estimated_cost": 0.0, "endpoints": {}})
            account_summary["calls"] += count
            account_summary["estimated_cost"] = round(account_summary["estimated_cost"] + cost, 4)
            account_summary["endpoints"][row["endpoint"]] = account_summary["endpoints"].get(row["endpoint"], 0) + count
        return {
            "total_calls": total_calls,
            "total_estimated_cost": round(total_cost, 4),
            "accounts": accounts,
            "rows": self.rows,
        }


class DajialaClient:
    def __init__(
        self,
        api_key: str,
        verify_code: str = "",
        session: Any | None = None,
        timeout: int = 30,
        trace: TraceRecorder | None = None,
        http_label_prefix: str = "",
    ):
        self.api_key = api_key
        self.verify_code = verify_code
        self.session = session or requests.Session()
        self.timeout = timeout
        self._trace = trace
        self._http_label_prefix = http_label_prefix

    def _label(self, name: str) -> str:
        p = self._http_label_prefix
        return f"{p}{name}" if p else name

    def get_remain_money(self) -> float | None:
        payload = self._post("/monitor/v3/get_remain_money", {"key": self.api_key})
        data = payload.get("data")
        if isinstance(data, dict):
            return _to_float(data.get("remain_money"))
        return _to_float(payload.get("remain_money"))

    def fetch_history(self, target: str, page: int = 1) -> list[dict[str, Any]]:
        """
        历史发文列表（分页）。

        请求体 ``biz`` / ``url`` / ``name`` 三选一（优先 biz > url > name），与 Apifox 文档一致：
        ``name``=公众号名称或微信 id；``url``=文章链接；``biz``=biz 串。
        """
        body = _build_post_history_body(
            self.api_key,
            target,
            page=page,
            verify_code=self.verify_code or None,
        )
        payload = self._post_json("/monitor/v3/post_history", body)
        return normalize_articles(payload)

    def fetch_post_condition(self, target: str) -> list[dict[str, Any]]:
        """
        当天实时发文（文档：``post_condition``）。

        请求体为 JSON，``biz`` / ``url`` / ``name`` 三选一（优先 biz > url > name）。
        ``name`` 可填公众号名称或微信 id；``url`` 为文章链接。
        """
        body = _build_post_condition_body(self.api_key, target, verify_code=self.verify_code or None)
        payload = self._post_json("/monitor/v3/post_condition", body)
        return normalize_articles(payload)

    def fetch_latest(self, account: str) -> list[dict[str, Any]]:
        """兼容别名：等同 ``fetch_post_condition``。"""
        return self.fetch_post_condition(account)

    def fetch_article_html_from_url(self, url: str) -> str:
        """通过文章链接 GET 微信页（默认整页响应，见 ``GZH_DAJIALA_URL_FETCH_MODE``）。"""
        from gzh_pipeline.dajiala.url_fetch import fetch_weixin_article_by_mode, parse_url_fetch_mode

        mode = getattr(self, "url_fetch_mode", None) or parse_url_fetch_mode()
        return fetch_weixin_article_by_mode(self.session, url, mode=mode, timeout=self.timeout)

    def fetch_article_html(self, url: str) -> str:
        payload = self._post("/monitor/v3/article_html", {"key": self.api_key, "url": url})
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("html", "content", "article_html", "content_html"):
                if data.get(key):
                    return str(data[key])
        if isinstance(data, str):
            return data
        for key in ("html", "content", "article_html", "content_html"):
            if payload.get(key):
                return str(payload[key])
        return ""

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        payload_data = dict(data)
        if self.verify_code:
            payload_data = {**payload_data, "verifycode": self.verify_code}
        url = f"{BASE_URL}{path}"
        t0 = time.monotonic()
        label = self._label(path.strip("/").replace("/", "_"))
        try:
            response = self.session.post(url, data=payload_data, timeout=self.timeout)
        except Exception as exc:
            if self._trace:
                self._trace.add_http_request(
                    label,
                    "POST",
                    url,
                    request_headers=None,
                    request_body=payload_data,
                    response_status=0,
                    response_body=None,
                    elapsed_ms=monotonic_ms(t0),
                    ok=False,
                    err=str(exc),
                )
            raise
        elapsed = monotonic_ms(t0)
        try:
            payload = response.json()
        except Exception:
            payload = {"_non_json_body": response.text[:8000] if response.text else ""}
        http_ok = response.ok
        biz_ok = True
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            http_ok = False
            biz_ok = False
        if self._trace:
            self._trace.add_http_request(
                label,
                "POST",
                url,
                request_headers=dict(response.request.headers) if response.request else None,
                request_body=payload_data,
                response_status=response.status_code,
                response_body=payload,
                elapsed_ms=elapsed,
                ok=http_ok and biz_ok,
                err=None if (http_ok and biz_ok) else (response.reason or "api_code"),
            )
        response.raise_for_status()
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"Dajiala API error: {payload.get('msg') or payload}")
        return payload if isinstance(payload, dict) else {"data": payload}

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """``post_condition`` 等接口要求 ``application/json`` 请求体。"""
        url = f"{BASE_URL}{path}"
        t0 = time.monotonic()
        label = self._label(path.strip("/").replace("/", "_"))
        headers = {"Content-Type": "application/json"}
        try:
            response = self.session.post(url, json=body, headers=headers, timeout=self.timeout)
        except Exception as exc:
            if self._trace:
                self._trace.add_http_request(
                    label,
                    "POST",
                    url,
                    request_headers=headers,
                    request_body=body,
                    response_status=0,
                    response_body=None,
                    elapsed_ms=monotonic_ms(t0),
                    ok=False,
                    err=str(exc),
                )
            raise
        elapsed = monotonic_ms(t0)
        try:
            payload = response.json()
        except Exception:
            payload = {"_non_json_body": response.text[:8000] if response.text else ""}
        http_ok = response.ok
        biz_ok = True
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            http_ok = False
            biz_ok = False
        if self._trace:
            self._trace.add_http_request(
                label,
                "POST",
                url,
                request_headers=headers,
                request_body=body,
                response_status=response.status_code,
                response_body=payload,
                elapsed_ms=elapsed,
                ok=http_ok and biz_ok,
                err=None if (http_ok and biz_ok) else (response.reason or "api_code"),
            )
        response.raise_for_status()
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"Dajiala API error: {payload.get('msg') or payload}")
        return payload if isinstance(payload, dict) else {"data": payload}


def _classify_dajiala_target(value: str) -> str:
    """返回 ``post_condition`` / ``post_history`` 应使用的字段名：``url`` | ``biz`` | ``name``。"""
    v = (value or "").strip()
    if not v:
        return "name"
    if v.startswith(("http://", "https://")) or "mp.weixin.qq.com" in v:
        return "url"
    if "__biz=" in v and not v.startswith(("http://", "https://")):
        return "biz"
    return "name"


def _build_dajiala_target_body(
    api_key: str,
    target: str,
    *,
    verify_code: str | None = None,
    page: int | None = None,
) -> dict[str, Any]:
    kind = _classify_dajiala_target(target)
    body: dict[str, Any] = {"key": api_key, "biz": "", "url": "", "name": ""}
    body[kind] = target.strip()
    if page is not None:
        body["page"] = page
    if verify_code:
        body["verifycode"] = verify_code
    return body


def _build_post_condition_body(
    api_key: str,
    target: str,
    *,
    verify_code: str | None = None,
) -> dict[str, Any]:
    return _build_dajiala_target_body(api_key, target, verify_code=verify_code)


def _build_post_history_body(
    api_key: str,
    target: str,
    *,
    page: int = 1,
    verify_code: str | None = None,
) -> dict[str, Any]:
    return _build_dajiala_target_body(api_key, target, verify_code=verify_code, page=page)


def normalize_articles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    candidates: Any = data
    if isinstance(data, dict):
        for key in ("list", "rows", "items", "articles", "article_list", "data"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def article_post_date_iso(article: dict[str, Any]) -> str:
    """从 ``post_time_str`` 或 ``post_time`` 时间戳得到 ``YYYY-MM-DD``；无法解析则返回空串。"""
    raw_str = str(article.get("post_time_str") or "").strip()
    if len(raw_str) >= 10 and raw_str[4:5] == "-" and raw_str[7:8] == "-":
        return raw_str[:10]
    ts = article.get("post_time")
    if ts is not None and str(ts).strip() != "":
        try:
            sec = float(ts)
            if sec > 1e12:
                sec /= 1000.0
            return datetime.fromtimestamp(sec).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    return ""


def _filter_articles_by_date(articles: list[dict[str, Any]], target_date: str) -> list[dict[str, Any]]:
    return [a for a in articles if article_post_date_iso(a) == target_date]


def history_page_should_stop_paging(articles: list[dict[str, Any]], target_date: str) -> bool:
    """
    ``post_history`` 从新到旧翻页时是否可停止。

    当该页**最早**发文日已早于 ``target_date``，说明目标日文章已扫完。空页可停。
    页内全无日期时不停止，避免误停。
    """
    if not articles:
        return True
    dates = [d for d in (article_post_date_iso(a) for a in articles) if d]
    if not dates:
        return False
    return min(dates) < target_date


def _looks_like_identifier(value: str) -> bool:
    return (
        value.startswith(("http://", "https://", "gh_"))
        or bool(re.fullmatch(r"[A-Za-z0-9_-]{5,}", value))
        or "mp.weixin.qq.com" in value
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def balance_cost(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(before - after, 4)
