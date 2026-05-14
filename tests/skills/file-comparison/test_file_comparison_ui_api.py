import json
import threading
import uuid
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from file_comparison.runtime.config import load_file_comparison_runtime_config, write_runtime_config
from file_comparison.runtime.execution import PairManifest, TaskManifest
from file_comparison.runtime.settings import resolve_paths
from file_comparison.web import server as server_module
from file_comparison.web.server import create_app


def request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request) as response:  # noqa: S310
        return response.status, json.loads(response.read().decode("utf-8"))


def request_multipart(url: str, *, files: list[tuple[str, bytes]]) -> tuple[int, dict]:
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    body = bytearray()
    for filename, content in files:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                "Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(request) as response:  # noqa: S310
        return response.status, json.loads(response.read().decode("utf-8"))


def request_json_allow_error(url: str, *, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def request_bytes(url: str) -> tuple[int, bytes, str]:
    request = Request(url, method="GET")
    with urlopen(request) as response:  # noqa: S310
        return response.status, response.read(), response.headers.get("Content-Disposition", "")


class FakeTaskManager:
    manifest_template: TaskManifest | None = None

    def __init__(self, manifest: TaskManifest, paths=None) -> None:
        if isinstance(manifest, TaskManifest):
            self.manifest = manifest
        elif self.manifest_template is not None:
            self.manifest = self.manifest_template
        else:
            raise TypeError("FakeTaskManager requires a TaskManifest template")

    def create_task(self, folder_path, pairs=None):
        self.pairs = pairs
        return self.manifest

    def rerun_batch(self, task_id, pair_id, batch_id):
        self.rerun_args = (task_id, pair_id, batch_id)
        return {"task_id": task_id, "pair_id": pair_id, "batch_id": batch_id, "status": "running"}

    def request_abort(self, task_id):
        return {"task_id": task_id, "status": "abort_requested"}


class CapturingTaskManager:
    """记录创建任务时实际使用的运行配置，方便验证配置热加载。"""

    created_configs = []

    def __init__(self, runtime_config, paths) -> None:
        self.runtime_config = runtime_config
        self.paths = paths

    def create_task(self, folder_path, pairs=None):
        CapturingTaskManager.created_configs.append(self.runtime_config)
        pair = pairs[0]
        return TaskManifest(
            task_id="task-reload",
            run_dir=(self.paths.runs_root / "task-reload"),
            status="pending",
            poll_interval_seconds=2.0,
            pair_count=1,
            success_count=0,
            failed_count=0,
            pairs=(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="pending",
                ),
            ),
        )


