"""
Download a 36kr CDN direct URL (e.g. from <video src> or initialState) to a local .mp4.

Examples:
  set PYTHONPATH=src
  python scripts/kr36/download_36krcdn_url.py ^
    "https://videos.36krcdn.com/20260414/v2_1776165984015_video_mp4_v11" ^
    output/reports/kr36_report/sample.mp4

Uses the same curl/urllib + Referer https://36kr.com/ as topic_media.download_kr36_topic_video.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from kr36.topic_media import (  # noqa: E402
    KR36_VIDEO_DOWNLOAD_USER_AGENT,
    download_kr36_topic_video,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download videos.36krcdn.com / video.36krcdn.com URL to mp4.")
    parser.add_argument("url", help="Full CDN URL (with or without .mp4 suffix).")
    parser.add_argument(
        "output",
        nargs="?",
        default="",
        help="Output .mp4 path (default: output/reports/kr36_report/_cdn_<last_path_segment>.mp4).",
    )
    parser.add_argument(
        "--referer",
        default="https://36kr.com/",
        help="Referer header (default: 36kr PC home).",
    )
    args = parser.parse_args()

    if args.output:
        out = Path(args.output).resolve()
    else:
        tail = str(args.url).rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0] or "video"
        out = (_REPO_ROOT / "output" / "reports" / "kr36_report" / f"_cdn_{tail}.mp4").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    ok, err = download_kr36_topic_video(
        args.url,
        out,
        user_agent=KR36_VIDEO_DOWNLOAD_USER_AGENT,
        referer=str(args.referer).strip() or "https://36kr.com/",
        cookie_header="",
        curl_max_time_seconds=1200,
    )
    if not ok:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 1
    size = out.stat().st_size
    print(f"[OK] {out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
