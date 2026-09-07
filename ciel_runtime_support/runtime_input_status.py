"""Durable lifecycle state for private runtime input requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable


RUNTIME_INPUT_STATES = frozenset({"queued", "submitted", "replied", "failed"})
_TERMINAL_STATES = frozenset({"replied", "failed"})


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
    ) -> dict[str, Any]:
        request_id = int(request_id)
        status = str(status or "").strip().lower()
        if request_id <= 0:
            raise ValueError("request_id must be positive")
        if status not in RUNTIME_INPUT_STATES:
            raise ValueError(f"invalid runtime input status: {status}")
        with self.lock:
            current = self.get(request_id, _locked=True)
            current_status = str((current or {}).get("status") or "")
            if current_status in _TERMINAL_STATES or current_status == status:
                return current or self._record(request_id, status, reason, data)
            allowed = {
                "": {"queued"},
                "queued": {"submitted", "failed"},
                "submitted": {"replied", "failed"},
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

    def _items(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(item, dict):
                        items.append(item)
        except OSError as exc:
            self.log(
                "WARN",
                f"runtime_input_status_read_failed error={type(exc).__name__}: {exc}",
            )
        return items


__all__ = ["RUNTIME_INPUT_STATES", "RuntimeInputStatusRepository"]
