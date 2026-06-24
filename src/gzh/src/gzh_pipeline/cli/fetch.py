"""CLI：大佳啦批量导出。"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from gzh_pipeline.audit.export_trace import default_export_audit_root
from gzh_pipeline.dajiala.body_fetch import parse_body_sources
from gzh_pipeline.dajiala.client import DajialaClient
from gzh_pipeline.dajiala.export import BatchExporter
from gzh_pipeline.dajiala.fetch_mode import (
    canonical_fetch_mode as _canonical_fetch_mode,
    fetch_mode_from_env as fetch_mode_default_from_env,
    resolve_export_biz_date,
    resolve_fetch_target_date,
)
from gzh_pipeline.util.dotenv_tools import load_dotenv_near_cli
from gzh_pipeline.util.text import biz_date_for_path, parse_accounts, parse_biz_date


def load_accounts(accounts_arg: str | None, accounts_file: str | None) -> list[str]:
    """合并 CLI 与 ``--accounts-file``；文件行遵循 ``load_accounts_from_config``（``#`` 为注释）。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in (accounts_arg,):
        if not raw:
            continue
        for name in parse_accounts(raw):
            if name not in seen:
                seen.add(name)
                out.append(name)
    if accounts_file:
        for name in load_accounts_from_config(Path(accounts_file)):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def default_accounts_config_path() -> Path:
    """
    默认公众号列表配置文件路径。

    优先 ``GZH_DAJIALA_ACCOUNTS_FILE`` / ``DAJIALA_ACCOUNTS_FILE``；
    否则自当前工作目录向上查找 ``config/accounts.txt``；
    再否则返回 ``<cwd>/config/accounts.txt``（可能尚不存在）。
    """
    for key in ("GZH_DAJIALA_ACCOUNTS_FILE", "DAJIALA_ACCOUNTS_FILE"):
        raw = os.getenv(key, "").strip()
        if raw:
            return Path(raw).expanduser()
    cur = Path.cwd().resolve()
    for _ in range(10):
        candidate = cur / "config" / "accounts.txt"
        if candidate.is_file():
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return Path.cwd().resolve() / "config" / "accounts.txt"


def load_accounts_from_config(path: Path | None = None) -> list[str]:
    """从配置文件读取公众号名（``#`` 开头为注释，支持逗号/换行分隔）。"""
    p = path or default_accounts_config_path()
    if not p.is_file():
        return []
    raw = p.read_text(encoding="utf-8")
    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(line)
    return parse_accounts("\n".join(lines))


def resolve_fetch_accounts(accounts_arg: str | None, accounts_file: str | None) -> tuple[list[str], str | None]:
    """
    解析待抓取公众号列表。

    优先级：``--accounts`` / ``--accounts-file`` → 默认配置文件。
    返回 ``(names, config_path_used)``；后者在来自配置文件时非空。
    """
    from_cli = load_accounts(accounts_arg, accounts_file)
    if from_cli:
        used = str(Path(accounts_file).resolve()) if accounts_file and not accounts_arg else None
        return from_cli, used
    cfg = default_accounts_config_path()
    from_cfg = load_accounts_from_config(cfg)
    if from_cfg:
        return from_cfg, str(cfg.resolve())
    return [], str(cfg.resolve()) if cfg else None


def fetch_max_pages_default_from_env() -> int:
    """未传 ``--max-pages`` 时：``GZH_DAJIALA_FETCH_MAX_PAGES``，否则 ``1``。"""
    raw = os.getenv("GZH_DAJIALA_FETCH_MAX_PAGES", "").strip()
    try:
        return max(1, int(raw)) if raw else 1
    except ValueError:
        return 1


