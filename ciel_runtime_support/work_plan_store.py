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
MAX_WORK_PLAN_BYTES = 32 * 1024 * 1024

# Observers are process-wide so writes through independently constructed stores
# reach the same subscriber. Events are invalidations, not an ordered journal.
WorkPlanChangeCallback = Callable[[Path, dict[str, Any], dict[str, Any]], Any]
_OBSERVERS: dict[object, WorkPlanChangeCallback] = {}
_OBSERVERS_LOCK = threading.Lock()


def subscribe_work_plan_changes(callback: WorkPlanChangeCallback) -> Callable[[], None]:
    """Subscribe to commits from all stores in this process.

    ``callback(path, before, after)`` receives detached documents after atomic
    replacement and outside store/file/observer locks. Filter by exact store path
    and project only safe fields before forwarding an event to external clients.
    Concurrent commits can deliver out of order; consumers must compare versions
    or reread. Unsubscribe is idempotent; an already captured callback may finish.
    Callback failures cannot roll back or fail a committed transaction.
    """

    if not callable(callback):
        raise TypeError("Work plan change callback must be callable")
    token = object()
    with _OBSERVERS_LOCK:
        _OBSERVERS[token] = callback

    def unsubscribe() -> None:
        with _OBSERVERS_LOCK:
            _OBSERVERS.pop(token, None)

    return unsubscribe


def _notify_changes(path: Path, before: dict[str, Any], after: dict[str, Any]) -> None:
    with _OBSERVERS_LOCK:
        callbacks = tuple(_OBSERVERS.values())
    for callback in callbacks:
        try:
            callback(path, copy.deepcopy(before), copy.deepcopy(after))
        except Exception:
            pass


class WorkPlanConflict(WorkPlanError):
    """The stored plan changed since the caller read it."""


class WorkPlanStore:
    def __init__(self, path: Path, *, log: Callable[[str, str], Any] | None = None) -> None:
        self.path = Path(path).resolve()
        self._lock = threading.RLock()
        self._log = log

    def read(self) -> dict[str, Any]:
        with self._lock, exclusive_file_lock(self.path):
            return self._read_unlocked()

    def read_snapshot(self) -> dict[str, Any]:
        """Read one bounded snapshot without locks, directory creation or quarantine.

        This is intended for status/event consumers that must not create lock-file
        sidecars or mutate a corrupt artifact merely by observing it.
        """

        payload = None
        for attempt in range(20):
            try:
                with self.path.open("rb") as handle:
                    payload = handle.read(MAX_WORK_PLAN_BYTES + 1)
                break
            except FileNotFoundError:
                return empty_document()
            except PermissionError as error:
                if attempt == 19:
                    raise WorkPlanError("work plan snapshot could not be read") from error
                time.sleep(0.002)
            except OSError as error:
                raise WorkPlanError("work plan snapshot could not be read") from error
        if payload is None:
            raise WorkPlanError("work plan snapshot could not be read")
        if len(payload) > MAX_WORK_PLAN_BYTES:
            raise WorkPlanError("work plan snapshot exceeds the size limit")
        try:
            return normalize_document(json.loads(payload.decode("utf-8")))
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise WorkPlanError("invalid Work plan snapshot") from error

    def transaction(
        self,
        mutate: Callable[[dict[str, Any]], Any],
        *,
        expected_version: int | None = None,
        _deferred_notifications: list | None = None,
    ) -> tuple[dict[str, Any], Any]:
        """Apply ``mutate`` to a copy and persist changed state with CAS semantics."""

        if expected_version is not None and type(expected_version) is not int:
            raise WorkPlanError("expected_version must be an integer")
        notification: tuple[dict[str, Any], dict[str, Any]] | None = None
        with self._lock, exclusive_file_lock(self.path):
            current = self._read_unlocked()
            if expected_version is not None and expected_version != current["version"]:
                raise WorkPlanConflict(
                    f"the plan changed (stored version {current['version']}, expected {expected_version}); "
                    "call work_status and retry"
                )
            working = copy.deepcopy(current)
            result = mutate(working)
            if working != current:
                working["version"] = current["version"] + 1
                working["updated_at"] = time.time()
                self._write_unlocked(working)
                notification = (copy.deepcopy(current), copy.deepcopy(working))
                current = working
        if notification is not None:
            if _deferred_notifications is not None:
                _deferred_notifications.append((self.path, *notification))
            else:
                _notify_changes(self.path, *notification)
        return current, result

    def expire_completed(self, *, now: float | None = None) -> int:
        from .work_plan_tree import expire_completed
        if not self.path.is_file() or self.path.is_symlink():
            return 0
        _, removed = self.transaction(lambda doc: expire_completed(doc, now=now))
        return removed

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            with self.path.open("rb") as handle:
                payload = handle.read(MAX_WORK_PLAN_BYTES + 1)
        except FileNotFoundError:
            return empty_document()
        except OSError as error:
            raise WorkPlanError("work plan could not be read") from error
        if len(payload) > MAX_WORK_PLAN_BYTES:
            raise WorkPlanError("work plan exceeds the size limit")
        try:
            return normalize_document(json.loads(payload.decode("utf-8")))
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
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
        payload = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
        if len(payload) > MAX_WORK_PLAN_BYTES:
            raise WorkPlanError("work plan exceeds the size limit")
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(20):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.002)
        finally:
            temporary.unlink(missing_ok=True)


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
    from .work_session_lineage import WorkSessionStore
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    path = Path(state_dir) / "work-sessions" / digest / WORK_PLAN_FILE_NAME
    key = os.path.normcase(str(path.resolve()))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = WorkSessionStore(Path(state_dir), session_id, store_factory=WorkPlanStore,
                                     notify=_notify_changes, log=log)
            _STORES[key] = store
        return store
