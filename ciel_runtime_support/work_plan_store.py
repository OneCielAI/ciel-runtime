"""Durable per-session storage for the Work plan document.

Every mutation runs under an in-process lock plus the cross-process advisory
file lock, re-reads the stored document, applies the change and replaces the
file atomically.  ``expected_version`` turns a write into a compare-and-swap:
a caller holding a stale version is rejected instead of overwriting a newer
plan.  A corrupt file is moved aside, never silently overwritten.
"""

from __future__ import annotations

import copy
import hashlib
import re
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support.channel_message_repository import exclusive_file_lock
from ciel_runtime_support.work_plan_tree import WorkPlanError, empty_document, normalize_document

WORK_PLAN_FILE_NAME = "work-plan.json"


class WorkPlanConflict(WorkPlanError):
    """The stored plan changed since the caller read it."""


class WorkPlanStore:
    def __init__(self, path: Path, *, log: Callable[[str, str], Any] | None = None) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._log = log

    def read(self) -> dict[str, Any]:
        with self._lock, exclusive_file_lock(self.path):
            return self._read_unlocked()

    def transaction(
        self,
        mutate: Callable[[dict[str, Any]], Any],
        *,
        expected_version: int | None = None,
    ) -> tuple[dict[str, Any], Any]:
        """Apply ``mutate`` to a copy of the stored document and persist it if it changed."""

        with self._lock, exclusive_file_lock(self.path):
            current = self._read_unlocked()
            if expected_version is not None and int(expected_version) != int(current["version"]):
                raise WorkPlanConflict(
                    f"the plan changed (stored version {current['version']}, expected {expected_version}); "
                    "call work_status and retry"
                )
            working = copy.deepcopy(current)
            result = mutate(working)
            if working != current:
                working["version"] = int(current["version"]) + 1
                working["updated_at"] = time.time()
                self._write_unlocked(working)
                return working, result
            return current, result

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty_document()
        try:
            return normalize_document(json.loads(text))
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            quarantine = self.path.with_name(f"{self.path.name}.corrupt-{time.time_ns()}")
            try:
                os.replace(self.path, quarantine)
            except OSError as move_error:
                raise WorkPlanError("invalid Work plan could not be quarantined; writes are blocked") from move_error
            if self._log is not None:
                self._log("WARN", f"work_plan_quarantined path={quarantine} reason={type(error).__name__}")
            raise WorkPlanError("invalid Work plan was quarantined; inspect the saved file before rebuilding") from error

    def _write_unlocked(self, doc: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.path)


_STORES: dict[str, WorkPlanStore] = {}
_STORES_LOCK = threading.Lock()


def session_work_plan_store(state_dir: Path, session_id: str, *,
                            log: Callable[[str, str], Any] | None = None) -> WorkPlanStore:
    """One store per exact conversation, never the legacy workspace-wide plan.

    Clients qualify the conversation id with their runtime (claude:/codex:).
    A missing identity must fail closed rather than select a shared default.
    Hashing keeps caller-controlled identifiers out of filesystem paths.
    """

    if not isinstance(session_id, str) or not re.fullmatch(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}", session_id):
        raise WorkPlanError("a runtime-qualified conversation session_id is required (claude:<id> or codex:<id>)")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    path = Path(state_dir) / "work-sessions" / digest / WORK_PLAN_FILE_NAME
    key = str(path.resolve()) if path.parent.exists() else str(path)
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = WorkPlanStore(path, log=log)
            _STORES[key] = store
        return store
