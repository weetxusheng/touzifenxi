"""CLI：聚合解析。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gzh_pipeline.parse import aggregate as aggregate_mod
from gzh_pipeline.parse.aggregate import run_aggregate_job
from gzh_pipeline.parse.inputs import resolve_parse_accounts, resolve_parse_biz_date
from gzh_pipeline.util.dotenv_tools import load_dotenv_near_cli

DEFAULT_PARSE_ACCOUNT_WORKERS = 10
MAX_PARSE_ACCOUNT_WORKERS = 10

_stderr_lock = threading.Lock()


def resolve_parse_account_workers(cli_value: int | None = None) -> int:
    """
    多公众号并行解析的线程上限（1～10）。

    优先 CLI ``--account-workers``，其次环境变量 ``GZH_PARSE_ACCOUNT_WORKERS``，默认 10。
    """
    if cli_value is not None:
        n = int(cli_value)
    else:
        raw = os.environ.get("GZH_PARSE_ACCOUNT_WORKERS", "").strip()
        if not raw:
            n = DEFAULT_PARSE_ACCOUNT_WORKERS
        else:
            try:
                n = int(raw)
            except ValueError:
                n = DEFAULT_PARSE_ACCOUNT_WORKERS
    return max(1, min(MAX_PARSE_ACCOUNT_WORKERS, n))


def _account_job_succeeded(summary: dict) -> bool:
    st = summary.get("status")
    return st == "success" or (st == "skipped" and summary.get("reason") == "idempotent_same_bundle")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="按公众号+业务日聚合 exports 下多篇 HTML 为解析成品；"
        "未传 --biz-date / --account 时业务日由 GZH_DAJIALA_FETCH_MODE 决定（today→当天，yesterday→前一天），处理该日下全部公众号目录。"
    )
    p.add_argument(
        "--biz-date",
        default=None,
        metavar="DATE",
        help="与 exports 下子目录同名：yyyyMMdd 或 YYYY-MM-DD；默认与 GZH_DAJIALA_FETCH_MODE 对齐（today→当天，yesterday→前一天）",
    )
    p.add_argument(
        "--account",
        default=None,
        help="公众号目录名，与 exports 下一致；默认处理该业务日下所有含 .html 的公众号子目录",
    )
    p.add_argument("--input-root", default=None, type=Path, help="抓取根目录，默认 env PARSE_INPUT_ROOT 或 exports")
    p.add_argument("--output-root", default=None, type=Path, help="解析产出根，默认 env PARSE_OUTPUT_ROOT 或 parsed_exports")
    p.add_argument(
        "--audit-json-root",
        default=None,
        type=Path,
        help="解析留痕根目录，默认 env PARSE_AUDIT_JSON_ROOT 或 audit_traces",
    )
    p.add_argument(
        "--output-stem",
        default=None,
        help="成品文件名（不含 .html），默认 env PARSE_AGGREGATE_OUTPUT_STEM 或 summary",
    )
    p.add_argument(
        "--image-mode",
        choices=("remote", "mirror"),
        default=None,
        help="图片策略；默认环境变量 PARSE_IMAGE_MODE=remote|mirror",
    )
    p.add_argument(
        "--account-workers",
        default=None,
        type=int,
        metavar="N",
        help=f"多公众号并行解析线程数（1～{MAX_PARSE_ACCOUNT_WORKERS}，默认 env GZH_PARSE_ACCOUNT_WORKERS 或 {DEFAULT_PARSE_ACCOUNT_WORKERS}）",
    )
    p.add_argument(
        "--idempotent-skip",
        action="store_true",
        help="仅当 exports 与上次解析指纹一致且版本相同则跳过（省时间与 API）；"
        "默认每次运行都会重跑并覆盖 summary 等产出。",
    )
    p.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="将 summary 以 JSON 打印到 stdout")
    return p


def _print_human_summary(bundle: dict) -> None:
    print(f"业务日: {bundle.get('biz_date')}")
    print(f"公众号 ({len(bundle.get('accounts') or [])}): {', '.join(bundle.get('accounts') or [])}")
    workers = bundle.get("account_workers")
    if workers is not None:
        print(f"并行线程: {workers}")
    for row in bundle.get("results") or []:
        account = row.get("account", "?")
        st = row.get("status")
        print(f"- {account}: {st}")
        if st == "success":
            outs = row.get("output_paths")
            if isinstance(outs, list) and outs:
                for path in outs:
                    print(f"    {path}")
            elif row.get("output_path"):
                print(f"    {row['output_path']}")
        elif row.get("reason") or row.get("error"):
            print(f"    {row.get('reason') or row.get('error')}")
    print(f"审计根: {bundle.get('audit_root')}")


def _run_account_job(
    account: str,
    *,
    biz_date: str,
    input_root: Path,
    output_root: Path,
    audit_root: Path,
    output_stem: str,
    force_run: bool,
    image_mode: str | None,
) -> dict:
    aggregate_mod.set_progress_account(account)
    try:
        with _stderr_lock:
            print(f"[gzh-parse] >>> {account}", file=sys.stderr, flush=True)
        summary = run_aggregate_job(
            biz_date=biz_date,
            account=account,
            input_root=input_root,
            output_root=output_root,
            audit_root=audit_root,
            output_stem=output_stem,
            force=force_run,
            image_mode=image_mode,
        )
        summary["account"] = account
        return summary
    finally:
        aggregate_mod.set_progress_account(None)


def _run_accounts_serial(
    accounts: list[str],
    *,
    biz_date: str,
    input_root: Path,
    output_root: Path,
    audit_root: Path,
    output_stem: str,
    force_run: bool,
    image_mode: str | None,
) -> tuple[list[dict], int]:
    results: list[dict] = []
    exit_code = 0
    for account in accounts:
        summary = _run_account_job(
            account,
            biz_date=biz_date,
            input_root=input_root,
            output_root=output_root,
            audit_root=audit_root,
            output_stem=output_stem,
            force_run=force_run,
            image_mode=image_mode,
        )
        results.append(summary)
        if not _account_job_succeeded(summary):
            exit_code = 1
    return results, exit_code


def _run_accounts_parallel(
    accounts: list[str],
    *,
    workers: int,
    biz_date: str,
    input_root: Path,
    output_root: Path,
    audit_root: Path,
    output_stem: str,
    force_run: bool,
    image_mode: str | None,
) -> tuple[list[dict], int]:
    by_account: dict[str, dict] = {}
    exit_code = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_account_job,
                account,
                biz_date=biz_date,
                input_root=input_root,
                output_root=output_root,
                audit_root=audit_root,
                output_stem=output_stem,
                force_run=force_run,
                image_mode=image_mode,
            ): account
            for account in accounts
        }
        for fut in as_completed(futures):
            account = futures[fut]
            try:
                summary = fut.result()
            except Exception as e:  # noqa: BLE001
                summary = {
                    "account": account,
                    "status": "failed",
                    "error": str(e),
                }
            by_account[account] = summary
            if not _account_job_succeeded(summary):
                exit_code = 1
    results = [by_account[a] for a in accounts]
    return results, exit_code


def main(argv: list[str] | None = None) -> int:
    load_dotenv_near_cli(__file__)
    args = build_parser().parse_args(argv)
    input_root = args.input_root or Path(os.environ.get("PARSE_INPUT_ROOT", "exports"))
    output_root = args.output_root or Path(os.environ.get("PARSE_OUTPUT_ROOT", "parsed_exports"))
    audit_root = args.audit_json_root or Path(os.environ.get("PARSE_AUDIT_JSON_ROOT", "audit_traces"))
    stem = (args.output_stem or os.environ.get("PARSE_AGGREGATE_OUTPUT_STEM") or "summary").strip()

    force_run = not args.idempotent_skip

    biz_date = resolve_parse_biz_date(input_root, args.biz_date)
    accounts = resolve_parse_accounts(input_root, biz_date, args.account)
    if not accounts:
        msg = f"未找到可解析源：{input_root / biz_date} 下无含 .html 的公众号子目录"
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({"status": "failed", "biz_date": biz_date, "error": msg}, ensure_ascii=False, indent=2))
        return 1

    max_workers = resolve_parse_account_workers(args.account_workers)
    workers = min(max_workers, len(accounts))
    parallel = workers > 1

    print(
        "[gzh-parse] 已开始运行。若光标停住无新行，多半是：下载正文插图、或对每篇文章/批次调用大模型"
        "（默认单次请求超时可达数分钟）；阶段日志请看下方以 [gzh-parse] 开头的行。",
        file=sys.stderr,
        flush=True,
    )
    mode = f"parallel workers={workers}" if parallel else "serial"
    print(
        f"[gzh-parse] biz_date={biz_date} accounts={len(accounts)} mode={mode}",
        file=sys.stderr,
        flush=True,
    )

    common = {
        "biz_date": biz_date,
        "input_root": input_root,
        "output_root": output_root,
        "audit_root": audit_root,
        "output_stem": stem,
        "force_run": force_run,
        "image_mode": args.image_mode,
    }
    if parallel:
        results, exit_code = _run_accounts_parallel(accounts, workers=workers, **common)
    else:
        results, exit_code = _run_accounts_serial(accounts, **common)

    bundle = {
        "status": "success" if exit_code == 0 else "partial_failed",
        "biz_date": biz_date,
        "accounts": accounts,
        "account_workers": workers,
        "parallel_accounts": parallel,
        "results": results,
        "audit_root": str(audit_root),
    }

    if args.json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human_summary(bundle)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
