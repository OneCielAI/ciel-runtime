"""Durable lifecycle state for private runtime input requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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

_HISTORY_BYTE_BUDGET = 8 * 1024 * 1024
_HISTORY_LINE_BUDGET = 256 * 1024
_HISTORY_DIAGNOSTIC_LIMIT = 32


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


def _nul_remainder_identity(raw: bytes) -> dict[str, Any]:
    """Describe a writer-shaped remainder, never recover it as history."""
    prefix = len(raw) - len(raw.lstrip(b"\x00"))
    if not prefix or not raw.endswith(b"\n"):
        return {}
    try:
        row = _decode_history_row(raw[prefix:])
    except (ValueError, TypeError, RecursionError):
        return {}
    required = {"request_id", "status", "updated_at", "updated_at_epoch"}
    if not required <= row.keys() or row.keys() - (required | {"reason", "data"}):
        return {}
    if type(row["request_id"]) is not int or not isinstance(row["updated_at"], str):
        return {}
    try:
        stamp = datetime.fromisoformat(row["updated_at"])
    except ValueError:
        return {}
    if stamp.tzinfo is None or type(row["updated_at_epoch"]) not in (int, float):
        return {}
    if "reason" in row and not isinstance(row["reason"], str):
        return {}
    return {"remainder_request_id": row["request_id"], "remainder_status": row["status"]}


class WakeHistoryUnavailable(RuntimeError):
    """Ownership cannot be established from an incomplete durable history."""

    def __init__(self, line_number: int = 0) -> None:
        self.line_number = line_number
        super().__init__("wake ownership history contains an incomplete record")


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
            from .runtime_input_recovery_marker import guard_status_history
            guard_status_history(self.path, [request_id])
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
                from .runtime_input_recovery_marker import guard_status_history
                guard_status_history(self.path, ids)
                latest = {int(row["request_id"]): row for row in self._items(strict=True)
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
        except (ChannelFileLockBusy, WakeHistoryUnavailable) as exc:
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
        # Do not advance a scan frontier from a truncated ownership history.
        if self.path.exists() and self.path.stat().st_size > 8 * 1024 * 1024:
            raise RuntimeError("wake ownership history exceeds reconciliation budget")
        with self.lock:
            latest = {int(row["request_id"]): row for row in self._items(strict=True)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def get(self, request_id: int, *, _locked: bool = False) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            latest = None
            for item in self._items(strict=_locked):
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

    def _items(self, *, strict: bool = False) -> list[dict[str, Any]]:
        from .runtime_input_recovery_marker import guard_status_history
        guard_status_history(self.path)
        items, health = self._scan_history()
        if not health["complete"]:
            first = health["diagnostics"][0]
            if strict:
                raise WakeHistoryUnavailable(first["line_number"])
            self.log("WARN", "runtime_input_status_history_incomplete "
                     f"coverage={health['coverage']} line={first['line_number']} "
                     f"byte_offset={first['byte_offset']} category={first['failure_category']}")
        return items

    def _scan_history(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items: list[dict[str, Any]] = []
        health: dict[str, Any] = {
            "complete": True, "coverage": "complete", "scanned_bytes": 0,
            "failure_count": 0, "diagnostics": [], "diagnostics_truncated": False,
        }

        def failure(line_number, byte_offset, category, raw=b""):
            health["complete"] = False
            health["coverage"] = "partial"
            health["failure_count"] += 1
            if len(health["diagnostics"]) < _HISTORY_DIAGNOSTIC_LIMIT:
                diagnostic = {"line_number": line_number, "byte_offset": byte_offset,
                              "failure_category": category}
                if raw.startswith(b"\x00"):
                    identity = _nul_remainder_identity(raw)
                    if identity:
                        diagnostic["failure_category"] = "leading_nul_prefix_candidate"
                        diagnostic.update(identity)
                health["diagnostics"].append(diagnostic)
            else:
                health["diagnostics_truncated"] = True

        line_number = 0
        offset = 0
        opened = False
        try:
            with self.path.open("rb") as stream:
                opened = True
                before = os.fstat(stream.fileno())
                while offset < _HISTORY_BYTE_BUDGET:
                    raw = stream.readline(min(_HISTORY_LINE_BUDGET + 1,
                                              _HISTORY_BYTE_BUDGET - offset))
                    if not raw:
                        break
                    line_number += 1
                    start = offset
                    offset += len(raw)
                    health["scanned_bytes"] = offset
                    if len(raw) > _HISTORY_LINE_BUDGET:
                        failure(line_number, start, "line_budget_exceeded")
                        break
                    if not raw.endswith(b"\n"):
                        category = ("byte_budget_exceeded" if before.st_size > offset
                                    and offset == _HISTORY_BYTE_BUDGET else "unfinished_eof")
                        failure(line_number, start, category)
                        break
                    try:
                        items.append(_decode_history_row(raw))
                    except (ValueError, TypeError, RecursionError) as exc:
                        category = str(exc) if isinstance(exc, _HistoryDecodeFailure) else "invalid_record"
                        failure(line_number, start, category, raw)
                after = os.fstat(stream.fileno())
                current = self.path.stat()
                if before.st_size > offset and offset == _HISTORY_BYTE_BUDGET and raw.endswith(b"\n"):
                    failure(line_number + 1, offset, "byte_budget_exceeded")
                def signature(stat):
                    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if signature(before) != signature(after) or signature(after) != signature(current):
                    failure(line_number, offset, "changed_during_read")
        except FileNotFoundError:
            if opened:
                failure(line_number, offset, "changed_during_read")
        except OSError:
            failure(line_number, offset, "read_unavailable")
            health["coverage"] = "unknown"
        return items, health


__all__ = ["RUNTIME_INPUT_STATES", "RuntimeInputStatusRepository", "WakeAttemptConflict", "WakeHistoryUnavailable"]
