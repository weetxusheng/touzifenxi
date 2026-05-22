from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from gzh_pipeline.audit.export_trace import article_urls_preview, write_export_account_trace
from gzh_pipeline.audit.trace import TraceRecorder
from gzh_pipeline.dajiala.client import (
    CostTracker,
    balance_cost,
    normalize_articles,
    history_page_should_stop_paging,
)
from gzh_pipeline.dajiala.client import _filter_articles_by_date  # noqa: PLC2701
from gzh_pipeline.dajiala.body_fetch import (
    BodySource,
    fetch_article_bodies_parallel,
    parse_body_sources,
    write_export_html_files,
)
from gzh_pipeline.dajiala.url_fetch import parse_url_fetch_mode
from gzh_pipeline.util.text import biz_date_for_path, parse_biz_date, safe_filename


@dataclass
class BatchExporter:
    client: Any
    output_dir: str | Path
    dry_run: bool = False
    sleep_seconds: float = 0.0
    progress: bool = False
    biz_date: str | None = None
    audit_json_root: Path | None = None
    run_id: str | None = None
    body_sources: frozenset[BodySource] | None = None
    url_fetch_mode: str | None = None
    max_audit_body_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        raw = self.biz_date.strip() if self.biz_date else None
        self.biz_date = biz_date_for_path(raw or date.today())
        self.costs = CostTracker()
        if self.body_sources is None:
            self.body_sources = parse_body_sources(None)
        if self.url_fetch_mode is None:
            self.url_fetch_mode = parse_url_fetch_mode()

    def export(
        self, accounts: Iterable[str], mode: str = "latest", max_pages: int = 1, target_date: str | None = None
    ) -> dict[str, Any]:
        accounts = list(accounts)
        before_balance = self.client.get_remain_money()
        if self.progress:
            print(f"Start balance: {before_balance}", file=sys.stderr)
        results: dict[str, Any] = {
            "accounts": {},
            "before_balance": before_balance,
            "after_balance": None,
            "run_id": self.run_id,
            "biz_date": self.biz_date,
            "audit_json_root": str(self.audit_json_root) if self.audit_json_root else None,
        }
        trace_paths: list[str] = []

        for account in accounts:
            acct_trace = (
                TraceRecorder(max_body_bytes=self.max_audit_body_bytes)
                if self.audit_json_root and self.run_id
                else None
            )
            prev_client_trace = getattr(self.client, "_trace", None)
            if acct_trace is not None:
                self.client._trace = acct_trace

            try:
                if acct_trace:
                    acct_trace.add_step(
                        "account_start",
                        f"account:{account}",
                        True,
                        {
                            "account": account,
                            "mode": mode,
                            "max_pages": max_pages,
                            "target_date": target_date,
                            "body_sources": sorted(self.body_sources or ()),
                            "url_fetch_mode": self.url_fetch_mode,
                            "output_dir": str(self.output_dir / self.biz_date / safe_filename(account)),
                            "dry_run": self.dry_run,
                            "job_before_balance": before_balance,
                        },
                    )

                articles, fallback, list_stats = self._fetch_articles(
                    account, mode, max_pages, target_date, trace=acct_trace
                )
                exported = self._export_account(account, articles, trace=acct_trace)
                acct_summary = self.costs.summary()
                acct_cost = acct_summary["accounts"].get(account, {})

                acct_result: dict[str, Any] = {
                    "found": len(articles),
                    "exported": exported,
                    "fallback": fallback,
                    "error": None,
                    "trace_path": None,
                }
                if list_stats:
                    acct_result["list_filter_date"] = list_stats.get("filter_date")
                    acct_result["list_raw"] = list_stats.get("raw_count")
                    acct_result["list_matched"] = list_stats.get("matched_count")
                results["accounts"][account] = acct_result

                if acct_trace and self.audit_json_root and self.run_id:
                    acct_trace.add_step(
                        "account_done",
                        f"account:{account}",
                        True,
                        {
                            "found": len(articles),
                            "exported": exported,
                            "fallback": fallback,
                            "estimated_cost": acct_cost.get("estimated_cost"),
                            "endpoints": acct_cost.get("endpoints"),
                        },
                    )
                    trace_path = write_export_account_trace(
                        self.audit_json_root,
                        self.run_id,
                        self.biz_date,
                        account,
                        {
                            "mode": mode,
                            "max_pages": max_pages,
                            "target_date": target_date,
                            "body_sources": sorted(self.body_sources or ()),
                            "url_fetch_mode": self.url_fetch_mode,
                            "output_root": str(self.output_dir),
                            "found": len(articles),
                            "exported": exported,
                            "fallback": fallback,
                            "before_balance": before_balance,
                            "after_balance": None,
                            "account_estimated_cost": acct_cost.get("estimated_cost"),
                            "endpoints": acct_cost.get("endpoints"),
                        },
                        acct_trace.steps,
                    )
                    results["accounts"][account]["trace_path"] = str(trace_path)
                    trace_paths.append(str(trace_path))

            except Exception as exc:
                results["accounts"][account] = {
                    "found": 0,
                    "exported": 0,
                    "fallback": None,
                    "error": str(exc),
                    "trace_path": None,
                }
                if acct_trace:
                    acct_trace.add_step("account_error", f"account:{account}", False, {"account": account}, err=str(exc))
                    if self.audit_json_root and self.run_id:
                        trace_path = write_export_account_trace(
                            self.audit_json_root,
                            self.run_id,
                            self.biz_date,
                            account,
                            {
                                "mode": mode,
                                "error": str(exc),
                                "before_balance": before_balance,
                            },
                            acct_trace.steps,
                        )
                        results["accounts"][account]["trace_path"] = str(trace_path)
                        trace_paths.append(str(trace_path))
            finally:
                if acct_trace is not None:
                    self.client._trace = prev_client_trace

        after_balance = self.client.get_remain_money()
        summary = self.costs.summary()
        results["after_balance"] = after_balance
        results["summary"] = {
            **summary,
            "balance_cost": balance_cost(before_balance, after_balance),
            "job_after_balance": after_balance,
        }
        if trace_paths:
            results["export_trace_paths"] = trace_paths
            results["export_trace_path"] = trace_paths[-1]

        if self.progress:
            print(f"End balance: {after_balance}", file=sys.stderr)
            print(f"Balance cost: {results['summary']['balance_cost']}", file=sys.stderr)
            print(f"Estimated cost: {summary['total_estimated_cost']:.4f}", file=sys.stderr)

        return results

    def _fetch_articles(
        self,
        account: str,
        mode: str,
        max_pages: int,
        target_date: str | None = None,
        *,
        trace: TraceRecorder | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
        if mode == "today":
            day_iso = target_date or date.today().isoformat()
            day = parse_biz_date(day_iso.strip()).isoformat()
            if trace:
                trace.add_step(
                    "list_fetch_start",
                    "post_condition",
                    True,
                    {"account": account, "mode": "today", "filter_date": day},
                )
            self.costs.record(account, "post_condition")
            articles = self.client.fetch_post_condition(account)
            filtered = _filter_articles_by_date(articles or [], day)
            fallback = f"post_condition:{day}"
            if trace:
                trace.add_step(
                    "list_fetch_done",
                    "post_condition",
                    True,
                    {
                        "raw_count": len(articles or []),
                        "filtered_count": len(filtered),
                        "fallback": fallback,
                        "articles": article_urls_preview(filtered),
                    },
                )
            stats = {"filter_date": day, "raw_count": len(articles or []), "matched_count": len(filtered)}
            return filtered, fallback, stats

        if mode == "yesterday":
            day_iso = target_date or date.today().isoformat()
            day = parse_biz_date(day_iso.strip()).isoformat()
            if trace:
                trace.add_step(
                    "list_fetch_start",
                    "post_history",
                    True,
                    {
                        "account": account,
                        "mode": "yesterday",
                        "filter_date": day,
                        "max_pages": max_pages,
                    },
                )
            hist_target = self._post_history_target(account, trace=trace)
            matched: list[dict[str, Any]] = []
            total_raw = 0
            pages_fetched = 0
            for page in range(1, max_pages + 1):
                self.costs.record(account, "post_history")
                page_articles = self.client.fetch_history(hist_target, page) or []
                pages_fetched = page
                if trace:
                    trace.add_step(
                        "list_fetch_page",
                        f"post_history:p{page}",
                        True,
                        {
                            "page": page,
                            "raw_count": len(page_articles),
                            "filter_date": day,
                            "history_target": hist_target,
                        },
                    )
                if not page_articles:
                    break
                total_raw += len(page_articles)
                matched.extend(_filter_articles_by_date(page_articles, day))
                if history_page_should_stop_paging(page_articles, day):
                    break
                if self.sleep_seconds:
                    time.sleep(self.sleep_seconds)
            fallback = f"post_history:{day}"
            if trace:
                trace.add_step(
                    "list_fetch_done",
                    "post_history",
                    True,
                    {
                        "filter_date": day,
                        "pages_fetched": pages_fetched,
                        "total_raw_count": total_raw,
                        "matched_count": len(matched),
                        "fallback": fallback,
                        "articles": article_urls_preview(matched),
                    },
                )
            stats = {"filter_date": day, "raw_count": total_raw, "matched_count": len(matched)}
            return matched, fallback, stats

        if mode == "latest":
            if trace:
                trace.add_step("list_fetch_start", "post_condition", True, {"account": account, "mode": "latest"})
            self.costs.record(account, "post_condition")
            try:
                articles = self.client.fetch_post_condition(account)
                if trace:
                    trace.add_step(
                        "list_fetch_done",
                        "post_condition",
                        True,
                        {"count": len(articles or []), "articles": article_urls_preview(articles or [])},
                    )
                return articles, None, None
            except Exception as first_exc:
                if trace:
                    trace.add_step(
                        "list_fetch_fallback",
                        "post_history",
                        False,
                        {"account": account, "reason": str(first_exc)},
                        err=str(first_exc),
                    )
                try:
                    hist_target = self._post_history_target(account, trace=trace)
                except Exception:
                    raise first_exc from None
                self.costs.record(account, "post_history")
                page_articles = self.client.fetch_history(hist_target, 1)
                if not page_articles:
                    raise first_exc from None
                if trace:
                    trace.add_step(
                        "list_fetch_done",
                        "post_history",
                        True,
                        {
                            "history_target": hist_target,
                            "count": len(page_articles or []),
                            "articles": article_urls_preview(page_articles or []),
                        },
                    )
                return page_articles, "history_page_1", None

        if mode not in ("today", "yesterday", "latest", "all"):
            raise ValueError("mode must be 'today', 'yesterday', 'latest' or 'all'")

        if trace:
            trace.add_step("list_fetch_start", "post_history", True, {"account": account, "mode": "all", "max_pages": max_pages})
        hist_target = self._post_history_target(account, trace=trace)
        articles: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            self.costs.record(account, "post_history")
            page_articles = self.client.fetch_history(hist_target, page)
            if trace:
                trace.add_step(
                    "list_fetch_page",
                    f"post_history:p{page}",
                    True,
                    {"page": page, "count": len(page_articles or []), "history_target": hist_target},
                )
            if not page_articles:
                break
            articles.extend(page_articles)
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)
        if trace:
            trace.add_step(
                "list_fetch_done",
                "post_history",
                True,
                {"total_count": len(articles), "articles": article_urls_preview(articles)},
            )
        return articles, None, None

    def _post_history_target(self, account: str, *, trace: TraceRecorder | None = None) -> str:
        """
        ``post_history`` 定位参数（写入请求体 ``biz`` / ``url`` / ``name`` 之一，见 ``_classify_dajiala_target``）。

        ``accounts.txt`` 里的中文名走 ``name`` 字段，不是 ``url``。
        """
        target = account.strip()
        if trace:
            trace.add_step(
                "history_target",
                target[:120],
                True,
                {"account": account, "history_url_param": target},
            )
        return target

    def _export_account(
        self,
        account: str,
        articles: list[dict[str, Any]],
        *,
        trace: TraceRecorder | None = None,
    ) -> int:
        account_dir = self.output_dir / self.biz_date / safe_filename(account)
        account_dir.mkdir(parents=True, exist_ok=True)
        exported = 0
        seen_urls: set[str] = set()
        used_names: set[str] = set()

        if trace:
            trace.add_step(
                "export_articles_start",
                f"account:{account}",
                True,
                {"article_count": len(articles), "dry_run": self.dry_run},
            )

        for index, article in enumerate(articles, start=1):
            url = article.get("url") or article.get("link") or article.get("content_url")
            title = str(article.get("title") or article.get("digest") or f"article-{index}")
            if not url or url in seen_urls:
                if trace and url and url in seen_urls:
                    trace.add_step("article_skip", title[:80], True, {"reason": "duplicate_url", "url": str(url)})
                continue
            seen_urls.add(str(url))
            filename = _unique_filename(safe_filename(title, f"article-{index}"), used_names)
            article_label = f"{index}:{title[:60]}"

            if self.dry_run:
                if trace:
                    trace.add_step(
                        "article_dry_run",
                        article_label,
                        True,
                        {"title": title, "url": str(url), "filename_stem": filename},
                    )
                exported += 1
                continue

            sources = self.body_sources or frozenset({"dajiala"})
            if "dajiala" in sources:
                self.costs.record(account, "article_html")
            if "url" in sources:
                self.costs.record(account, "article_url_fetch")

            bodies, errors = fetch_article_bodies_parallel(
                self.client,
                str(url),
                sources,
                trace=trace,
                article_label=article_label,
            )
            if errors and set(errors.keys()) >= set(sources):
                if trace:
                    trace.add_step(
                        "article_export_error",
                        article_label,
                        False,
                        {"url": str(url), "errors": errors},
                        err="; ".join(f"{k}: {v}" for k, v in sorted(errors.items())),
                    )
                raise RuntimeError("; ".join(f"{k}: {v}" for k, v in sorted(errors.items())))

            written_paths, body_meta = write_export_html_files(
                account_dir=account_dir,
                filename_stem=filename,
                title=title,
                source_url=str(url),
                bodies=bodies,
                errors=errors,
                sources=sources,
                url_fetch_mode=parse_url_fetch_mode(self.url_fetch_mode),
            )
            for p in written_paths:
                b = p.read_bytes()
                if trace:
                    self._trace_file_write(trace, p, b, account, str(url), sources, body_meta)
            exported += 1
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)

        if trace:
            trace.add_step("export_articles_done", f"account:{account}", True, {"exported": exported})
        return exported

    @staticmethod
    def _trace_file_write(
        trace: TraceRecorder,
        path: Path,
        content: bytes,
        account: str,
        source_url: str,
        sources: frozenset[BodySource],
        body_meta: dict[str, Any],
    ) -> None:
        trace.add_step(
            "file_write",
            f"export:{path.name}",
            True,
            {
                "path": str(path),
                "byte_length": len(content),
                "sha256_hex": hashlib.sha256(content).hexdigest(),
                "account": account,
                "source_url": source_url,
                "body_sources": sorted(sources),
                "body_meta": body_meta,
            },
        )


def _unique_filename(base: str, used_names: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in used_names:
        candidate = f"{base}-{counter}"
        counter += 1
    used_names.add(candidate)
    return candidate


def normalize_articles_pub(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容旧测试名；等价于 ``normalize_articles``。"""
    return normalize_articles(payload)
