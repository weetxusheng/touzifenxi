"""管理 file-comparison 本地页面服务的开发辅助逻辑。"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..runtime.config import UIRuntimeConfig, load_file_comparison_runtime_config
from ..runtime.python_env import resolve_python_executable


@dataclass(frozen=True, slots=True)
class DevServerPaths:
    """描述本地页面服务的状态文件路径。"""

    state_dir: Path
    pid_file: Path
    log_file: Path


@dataclass(frozen=True, slots=True)
class RestartResult:
    """描述一次重启后的关键信息。"""

    pid: int
    url: str
    pid_file: Path
    log_file: Path
    stopped_pids: tuple[int, ...]


def load_ui_runtime_config(base_path: Path) -> UIRuntimeConfig:
    """读取页面监听配置，便于脚本和测试共用。"""
    return load_file_comparison_runtime_config(base_path).ui


def resolve_dev_server_paths(skill_root: Path) -> DevServerPaths:
    """把 PID 和日志统一放进项目级 state 目录。"""
    project_root = skill_root.parent.parent
    state_dir = project_root / "state" / "file-comparison"
    return DevServerPaths(
        state_dir=state_dir,
        pid_file=state_dir / "file-comparison-web.pid",
        log_file=state_dir / "file-comparison-web.log",
    )


def build_web_server_command(
    *,
    skill_root: Path,
    python_executable: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> list[str]:
    """构建页面服务启动命令。"""
    command = [
        resolve_python_executable(
            skill_root=skill_root,
            explicit_python_executable=python_executable,
            current_executable=sys.executable,
        ),
        str(skill_root / "scripts" / "file_comparison_web.py"),
    ]
    if host:
        command.extend(["--host", host])
    if port is not None:
        command.extend(["--port", str(port)])
    return command


def read_pid(pid_file: Path) -> int | None:
    """读取 PID 文件中的进程号。"""
    if not pid_file.exists():
        return None
    raw = pid_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def process_exists(pid: int) -> bool:
    """判断进程是否仍然存在。"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_command(pid: int) -> str:
    """读取进程命令行，便于确认 PID 是否属于目标服务。"""
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


def pid_matches_server(pid: int, script_path: Path) -> bool:
    """确认 PID 对应的确实是 file-comparison 页面服务。"""
    if not process_exists(pid):
        return False
    command = process_command(pid)
    script_token = str(script_path)
    return script_token in command or script_path.name in command


def run_capture(command: list[str]) -> str:
    """执行辅助命令并返回标准输出；失败时按无结果处理。"""
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def collect_existing_server_pids(*, pid_file: Path, script_path: Path, port: int | None = None) -> list[int]:
    """收集可能需要清理的旧页面服务进程。"""
    candidates: list[int] = []
    current_pid = os.getpid()

    pid_from_file = read_pid(pid_file)
    if pid_from_file is not None and pid_from_file != current_pid and pid_matches_server(pid_from_file, script_path):
        candidates.append(pid_from_file)

    for raw in run_capture(["pgrep", "-f", str(script_path)]).splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            pid = int(raw)
        except ValueError:
            continue
        if pid != current_pid and pid_matches_server(pid, script_path) and pid not in candidates:
            candidates.append(pid)

    if port is not None:
        for raw in run_capture(["lsof", "-ti", f"tcp:{port}"]).splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                pid = int(raw)
            except ValueError:
                continue
            if pid != current_pid and pid not in candidates:
                candidates.append(pid)

    return candidates


def terminate_pid(pid: int, timeout_seconds: float = 5.0) -> None:
    """优雅停止旧进程，必要时再强制结束。"""
    if not process_exists(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.1)
    if process_exists(pid):
        os.kill(pid, signal.SIGKILL)


def spawn_server_process(*, command: list[str], cwd: Path, log_file: Path):
    """后台拉起页面服务并把日志落到 state 目录。"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_file.open("ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    return process


def wait_for_server_ready(host: str, port: int, pid: int, timeout_seconds: float = 10.0) -> None:
    """等待页面服务开始监听端口。"""
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_exists(pid):
            raise RuntimeError("页面服务启动后立刻退出，请查看日志。")
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("页面服务启动超时，请查看日志。")


def restart_web_server(
    *,
    skill_root: Path,
    host: str | None = None,
    port: int | None = None,
    python_executable: str | None = None,
) -> RestartResult:
    """停止旧页面服务并重新拉起一个新进程。"""
    ui_config = load_ui_runtime_config(skill_root)
    effective_host = host or ui_config.host
    effective_port = port if port is not None else ui_config.port
    script_path = skill_root / "scripts" / "file_comparison_web.py"
    paths = resolve_dev_server_paths(skill_root)
    paths.state_dir.mkdir(parents=True, exist_ok=True)

    stopped_pids = collect_existing_server_pids(
        pid_file=paths.pid_file,
        script_path=script_path,
        port=effective_port,
    )
    for pid in stopped_pids:
        terminate_pid(pid)

    command = build_web_server_command(
        skill_root=skill_root,
        python_executable=python_executable,
        host=host,
        port=port,
    )
    process = spawn_server_process(command=command, cwd=skill_root, log_file=paths.log_file)
    paths.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        wait_for_server_ready(effective_host, effective_port, process.pid)
    except Exception:
        if paths.pid_file.exists():
            paths.pid_file.unlink()
        raise

    return RestartResult(
        pid=process.pid,
        url=f"http://{effective_host}:{effective_port}",
        pid_file=paths.pid_file,
        log_file=paths.log_file,
        stopped_pids=tuple(stopped_pids),
    )
