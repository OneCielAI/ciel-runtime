"""Router health identity and diagnostic projection policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RouterHealthPolicy:
    version: str
    source_fingerprint: str
    config_dir: Path
    router_base: str
    pid_path: Path
    current_user: Callable[[], str]
    health: Callable[[], dict[str, Any] | None]
    connectivity_summary: Callable[[], str]

    def summary(
        self,
        health: dict[str, Any] | None = None,
    ) -> str:
        current = self.health() if health is None else health
        if isinstance(current, dict):
            return (
                "health=ok "
                f"base={self.router_base} "
                f"pid={current.get('pid') or '-'} "
                f"version={current.get('version') or '-'} "
                "source="
                f"{current.get('source_fingerprint') or '-'} "
                f"provider={current.get('provider') or '-'} "
                f"model={current.get('model') or '-'} "
                f"config_dir={current.get('config_dir') or '-'}"
            )
        return (
            f"health=down base={self.router_base} "
            f"pid_file={self._pid_state()} "
            f"{self.connectivity_summary()}"
        )

    def matches_current(
        self,
        health: dict[str, Any] | None,
    ) -> bool:
        if health is None:
            return False
        return (
            str(health.get("version") or "") == self.version
            and str(health.get("source_fingerprint") or "")
            == self.source_fingerprint
            and str(health.get("user") or "") == self.current_user()
            and self.config_matches_current(health)
        )

    def config_matches_current(
        self,
        health: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(health, dict):
            return False
        return self.path_identity(
            health.get("config_dir")
        ) == self.path_identity(self.config_dir)

    def has_foreign_config(
        self,
        health: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(health, dict):
            return False
        config_dir = self.path_identity(health.get("config_dir"))
        return bool(config_dir) and config_dir != self.path_identity(
            self.config_dir
        )

    @staticmethod
    def path_identity(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(Path(text).expanduser().resolve(strict=False))
        except Exception:
            return text

    def _pid_state(self) -> str:
        try:
            if self.pid_path.exists():
                return (
                    self.pid_path.read_text(encoding="utf-8").strip()
                    or "empty"
                )
        except Exception as exc:
            return f"read_failed:{type(exc).__name__}"
        return "missing"


__all__ = ["RouterHealthPolicy"]
