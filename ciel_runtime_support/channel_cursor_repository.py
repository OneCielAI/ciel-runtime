from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class CursorReadResolution:
    value: int
    persist: bool = False


class ChannelCursorStatePolicy:
    """Merge durable and process-local cursor state monotonically."""

    @staticmethod
    def resolve_read(
        file_cursor: int | None,
        cached_cursor: int | None,
        scan_cursor: Callable[[], int],
    ) -> CursorReadResolution:
        if file_cursor is not None:
            return CursorReadResolution(
                max(file_cursor, cached_cursor or 0)
            )
        if cached_cursor is not None:
            return CursorReadResolution(cached_cursor)
        return CursorReadResolution(max(0, scan_cursor()), persist=True)

    @staticmethod
    def newer(candidate: int | None, current: int) -> int | None:
        if candidate is None or candidate <= current:
            return None
        return candidate


@dataclass(frozen=True, slots=True)
class ChannelCursorRepository:
    path: Path
    log: Callable[[str, str], None]

    def read(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.log(
                "WARN",
                f"channel_cursor_read_failed path={self.path} error={type(exc).__name__}: {exc}",
            )
            return None
        if not isinstance(data, dict):
            self.log("WARN", f"channel_cursor_read_failed path={self.path} error=invalid_payload")
            return None
        try:
            return max(0, int(data.get("last_id") or 0))
        except (TypeError, ValueError) as exc:
            self.log(
                "WARN",
                f"channel_cursor_read_failed path={self.path} error={type(exc).__name__}: {exc}",
            )
            return None

    def write(self, last_id: int, *, metadata: dict[str, Any] | None = None) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {"last_id": max(0, int(last_id))}
            if metadata:
                payload.update(metadata)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return True
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            self.log(
                "WARN",
                f"channel_cursor_write_failed path={self.path} error={type(exc).__name__}: {exc}",
            )
            return False
