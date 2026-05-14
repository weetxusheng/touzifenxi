"""提供 file-comparison skill 的本地批处理页面与 JSON API。"""

from __future__ import annotations

import json
import mimetypes
import tempfile
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

from ..compare.engine import TaskManager, scan_folder_for_pairs
from ..compare.engine.abort_control import is_abort_requested
from ..compare.models import PairMatch
from ..compare.rerender import rerender_pair
from ..runtime.checkpoint import atomic_write_json, pair_checkpoint_path
from ..runtime.config import FileComparisonRuntimeConfig, load_file_comparison_runtime_config
from ..runtime.execution import PairManifest, task_status_from_pairs
from ..runtime.settings import ensure_directories, pair_dir_for, resolve_paths

STATIC_DIR = Path(__file__).resolve().parent / "static"
SKILL_ROOT = Path(__file__).resolve().parents[3]
TERMINAL_TASK_STATUSES = {"completed", "failed", "partial_failed", "aborted"}


def list_word_files(folder_path: Path) -> list[dict[str, str]]:
    """列出目录中可参与配对的 Word 文件。"""
    return [
        {"label": path.name, "path": str(path)}
        for path in sorted(folder_path.iterdir())
        if path.is_file() and path.suffix.lower() in {".doc", ".docx"} and not path.name.startswith(".~")
    ]


def pair_to_payload(pair: PairMatch) -> dict[str, str]:
    """把 PairMatch 转成前端可编辑的 JSON 结构。"""
    return {
        "pair_id": pair.pair_id,
        "key": pair.key,
        "old_label": pair.old_label,
        "new_label": pair.new_label,
        "old_path": str(pair.old_path),
        "new_path": str(pair.new_path),
    }


