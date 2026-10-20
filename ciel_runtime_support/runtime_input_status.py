"""Durable lifecycle state for private runtime input requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
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

    def _items(self, *, strict: bool = False) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        if strict:
                            raise WakeHistoryUnavailable(line_number)
                        continue
                    if isinstance(item, dict):
                        items.append(item)
        except OSError as exc:
            if strict:
                raise
            self.log(
                "WARN",
                f"runtime_input_status_read_failed error={type(exc).__name__}: {exc}",
            )
        return items


__all__ = ["RUNTIME_INPUT_STATES", "RuntimeInputStatusRepository", "WakeAttemptConflict", "WakeHistoryUnavailable"]
