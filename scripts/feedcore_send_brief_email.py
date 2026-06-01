"""FeedCore 简报邮件发送脚本。

用法:
    python scripts/feedcore_send_brief_email.py --to a@x.com b@y.com
    python scripts/feedcore_send_brief_email.py --run-dir output/reports/feedcore_report/fetch_xxx
    python scripts/feedcore_send_brief_email.py --to a@x.com --subject "FeedCore 政经简报 2026-05-20"

行为:
- 默认找 `output/reports/feedcore_report/` 下最新的 `fetch_*` 目录里的 `brief.html`（也回退到 brief.md → 单独渲染 html）
- 主题默认从 brief.md 第一行 H1 提取；找不到时用 "FeedCore 简报"
- SMTP 配置走 `.env` 里的 TOUZIFENXI_EMAIL_* 变量（与 c114/chip 共用）

不发邮件失败时直接 raise，保留 traceback 便于 Windows 任务计划事件查看。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.tools.output.email import send_email  # noqa: E402


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip()


def find_latest_brief(reports_root: Path) -> Path | None:
    """返回最新 fetch_* run dir 里的 brief.html (优先) 或 brief.md。"""

    if not reports_root.exists():
        return None
    candidates = sorted(
        (p for p in reports_root.iterdir() if p.is_dir() and p.name.startswith("fetch_")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for run_dir in candidates:
        html = run_dir / "brief.html"
        if html.exists() and html.stat().st_size > 0:
            return html
        md = run_dir / "brief.md"
        if md.exists() and md.stat().st_size > 0:
            return md
    return None


def extract_subject_from_md(md_path: Path, fallback: str = "FeedCore 简报") -> str:
    """从 markdown 首行 H1 取邮件主题。"""

    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip() or fallback
            if line:
                break  # 非空非 H1，直接 fallback
    except OSError:
        pass
    return fallback


def main() -> int:
    _load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="发送最新 FeedCore 简报邮件")
    parser.add_argument(
        "--to",
        nargs="+",
        default=["zx944532395@sina.com"],
        help="收件人邮箱（可多个空格分隔）。默认: zx944532395@sina.com",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="指定 run 目录（包含 brief.html / brief.md）。缺省取最新一份。",
    )
    parser.add_argument(
        "--reports-root",
        default=str(ROOT / "output" / "reports" / "feedcore_report"),
        help="FeedCore 产物根目录。",
    )
    parser.add_argument(
        "--subject",
        default=None,
        help="邮件主题。缺省取 brief.md H1，仍找不到则用 'FeedCore 简报'。",
    )
    args = parser.parse_args()

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        html_path = run_dir / "brief.html"
        if not html_path.exists():
            print(f"[FeedCore mail] ERROR: {html_path} 不存在", file=sys.stderr)
            return 2
    else:
        reports_root = Path(args.reports_root).resolve()
        html_path = find_latest_brief(reports_root)
        if html_path is None:
            print(f"[FeedCore mail] ERROR: {reports_root} 下未找到 brief.html / brief.md", file=sys.stderr)
            return 2
        run_dir = html_path.parent

    md_path = run_dir / "brief.md"
    subject = args.subject or extract_subject_from_md(md_path)

    html_text = html_path.read_text(encoding="utf-8")
    body_text = md_path.read_text(encoding="utf-8") if md_path.exists() else "请在 HTML 邮件正文中查看本期简报。"

    print(f"[FeedCore mail] subject: {subject}")
    print(f"[FeedCore mail] run_dir: {run_dir}")
    print(f"[FeedCore mail] html: {html_path}  ({html_path.stat().st_size:,} bytes)")
    print(f"[FeedCore mail] to: {', '.join(args.to)}")

    send_email(
        recipient_emails=args.to,
        subject=subject,
        body_text=body_text,
        body_html=html_text,
    )
    print(f"[FeedCore mail] 发送完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