def _cli_fetch_mode_type(s: str) -> str:
    m = _canonical_fetch_mode(s)
    if m is None:
        raise argparse.ArgumentTypeError(
            f"invalid fetch mode {s!r}; expected today | yesterday | latest | all "
            f"(see GZH_DAJIALA_FETCH_MODE)"
        )
    return m


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量抓取大佳啦公众号文章并导出 HTML。")
    parser.add_argument("--accounts", help="公众号名称列表（逗号/换行）；不设则读配置文件，见 GZH_DAJIALA_ACCOUNTS_FILE / config/accounts.txt")
    parser.add_argument("--accounts-file", help="公众号列表文件路径；优先级高于默认配置文件")
    parser.add_argument(
        "--mode",
        type=_cli_fetch_mode_type,
        default=argparse.SUPPRESS,
        metavar="{today,yesterday,latest,all}",
        help="today=post_condition 按日筛选；yesterday=post_history 分页抓基准日减 1 天；"
        "latest=最新一页；all=分页历史。不设则读 GZH_DAJIALA_FETCH_MODE，默认 today",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="today/yesterday 的基准日（yesterday 时过滤日=基准日-1），可为 YYYY-MM-DD 或 yyyyMMdd",
    )
    parser.add_argument(
        "--biz-date",
        default=None,
        metavar="DATE",
        help="导出目录下的业务日子目录（yyyyMMdd）；不写则与 --date 同一天；可与 --date 一样写 ISO 或 yyyyMMdd",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=argparse.SUPPRESS,
        help="all/yesterday 的 post_history 最多页数；不设则读 GZH_DAJIALA_FETCH_MAX_PAGES，默认 1",
    )
    parser.add_argument("--output-dir", default="exports", help="HTML 导出根目录，实际路径为 <output-dir>/<biz-date>/<公众号>/")
    parser.add_argument("--dry-run", action="store_true", help="只抓列表并估算，不拉正文")
    parser.add_argument(
        "--body-sources",
        default=argparse.SUPPRESS,
        metavar="SRC",
        help="正文来源：both（默认，并行 dajiala+url 并写 _compare/）| dajiala | url；等同 GZH_DAJIALA_BODY_SOURCES",
    )
    parser.add_argument("--sleep", type=float, default=0.0, help="每次请求后等待秒数")
    parser.add_argument("--json-summary", action="store_true", help="以 JSON 输出汇总结果")
    parser.add_argument(
        "--audit-json-root",
        default=None,
        help="抓取留痕 JSON 根目录（默认 EXPORT_AUDIT_JSON_ROOT 或 PARSE_AUDIT_JSON_ROOT 或 audit_traces）",
    )
    return parser


def print_summary(result: dict) -> None:
    print("抓取完成")
    print(f"调用前余额: {result.get('before_balance')}")
    print(f"调用后余额: {result.get('after_balance')}")
    summary = result["summary"]
    print(f"估算调用次数: {summary['total_calls']}")
    print(f"估算费用: {summary['total_estimated_cost']:.4f} 元")
    if summary.get("balance_cost") is not None:
        print(f"余额差额: {summary['balance_cost']:.4f} 元")
    for account, info in result["accounts"].items():
        cost = summary["accounts"].get(account, {}).get("estimated_cost", 0.0)
        line = f"- {account}: 找到 {info['found']} 篇，导出 {info['exported']} 篇，估算 {cost:.4f} 元"
        list_raw = info.get("list_raw")
        if list_raw is not None and info["found"] == 0 and list_raw > 0:
            line += (
                f"（列表 API 共 {list_raw} 篇，过滤日 {info.get('list_filter_date')} 无匹配，"
                "请核对日期或翻页上限）"
            )
        if info.get("error"):
            line += f"  错误: {info['error']}"
        if info.get("trace_path"):
            line += f"  留痕: {info['trace_path']}"
        print(line)
    if result.get("export_trace_paths"):
        biz = result.get("biz_date") or ""
        print(
            f"抓取留痕目录: {result.get('audit_json_root')}/{biz}/export_{result.get('run_id')}/"
        )


