"""上传期 .doc → .docx 异步转换管理。

职责:
- 上传 HTTP 请求保存完文件后立即调用 `start_background_conversion`，**HTTP 立即返回**。
- 后台线程**串行**用 Word COM 把 .doc 转成 .docx，落到 `<upload_dir>/converted/`，避免 Word 单例并发冲突。
- 转换进度写入 `<upload_dir>/conversion_status.json`，前端轮询。
- 比对管线读 `resolve_converted_docx(.doc 原路径)` 复用已落地的 .docx，跳过 Word 调用。

不负责:
- Word COM 调用本身（见 `compare.extractor.convert_doc_to_docx`）。
- 上传文件保存与目录创建（见 `web.server` 的 upload-files 分支）。
- 失败 .doc 的自动重试（一次失败即记入 status，等用户重传）。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..runtime.checkpoint import atomic_write_json

CONVERSION_STATUS_FILENAME = "conversion_status.json"
CONVERTED_SUBDIR = "converted"
_LEGACY_DOC_EXTENSIONS = (".doc",)
_DOCX_EXTENSION = ".docx"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# 同一 upload_dir 只允许一个后台线程在跑；多次触发是幂等的（避免用户连点上传）。
_active_threads_lock = threading.Lock()
_active_threads: dict[str, threading.Thread] = {}


def conversion_status_path(upload_dir: Path) -> Path:
    """返回该上传目录里 conversion_status.json 的位置。"""
    return upload_dir / CONVERSION_STATUS_FILENAME


def converted_dir(upload_dir: Path) -> Path:
    """返回该上传目录下放转换产物的子目录。"""
    return upload_dir / CONVERTED_SUBDIR


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_legacy_doc(path: Path) -> bool:
    """按扩展名 + 文件头判断是否为需要转换的旧版 .doc(OLE2)。

    扩展名为 .doc 即认为需要转换；文件名是 .docx 但内容是 OLE2(用户改名)也按需要转换处理。
    """
    if not path.is_file() or path.name.startswith("."):
        return False
    suffix = path.suffix.lower()
    if suffix in _LEGACY_DOC_EXTENSIONS:
        return True
    if suffix == _DOCX_EXTENSION:
        try:
            with open(path, "rb") as handle:
                head = handle.read(8)
        except OSError:
            return False
        return head == _OLE2_MAGIC
    return False


def _scan_targets(upload_dir: Path) -> list[Path]:
    """扫描 upload_dir 顶层里所有需要转换的 .doc/伪 .docx，按文件名稳定排序。"""
    out: list[Path] = []
    for entry in sorted(upload_dir.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            continue
        if _is_legacy_doc(entry):
            out.append(entry)
    return out


def _build_initial_status(upload_dir: Path, targets: list[Path]) -> dict[str, Any]:
    """构造 conversion_status.json 的初始结构。"""
    started = _now()
    return {
        "upload_id": upload_dir.name,
        "upload_dir": str(upload_dir),
        "converted_dir": str(converted_dir(upload_dir)),
        "status": "pending" if targets else "completed",
        "total_count": len(targets),
        "completed_count": 0,
        "failed_count": 0,
        "started_at": started,
        "updated_at": started,
        "items": [
            {
                "original_name": t.name,
                "original_path": str(t),
                "converted_path": "",
                "status": "pending",
                "error": "",
                "started_at": "",
                "completed_at": "",
            }
            for t in targets
        ],
    }


def read_conversion_status(upload_dir: Path) -> dict[str, Any]:
    """读取上传目录的 conversion_status.json；不存在或损坏返回空 dict。"""
    path = conversion_status_path(upload_dir)
    if not path.exists():
        return {}
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_converted_docx(original_path: Path) -> Path | None:
    """对一个原始 .doc 路径，返回 `<原目录>/converted/<stem>.docx`（若已是真 zip）。

    没有现成产物或产物不是真 docx 时返回 None，由调用方自行触发懒转换或回退原路径。
    """
    candidate = original_path.parent / CONVERTED_SUBDIR / f"{original_path.stem}{_DOCX_EXTENSION}"
    if not candidate.exists():
        return None
    try:
        with open(candidate, "rb") as handle:
            head = handle.read(4)
    except OSError:
        return None
    return candidate if head == _ZIP_MAGIC else None


def start_background_conversion(upload_dir: Path) -> dict[str, Any]:
    """对刚刚保存好的 upload 目录启动后台转换；幂等：已有任务在跑就直接返回当前 status。

    HTTP 上传分支应在保存完文件、即将返回响应前调用本函数；调用本身极快（< 10ms）。
    """
    upload_dir = upload_dir.resolve()
    upload_id = upload_dir.name
    with _active_threads_lock:
        existing = _active_threads.get(upload_id)
        if existing is not None and existing.is_alive():
            return read_conversion_status(upload_dir) or {"status": "running", "upload_id": upload_id}
        targets = _scan_targets(upload_dir)
        initial = _build_initial_status(upload_dir, targets)
        atomic_write_json(conversion_status_path(upload_dir), initial)
        if not targets:
            return initial
        thread = threading.Thread(
            target=_run_conversion_loop,
            args=(upload_dir,),
            name=f"doc-convert-{upload_id}",
            daemon=True,
        )
        _active_threads[upload_id] = thread
        thread.start()
    return initial


def _run_conversion_loop(upload_dir: Path) -> None:
    """后台线程主循环：串行转换每个 .doc，并在每步更新 status 文件。"""
    # 延迟导入 convert_doc_to_docx，避免在 macOS / 无 pywin32 的环境下 import 阶段就炸；
    # 真正调用时再触发其平台检查与中文错误。
    from ..compare.extractor import convert_doc_to_docx  # noqa: PLC0415

    # 外层兜底：threading.Thread 起的新线程不会自动初始化 COM，漏掉会触发 -2147221008
    # 「尚未调用 CoInitialize」。convert_doc_to_docx 内部也会做一次（防直调），这里做线程级别一次更稳。
    thread_com_initialized = False
    try:
        import pythoncom  # pywin32 自带，仅 Windows 可用

        pythoncom.CoInitialize()
        thread_com_initialized = True
    except Exception:  # noqa: BLE001
        pass

    status_path = conversion_status_path(upload_dir)
    out_dir = converted_dir(upload_dir)
    payload = read_conversion_status(upload_dir)
    if not payload or not payload.get("items"):
        if thread_com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
        return
    payload["status"] = "running"
    payload["updated_at"] = _now()
    atomic_write_json(status_path, payload)

    try:
        for index, item in enumerate(payload["items"]):
            src = Path(item["original_path"])
            item["status"] = "running"
            item["started_at"] = _now()
            payload["updated_at"] = item["started_at"]
            atomic_write_json(status_path, payload)

            attempt_started = time.perf_counter()
            try:
                produced = convert_doc_to_docx(src, out_dir)
                item["converted_path"] = str(produced)
                item["status"] = "completed"
                payload["completed_count"] = sum(1 for it in payload["items"] if it["status"] == "completed")
            except Exception as exc:  # noqa: BLE001
                item["status"] = "failed"
                item["error"] = f"{type(exc).__name__}: {exc}"
                payload["failed_count"] = sum(1 for it in payload["items"] if it["status"] == "failed")
            item["completed_at"] = _now()
            item["duration_ms"] = int((time.perf_counter() - attempt_started) * 1000)
            payload["updated_at"] = item["completed_at"]
            atomic_write_json(status_path, payload)
            del index
        # 汇总最终状态：全部 completed -> completed；存在 failed -> partial / failed。
        completed = payload["completed_count"]
        failed = payload["failed_count"]
        total = payload["total_count"]
        if failed == 0 and completed == total:
            payload["status"] = "completed"
        elif completed == 0:
            payload["status"] = "failed"
        else:
            payload["status"] = "partial"
        payload["updated_at"] = _now()
        atomic_write_json(status_path, payload)
    finally:
        with _active_threads_lock:
            _active_threads.pop(upload_dir.name, None)
        if thread_com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
