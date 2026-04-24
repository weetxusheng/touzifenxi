"""36kr 专题视频：下载（curl + urllib 兜底）、抽取 MP3（ffmpeg）。"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from shutil import which
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 与浏览器一致的全量 UA，过短 UA 在部分 CDN 上会被拒或限流。
KR36_VIDEO_DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def ffmpeg_executable() -> str:
    return which("ffmpeg") or ""


def kr36_video_url_candidates(url: str) -> list[str]:
    """
    36kr CDN 常见无后缀直链；部分环境需带 .mp4 或原链二选一。
    """
    raw = str(url or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for candidate in (raw,):
        base = candidate.split("?", 1)[0].lower()
        host = candidate.lower()
        on_36kr_cdn = (
            "videos.36krcdn.com" in host or "video.36krcdn.com" in host
        )
        if on_36kr_cdn and ".m3u8" not in base:
            if not base.endswith(".mp4"):
                alt = candidate.rstrip("/") + ".mp4"
                for u in (candidate, alt):
                    if u not in seen:
                        seen.add(u)
                        out.append(u)
                continue
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def download_video_curl(
    url: str,
    output_path: Path,
    *,
    user_agent: str,
    referer: str,
    cookie_header: str,
    max_time_seconds: int = 600,
) -> tuple[bool, str]:
    command: list[str] = [
        "curl",
        "-L",
        "--fail",
        "--silent",
        "--show-error",
        "--ssl-no-revoke",
        "--max-time",
        str(max_time_seconds),
        url,
        "-o",
        str(output_path),
        "-H",
        f"user-agent: {user_agent}",
        "-H",
        f"referer: {referer}",
        "-H",
        "accept: */*",
    ]
    if cookie_header:
        command.extend(["-H", f"cookie: {cookie_header}"])
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return False, "curl executable not found"
    ok = completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    err = (completed.stderr or "").strip() or (completed.stdout or "").strip()
    return ok, err


def download_video_urllib(
    url: str,
    output_path: Path,
    *,
    user_agent: str,
    referer: str,
    cookie_header: str,
    max_time_seconds: int = 600,
) -> tuple[bool, str]:
    headers = {
        "User-Agent": user_agent,
        "Referer": referer,
        "Accept": "*/*",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = Request(url, headers=headers, method="GET")
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(float(max_time_seconds))
    try:
        try:
            with urlopen(req) as resp:
                code = resp.getcode()
                if code >= 400:
                    return False, f"HTTP {code}"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as stream:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
        except HTTPError as exc:
            return False, f"HTTP {exc.code}: {exc.reason}"
        except URLError as exc:
            return False, str(exc.reason or exc)
    finally:
        socket.setdefaulttimeout(old_timeout)
    ok = output_path.exists() and output_path.stat().st_size > 0
    return ok, "" if ok else "empty file"


def download_kr36_topic_video(
    url: str,
    output_path: Path,
    *,
    user_agent: str,
    referer: str,
    cookie_header: str,
    curl_max_time_seconds: int = 600,
) -> tuple[bool, str]:
    """
    先 curl，失败或无 curl 时用 urllib 流式下载。
    referer 应传当前视频页 https://36kr.com/video/{id}，利于 CDN 校验。
    返回 (是否成功, 最后一则错误信息)。
    """
    ua = user_agent.strip()
    if len(ua) < 80:
        ua = KR36_VIDEO_DOWNLOAD_USER_AGENT
    last_err = ""
    for attempt_url in kr36_video_url_candidates(url):
        if output_path.exists():
            output_path.unlink(missing_ok=True)
        ok, err = download_video_curl(
            attempt_url,
            output_path,
            user_agent=ua,
            referer=referer,
            cookie_header=cookie_header,
            max_time_seconds=curl_max_time_seconds,
        )
        if ok:
            return True, ""
        last_err = err or "curl failed"
        ok, err_u = download_video_urllib(
            attempt_url,
            output_path,
            user_agent=ua,
            referer=referer,
            cookie_header=cookie_header,
            max_time_seconds=curl_max_time_seconds,
        )
        if ok:
            return True, ""
        last_err = err_u or last_err
    return False, last_err


def extract_mp3_for_speech(
    video_path: Path,
    mp3_path: Path,
    *,
    ffmpeg_bin: str | None = None,
) -> bool:
    """抽取单声道 16kHz MP3，适配语音识别接口常见输入。"""
    exe = ffmpeg_bin or ffmpeg_executable()
    if not exe:
        return False
    command = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(mp3_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode == 0 and mp3_path.exists() and mp3_path.stat().st_size > 0
