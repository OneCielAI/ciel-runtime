"""Workspace-local completed Work retention; no transcript or artifact deletion."""
from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any, Callable

from .work_plan_store import WorkPlanStore
from .work_plan_tree import COMPLETED_RETENTION_SECONDS, WorkPlanError

WORK_COMPLETED_AUTO_DELETE_KEY = "work_completed_auto_delete"


def retention_enabled(config: dict[str, Any]) -> bool:
    return config.get(WORK_COMPLETED_AUTO_DELETE_KEY) is not False


def retention_status(config: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": retention_enabled(config), "completed_after_seconds": COMPLETED_RETENTION_SECONDS,
            "sweep_interval_seconds": 60, "focused_items_preserved": True}


class WorkRetentionService:
    def __init__(self, state_dir: Path, load_config: Callable[[], dict[str, Any]],
                 log: Callable[[str, str], Any], *, interval: float = 60.0) -> None:
        self.root = Path(state_dir) / "work-sessions"
        self.load_config = load_config
        self.log = log
        self.interval = max(1.0, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep(self) -> int:
        if (not retention_enabled(self.load_config()) or not self.root.is_dir()
                or self.root.is_symlink() or getattr(self.root.lstat(), "st_file_attributes", 0) & 1024):
            return 0
        removed = 0
        for folder in self.root.iterdir():
            if (not re.fullmatch(r"[a-f0-9]{64}", folder.name) or not folder.is_dir()
                    or folder.is_symlink() or getattr(folder.lstat(), "st_file_attributes", 0) & 1024):
                continue
            path = folder / "work-plan.json"
            if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 1024):
                continue
            try:
                store = WorkPlanStore(path)
                store.read_snapshot()
                removed += store.expire_completed()
            except (OSError, WorkPlanError):
                self.log("WARN", "work_retention_skipped reason=state_unavailable")
        if removed:
            self.log("INFO", f"work_completed_expired nodes={removed}")
        return removed

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.sweep()
                except Exception as error:
                    self.log("WARN", f"work_retention_failed error={type(error).__name__}")
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=run, name="work-retention", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
