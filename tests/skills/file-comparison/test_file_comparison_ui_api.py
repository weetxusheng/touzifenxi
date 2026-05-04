import json
import threading
import uuid
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
    artifact_dir = server.paths.runs_root / "task-001" / "pairs" / "pair-001" / "outputs"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "comparison.docx").write_bytes(b"diagnostic-doc")
    batch_dir = server.paths.runs_root / "task-001" / "pairs" / "pair-001" / "llm" / "batch-001"
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
    artifact_dir = server.paths.runs_root / "task-001" / "pairs" / "pair-001" / "outputs"
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