def test_scan_folder_and_task_status_endpoints(tmp_path, monkeypatch):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "基金合同_3月.docx").write_text("", encoding="utf-8")
    (folder / "基金合同_6月.docx").write_text("", encoding="utf-8")

    paths = resolve_paths(tmp_path)
    FakeTaskManager.manifest_template = TaskManifest(
        task_id="task-001",
        run_dir=(paths.runs_root / "task-001"),
        status="pending",
        poll_interval_seconds=2.0,
        pair_count=1,
        success_count=0,
        failed_count=0,
        pairs=(
            PairManifest(
                pair_id="pair-001",
                key="基金合同",
                old_path=str(folder / "基金合同_3月.docx"),
                new_path=str(folder / "基金合同_6月.docx"),
                status="pending",
            ),
        ),
    )
    monkeypatch.setattr(server_module, "TaskManager", FakeTaskManager)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = paths
    status_dir = server.paths.runs_root / "task-001"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "status.json").write_text(
        json.dumps(
            {
                "task_id": "task-001",
                "status": "running",
                "duration_ms": 1234,
                "pair_count": 1,
                "success_count": 0,
                "failed_count": 0,
                "pairs": [{"pair_id": "pair-001", "key": "基金合同", "status": "pending"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (status_dir / "task.json").write_text(
        json.dumps({"task_id": "task-001", "folder_path": str(folder)}, ensure_ascii=False),
        encoding="utf-8",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, scan_payload = request_json(f"{base_url}/api/file-comparison/scan-folder", method="POST", payload={"folder_path": str(folder)})
        assert status == 200
        assert scan_payload["pairs"][0]["old_label"] == "基金合同_3月.docx"
        assert scan_payload["files"][0]["label"] == "基金合同_3月.docx"

        status, task_payload = request_json(
            f"{base_url}/api/file-comparison/task",
            method="POST",
            payload={
                "folder_path": str(folder),
                "pairs": [
                    {
                        "key": "人工确认配对",
                        "old_path": str(folder / "基金合同_3月.docx"),
                        "new_path": str(folder / "基金合同_6月.docx"),
                    }
                ],
            },
        )
        assert status == 201
        assert task_payload["task_id"] == "task-001"
        assert task_payload["pairs"][0]["pair_id"] == "pair-001"

        status, status_payload = request_json(f"{base_url}/api/file-comparison/task/task-001/status")
        assert status == 200
        assert status_payload["status"] == "running"
        assert status_payload["duration_ms"] == 1234
        assert status_payload["pairs"][0]["key"] == "基金合同"
        assert status_payload["folder_path"] == str(folder)
        assert len(status_payload["files"]) == 2
        assert status_payload["files"][0]["label"] == "基金合同_3月.docx"

        status, pairs_payload = request_json(f"{base_url}/api/file-comparison/task/task-001/pairs")
        assert status == 200
        assert pairs_payload["pairs"][0]["pair_id"] == "pair-001"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_create_task_reloads_runtime_config_before_starting(tmp_path, monkeypatch):
    write_runtime_config(
        tmp_path,
        {
            "llm": {
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "",
                        "api_key_env": "MINIMAX_API_KEY",
                        "base_url": "https://api.minimaxi.com/v1",
                    }
                ],
                "task_routing": {"batch_compare": ["minimax"]},
            },
            "paths": {"output_root": str(tmp_path / "runs")},
            "ui": {"port": 0},
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    old_file = folder / "基金合同_3月.docx"
    new_file = folder / "基金合同_6月.docx"
    old_file.write_text("", encoding="utf-8")
    new_file.write_text("", encoding="utf-8")
    CapturingTaskManager.created_configs = []
    monkeypatch.setattr(server_module, "TaskManager", CapturingTaskManager)

    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    write_runtime_config(
        tmp_path,
        {
            "llm": {
                "providers": [
                    {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "api_key": "",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "base_url": "https://api.deepseek.com",
                    }
                ],
                "task_routing": {"batch_compare": ["deepseek"]},
            },
        },
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json(
            f"{base_url}/api/file-comparison/task",
            method="POST",
            payload={
                "folder_path": str(folder),
                "pairs": [
                    {
                        "key": "人工确认配对",
                        "old_path": str(old_file),
                        "new_path": str(new_file),
                    }
                ],
            },
        )
        assert status == 201
        assert payload["task_id"] == "task-reload"
        assert CapturingTaskManager.created_configs[-1].llm.primary.provider == "deepseek"
        assert CapturingTaskManager.created_configs[-1].llm.primary.model == "deepseek-v4-flash"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rerender_docx_clears_failed_pair_status_and_error(tmp_path, monkeypatch):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)

    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)

    run_dir = server.paths.runs_root / "task-rerender"
    pair_dir = run_dir / "pair-001"
    outputs_dir = pair_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    docx_path = outputs_dir / "基金合同 对照表.docx"
    doc_path = outputs_dir / "基金合同 对照表.doc"
    docx_path.write_bytes(b"docx")
    doc_path.write_bytes(b"doc")
    (pair_dir / "pair.json").write_text(
        json.dumps(
            {
                "pair_id": "pair-001",
                "key": "基金合同",
                "old_path": str(tmp_path / "old.docx"),
                "new_path": str(tmp_path / "new.docx"),
                "old_label": "基金合同_3月.docx",
                "new_label": "基金合同_6月.docx",
                "status": "failed",
                "error": "DOCX 写出失败",
                "docx_path": "",
                "doc_path": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "task_id": "task-rerender",
                "run_dir": str(run_dir),
                "status": "failed",
                "duration_ms": 1234,
                "poll_interval_seconds": 2.0,
                "pair_count": 1,
                "success_count": 0,
                "failed_count": 1,
                "completed_pair_count": 0,
                "failed_pair_count": 1,
                "pairs": [
                    {
                        "pair_id": "pair-001",
                        "key": "基金合同",
                        "status": "failed",
                        "old_path": str(tmp_path / "old.docx"),
                        "new_path": str(tmp_path / "new.docx"),
                        "old_label": "基金合同_3月.docx",
                        "new_label": "基金合同_6月.docx",
                        "error": "DOCX 写出失败",
                        "completed_batch_count": 3,
                        "failed_batch_count": 0,
                        "planned_batch_count": 3,
                        "batches": [
                            {
                                "batch_id": "batch-001",
                                "status": "success",
                                "chapter_range": ["第一部分"],
                                "attempt_count": 1,
                                "provider": "minimax",
                                "error": "",
                                "duration_ms": 100,
                                "total_duration_ms": 100,
                                "provider_available": True,
                                "call_status": "success",
                                "repair_used": False,
                                "fallback_name": "",
                                "resume_from": "",
                            }
                        ],
                    }
                ],
                "governance_summary": {
                    "completed_batch_count": 1,
                    "failed_batch_count": 0,
                    "planned_batch_count": 1,
                    "total_batch_count": 1,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        server_module,
        "rerender_pair",
        lambda pair_dir, mode, overwrite: {
            "pair_id": "pair-001",
            "mode": mode,
            "row_count": 5,
            "docx_path": str(docx_path),
            "doc_path": str(doc_path),
        },
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, rerender_payload = request_json(
            f"{base_url}/api/file-comparison/task/task-rerender/pair/pair-001/rerender-docx",
            method="POST",
            payload={},
        )
        assert status == 200
        assert rerender_payload["pair_id"] == "pair-001"

        status, status_payload = request_json(f"{base_url}/api/file-comparison/task/task-rerender/status")
        assert status == 200
        assert status_payload["status"] == "completed"
        assert status_payload["failed_count"] == 0
        assert status_payload["pairs"][0]["status"] == "completed"
        assert status_payload["pairs"][0]["error"] == ""
        assert status_payload["pairs"][0]["docx_path"] == str(docx_path)
        assert status_payload["pairs"][0]["doc_path"] == str(doc_path)

        stored_pair_payload = json.loads((pair_dir / "pair.json").read_text(encoding="utf-8"))
        assert stored_pair_payload["status"] == "completed"
        assert stored_pair_payload["error"] == ""
        assert stored_pair_payload["docx_path"] == str(docx_path)
        assert stored_pair_payload["doc_path"] == str(doc_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_create_task_reports_runtime_config_reload_error(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    old_file = folder / "基金合同_3月.docx"
    new_file = folder / "基金合同_6月.docx"
    old_file.write_text("", encoding="utf-8")
    new_file.write_text("", encoding="utf-8")
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    (tmp_path / "config" / "runtime.local.json").write_text('{"llm": ', encoding="utf-8")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json_allow_error(
            f"{base_url}/api/file-comparison/task",
            method="POST",
            payload={
                "folder_path": str(folder),
                "pairs": [
                    {
                        "key": "人工确认配对",
                        "old_path": str(old_file),
                        "new_path": str(new_file),
                    }
                ],
            },
        )
        assert status == 400
        assert "运行配置读取失败" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rerun_batch_endpoint_starts_single_batch_rerun(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    fake_manager = FakeTaskManager(
        TaskManifest(
            task_id="task-001",
            run_dir=(server.paths.runs_root / "task-001"),
            status="pending",
            poll_interval_seconds=2.0,
            pair_count=1,
            success_count=0,
            failed_count=0,
        )
    )
    server.task_manager = fake_manager
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json(
            f"{base_url}/api/file-comparison/task/task-001/pair/pair-001/batch/batch-003/rerun",
            method="POST",
            payload={},
        )
        assert status == 202
        assert payload["status"] == "running"
        assert payload["batch_id"] == "batch-003"
        assert fake_manager.rerun_args == ("task-001", "pair-001", "batch-003")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rerender_docx_endpoint_reuses_stored_batches(tmp_path, monkeypatch):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    pair_dir = server.paths.runs_root / "task-001" / "pair-001"
    pair_dir.mkdir(parents=True, exist_ok=True)
    rerender_calls: list[tuple[str, str, bool]] = []

    def fake_rerender(target_pair_dir, *, mode, overwrite):
        rerender_calls.append((str(target_pair_dir), mode, overwrite))
        return {
            "pair_id": "pair-001",
            "mode": mode,
            "row_count": 3,
            "docx_path": str(target_pair_dir / "outputs" / "comparison.docx"),
            "doc_path": str(target_pair_dir / "outputs" / "comparison.doc"),
        }

    monkeypatch.setattr(server_module, "rerender_pair", fake_rerender)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json(
            f"{base_url}/api/file-comparison/task/task-001/pair/pair-001/rerender-docx",
            method="POST",
            payload={},
        )
        assert status == 200
        assert payload["pair_id"] == "pair-001"
        assert payload["mode"] == "stored"
        assert rerender_calls == [(str(pair_dir), "stored", True)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_upload_files_endpoint_saves_files_and_returns_pairs(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_multipart(
            f"{base_url}/api/file-comparison/upload-files",
            files=[
                ("基金合同_3月.docx", b"old-doc"),
                ("基金合同_6月.docx", b"new-doc"),
            ],
        )
        assert status == 201
        assert payload["uploaded_count"] == 2
        assert len(payload["files"]) == 2
        assert payload["pairs"][0]["old_label"] == "基金合同_3月.docx"
        assert payload["pairs"][0]["new_label"] == "基金合同_6月.docx"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_artifact_download_blocks_fallback_only_document(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    artifact_dir = server.paths.runs_root / "task-001" / "pair-001" / "outputs"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "comparison.docx").write_bytes(b"diagnostic-doc")
    batch_dir = server.paths.runs_root / "task-001" / "pair-001" / "llm" / "batch-001"
    batch_dir.mkdir(parents=True)
    (batch_dir / "final_status.json").write_text(
        json.dumps({"status": "fallback_succeeded", "fallback": "compare-units"}, ensure_ascii=False),
        encoding="utf-8",
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json_allow_error(
            f"{base_url}/api/file-comparison/task/task-001/artifact/pair-001/docx"
        )
        assert status == 409
        assert "fallback" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_artifact_download_serves_chinese_named_output(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    artifact_dir = server.paths.runs_root / "task-001" / "pair-001" / "outputs"
    artifact_dir.mkdir(parents=True)
    artifact_name = "基金合同_3月 与 基金合同_6月 对照表 20260501_101530.docx"
    (artifact_dir / artifact_name).write_bytes(b"official-doc")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, body, disposition = request_bytes(f"{base_url}/api/file-comparison/task/task-001/artifact/pair-001/docx")
        assert status == 200
        assert body == b"official-doc"
        assert "filename*=UTF-8''" in disposition
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_status_hydration_uses_stable_planned_batch_count(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    run_dir = server.paths.runs_root / "task-001"
    pair_dir = run_dir / "pair-001"
    (pair_dir / "extracted").mkdir(parents=True)
    (run_dir / "checkpoints").mkdir(parents=True)
    (pair_dir / "extracted" / "batch_plan.json").write_text(
        json.dumps(
            {
                "pair_id": "pair-001",
                "batches": [
                    {"batch_id": "batch-001"},
                    {"batch_id": "batch-002"},
                    {"batch_id": "batch-003"},
                    {"batch_id": "batch-004"},
                    {"batch_id": "batch-005"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "task_id": "task-001",
                "status": "running",
                "duration_ms": 1,
                "pair_count": 1,
                "completed_pair_count": 0,
                "failed_pair_count": 0,
                "pairs": [{"pair_id": "pair-001", "key": "基金合同", "status": "llm_running"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "checkpoints" / "pair-001.json").write_text(
        json.dumps(
            {
                "pair_id": "pair-001",
                "entries": [
                    {
                        "entry_id": "batch-001",
                        "status": "success",
                        "attempt_count": 1,
                        "provider": "deepseek",
                        "request_context": {"chapter_range": ["第一部分"]},
                    },
                    {
                        "entry_id": "batch-002",
                        "status": "error",
                        "attempt_count": 2,
                        "provider": "kimi-code",
                        "provider_available": False,
                        "error": {"message": "timeout"},
                        "request_context": {"chapter_range": ["第二部分"]},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json(f"{base_url}/api/file-comparison/task/task-001/status")
        assert status == 200
        assert payload["governance_summary"]["completed_batch_count"] == 1
        assert payload["governance_summary"]["planned_batch_count"] == 5
        assert payload["governance_summary"]["total_batch_count"] == 5
        assert payload["pairs"][0]["planned_batch_count"] == 5
        assert payload["pairs"][0]["completed_batch_count"] == 1
        assert payload["pairs"][0]["failed_batch_count"] == 1
        assert [batch["batch_id"] for batch in payload["pairs"][0]["batches"]] == [
            "batch-001",
            "batch-002",
            "batch-003",
            "batch-004",
            "batch-005",
        ]
        assert [batch["status"] for batch in payload["pairs"][0]["batches"]] == [
            "success",
            "error",
            "pending",
            "pending",
            "pending",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_running_status_duration_is_recomputed_from_started_at(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    run_dir = server.paths.runs_root / "task-001"
    run_dir.mkdir(parents=True)
    started_at = (datetime.now().astimezone() - timedelta(seconds=3)).isoformat()
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "task_id": "task-001",
                "status": "running",
                "started_at": started_at,
                "duration_ms": 1,
                "pair_count": 1,
                "completed_pair_count": 0,
                "failed_pair_count": 0,
                "pairs": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json(f"{base_url}/api/file-comparison/task/task-001/status")
        assert status == 200
        assert payload["duration_ms"] >= 2500
        assert payload["started_at"] == started_at
        assert payload["updated_at"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_request_task_abort_writes_flag(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    run_dir = server.paths.runs_root / "task-abort-1"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps({"task_id": "task-abort-1", "status": "running", "pairs": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json(
            f"{base_url}/api/file-comparison/task/task-abort-1/abort",
            method="POST",
            payload={},
        )
        assert status == 200
        assert payload["task_id"] == "task-abort-1"
        assert payload["status"] == "abort_requested"
        assert (run_dir / "abort_requested").is_file()
        st, status_body = request_json(f"{base_url}/api/file-comparison/task/task-abort-1/status")
        assert st == 200
        assert status_body.get("abort_requested") is True
        assert status_body.get("status") == "running"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_request_task_abort_rejects_completed(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config, config_base_path=tmp_path)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    run_dir = server.paths.runs_root / "task-abort-done"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps({"task_id": "task-abort-done", "status": "completed", "pairs": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = request_json_allow_error(
            f"{base_url}/api/file-comparison/task/task-abort-done/abort",
            method="POST",
            payload={},
        )
        assert status == 409
        assert "结束" in payload.get("error", "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
