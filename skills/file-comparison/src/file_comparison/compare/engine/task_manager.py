
"""网页后台任务与 batch 重跑。

职责：`TaskManager` 后台执行 `run_task`、`rerun_batch` 清理单 batch 现场。
不负责：单 pair 比较细节（见 `pair_compare`）。
"""

from __future__ import annotations

import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ...runtime.checkpoint import PairCheckpointStore, atomic_write_json, pair_checkpoint_path
from ...runtime.config import FileComparisonRuntimeConfig
from ...runtime.execution import PairManifest, TaskManifest
from ...runtime.settings import AppPaths, pair_dir_for, prepare_run_dir, resolve_paths
from ..artifacts import write_status_json
from ..models import PairMatch
from ..pairing import scan_folder_for_pairs
from .abort_control import clear_abort_requested_flag, touch_abort_requested_flag
from .batch_validation import _read_json_object
from .task_runner import run_task

_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "partial_failed", "aborted"})

class TaskManager:
    """为本地页面提供后台任务创建与线程托管能力。"""

    def __init__(self, runtime_config: FileComparisonRuntimeConfig, paths: AppPaths | None = None) -> None:
        """保存运行配置、输出路径和后台线程表。"""
        self.runtime_config = runtime_config
        self.paths = paths or resolve_paths()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def create_task(self, folder_path: Path, pairs: list[PairMatch] | None = None) -> TaskManifest:
        """创建一个后台运行任务，并立即返回初始 manifest。"""
        run_dir = prepare_run_dir(self.paths.runs_root)
        task_id = run_dir.name
        task_started_wall_at = datetime.now().astimezone().isoformat()
        pairs = pairs or scan_folder_for_pairs(folder_path, self.runtime_config.pairing.month_pattern)
        initial_manifest = TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status="pending",
            poll_interval_seconds=self.runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=0,
            failed_count=0,
            completed_pair_count=0,
            failed_pair_count=0,
            pairs=tuple(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="pending",
                )
                for pair in pairs
            ),
            started_at=task_started_wall_at,
        )
        atomic_write_json(
            run_dir / "task.json",
            {
                "task_id": task_id,
                "folder_path": str(folder_path),
                "pair_count": len(pairs),
                "generated_at": task_started_wall_at,
                "started_at": task_started_wall_at,
            },
        )
        write_status_json(run_dir, initial_manifest)
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir, pairs), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return initial_manifest

    def rerun_batch(self, task_id: str, pair_id: str, batch_id: str) -> dict[str, str]:
        """清理单个 batch 的恢复现场，并启动同任务恢复执行。"""
        task_id = str(task_id).strip()
        pair_id = str(pair_id).strip()
        batch_id = str(batch_id).strip()
        if not task_id or not pair_id or not batch_id:
            raise ValueError("task_id、pair_id、batch_id 不能为空")
        with self._lock:
            current_thread = self._threads.get(task_id)
            if current_thread and current_thread.is_alive():
                raise RuntimeError("任务仍在运行，当前请求结束后再重跑该批次")
        run_dir = self.paths.runs_root / task_id
        if not run_dir.exists():
            raise FileNotFoundError(f"task not found: {task_id}")
        task_payload = _read_json_object(run_dir / "task.json")
        status_payload = _read_json_object(run_dir / "status.json")
        pairs = _pairs_from_status_payload(status_payload)
        if not pairs:
            raise ValueError("任务状态中没有可恢复的文件配对")
        target_pair = next((pair for pair in pairs if pair.pair_id == pair_id), None)
        if target_pair is None:
            raise ValueError(f"pair not found: {pair_id}")
        self._clear_batch_for_rerun(run_dir=run_dir, pair_id=pair_id, batch_id=batch_id)
        clear_abort_requested_flag(run_dir)
        folder_path = Path(str(task_payload.get("folder_path") or target_pair.old_path.parent)).expanduser().resolve()
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir, pairs), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return {"task_id": task_id, "pair_id": pair_id, "batch_id": batch_id, "status": "running"}

    def request_abort(self, task_id: str) -> dict[str, str]:
        """请求暂停当次任务：执行线程会在当前文件对结束后停止后续文件对。"""
        task_id = str(task_id).strip()
        if not task_id:
            raise ValueError("task_id 不能为空")
        run_dir = self.paths.runs_root / task_id
        if not run_dir.is_dir():
            raise FileNotFoundError(f"task not found: {task_id}")
        status_payload = _read_json_object(run_dir / "status.json")
        if not status_payload:
            raise FileNotFoundError(f"task not found: {task_id}")
        st = str(status_payload.get("status", "")).strip()
        if st in _TERMINAL_TASK_STATUSES:
            raise RuntimeError("任务已结束，无法暂停")
        touch_abort_requested_flag(run_dir)
        return {"task_id": task_id, "status": "abort_requested"}

    def _clear_batch_for_rerun(self, *, run_dir: Path, pair_id: str, batch_id: str) -> None:
        """删除单个 batch 的过程文件、checkpoint entry 和旧产物。"""
        pair_dir = pair_dir_for(run_dir, pair_id)
        batch_dir = pair_dir / "llm" / batch_id
        if batch_dir.exists():
            shutil.rmtree(batch_dir)
        ckpt_path = pair_checkpoint_path(run_dir, pair_id)
        if ckpt_path.exists() or (ckpt_path.parent / f"pair_{pair_id}_checkpoint.json").exists():
            pair_store = PairCheckpointStore.load_or_create(ckpt_path, pair_id=pair_id, task_id=run_dir.name)
            pair_store.remove_entry(batch_id)
        outputs_dir = pair_dir / "outputs"
        if outputs_dir.is_dir():
            for target in tuple(outputs_dir.glob("*.docx")) + tuple(outputs_dir.glob("*.doc")):
                if target.exists():
                    target.unlink()

    def _run_background(self, folder_path: Path, run_dir: Path, pairs: list[PairMatch]) -> None:
        """在线程中执行真实任务，并在结束后清理线程登记。"""
        try:
            run_task(folder_path=folder_path, runtime_config=self.runtime_config, run_dir=run_dir, pairs=pairs)
        finally:
            with self._lock:
                self._threads.pop(run_dir.name, None)

def _pairs_from_status_payload(payload: dict[str, Any]) -> list[PairMatch]:
    """从 status.json 中恢复任务的文件配对列表。"""
    raw_pairs = payload.get("pairs", [])
    if not isinstance(raw_pairs, list):
        return []
    pairs: list[PairMatch] = []
    for index, item in enumerate(raw_pairs, start=1):
        if not isinstance(item, dict):
            continue
        old_path = Path(str(item.get("old_path", ""))).expanduser().resolve()
        new_path = Path(str(item.get("new_path", ""))).expanduser().resolve()
        if not old_path.exists() or not new_path.exists():
            continue
        pairs.append(
            PairMatch(
                pair_id=str(item.get("pair_id") or f"pair-{index:03d}"),
                key=str(item.get("key") or old_path.stem),
                old_path=old_path,
                new_path=new_path,
                old_label=old_path.name,
                new_label=new_path.name,
            )
        )
    return pairs
