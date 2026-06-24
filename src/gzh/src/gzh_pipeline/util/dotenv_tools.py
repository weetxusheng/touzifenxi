"""在任意工作目录启动 CLI 时，仍能从仓库根加载 ``.env``。"""

from __future__ import annotations

from pathlib import Path


def load_dotenv_near_cli(cli_file: str) -> Path | None:
    """
    自 ``cli_file``（一般为 ``…/gzh_pipeline/cli/*.py``）向上查找 ``.env`` 并加载。

    若未找到文件则回退 ``load_dotenv()``（仅当前目录），与旧行为兼容。
    返回已成功加载的路径；若未安装 python-dotenv 则返回 ``None``。
    """
    try:
        from dotenv import load_dotenv as _ld
    except ImportError:
        return None

    root = Path(cli_file).resolve()
    cur = root.parent
    for _ in range(10):
        candidate = cur / ".env"
        if candidate.is_file():
            _ld(candidate, override=False)
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent

    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        _ld(cwd_env, override=False)
        return cwd_env

    _ld(override=False)
    return cwd_env if cwd_env.is_file() else None
