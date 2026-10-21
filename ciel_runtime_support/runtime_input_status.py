"""Durable lifecycle state for private runtime input requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import json
import math
import os
import uuid
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .channel_message_repository import ChannelFileLockBusy, exclusive_file_lock


RUNTIME_INPUT_STATES = frozenset({"queued", "submitted", "replied", "failed", "skipped", "interrupted_uncertain"})
# skipped: the scan consumed the input without delivering it (for example a
# notice superseded by a newer one); a queued record would otherwise look
# pending forever.
_TERMINAL_STATES = frozenset({"replied", "failed", "skipped", "interrupted_uncertain"})

_HISTORY_LINE_BUDGET = 256 * 1024
_HISTORY_DIAGNOSTIC_LIMIT = 32
# The history is append-only and tens of thousands of inputs pass a day. Past
# this size it is rewritten as one latest row per request; superseded rows and
# unreadable lines are deleted, never archived.
_HISTORY_COMPACT_BYTES = 4 * 1024 * 1024
_HISTORY_COMPACT_RETRY_SECONDS = 60.0


class _HistoryDecodeFailure(ValueError):
    pass


def _history_object(pairs):
    item = {}
    for key, value in pairs:
        if key in item:
            raise _HistoryDecodeFailure("duplicate_key")
        item[key] = value
    return item


def _history_constant(_value):
    raise _HistoryDecodeFailure("nonfinite_number")


def _history_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise _HistoryDecodeFailure("nonfinite_number")
    return number


def _decode_history_row(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _HistoryDecodeFailure("invalid_utf8") from exc
    if "\x00" in text:
        raise _HistoryDecodeFailure("nul_byte")
    try:
        item = json.loads(text, object_pairs_hook=_history_object,
                          parse_constant=_history_constant, parse_float=_history_float)
    except json.JSONDecodeError as exc:
        raise _HistoryDecodeFailure("invalid_json") from exc
    if not isinstance(item, dict):
        raise _HistoryDecodeFailure("invalid_record")
    request_id = item.get("request_id")
    # Keep sparse/string-ID legacy-shaped rows valid. The exact writer schema
    # below is only for diagnostics, not a migration or admission requirement.
    valid_id = (type(request_id) is int and request_id > 0) or (
        isinstance(request_id, str) and request_id.isascii()
        and request_id.isdigit() and int(request_id) > 0)
    if not valid_id or item.get("status") not in RUNTIME_INPUT_STATES:
        raise _HistoryDecodeFailure("invalid_record")
    if "data" in item and not isinstance(item["data"], dict):
        raise _HistoryDecodeFailure("invalid_record")
    return item


class StatusHistoryUnreadable(ChannelFileLockBusy):
    """The history file could not be read now; defer like a busy lock.

    Reading nothing would make delivered inputs look new.
    """


class WakeAttemptConflict(RuntimeError):
    """Batch admission was refused before any ownership record or send."""

    def __init__(self, request_id: int, status: str) -> None:
        self.request_id = request_id
        self.status = status
        super().__init__(f"wake ownership conflict request_id={request_id} status={status}")


@dataclass(slots=True)
class RuntimeInputStatusRepository:
    path: Path
    publish_event: Callable[..., Any]
    log: Callable[[str, str], None]
    lock: threading.Lock | threading.RLock
    # Lowest input id the queue can still return. Statuses below it can never
    # be selected again, so compaction deletes them; 0 keeps every request.
    request_floor: Callable[[], int] = lambda: 0
    _compact_after: float = field(default=0.0, init=False, repr=False)
    _compacted_size: int = field(default=0, init=False, repr=False)
    _damage_logged: tuple[int, int] = field(default=(0, 0), init=False, repr=False)

    def transition(
        self,
        request_id: int,
        status: str,
        *,
        reason: str = "",
        data: dict[str, Any] | None = None,
        expected_attempt: str | None = None,
    ) -> dict[str, Any]:
        request_id = int(request_id)
        status = str(status or "").strip().lower()
        if request_id <= 0:
            raise ValueError("request_id must be positive")
        if status not in RUNTIME_INPUT_STATES:
            raise ValueError(f"invalid runtime input status: {status}")
        with self.lock, exclusive_file_lock(self.path):
            current = self.get(request_id, _locked=True)
            current_status = str((current or {}).get("status") or "")
            current_data = dict((current or {}).get("data") or {})
            if expected_attempt is not None and current_data.get("attempt_id") != expected_attempt:
                return current or {}
            data = {**current_data, **(data or {})}
            if current_status == status:
                # Equal status with new ownership data (a launch attempt
                # adopted by attempt bookkeeping) still records once, so the
                # row carries the epoch its reconciliation needs.
                if data and dict((current or {}).get("data") or {}) != data:
                    return self._record(request_id, status, reason, data)
                return current or self._record(request_id, status, reason, data)
            if status == "skipped" and not current_status:
                # Only queued runtime inputs carry a status; a skipped message
                # that never had one needs no record.
                return {}
            late_completion = current_status == "failed" and status == "replied"
            if current_status in _TERMINAL_STATES and not late_completion:
                return current or self._record(request_id, status, reason, data)
            allowed = {
                "": {"queued", "submitted"} if data.get("attempt_id") else {"queued"},
                "queued": {"submitted", "failed", "skipped"},
                "submitted": {"replied", "failed", "interrupted_uncertain"},
                # A late completion corrects a failed record: the failure only
                # said the confirmation window expired, while the wake evidence
                # now proves the message ran (2026-09-22: message 6450 was
                # injected 12 minutes late and recorded failed while the session
                # had already run it).
                "failed": {"replied"},
            }
            if status not in allowed.get(current_status, set()):
                self.log(
                    "WARN",
                    "runtime_input_status_transition_rejected "
                    f"request_id={request_id} from={current_status or '-'} to={status}",
                )
                return current or {}
            record = self._record(request_id, status, reason, data)
        self.publish_event(
            level="error" if status == "failed" else "info",
            category="runtime_input.status",
            message=f"runtime input {status}",
            source="runtime-input",
            request_id=str(request_id),
            data=record,
        )
        return record

    def begin_attempt(self, message_ids: list[int], child_epoch: str) -> str:
        """Persist ownership before a transport is permitted to send a batch."""
        if not child_epoch:
            raise ValueError("child epoch is required")
        attempt_id = uuid.uuid4().hex
        ids = sorted(set(int(value) for value in message_ids if int(value) > 0))
        writing = False
        try:
            with self.lock, exclusive_file_lock(self.path):
                latest = {int(row["request_id"]): row for row in self._items()
                          if str(row.get("request_id", "")).isdigit()}
                # Validate the whole batch before writing its first member. A
                # completion racing candidate selection must not strand earlier
                # queued work as submitted. Existing ownership is never stolen.
                for request_id in ids:
                    status = str(latest.get(request_id, {}).get("status") or "")
                    if status not in {"", "queued"}:
                        raise WakeAttemptConflict(request_id, status)
                records = []
                for request_id in ids:
                    data = {**(latest.get(request_id, {}).get("data") or {}),
                            "child_epoch": child_epoch, "attempt_id": attempt_id,
                            "batch_ids": ids}
                    writing = True
                    records.append(self._record(request_id, "submitted", "", data))
            for record in records:
                self.publish_event(level="info", category="runtime_input.status",
                                   message="runtime input submitted", source="runtime-input",
                                   request_id=str(record["request_id"]), data=record)
        except ChannelFileLockBusy as exc:
            if writing:
                raise RuntimeError("wake ownership persistence uncertain") from exc
            raise
        return attempt_id

    def reconcile_child_exit(self, child_epoch: str, *, verified_exit: bool) -> list[int]:
        """Quarantine only attempts owned by a child whose exit was verified.

        A new launch scope, timeout, missing transcript, or dequeue is not exit
        proof. No transcript evidence is promoted to completion here.
        """
        if not verified_exit or not child_epoch:
            return []
        with self.lock:
            latest = {int(row["request_id"]): row for row in self._items()
                      if str(row.get("request_id", "")).isdigit()}
        reconciled = []
        for request_id, row in latest.items():
            data = row.get("data") or {}
            if row.get("status") != "submitted" or data.get("child_epoch") != child_epoch:
                continue
            record = self.transition(
                request_id, "interrupted_uncertain", reason="verified_child_exit",
                data={"exit_verified": True}, expected_attempt=data.get("attempt_id"),
            )
            if record.get("status") != "interrupted_uncertain":
                raise RuntimeError("wake interruption persistence rejected")
            reconciled.append(request_id)
        return sorted(reconciled)

    def _record(
        self,
        request_id: int,
        status: str,
        reason: str,
        data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = time.time()
        record: dict[str, Any] = {
            "request_id": request_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "updated_at_epoch": now,
        }
        if reason:
            record["reason"] = str(reason)
        if data:
            record["data"] = dict(data)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                # A torn tail without its newline would swallow this record.
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    line = b"\n" + line
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self._compact_if_oversized()
        return record

    def _compact_if_oversized(self) -> None:
        """Delete old history in place; callers hold the file lock.

        Failure only postpones the next attempt (Windows refuses the replace
        while another process has the file open); the append already stands.
        """
        now = time.monotonic()
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.compact")
        try:
            # Rows the queue still holds survive compaction; wait for the
            # file to double again rather than rescanning it on every append.
            limit = max(_HISTORY_COMPACT_BYTES, 2 * self._compacted_size)
            if now < self._compact_after or self.path.stat().st_size <= limit:
                return
            items, health = self._scan_history()
            if health["coverage"] == "unknown":
                raise OSError("status history read unavailable")
            latest: dict[int, dict[str, Any]] = {}
            for item in items:
                latest[int(item["request_id"])] = item
            # Delete superseded transitions and requests the queue no longer
            # holds; a request still in the queue keeps its ownership.
            floor = int(self.request_floor())
            kept = [request_id for request_id in sorted(latest) if request_id >= floor]
            with temporary.open("wb") as stream:
                for request_id in kept:
                    stream.write(json.dumps(latest[request_id], ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._compacted_size = self.path.stat().st_size
            self.log("INFO", f"runtime_input_status_history_compacted kept={len(kept)} "
                     f"dropped_rows={len(items) - len(kept)}")
        except Exception as exc:
            self._compact_after = now + _HISTORY_COMPACT_RETRY_SECONDS
            self.log("WARN", f"runtime_input_status_history_compact_deferred error={type(exc).__name__}")
            try:
                temporary.unlink()
            except OSError:
                pass

    def get(self, request_id: int, *, _locked: bool = False) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            latest = None
            for item in self._items():
                try:
                    if int(item.get("request_id") or 0) == int(request_id):
                        latest = item
                except (TypeError, ValueError):
                    continue
            return latest

        if _locked:
            return read()
        with self.lock:
            return read()

    def list_latest(
        self,
        *,
        after_request_id: int = 0,
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.lock:
            latest: dict[int, dict[str, Any]] = {}
            for item in self._items():
                try:
                    request_id = int(item.get("request_id") or 0)
                except (TypeError, ValueError):
                    continue
                if request_id > after_request_id:
                    latest[request_id] = item
        expected = str(status or "").strip().lower()
        rows = [latest[key] for key in sorted(latest)]
        if expected:
            rows = [row for row in rows if row.get("status") == expected]
        return rows[: max(1, min(500, int(limit)))]

    def history_health(self) -> dict[str, Any]:
        """Read-only, bounded coverage and payload-free physical diagnostics."""
        with self.lock:
            _, health = self._scan_history()
        return health

    def _items(self) -> list[dict[str, Any]]:
        items, health = self._scan_history()
        if health["coverage"] == "unknown":
            raise StatusHistoryUnreadable(errno.EIO, "runtime input status history unreadable")
        if not health["complete"]:
            # Damaged lines are ignored, never a reason to hold delivery.
            # Report each new damage state once rather than on every read.
            first = health["diagnostics"][0]
            seen = (health["failure_count"], first["line_number"])
            if seen != self._damage_logged:
                self._damage_logged = seen
                self.log("WARN", "runtime_input_status_history_damage_ignored "
                         f"lines={health['failure_count']} first_line={first['line_number']} "
                         f"category={first['failure_category']}")
        return items

    def _scan_history(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items: list[dict[str, Any]] = []
        health: dict[str, Any] = {
            "complete": True, "coverage": "complete", "scanned_bytes": 0,
            "failure_count": 0, "recovered_count": 0,
            "diagnostics": [], "diagnostics_truncated": False,
        }

        def failure(line_number, byte_offset, category):
            health["complete"] = False
            health["coverage"] = "partial"
            health["failure_count"] += 1
            if len(health["diagnostics"]) < _HISTORY_DIAGNOSTIC_LIMIT:
                health["diagnostics"].append({"line_number": line_number, "byte_offset": byte_offset,
                                              "failure_category": category})
            else:
                health["diagnostics_truncated"] = True

        line_number = 0
        offset = 0
        try:
            with self.path.open("rb") as stream:
                while True:
                    raw = stream.readline(_HISTORY_LINE_BUDGET + 1)
                    if not raw:
                        break
                    line_number += 1
                    start = offset
                    offset += len(raw)
                    # A torn append can leave a NUL run of any length before
                    # the record written after it. The NULs are ignored; only
                    # the surviving record counts against the line budget.
                    prefixed = raw.startswith(b"\x00")
                    body = raw.lstrip(b"\x00")
                    while prefixed and not raw.endswith(b"\n") and len(body) <= _HISTORY_LINE_BUDGET:
                        raw = stream.readline(_HISTORY_LINE_BUDGET + 1)
                        if not raw:
                            break
                        offset += len(raw)
                        body = body + raw if body else raw.lstrip(b"\x00")
                    if len(body) > _HISTORY_LINE_BUDGET:
                        while raw and not raw.endswith(b"\n"):
                            raw = stream.readline(_HISTORY_LINE_BUDGET + 1)
                            offset += len(raw)
                        failure(line_number, start, "line_budget_exceeded")
                        continue
                    try:
                        items.append(_decode_history_row(body))
                    except (ValueError, TypeError, RecursionError) as exc:
                        # A complete record without its newline is still
                        # history; only an undecodable tail is unfinished.
                        category = ("unfinished_eof" if not body.endswith(b"\n") else
                                    str(exc) if isinstance(exc, _HistoryDecodeFailure) else "invalid_record")
                        failure(line_number, start, category)
                        continue
                    if prefixed:
                        health["recovered_count"] += 1
        except FileNotFoundError:
            pass
        except OSError:
            failure(line_number, offset, "read_unavailable")
            health["coverage"] = "unknown"
        health["scanned_bytes"] = offset
        return items, health


__all__ = ["RUNTIME_INPUT_STATES", "RuntimeInputStatusRepository", "StatusHistoryUnreadable", "WakeAttemptConflict"]
