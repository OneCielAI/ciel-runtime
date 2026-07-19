from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable


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
