import json
import threading
import uuid
from urllib.request import Request, urlopen

import pytest

from file_comparison.runtime.config import load_file_comparison_runtime_config, write_runtime_config
from file_comparison.runtime.execution import PairManifest, TaskManifest
from file_comparison.runtime.settings import resolve_paths
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


class FakeTaskManager:
    def __init__(self, manifest: TaskManifest) -> None:
        self.manifest = manifest

    def create_task(self, folder_path):
        return self.manifest


def test_scan_folder_and_task_status_endpoints(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "基金合同_3月.docx").write_text("", encoding="utf-8")
    (folder / "基金合同_6月.docx").write_text("", encoding="utf-8")

    try:
        server = create_app(runtime_config)
    except PermissionError as exc:
        pytest.skip(f"socket bind not permitted in sandbox: {exc}")
    server.paths = resolve_paths(tmp_path)
    server.task_manager = FakeTaskManager(
        TaskManifest(
            task_id="task-001",
            run_dir=(server.paths.runs_root / "task-001"),
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
    )
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

        status, task_payload = request_json(f"{base_url}/api/file-comparison/task", method="POST", payload={"folder_path": str(folder)})
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


def test_upload_files_endpoint_saves_files_and_returns_pairs(tmp_path):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}, "ui": {"port": 0}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    try:
        server = create_app(runtime_config)
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
        assert payload["pairs"][0]["old_label"] == "基金合同_3月.docx"
        assert payload["pairs"][0]["new_label"] == "基金合同_6月.docx"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
