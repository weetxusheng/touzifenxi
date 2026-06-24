"""正文图片扫描、懒加载属性优先级与可选本地镜像。"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from gzh_pipeline.audit.trace import TraceRecorder, monotonic_ms

# 需求 §4.2.1：懒加载多属性时的选用顺序（写入留痕 summary）
IMAGE_SRC_PRIORITY = (
    "data-src",
    "data-original",
    "data-src-url",
    "src",
)

# 插图已镜像到本地文件名后，为「识图/VL」阶段保留的远程 URL（参见 vision_llm.list_http_image_urls）
IMG_ATTR_REMOTE_FOR_VISION = "data-gzh-vision-src"

IFRAME_UNSUPPORTED_NOTE = "正文含 iframe：若内嵌无法静态解析图片，请在微信原文页核对。"


def _pick_img_url(tag) -> tuple[str | None, str]:
    """返回 (url, picked_attr)。"""
    for attr in IMAGE_SRC_PRIORITY:
        v = tag.get(attr)
        if v and str(v).strip() and not str(v).startswith("data:"):
            return str(v).strip(), attr
    return None, ""


def scan_and_rewrite_images(
    body_html: str,
    *,
    source_stem: str,
    assets_dir: Path | None,
    image_mode: str,
    trace: TraceRecorder | None,
    http_session: requests.Session,
    max_image_bytes: int,
    image_index_start: int = 1,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int]:
    """
    返回：(改写后的 body_html, image_records, image_failures, next_index)。
    ``image_mode``: ``remote`` | ``mirror``
    """
    soup = BeautifulSoup(f'<div class="gzh-parse-root">{body_html}</div>', "html.parser")
    root = soup.select_one(".gzh-parse-root")
    if root is None:
        root = soup
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    idx = image_index_start

    for tag in root.find_all("img"):
        url, attr = _pick_img_url(tag)
        alt = tag.get("alt") or ""
        title = tag.get("title") or ""
        snap = {k: tag.get(k) for k in sorted(tag.attrs.keys()) if k.startswith("data-") or k in ("class", "id", "style")}
        rec: dict[str, Any] = {
            "order_in_source": idx,
            "source_file_stem": source_stem,
            "picked_attr": attr,
            "alt": str(alt)[:500],
            "title": str(title)[:500],
            "attrs_snapshot": snap,
            "chosen_url": url,
        }
        if not url:
            rec["status"] = "missing_src"
            failures.append({"source_stem": source_stem, "order": idx, "reason": "no_image_url"})
            idx += 1
            records.append(rec)
            continue

        if image_mode == "remote":
            tag["src"] = url
            for a in ("data-src", "data-original", "data-src-url"):
                if a in tag.attrs:
                    del tag.attrs[a]
            rec["status"] = "remote"
            records.append(rec)
            idx += 1
            continue

        # mirror
        assert assets_dir is not None
        assets_dir.mkdir(parents=True, exist_ok=True)
        ext = _guess_ext(url)
        fname = f"{source_stem}__img_{idx}{ext}"
        local_path = assets_dir / fname
        ok, detail = _download_image(url, local_path, trace, http_session, max_image_bytes)
        if ok:
            tag[IMG_ATTR_REMOTE_FOR_VISION] = url
            tag["src"] = fname
            for a in ("data-src", "data-original", "data-src-url"):
                if a in tag.attrs:
                    del tag.attrs[a]
            rec["status"] = "mirrored"
            rec["local_path"] = str(local_path)
            rec["byte_length"] = detail.get("byte_length")
            rec["sha256_hex"] = detail.get("sha256_hex")
        else:
            rec["status"] = "mirror_failed"
            rec["failure"] = detail
            failures.append({"source_stem": source_stem, "order": idx, "url": url, **detail})
            tag["src"] = url
            tag["data-mirror-failed"] = "1"
        records.append(rec)
        idx += 1

    for pic in root.find_all("picture"):
        if trace and pic.find("source"):
            trace.add_step(
                "parse_partial",
                f"picture:{source_stem}",
                True,
                {"note": "picture/source 节点存在；首期未逐项重写 srcset，保留原始 DOM。"},
            )

    out_html = root.decode_contents()
    return out_html, records, failures, idx


def iframes_in_body(body_html: str) -> int:
    soup = BeautifulSoup(body_html, "html.parser")
    return len(soup.find_all("iframe"))


def _guess_ext(url: str) -> str:
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return ext
    guess, _ = mimetypes.guess_type(path)
    if guess == "image/jpeg":
        return ".jpg"
    if guess == "image/png":
        return ".png"
    if guess == "image/webp":
        return ".webp"
    return ".bin"


def _download_image(
    url: str,
    dest: Path,
    trace: TraceRecorder | None,
    session: requests.Session,
    max_bytes: int,
) -> tuple[bool, dict[str, Any]]:
    t0 = __import__("time").monotonic()
    try:
        r = session.get(
            url,
            timeout=45,
            headers={"User-Agent": "Mozilla/5.0 (compatible; gzh-pipeline/1.0; +https://example.invalid)"},
            stream=True,
        )
        elapsed = monotonic_ms(t0)
        blen = 0
        h = hashlib.sha256()
        if r.status_code != 200:
            detail = {"http_status": r.status_code, "error": r.reason}
            if trace:
                trace.add_http_request(
                    f"image_get:{dest.name}",
                    "GET",
                    url,
                    request_headers=dict(r.request.headers) if r.request else None,
                    request_body=None,
                    response_status=r.status_code,
                    response_body={"reason": r.reason},
                    elapsed_ms=elapsed,
                    ok=False,
                    err=r.reason,
                )
            return False, detail
        with dest.open("wb") as f:
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                blen += len(chunk)
                if blen > max_bytes:
                    detail = {"error": "max_image_bytes_exceeded", "max": max_bytes}
                    if trace:
                        trace.add_step("image_pull", url[:200], False, detail)
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    return False, detail
                h.update(chunk)
                f.write(chunk)
        sha = h.hexdigest()
        if trace:
            trace.add_http_request(
                f"image_get:{dest.name}",
                "GET",
                url,
                request_headers=dict(r.request.headers) if r.request else None,
                request_body=None,
                response_status=r.status_code,
                response_body={"saved_to": str(dest), "byte_length": blen, "sha256_hex": sha},
                elapsed_ms=elapsed,
                ok=True,
            )
        return True, {"byte_length": blen, "sha256_hex": sha}
    except Exception as e:
        if trace:
            trace.add_step("image_pull", url[:180], False, {}, err=str(e))
        return False, {"error": str(e)}
