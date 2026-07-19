"""Persistent single-slot repository for channel-triggered compact requests."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ChannelCompactRequestRepository:
    def __init__(
        self,
        path: Path,
        lock: Any,
        save: Callable[[dict[str, Any], str], None],
        truncate: Callable[[str, int], str],
        log: Callable[[str, str], None],
        ttl: Callable[[], float],
    ) -> None:
        self.path = path
        self.lock = lock
        self.save = save
        self.truncate = truncate
        self.log = log
        self.ttl = ttl

    def payload(self, source: str, reason: str) -> dict[str, Any]:
        now = time.time()
        return {
            "id": uuid.uuid4().hex,
            "command": "/compact",
            "source": str(source or "mcp"),
            "reason": self.truncate(str(reason or ""), 1000),
            "requested_at": now,
            "expires_at": now + self.ttl(),
            "pid": os.getpid(),
        }

    def queue(self, source: str = "mcp", reason: str = "") -> dict[str, Any]:
        request = self.payload(source, reason)
        with self.lock:
            self.save(request, "channel_compact_request")
        self.log(
            "INFO",
            f"channel_compact_request_queued id={request['id']} source={request['source']}",
        )
        return request

    def read(self) -> dict[str, Any] | None:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except Exception as exc:
                self.log(
                    "WARN",
                    f"channel_compact_request_read_failed error={type(exc).__name__}: {exc}",
                )
                return None
            if not isinstance(data, dict):
                return None
            try:
                expires_at = float(data.get("expires_at") or 0)
            except Exception:
                expires_at = 0.0
            if expires_at and time.time() > expires_at:
                self._unlink(str(data.get("id") or "-"))
                self.log(
                    "INFO", f"channel_compact_request_expired id={data.get('id') or '-'}"
                )
                return None
            return data

    def clear(self, request_id: str | None = None) -> bool:
        with self.lock:
            if request_id:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    return False
                except Exception:
                    data = {}
                if isinstance(data, dict) and str(data.get("id") or "") != str(request_id):
                    return False
            return self._unlink(request_id or "-", missing=False)

    def _unlink(self, request_id: str, *, missing: bool = True) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return missing
        except Exception as exc:
            self.log(
                "WARN",
                f"channel_compact_request_clear_failed id={request_id} "
                f"error={type(exc).__name__}: {exc}",
            )
            return False


def compact_request_ttl(value: str | None) -> float:
    if value is None:
        return 600.0
    try:
        return max(5.0, min(3600.0, float(str(value).strip())))
    except (TypeError, ValueError):
        return 600.0