def main(argv: list[str] | None = None) -> int:
    load_dotenv_near_cli(__file__)
    parser = build_parser()
    args = parser.parse_args(argv)
    accounts, accounts_cfg = resolve_fetch_accounts(args.accounts, args.accounts_file)
    if not accounts:
        cfg_hint = accounts_cfg or str(default_accounts_config_path())
        parser.error(
            f"未配置公众号：请创建配置文件并每行写一个名称，或设置环境变量 GZH_DAJIALA_ACCOUNTS_FILE。\n"
            f"  期望路径: {cfg_hint}\n"
            f"  可参考: config/accounts.example.txt\n"
            f"  亦可使用: --accounts 或 --accounts-file"
        )

    api_key = os.getenv("DAJIALA_API_KEY", "").strip()
    if not api_key:
        parser.error("缺少 DAJIALA_API_KEY，请先在 .env 中配置。")

    audit_path = Path(args.audit_json_root) if getattr(args, "audit_json_root", None) else default_export_audit_root()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    max_body = int(os.getenv("AUDIT_STEP_BODY_MAX_BYTES", "2000000"))
    # 任务级余额查询（逐步留痕写在各公众号 trace 的 account_start / summary 中）
    _ = max_body  # TraceRecorder 在各公众号循环内按默认上限创建

    mode = getattr(args, "mode", None) or fetch_mode_default_from_env()
    export_mode, target_iso = resolve_fetch_target_date(mode, args.date)
    max_pages = getattr(args, "max_pages", None)
    if max_pages is None:
        max_pages = fetch_max_pages_default_from_env()

    if not args.json_summary:
        print(f"公众号 ({len(accounts)}): {', '.join(accounts)}")
        if accounts_cfg:
            print(f"列表来源: {accounts_cfg}")
        if mode == "yesterday":
            print(
                f"抓取模式: yesterday（post_history 分页，过滤日 {target_iso}，基准日 {args.date}）"
            )
        else:
            print(f"抓取模式: {mode}（过滤日 {target_iso}，GZH_DAJIALA_FETCH_MODE 或 --mode）")
        print(f"max_pages: {max_pages}（环境变量 GZH_DAJIALA_FETCH_MAX_PAGES 或 CLI --max-pages）")
        body_src = getattr(args, "body_sources", None) or os.getenv("GZH_DAJIALA_BODY_SOURCES", "both")
        print(f"正文来源: {body_src}（GZH_DAJIALA_BODY_SOURCES / --body-sources）")
        biz_preview = (
            resolve_export_biz_date(mode, reference_str=args.date)
            if not getattr(args, "biz_date", None)
            else biz_date_for_path(args.biz_date.strip())
        )
        print(
            f"留痕目录: {audit_path}/{biz_preview}/export_{run_id}/"
            f"（每公众号一个 {{业务日}}_{{公众号}}.trace.json）"
        )
        print()

    from gzh_pipeline.dajiala.url_fetch import parse_url_fetch_mode

    client = DajialaClient(
        api_key,
        verify_code=os.getenv("DAJIALA_VERIFY_CODE", "").strip(),
        trace=None,
    )
    client.url_fetch_mode = parse_url_fetch_mode()  # type: ignore[attr-defined]
    biz_key = (
        biz_date_for_path(args.biz_date.strip())
        if args.biz_date
        else resolve_export_biz_date(mode, reference_str=args.date)
    )
    body_sources_raw = getattr(args, "body_sources", None)
    body_sources = parse_body_sources(body_sources_raw)

    exporter = BatchExporter(
        client,
        args.output_dir,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
        progress=True,
        biz_date=biz_key,
        audit_json_root=audit_path,
        run_id=run_id,
        body_sources=body_sources,
        url_fetch_mode=parse_url_fetch_mode(),
        max_audit_body_bytes=max_body,
    )
    result = exporter.export(accounts, mode=export_mode, max_pages=max_pages, target_date=target_iso)
    if args.json_summary:
        # 可序列化：去掉不可 JSON 的路径对象等
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
