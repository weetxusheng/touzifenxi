"""load_dotenv_near_cli 查找 .env。"""

from pathlib import Path


def test_dotenv_found_ascending_from_cli_file(tmp_path, monkeypatch):
    repo = tmp_path / "gongzhonghao"
    cli_dir = repo / "src" / "gzh_pipeline" / "cli"
    cli_dir.mkdir(parents=True)
    cli_file = cli_dir / "parse.py"
    cli_file.write_text("# stub", encoding="utf-8")
    env_path = repo / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=testkey123\n", encoding="utf-8")

    (tmp_path / "other_cwd").mkdir()
    monkeypatch.chdir(tmp_path / "other_cwd")

    from gzh_pipeline.util.dotenv_tools import load_dotenv_near_cli

    assert load_dotenv_near_cli(str(cli_file)) == env_path
    import os

    assert os.environ.get("DEEPSEEK_API_KEY") == "testkey123"