def enrich_status_with_task_workspace(run_dir: Path, payload: dict) -> dict:
    """从 task.json 附带工作区目录与 Word 列表，供带 task_id 刷新页面时恢复上传区与下拉选项。"""
    task_path = run_dir / "task.json"
    if not task_path.exists():
        return payload
    try:
        task_data = json.loads(task_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return payload
    if not isinstance(task_data, dict):
        return payload
    folder_raw = str(task_data.get("folder_path", "")).strip()
    if not folder_raw:
        return payload
    payload["folder_path"] = folder_raw
    folder = Path(folder_raw).expanduser().resolve()
    if folder.is_dir():
        payload["files"] = list_word_files(folder)
    else:
        payload["files"] = []
    return payload


def enrich_status_with_abort_flag(run_dir: Path, payload: dict) -> dict:
    """轮询时附带是否已请求暂停，便于前端在任务仍为 running 时展示「暂停待生效」。"""
    payload["abort_requested"] = bool(is_abort_requested(run_dir))
    return payload


def hydrate_status_from_checkpoints(run_dir: Path, payload: dict) -> dict:
    """用最新 pair checkpoint 补充页面轮询状态，提升运行中可观测性。"""
    pairs = payload.get("pairs", [])
    if not isinstance(pairs, list):
        return refresh_running_duration(payload)
    all_batches: list[dict] = []
    planned_batch_count = 0
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        pair_id = str(pair.get("pair_id", "")).strip()
        hydrate_pair_artifacts(run_dir, pair)
        planned_batches = planned_batch_payloads_from_batch_plan(run_dir, pair_id)
        pair_planned_batch_count = len(planned_batches)
        if pair_planned_batch_count <= 0:
            pair_planned_batch_count = int(pair.get("planned_batch_count", 0) or len(pair.get("batches", []) or []))
        pair["planned_batch_count"] = pair_planned_batch_count
        planned_batch_count += pair_planned_batch_count
        checkpoint_path = pair_checkpoint_path(run_dir, pair_id)
        legacy_path = checkpoint_path.parent / f"pair_{pair_id}_checkpoint.json"
        if not pair_id or not (checkpoint_path.exists() or legacy_path.exists()):
            pair_batches = planned_batches or (pair.get("batches", []) if isinstance(pair.get("batches"), list) else [])
            pair["batches"] = pair_batches
            all_batches.extend(pair_batches)
            continue
        try:
            read_from = checkpoint_path if checkpoint_path.exists() else legacy_path
            checkpoint = json.loads(read_from.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entries = checkpoint.get("entries", [])
        if not isinstance(entries, list):
            continue
        checkpoint_batches = [batch_payload_from_checkpoint_entry(entry) for entry in entries if isinstance(entry, dict)]
        batches = merge_planned_and_checkpoint_batches(planned_batches, checkpoint_batches)
        pair["batches"] = batches
        pair["completed_batch_count"] = sum(1 for batch in batches if batch["status"] == "success")
        pair["failed_batch_count"] = sum(1 for batch in batches if batch["status"] not in {"success", "pending"})
        pair["current_provider"] = current_provider_from_batches(batches)
        pair["active_providers"] = active_providers_from_batches(batches)
        all_batches.extend(batches)
    payload["pair_count"] = int(payload.get("pair_count", 0) or len(pairs))
    payload["completed_pair_count"] = sum(1 for pair in pairs if pair.get("status") == "completed")
    payload["failed_pair_count"] = sum(1 for pair in pairs if pair.get("status") == "failed")
    payload["governance_summary"] = build_governance_summary(
        all_batches,
        payload.get("governance_summary", {}),
        planned_batch_count=planned_batch_count,
    )
    return refresh_running_duration(payload)


def planned_batch_payloads_from_batch_plan(run_dir: Path, pair_id: str) -> list[dict]:
    """从 batch_plan.json 读取完整计划批次骨架。"""
    if not pair_id:
        return []
    batch_plan_path = pair_dir_for(run_dir, pair_id) / "extracted" / "batch_plan.json"
    if not batch_plan_path.exists():
        return []
    try:
        payload = json.loads(batch_plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    batches = payload.get("batches", [])
    if not isinstance(batches, list):
        return []
    planned_batches: list[dict] = []
    for index, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict):
            continue
        batch_id = str(batch.get("batch_id", "") or f"batch-{index:03d}").strip()
        planned_batches.append(
            {
                "batch_id": batch_id,
                "status": "pending",
                "chapter_range": list(batch.get("chapter_numbers", [])) if isinstance(batch.get("chapter_numbers"), list) else [],
                "attempt_count": 0,
                "provider": "",
                "error": "",
                "duration_ms": 0,
                "total_duration_ms": 0,
                "provider_available": True,
                "call_status": "",
                "repair_used": False,
                "fallback_name": "",
                "resume_from": "",
            }
        )
    return planned_batches


def planned_batch_count_from_batch_plan(run_dir: Path, pair_id: str) -> int:
    """从 batch_plan.json 读取稳定的计划批次数。"""
    return len(planned_batch_payloads_from_batch_plan(run_dir, pair_id))


def merge_planned_and_checkpoint_batches(planned_batches: list[dict], checkpoint_batches: list[dict]) -> list[dict]:
    """以计划批次为完整骨架，用 checkpoint 状态覆盖已执行批次。"""
    if not planned_batches:
        return checkpoint_batches
    checkpoint_by_id = {str(batch.get("batch_id", "")).strip(): batch for batch in checkpoint_batches}
    merged: list[dict] = []
    seen: set[str] = set()
    for planned_batch in planned_batches:
        batch_id = str(planned_batch.get("batch_id", "")).strip()
        checkpoint_batch = checkpoint_by_id.get(batch_id)
        if checkpoint_batch is not None:
            merged.append({**planned_batch, **checkpoint_batch})
            seen.add(batch_id)
            continue
        merged.append(planned_batch)
        seen.add(batch_id)
    merged.extend(batch for batch in checkpoint_batches if str(batch.get("batch_id", "")).strip() not in seen)
    return merged


def hydrate_pair_artifacts(run_dir: Path, pair: dict) -> None:
    """补齐 pair.json 里的产物路径、耗时和下载可用性。"""
    pair_id = str(pair.get("pair_id", "")).strip()
    if not pair_id:
        return
    pair_json_path = pair_dir_for(run_dir, pair_id) / "pair.json"
    if pair_json_path.exists():
        try:
            pair_payload = json.loads(pair_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pair_payload = {}
        if isinstance(pair_payload, dict):
            for key in (
                "key",
                "old_path",
                "new_path",
                "old_label",
                "new_label",
                "status",
                "docx_path",
                "doc_path",
                "error",
                "duration_ms",
                "completed_batch_count",
                "failed_batch_count",
                "planned_batch_count",
            ):
                value = pair_payload.get(key)
                if value not in (None, ""):
                    pair[key] = value
    pair["docx_available"] = resolve_pair_artifact_path(run_dir, pair_id, "docx") is not None
    pair["doc_available"] = resolve_pair_artifact_path(run_dir, pair_id, "doc") is not None
    pair["download_blocked"] = pair_has_fallback_diagnostic(run_dir, pair_id)


def current_provider_from_batches(batches: list[dict]) -> str:
    """从批次列表推导当前或最近一次使用的模型。"""
    if not batches:
        return ""
    current_batch = next((batch for batch in batches if batch.get("status") not in {"success", "aborted"}), batches[-1])
    return str(current_batch.get("provider", "")).strip()


def active_providers_from_batches(batches: list[dict]) -> list[str]:
    """返回当前仍在进行或最近有活动的 provider 列表。"""
    active: list[str] = []
    for batch in batches:
        provider = str(batch.get("provider", "")).strip()
        if not provider or batch.get("status") in {"success", "aborted"}:
            continue
        if provider not in active:
            active.append(provider)
    if active:
        return active
    provider = current_provider_from_batches(batches)
    return [provider] if provider else []


def batch_payload_from_checkpoint_entry(entry: dict) -> dict:
    """把 checkpoint entry 归一成前端 batch 摘要。"""
    context = entry.get("request_context", {}) if isinstance(entry.get("request_context"), dict) else {}
    error = entry.get("error", {}) if isinstance(entry.get("error"), dict) else {}
    return {
        "batch_id": str(entry.get("entry_id", "")),
        "status": str(entry.get("status", "")),
        "chapter_range": list(context.get("chapter_range", [])),
        "attempt_count": int(entry.get("attempt_count", 0) or 0),
        "provider": str(entry.get("provider", "")),
        "error": str(error.get("message", "")),
        "duration_ms": int(entry.get("duration_ms", 0) or 0),
        "total_duration_ms": int(entry.get("total_duration_ms", 0) or 0),
        "provider_available": bool(entry.get("provider_available", True)),
        "call_status": str(entry.get("call_status", "")),
        "repair_used": bool(entry.get("repair_used", False)),
        "fallback_name": str(entry.get("fallback_name", "")),
        "resume_from": str(entry.get("resume_from", "")),
    }


def build_governance_summary(batches: list[dict], existing: dict, *, planned_batch_count: int = 0) -> dict:
    """基于最新 batch 摘要重算页面治理统计。"""
    existing = existing if isinstance(existing, dict) else {}
    if not batches:
        planned_batch_count = planned_batch_count or int(existing.get("planned_batch_count", existing.get("total_batch_count", 0)) or 0)
        return {
            **existing,
            "planned_batch_count": planned_batch_count,
            "total_batch_count": planned_batch_count,
        }
    current_batch = next((batch for batch in batches if batch.get("status") not in {"success", "aborted"}), batches[-1])
    planned_batch_count = planned_batch_count or int(existing.get("planned_batch_count", existing.get("total_batch_count", len(batches))) or len(batches))
    active_providers = active_providers_from_batches(batches)
    return {
        "current_batch_id": current_batch.get("batch_id", ""),
        "current_provider": current_batch.get("provider", ""),
        "active_providers": active_providers,
        "current_call_status": current_batch.get("call_status", ""),
        "completed_batch_count": sum(1 for batch in batches if batch.get("status") == "success"),
        "failed_batch_count": sum(1 for batch in batches if batch.get("status") not in {"success", "pending"}),
        "provider_failure_count": sum(
            1
            for batch in batches
            if batch.get("provider") and not batch.get("provider_available") and batch.get("provider") != "fallback-rule"
        ),
        "repair_count": sum(1 for batch in batches if batch.get("repair_used")),
        "fallback_count": sum(1 for batch in batches if batch.get("fallback_name")),
        "planned_batch_count": planned_batch_count,
        "total_batch_count": planned_batch_count,
    }


def refresh_running_duration(payload: dict) -> dict:
    """运行中状态按 started_at 实时刷新耗时，终态保留落盘耗时。"""
    now = datetime.now().astimezone()
    payload["updated_at"] = now.isoformat()
    if str(payload.get("status", "")) in TERMINAL_TASK_STATUSES:
        return payload
    started_at = str(payload.get("started_at", "")).strip()
    if not started_at:
        return payload
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return payload
    if started.tzinfo is None:
        started = started.replace(tzinfo=now.tzinfo)
    duration_ms = max(int(payload.get("duration_ms", 0) or 0), int((now - started).total_seconds() * 1000))
    payload["duration_ms"] = duration_ms
    return payload


def pair_has_fallback_diagnostic(run_dir: Path, pair_id: str) -> bool:
    """判断文件对是否包含本地 fallback 诊断结果；包含则禁止下载正式产物。"""
    llm_dir = pair_dir_for(run_dir, pair_id) / "llm"
    if not llm_dir.exists():
        return False
    for final_status_path in llm_dir.glob("batch-*/final_status.json"):
        try:
            final_status = json.loads(final_status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(final_status, dict):
            continue
        status = str(final_status.get("status", "")).strip()
        provider = str(final_status.get("provider", "")).strip()
        fallback = str(final_status.get("fallback", "")).strip()
        if status == "fallback_succeeded" or provider == "fallback-rule" or fallback:
            return True
    return False


def resolve_pair_artifact_path(run_dir: Path, pair_id: str, kind: str) -> Path | None:
    """按 pair 记录或输出目录解析正式产物路径，兼容中文动态文件名。"""
    suffix_by_kind = {"docx": ".docx", "doc": ".doc"}
    suffix = suffix_by_kind.get(kind)
    if suffix is None:
        return None
    pair_dir = pair_dir_for(run_dir, pair_id)
    pair_json_path = pair_dir / "pair.json"
    if pair_json_path.exists():
        try:
            pair_payload = json.loads(pair_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pair_payload = {}
        recorded_path = Path(str(pair_payload.get(f"{kind}_path", ""))) if isinstance(pair_payload, dict) else Path("")
        if str(recorded_path) and recorded_path.exists() and recorded_path.suffix.lower() == suffix:
            return recorded_path
    output_dir = pair_dir / "outputs"
    candidates = [path for path in output_dir.glob(f"*{suffix}") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def update_pair_and_task_status_after_rerender(run_dir: Path, pair_id: str, rerender_payload: dict) -> None:
    """在重生成 DOCX 成功后，同步清理 pair/task 级失败状态和错误信息。"""
    pair_dir = pair_dir_for(run_dir, pair_id)
    pair_json_path = pair_dir / "pair.json"
    if pair_json_path.exists():
        try:
            pair_payload = json.loads(pair_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pair_payload = {}
        if isinstance(pair_payload, dict):
            pair_payload["status"] = "completed"
            pair_payload["error"] = ""
            pair_payload["docx_path"] = str(rerender_payload.get("docx_path", "") or "")
            pair_payload["doc_path"] = str(rerender_payload.get("doc_path", "") or "")
            atomic_write_json(pair_json_path, pair_payload)

    status_path = run_dir / "status.json"
    if not status_path.exists():
        return
    try:
        status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    pairs = status_payload.get("pairs", [])
    if not isinstance(pairs, list):
        return

    updated_pairs: list[dict] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        next_pair = dict(pair)
        if str(next_pair.get("pair_id", "")).strip() == pair_id:
            next_pair["status"] = "completed"
            next_pair["error"] = ""
            next_pair["docx_path"] = str(rerender_payload.get("docx_path", "") or "")
            next_pair["doc_path"] = str(rerender_payload.get("doc_path", "") or "")
            next_pair["docx_available"] = bool(next_pair["docx_path"])
            next_pair["doc_available"] = bool(next_pair["doc_path"])
        updated_pairs.append(next_pair)

    pair_manifests = [
        PairManifest(
            pair_id=str(pair.get("pair_id", "")).strip(),
            key=str(pair.get("key", "")).strip(),
            old_path=str(pair.get("old_path", "")).strip(),
            new_path=str(pair.get("new_path", "")).strip(),
            status=str(pair.get("status", "pending")).strip() or "pending",
            docx_path=str(pair.get("docx_path", "")).strip(),
            doc_path=str(pair.get("doc_path", "")).strip(),
            error=str(pair.get("error", "")).strip(),
            duration_ms=int(pair.get("duration_ms", 0) or 0),
            completed_batch_count=int(pair.get("completed_batch_count", 0) or 0),
            failed_batch_count=int(pair.get("failed_batch_count", 0) or 0),
            planned_batch_count=int(pair.get("planned_batch_count", 0) or 0),
        )
        for pair in updated_pairs
    ]
    completed_pair_count = sum(1 for pair in pair_manifests if pair.status == "completed")
    failed_pair_count = sum(1 for pair in pair_manifests if pair.status == "failed")
    status_payload["pairs"] = updated_pairs
    status_payload["status"] = task_status_from_pairs(pair_manifests)
    status_payload["success_count"] = completed_pair_count
    status_payload["failed_count"] = failed_pair_count
    status_payload["completed_pair_count"] = completed_pair_count
    status_payload["failed_pair_count"] = failed_pair_count
    atomic_write_json(status_path, status_payload)


class FileComparisonServer(ThreadingHTTPServer):
    """持有运行配置、任务管理器和路径约定的 HTTP 服务实例。"""

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        *,
        runtime_config: FileComparisonRuntimeConfig,
        config_base_path: Path | None = None,
    ) -> None:
        """初始化页面服务并准备运行目录。"""
        super().__init__(server_address, RequestHandlerClass)
        self.config_base_path = config_base_path or SKILL_ROOT
        self.apply_runtime_config(runtime_config)

    def apply_runtime_config(self, runtime_config: FileComparisonRuntimeConfig) -> None:
        """应用运行配置并重建路径与任务管理器。"""
        self.runtime_config = runtime_config
        self.paths = resolve_paths(self.config_base_path)
        ensure_directories(self.paths)
        self.task_manager = TaskManager(runtime_config, self.paths)

    def reload_runtime_config(self) -> FileComparisonRuntimeConfig:
        """从本地配置文件重新加载运行配置，供新任务启动前热更新。"""
        runtime_config = load_file_comparison_runtime_config(self.config_base_path)
        self.apply_runtime_config(runtime_config)
        return runtime_config


class RequestHandler(BaseHTTPRequestHandler):
    """实现本地页面静态资源与任务 API 的请求处理器。"""

    server: FileComparisonServer

    def do_GET(self):  # noqa: N802
        """处理页面静态资源、任务状态和产物下载请求。"""
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return
        if parsed.path.startswith("/static/"):
            target = STATIC_DIR / parsed.path.removeprefix("/static/")
            if not target.exists() or not target.is_file():
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_file(target)
            return
        if parsed.path.startswith("/api/file-comparison/task/") and parsed.path.endswith("/status"):
            task_id = parsed.path.split("/")[4]
            status_path = self.server.paths.runs_root / task_id / "status.json"
            if not status_path.exists():
                self._send_json({"error": "task not found"}, status=404)
                return
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            payload = hydrate_status_from_checkpoints(status_path.parent, payload)
            payload = enrich_status_with_task_workspace(status_path.parent, payload)
            self._send_json(enrich_status_with_abort_flag(status_path.parent, payload))
            return
        if parsed.path.startswith("/api/file-comparison/task/") and parsed.path.endswith("/pairs"):
            task_id = parsed.path.split("/")[4]
            status_path = self.server.paths.runs_root / task_id / "status.json"
            if not status_path.exists():
                self._send_json({"error": "task not found"}, status=404)
                return
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            self._send_json({"task_id": task_id, "pairs": status_payload.get("pairs", [])})
            return
        if parsed.path.startswith("/api/file-comparison/task/") and "/artifact/" in parsed.path:
            parts = parsed.path.strip("/").split("/")
            task_id = parts[3]
            pair_id = parts[5]
            kind = parts[6]
            run_dir = self.server.paths.runs_root / task_id
            if pair_has_fallback_diagnostic(run_dir, pair_id):
                self._send_json({"error": "该结果包含本地 fallback 诊断内容，禁止作为正式文档下载"}, status=409)
                return
            artifact_path = resolve_pair_artifact_path(run_dir, pair_id, kind)
            if artifact_path is None:
                self._send_json({"error": "artifact not found"}, status=404)
                return
            self._send_file(artifact_path)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        """处理文件上传、目录扫描和任务创建请求。"""
        parsed = urlparse(self.path)
        if parsed.path == "/api/file-comparison/upload-files":
            files = self._read_uploaded_files()
            if not files:
                self._send_json({"error": "no files uploaded"}, status=400)
                return
            upload_root = self.server.paths.output_root / "uploads"
            upload_root.mkdir(parents=True, exist_ok=True)
            folder_path = Path(tempfile.mkdtemp(prefix="upload-", dir=str(upload_root)))
            saved_files: list[str] = []
            for filename, content in files:
                target = folder_path / filename
                target.write_bytes(content)
                saved_files.append(filename)
            pairs = scan_folder_for_pairs(folder_path, self.server.runtime_config.pairing.month_pattern)
            self._send_json(
                {
                    "folder_path": str(folder_path),
                    "uploaded_count": len(saved_files),
                    "uploaded_files": saved_files,
                    "files": list_word_files(folder_path),
                    "pairs": [pair_to_payload(pair) for pair in pairs],
                },
                status=201,
            )
            return
        if parsed.path == "/api/file-comparison/scan-folder":
            payload = self._read_json_body()
            folder_path = self._resolve_folder_path(payload)
            if folder_path is None:
                return
            pairs = scan_folder_for_pairs(folder_path, self.server.runtime_config.pairing.month_pattern)
            self._send_json(
                {
                    "folder_path": str(folder_path),
                    "files": list_word_files(folder_path),
                    "pairs": [pair_to_payload(pair) for pair in pairs],
                }
            )
            return
        if parsed.path == "/api/file-comparison/task":
            payload = self._read_json_body()
            try:
                self.server.reload_runtime_config()
            except (OSError, TypeError, ValueError) as exc:
                self._send_json({"error": f"运行配置读取失败，请检查 runtime.local.json: {exc}"}, status=400)
                return
            folder_path = self._resolve_folder_path(payload)
            if folder_path is None:
                return
            pairs = self._resolve_pairs(payload, folder_path)
            if pairs is None:
                return
            manifest = self.server.task_manager.create_task(folder_path, pairs=pairs)
            self._send_json(
                {
                    "task_id": manifest.task_id,
                    "status": manifest.status,
                    "pair_count": manifest.pair_count,
                    "success_count": manifest.success_count,
                    "failed_count": manifest.failed_count,
                    "pairs": [
                        {
                            "pair_id": pair.pair_id,
                            "key": pair.key,
                            "status": pair.status,
                            "old_path": pair.old_path,
                            "new_path": pair.new_path,
                        }
                        for pair in manifest.pairs
                    ],
                },
                status=201,
            )
            return
        if parsed.path.startswith("/api/file-comparison/task/") and parsed.path.endswith("/abort"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 5:
                self._send_json({"error": "abort path 格式不正确"}, status=400)
                return
            task_id = parts[3]
            try:
                abort_payload = self.server.task_manager.request_abort(task_id)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(abort_payload, status=200)
            return
        if parsed.path.startswith("/api/file-comparison/task/") and parsed.path.endswith("/rerender-docx"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 7 or parts[4] != "pair":
                self._send_json({"error": "rerender-docx path 格式不正确"}, status=400)
                return
            task_id = parts[3]
            pair_id = parts[5]
            pair_dir = pair_dir_for(self.server.paths.runs_root / task_id, pair_id)
            if not pair_dir.exists():
                self._send_json({"error": f"pair not found: {pair_id}"}, status=404)
                return
            try:
                payload = rerender_pair(pair_dir, mode="stored", overwrite=True)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            update_pair_and_task_status_after_rerender(self.server.paths.runs_root / task_id, pair_id, payload)
            self._send_json({"task_id": task_id, **payload}, status=200)
            return
        if parsed.path.startswith("/api/file-comparison/task/") and parsed.path.endswith("/rerun"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 9 or parts[4] != "pair" or parts[6] != "batch":
                self._send_json({"error": "rerun path 格式不正确"}, status=400)
                return
            task_id = parts[3]
            pair_id = parts[5]
            batch_id = parts[7]
            try:
                payload = self.server.task_manager.rerun_batch(task_id, pair_id, batch_id)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload, status=202)
            return
        self._send_json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args):  # noqa: A003
        """关闭默认控制台访问日志，避免污染终端输出。"""
        return

    def _read_json_body(self):
        """读取并解析 JSON 请求体。"""
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _resolve_folder_path(self, payload) -> Path | None:
        """校验并解析请求中的目录路径。"""
        raw_path = str(payload.get("folder_path", "")).strip()
        if not raw_path:
            self._send_json({"error": "folder_path is required"}, status=400)
            return None
        folder_path = Path(raw_path).expanduser().resolve()
        if not folder_path.exists() or not folder_path.is_dir():
            self._send_json({"error": f"folder not found: {folder_path}"}, status=404)
            return None
        return folder_path

    def _resolve_pairs(self, payload, folder_path: Path) -> list[PairMatch] | None:
        """校验前端传回的人工确认配对；未传时退回自动扫描。"""
        raw_pairs = payload.get("pairs")
        if raw_pairs is None:
            return scan_folder_for_pairs(folder_path, self.server.runtime_config.pairing.month_pattern)
        if not isinstance(raw_pairs, list) or not raw_pairs:
            self._send_json({"error": "至少需要一组文件配对"}, status=400)
            return None
        pairs: list[PairMatch] = []
        for index, item in enumerate(raw_pairs, start=1):
            if not isinstance(item, dict):
                self._send_json({"error": "pairs 格式不正确"}, status=400)
                return None
            old_path = Path(str(item.get("old_path", ""))).expanduser().resolve()
            new_path = Path(str(item.get("new_path", ""))).expanduser().resolve()
            if not old_path.exists() or not old_path.is_file() or not new_path.exists() or not new_path.is_file():
                self._send_json({"error": f"配对文件不存在: {old_path} / {new_path}"}, status=400)
                return None
            if old_path == new_path:
                self._send_json({"error": "同一组配对的前后文件不能相同"}, status=400)
                return None
            pairs.append(
                PairMatch(
                    pair_id=f"pair-{index:03d}",
                    key=str(item.get("key", "")).strip() or old_path.stem,
                    old_path=old_path,
                    new_path=new_path,
                    old_label=old_path.name,
                    new_label=new_path.name,
                )
            )
        return pairs

    def _send_json(self, payload, *, status=200):
        """以 JSON 形式返回响应。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        """以 HTML 形式返回响应。"""
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        """返回磁盘文件内容，并在文档场景下附带下载头。"""
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        content_type, _ = mimetypes.guess_type(str(path))
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        if path.suffix.lower() in {".doc", ".docx"}:
            ascii_name = path.name.encode("ascii", "ignore").decode("ascii") or f"download{path.suffix}"
            self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        self.wfile.write(body)

    def _read_uploaded_files(self) -> list[tuple[str, bytes]]:
        """从 multipart/form-data 请求中提取上传文件。"""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return []
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b""
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
        )
        files: list[tuple[str, bytes]] = []
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True) or b""
            if not payload:
                continue
            files.append((Path(filename).name, payload))
        return files


def create_app(
    runtime_config: FileComparisonRuntimeConfig | None = None,
    *,
    config_base_path: Path | None = None,
) -> FileComparisonServer:
    """按当前配置创建一个可直接启动的页面服务实例。"""
    config = runtime_config or load_file_comparison_runtime_config(config_base_path)
    return FileComparisonServer(
        (config.ui.host, config.ui.port),
        RequestHandler,
        runtime_config=config,
        config_base_path=config_base_path,
    )
