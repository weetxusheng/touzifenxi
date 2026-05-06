from file_comparison.runtime.config import UIRuntimeConfig
from file_comparison.web import dev_server


def test_resolve_dev_server_paths_uses_project_state_dir(tmp_path):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    paths = dev_server.resolve_dev_server_paths(skill_root)

    assert paths.state_dir == tmp_path / "repo" / "state" / "file-comparison"
    assert paths.pid_file == paths.state_dir / "file-comparison-web.pid"
    assert paths.log_file == paths.state_dir / "file-comparison-web.log"


def test_restart_web_server_stops_existing_processes_and_records_new_pid(tmp_path, monkeypatch):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    script_path = skill_root / "scripts" / "file_comparison_web.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("print('placeholder')\n", encoding="utf-8")

    stopped_pids: list[int] = []
    waited: list[tuple[str, int, int]] = []
    spawned_commands: list[list[str]] = []

    class DummyProcess:
        pid = 98765

    monkeypatch.setattr(dev_server, "load_ui_runtime_config", lambda base_path: UIRuntimeConfig(5.0, "127.0.0.1", 8765))
    monkeypatch.setattr(dev_server, "collect_existing_server_pids", lambda **kwargs: [101, 202])
    monkeypatch.setattr(dev_server, "terminate_pid", lambda pid, timeout_seconds=5.0: stopped_pids.append(pid))
    monkeypatch.setattr(
        dev_server,
        "spawn_server_process",
        lambda **kwargs: spawned_commands.append(kwargs["command"]) or DummyProcess(),
    )
    monkeypatch.setattr(
        dev_server,
        "wait_for_server_ready",
        lambda host, port, pid, timeout_seconds=10.0: waited.append((host, port, pid)),
    )

    result = dev_server.restart_web_server(skill_root=skill_root, host="0.0.0.0", port=9001, python_executable="python3")

    assert stopped_pids == [101, 202]
    assert spawned_commands == [["python3", str(script_path), "--host", "0.0.0.0", "--port", "9001"]]
    assert waited == [("0.0.0.0", 9001, 98765)]
    assert result.pid == 98765
    assert result.url == "http://0.0.0.0:9001"
    assert result.pid_file.read_text(encoding="utf-8").strip() == "98765"


def test_build_web_server_command_omits_missing_overrides(tmp_path):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    command = dev_server.build_web_server_command(skill_root=skill_root, python_executable="python3")

    assert command == [
        "python3",
        str(skill_root / "scripts" / "file_comparison_web.py"),
    ]


def test_build_web_server_command_prefers_project_venv_when_no_explicit_python(tmp_path):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    project_root = tmp_path / "repo"
    venv_python = project_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")

    command = dev_server.build_web_server_command(
        skill_root=skill_root,
        python_executable=None,
    )

    assert command == [
        str(venv_python),
        str(skill_root / "scripts" / "file_comparison_web.py"),
    ]


def test_build_web_server_command_prefers_project_venv_python_by_default(tmp_path):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    project_python = tmp_path / "repo" / ".venv" / "bin" / "python3"
    project_python.parent.mkdir(parents=True, exist_ok=True)
    project_python.write_text("", encoding="utf-8")

    command = dev_server.build_web_server_command(skill_root=skill_root)

    assert command == [
        str(project_python),
        str(skill_root / "scripts" / "file_comparison_web.py"),
    ]
