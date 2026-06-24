"""就地修复已导出的 ``*.url.html``：懒加载图片/隐藏正文等，不调用大佳啦 API。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gzh_pipeline.dajiala.weixin_media import normalize_weixin_media_html
from gzh_pipeline.util.dotenv_tools import load_dotenv_near_cli


def repair_url_html_file(path: Path, *, dry_run: bool = False) -> dict:
    raw = path.read_text(encoding="utf-8")
    fixed = normalize_weixin_media_html(raw)
    changed = fixed != raw
    if changed and not dry_run:
        path.write_text(fixed, encoding="utf-8")
    return {
        "path": str(path),
        "changed": changed,
        "bytes_before": len(raw.encode("utf-8")),
        "bytes_after": len(fixed.encode("utf-8")),
    }


def discover_url_html_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.url.html"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="批量修复 exports 下已有 *.url.html（data-src→src、正文可见），不重新抓取。"
    )
    p.add_argument(
        "--exports-root",
        type=Path,
        default=Path("exports"),
        help="抓取根目录，默认 exports",
    )
    p.add_argument(
        "--biz-date",
        default=None,
        metavar="DATE",
        help="仅处理 exports/{biz_date}/ 下文件；默认处理 exports 下全部 *.url.html",
    )
    p.add_argument("--dry-run", action="store_true", help="只统计将修改的文件，不写回磁盘")
    p.add_argument("--json", action="store_true", help="以 JSON 输出汇总")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv_near_cli(__file__)
    args = build_parser().parse_args(argv)
    root = args.exports_root.resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 1

    scan_root = root / args.biz_date if args.biz_date else root
    if args.biz_date and not scan_root.is_dir():
        print(f"业务日目录不存在: {scan_root}", file=sys.stderr)
        return 1

    files = discover_url_html_files(scan_root)
    if not files:
        print(f"未找到 *.url.html: {scan_root}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    for fp in files:
        rows.append(repair_url_html_file(fp, dry_run=args.dry_run))

    changed_n = sum(1 for r in rows if r["changed"])
    summary = {
        "exports_root": str(root),
        "scan_root": str(scan_root),
        "dry_run": args.dry_run,
        "total": len(rows),
        "changed": changed_n,
        "unchanged": len(rows) - changed_n,
    }

    if args.json:
        print(json.dumps({**summary, "files": rows}, ensure_ascii=False, indent=2))
    else:
        mode = "（预览）" if args.dry_run else ""
        print(f"扫描 {summary['scan_root']}{mode}")
        print(f"共 {summary['total']} 个 *.url.html，将更新 {summary['changed']} 个，无需改动 {summary['unchanged']} 个")
        for r in rows:
            if r["changed"]:
                print(f"  已修复: {r['path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
