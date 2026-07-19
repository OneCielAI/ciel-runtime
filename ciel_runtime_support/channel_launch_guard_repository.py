from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelLaunchGuardRepository:
    path: Path
    now: Callable[[], float]
    log: Callable[[str, str], None]

    def read(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.log(
                "WARN",
                f"channel_llm_launch_guard_read_failed error={type(exc).__name__}: {exc}",
            )
            return None
        if not isinstance(data, dict):
            self.log("WARN", "channel_llm_launch_guard_read_failed error=invalid_payload")
            return None
        try:
            expires_at = float(data.get("expires_at") or 0)
            max_existing_id = int(data.get("max_existing_id") or 0)
        except (TypeError, ValueError) as exc:
            self.log(
                "WARN",
                f"channel_llm_launch_guard_read_failed error={type(exc).__name__}: {exc}",
            )
            return None
        if expires_at <= self.now() or max_existing_id <= 0:
            return None
        return {"max_existing_id": max_existing_id, "expires_at": expires_at}

    def write(self, max_existing_id: int, ttl_seconds: float = 180.0) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            now = self.now()
            payload = {
                "created_at": now,
                "expires_at": now + max(1.0, float(ttl_seconds)),
                "max_existing_id": max(0, int(max_existing_id)),
            }
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            self.log(
                "WARN",
                f"channel_llm_launch_guard_write_failed error={type(exc).__name__}: {exc}",
            )
