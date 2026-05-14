"""Windows 下：页面服务进程发现、停止与重启（PowerShell + taskkill）。"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from .dev_server import (
    RestartResult,
    build_web_server_command,
    load_ui_runtime_config,
    read_pid,
    resolve_dev_server_paths,
    run_capture,
)


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def process_exists(pid: int) -> bool:
    """通过 Win32 API 判断进程是否仍然存在。

    注意：直接使用 dev_server.process_exists 在 Windows 上不可用——
    Python 在 Windows 下把 `os.kill(pid, 0)` 翻译为 `TerminateProcess`，
    遇到已退出的 PID 会抛出 `SystemError`，而非干净地返回 False。
    """
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid_int)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _powershell_single_quoted(value: str) -> str:
    """嵌入 PowerShell 单引号字面量时的转义。"""
    return "'" + value.replace("'", "''") + "'"


def process_command(pid: int) -> str:
    """读取进程命令行（Win32_Process.CommandLine）。"""
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}').CommandLine",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or "").strip()


def pid_matches_server(pid: int, script_path: Path) -> bool:
    """确认 PID 对应的确实是 file-comparison 页面服务。"""
    if not process_exists(pid):
        return False
    command = process_command(pid)
    script_token = str(script_path)
    return script_token in command or script_path.name in command


def _find_pids_matching_script(script_path: Path) -> list[str]:
    """列出命令行中包含目标脚本路径的进程（替代 pgrep -f）。"""
    variants = list(dict.fromkeys([str(script_path.resolve()), str(script_path)]))
    pattern_exprs = ", ".join(f"[regex]::Escape({_powershell_single_quoted(v)})" for v in variants)
    ps_script = (
        f"$patterns = @({pattern_exprs}); "
        "Get-CimInstance Win32_Process | ForEach-Object { "
        "if (-not $_.CommandLine) { return }; "
        "foreach ($pat in $patterns) { "
        "if ($_.CommandLine -match $pat) { $_.ProcessId; break } } }"
    )
    raw = run_capture(["powershell", "-NoProfile", "-Command", ps_script])
    return raw.splitlines()


def _pids_listening_on_tcp_port(port: int) -> list[str]:
    """列出监听指定 TCP 端口的 OwningProcess（替代 lsof -ti）。"""
    port_i = int(port)
    raw = run_capture(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-NetTCPConnection -LocalPort "
            f"{port_i} -State Listen -ErrorAction SilentlyContinue)"
            ".OwningProcess",
        ]
    )
    return raw.splitlines()


def collect_existing_server_pids(*, pid_file: Path, script_path: Path, port: int | None = None) -> list[int]:
    """收集可能需要清理的旧页面服务进程。"""
    candidates: list[int] = []
    current_pid = os.getpid()

    pid_from_file = read_pid(pid_file)
    if pid_from_file is not None and pid_from_file != current_pid and pid_matches_server(pid_from_file, script_path):
        candidates.append(pid_from_file)

    for raw in _find_pids_matching_script(script_path):
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
        for raw in _pids_listening_on_tcp_port(port):
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
    subprocess.run(
        ["taskkill", "/PID", str(int(pid)), "/T"],
        capture_output=True,
        text=True,
        check=False,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.1)
    if process_exists(pid):
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )


def spawn_server_process(*, command: list[str], cwd: Path, log_file: Path):
    """后台拉起页面服务；每次重启清空日志，便于对照本次子进程输出。

    Windows 上仅靠 `start_new_session=True` 并不能真正脱离父进程的 console 组，
    一旦发起重启的 cmd/PowerShell 退出，子进程会被关联终止；
    显式叠加 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`
    让页面服务真正后台常驻。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_file.open("wb")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    creationflags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
            env=env,
        )
    finally:
        log_handle.close()
    return process


def _tail_log_snippet(log_file: Path | None, *, max_bytes: int = 8000) -> str:
    """失败时附带日志尾部，便于定位子进程崩溃原因。"""
    if log_file is None or not log_file.exists():
        return ""
    try:
        raw = log_file.read_bytes()
    except OSError:
        return ""
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    return raw.decode("utf-8", errors="replace").strip()


def _probe_socket_specs(host: str, port: int) -> list[tuple[int, tuple]]:
    """显式 IPv4/IPv6 探测规格，避免 create_connection/getaddrinfo 在个别 Win 环境下卡住。"""
    p = int(port)
    if host == "0.0.0.0":
        return [(socket.AF_INET, ("127.0.0.1", p))]
    if host == "localhost":
        return [
            (socket.AF_INET, ("127.0.0.1", p)),
            (socket.AF_INET6, ("::1", p, 0, 0)),
        ]
    if ":" in host:
        return [(socket.AF_INET6, (host, p, 0, 0))]
    return [(socket.AF_INET, (host, p))]


def _try_tcp_connect(family: int, addr: tuple, *, timeout: float) -> bool:
    sock: socket.socket | None = None
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(addr)
        return True
    except (TimeoutError, OSError):
        # Windows 上 connect 可能抛出 TimeoutError；须显式捕获（勿依赖与 OSError 的继承关系）
        return False
    finally:
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def _log_signals_listening(log_file: Path | None, port: int) -> bool:
    """子进程在 serve_forever 前会 print `http://host:port`，把这一行作为端口已 bind 的主信号。

    Windows 上某些 EDR/SASE（实测深信服）会拦截 loopback 连接探测，
    让"未就绪端口"的 `socket.connect` 也保持到超时再返回，
    导致 `_try_tcp_connect` 的探测频率被严重压低。
    直接读取子进程 stdout 重定向后的日志文件，更可靠。
    """
    if log_file is None:
        return False
    try:
        raw = log_file.read_bytes()
    except (OSError, FileNotFoundError):
        return False
    if not raw:
        return False
    try:
        text = raw.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return False
    marker = f":{int(port)}"
    return "http://" in text and marker in text


def wait_for_server_ready(
    host: str,
    port: int,
    pid: int,
    timeout_seconds: float = 120.0,
    *,
    log_file: Path | None = None,
    child_process: subprocess.Popen | None = None,
) -> None:
    """等待页面服务开始监听端口。

    Windows 上策略：
    1. 主就绪信号 = 子进程日志中出现 `http://host:port`（`create_app` 内部已完成 bind+activate，紧接着 print 该行）。
    2. 辅助验证 = 一次 TCP 连接，用尽量短的 timeout。
    3. 若 log 已显示就绪但 TCP 连接仍持续失败超过 grace period（说明本机的 EDR/SASE 把 loopback connect 也吃掉了），
       则信任 log，直接返回。
    """
    suffix = f" 日志文件: {log_file}" if log_file else ""
    deadline = time.monotonic() + timeout_seconds
    specs = _probe_socket_specs(host, port)
    connect_timeout = 0.3
    edr_fallback_grace_seconds = 3.0
    log_ready_since: float | None = None
    next_progress_at = time.monotonic() + 5.0

    while time.monotonic() < deadline:
        if child_process is not None:
            exit_code = child_process.poll()
            if exit_code is not None:
                tail = _tail_log_snippet(log_file)
                extra = f"\n--- 日志尾部 ---\n{tail}" if tail else ""
                raise RuntimeError(f"页面服务子进程已退出（退出码 {exit_code}），端口未就绪。{suffix}{extra}")
        if not process_exists(pid):
            tail = _tail_log_snippet(log_file)
            extra = f"\n--- 日志尾部 ---\n{tail}" if tail else ""
            raise RuntimeError(f"页面服务启动后立刻退出，请查看日志。{suffix}{extra}")

        log_ready = _log_signals_listening(log_file, port)
        if log_ready and log_ready_since is None:
            log_ready_since = time.monotonic()

        for family, addr in specs:
            if _try_tcp_connect(family, addr, timeout=connect_timeout):
                return

        if log_ready_since is not None and (time.monotonic() - log_ready_since) >= edr_fallback_grace_seconds:
            # 子进程已 print URL，端口确实已 bind；本机 loopback 连接被 EDR 静默吞掉时，避免无意义阻塞。
            return

        if time.monotonic() >= next_progress_at:
            hint = "（已检测到子进程输出 URL，验证中…）" if log_ready else ""
            print(f"... 等待页面服务就绪{hint}{suffix}", flush=True)
            next_progress_at = time.monotonic() + 5.0

        time.sleep(0.2)

    tail = _tail_log_snippet(log_file)
    extra = f"\n--- 日志尾部 ---\n{tail}" if tail else ""
    raise RuntimeError(f"页面服务启动超时，请查看日志。{suffix}{extra}")


def restart_web_server(
    *,
    skill_root: Path,
    host: str | None = None,
    port: int | None = None,
    python_executable: str | None = None,
) -> RestartResult:
    """停止旧页面服务并重新拉起一个新进程（Windows）。"""
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
        wait_for_server_ready(
            effective_host,
            effective_port,
            process.pid,
            log_file=paths.log_file,
            child_process=process,
        )
    except KeyboardInterrupt:
        try:
            terminate_pid(process.pid, timeout_seconds=3.0)
        except Exception:
            pass
        if paths.pid_file.exists():
            paths.pid_file.unlink()
        raise
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
