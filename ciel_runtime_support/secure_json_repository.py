from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable


@dataclass(frozen=True)
class SecureJsonEffects:
    log: Callable[[str, str], None]
    chmod: Callable[[Path, int], None] = os.chmod
    process_id: Callable[[], int] = os.getpid
    time_ns: Callable[[], int] = time.time_ns


@dataclass(frozen=True)
class SecureJsonRepository:
    path: Path
    effects: SecureJsonEffects

    def load(self, purpose: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.effects.log(
                "WARN",
                f"secure_json_read_failed purpose={purpose} path={self.path} "
                f"error={type(exc).__name__}: {exc}",
            )
            return None
        return value if isinstance(value, dict) else {}

    def quarantine(self, purpose: str) -> Path | None:
        """Move an unreadable state file aside so a fresh cycle can start.

        The original bytes are kept for diagnosis. A move that fails leaves
        the file in place, so callers keep failing closed rather than
        overwriting evidence they could not read.
        """

        target = self.path.with_name(f"{self.path.name}.corrupt-{self.effects.time_ns()}")
        try:
            self.path.replace(target)
            try:
                self.effects.chmod(target, 0o600)
            except OSError:
                pass
        except OSError as exc:
            self.effects.log(
                "WARN",
                f"secure_json_quarantine_failed purpose={purpose} path={self.path} "
                f"error={type(exc).__name__}: {exc}",
            )
            return None
        self.effects.log(
            "WARN",
            f"secure_json_quarantined purpose={purpose} path={self.path} moved_to={target}",
        )
        return target

    def save(self, value: dict[str, Any], purpose: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(
            f"{self.path.name}.{self.effects.process_id()}.{self.effects.time_ns()}.tmp"
        )
        temporary_path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            self.effects.chmod(temporary_path, 0o600)
        except OSError as exc:
            self.effects.log(
                "WARN",
                f"secure_json_chmod_failed purpose={purpose} path={temporary_path} "
                f"error={type(exc).__name__}: {exc}",
            )
        temporary_path.replace(self.path)
