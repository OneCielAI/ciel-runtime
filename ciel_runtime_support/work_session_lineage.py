"""Keep Work on an authenticated native parked-session continuation, never latest."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from .channel_message_repository import exclusive_file_lock
from .claude_session_handoff import follow_parked_session, session_records, _live_key
from .work_plan_tree import WorkPlanError

WORK_PLAN_FILE_NAME = "work-plan.json"

IDENTITY = re.compile(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}\Z")


def _path(root: Path, identity: str) -> Path:
    return root / "work-sessions" / hashlib.sha256(identity.encode()).hexdigest() / WORK_PLAN_FILE_NAME


def _alias(path: Path, identity: str) -> str | None:
    marker = path.with_name("work-lineage.json")
    try:
        if marker.is_symlink() or marker.stat().st_size > 4096:
            raise WorkPlanError("Work continuation mapping is invalid")
        value = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise WorkPlanError("Work continuation mapping is unreadable") from exc
    target = value.get("canonical") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get("session") != identity or not isinstance(target, str)
            or not IDENTITY.fullmatch(target) or not target.startswith("claude:")
            or target == identity or value.get("authority") != "native-parked-handoff"):
        raise WorkPlanError("Work continuation mapping is invalid")
    return target


def _canonical(root: Path, identity: str) -> str:
    seen = set()
    for _ in range(8):
        if identity in seen:
            raise WorkPlanError("Work continuation mapping contains a cycle")
        seen.add(identity)
        target = _alias(_path(root, identity), identity)
        if not target:
            return identity
        identity = target
    raise WorkPlanError("Work continuation mapping exceeds the chain limit")


def _parent(identity: str) -> str | None:
    if not identity.startswith("claude:"):
        return None
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"
    records = session_records(directory)
    current = [row for row in records if row.get("sessionId") == identity[7:]]
    if len(current) != 1 or current[0].get("kind") != "bg" or not _live_key(directory, current[0]):
        return None
    child = current[0]
    parents = []
    for row in records:
        if row.get("parkedJobId") != child.get("jobId") or row.get("sessionId") == child["sessionId"]:
            continue
        if not IDENTITY.fullmatch("claude:" + str(row.get("sessionId") or "")):
            continue
        if not _live_key(directory, row):
            continue
        handoff = follow_parked_session(directory, row, records)
        if not handoff.pending and handoff.record.get("sessionId") == child["sessionId"]:
            parents.append("claude:" + row["sessionId"])
    if len(set(parents)) > 1:
        raise WorkPlanError("Work continuation has multiple native parents; explicit recovery required")
    return parents[0] if parents else None


def resolve_work_path(root: Path, identity: str, store_factory: Any) -> Path:
    canonical = _canonical(root, identity)
    if canonical != identity:
        path = _path(root, canonical)
        if not path.is_file():
            raise WorkPlanError("Work continuation source is missing; refusing an empty replacement")
        return path
    parent = _parent(identity)
    if not parent:
        return _path(root, identity)
    parent = _canonical(root, parent)
    source, target = _path(root, parent), _path(root, identity)
    if not source.is_file():
        return target
    source_doc = store_factory(source).read_snapshot()
    if not source_doc["nodes"]:
        return target
    if target.exists():
        # A split board may already contain newer work; never guess a merge.
        target_doc = store_factory(target).read_snapshot()
        if target_doc["nodes"] or target_doc["version"]:
            raise WorkPlanError("Work continuation has two saved boards; both preserved, explicit recovery required")
    target.parent.mkdir(parents=True, exist_ok=True)
    marker = target.with_name("work-lineage.json")
    temporary = marker.with_name(marker.name + f".{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump({"session": identity, "canonical": parent, "authority": "native-parked-handoff"}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)
    return source


class WorkSessionStore:
    """Resolve under one workspace lock on every operation, including stale callers."""
    def __init__(self, root: Path, identity: str, *, store_factory: Any, notify: Any, log: Any = None) -> None:
        self.root, self.identity, self.log = root, identity, log
        self.store_factory, self.notify = store_factory, notify
        self.lock = root / "work-sessions" / "lineage"

    def _snapshot_path(self) -> Path:
        canonical = _canonical(self.root, self.identity)
        if canonical != self.identity:
            path = _path(self.root, canonical)
            if not path.is_file():
                raise WorkPlanError("Work continuation source is missing; refusing an empty replacement")
            return path
        if not _parent(self.identity):
            return _path(self.root, self.identity)
        # Only first discovery commits a mapping. Ordinary status reads remain lock-free.
        with exclusive_file_lock(self.lock):
            return resolve_work_path(self.root, self.identity, self.store_factory)

    @property
    def path(self) -> Path:
        return self._snapshot_path()

    def read(self) -> dict[str, Any]:
        return self.store_factory(self._snapshot_path(), log=self.log).read()

    def read_snapshot(self) -> dict[str, Any]:
        return self.store_factory(self._snapshot_path(), log=self.log).read_snapshot()

    def transaction(self, mutate: Any, *, expected_version: int | None = None) -> Any:
        notifications = []
        with exclusive_file_lock(self.lock):
            result = self.store_factory(resolve_work_path(self.root, self.identity, self.store_factory), log=self.log).transaction(
                mutate, expected_version=expected_version, _deferred_notifications=notifications)
        for notification in notifications:
            self.notify(*notification)
        return result

    def expire_completed(self, *, now: float | None = None) -> int:
        from .work_plan_tree import expire_completed
        if not self.path.is_file() or self.path.is_symlink():
            return 0
        _, removed = self.transaction(lambda doc: expire_completed(doc, now=now))
        return removed
