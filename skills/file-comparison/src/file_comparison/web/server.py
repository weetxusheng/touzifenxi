"""提供 file-comparison skill 的本地批处理页面与 JSON API。"""

from __future__ import annotations

import json
import mimetypes
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlparse

from ..compare.engine import TaskManager, scan_folder_for_pairs
from ..runtime.config import FileComparisonRuntimeConfig, load_file_comparison_runtime_config
from ..runtime.settings import ensure_directories, resolve_paths

STATIC_DIR = Path(__file__).resolve().parent / "static"


class FileComparisonServer(ThreadingHTTPServer):
    """持有运行配置、任务管理器和路径约定的 HTTP 服务实例。"""

    def __init__(self, server_address, RequestHandlerClass, *, runtime_config: FileComparisonRuntimeConfig) -> None:
        """初始化页面服务并准备运行目录。"""
        super().__init__(server_address, RequestHandlerClass)
        self.runtime_config = runtime_config
        self.paths = resolve_paths()
        ensure_directories(self.paths)
        self.task_manager = TaskManager(runtime_config, self.paths)


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
            self._send_json(json.loads(status_path.read_text(encoding="utf-8")))
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
            filename = "comparison.docx" if kind == "docx" else "comparison.doc"
            artifact_path = self.server.paths.runs_root / task_id / "pairs" / pair_id / "outputs" / filename
            if not artifact_path.exists():
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
                    "pairs": [
                        {
                            "pair_id": pair.pair_id,
                            "key": pair.key,
                            "old_label": pair.old_label,
                            "new_label": pair.new_label,
                            "old_path": str(pair.old_path),
                            "new_path": str(pair.new_path),
                        }
                        for pair in pairs
                    ],
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
                    "pairs": [
                        {
                            "pair_id": pair.pair_id,
                            "key": pair.key,
                            "old_label": pair.old_label,
                            "new_label": pair.new_label,
                            "old_path": str(pair.old_path),
                            "new_path": str(pair.new_path),
                        }
                        for pair in pairs
                    ],
                }
            )
            return
        if parsed.path == "/api/file-comparison/task":
            payload = self._read_json_body()
            folder_path = self._resolve_folder_path(payload)
            if folder_path is None:
                return
            manifest = self.server.task_manager.create_task(folder_path)
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

    def _send_json(self, payload, *, status=200):
        """以 JSON 形式返回响应。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        """以 HTML 形式返回响应。"""
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        """返回磁盘文件内容，并在文档场景下附带下载头。"""
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        content_type, _ = mimetypes.guess_type(str(path))
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if path.suffix.lower() in {".doc", ".docx"}:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
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


def create_app(runtime_config: FileComparisonRuntimeConfig | None = None) -> FileComparisonServer:
    """按当前配置创建一个可直接启动的页面服务实例。"""
    config = runtime_config or load_file_comparison_runtime_config()
    return FileComparisonServer((config.ui.host, config.ui.port), RequestHandler, runtime_config=config)
