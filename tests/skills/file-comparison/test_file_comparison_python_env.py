from file_comparison.runtime import python_env


def test_maybe_reexec_into_project_python_uses_local_venv_when_current_python_is_external(tmp_path):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    project_python = tmp_path / "repo" / ".venv" / "bin" / "python3"
    script_path = skill_root / "scripts" / "file_comparison_web.py"
    project_python.parent.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    project_python.write_text("", encoding="utf-8")
    script_path.write_text("print('placeholder')\n", encoding="utf-8")
    calls: list[tuple[str, list[str]]] = []

    changed = python_env.maybe_reexec_into_project_python(
        skill_root=skill_root,
        script_path=script_path,
        argv=["--port", "8765"],
        current_executable="/usr/bin/python3",
        execv=lambda executable, argv: calls.append((executable, argv)),
    )

    assert changed is True
    assert calls == [
        (
            str(project_python),
            [str(project_python), str(script_path), "--port", "8765"],
        )
    ]


def test_maybe_reexec_into_project_python_skips_when_already_using_project_venv(tmp_path):
    skill_root = tmp_path / "repo" / "skills" / "file-comparison"
    project_python = tmp_path / "repo" / ".venv" / "bin" / "python3"
    script_path = skill_root / "scripts" / "file_comparison_web.py"
    project_python.parent.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    project_python.write_text("", encoding="utf-8")
    script_path.write_text("print('placeholder')\n", encoding="utf-8")
    calls: list[tuple[str, list[str]]] = []

    changed = python_env.maybe_reexec_into_project_python(
        skill_root=skill_root,
        script_path=script_path,
        argv=["--host", "0.0.0.0"],
        current_executable=str(project_python),
        execv=lambda executable, argv: calls.append((executable, argv)),
    )

    assert changed is False
    assert calls == []
