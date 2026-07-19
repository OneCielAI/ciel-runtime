"""Runtime launch history repository and session-switch policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class LaunchStateRepository:
    path: Path
    config_dir: Path
    log: Callable[[str, str], None]
    process_id: Callable[[], int]
    clock: Callable[[], float]
    clock_ns: Callable[[], int]

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, state: dict[str, Any]) -> None:
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(
                f"{self.path.name}.{self.process_id()}.{self.clock_ns()}.tmp"
            )
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.path)
        except Exception as exc:
            self.log("WARN", f"launch_state_write_failed error={type(exc).__name__}: {exc}")

    def previous_for_cwd(self, cwd_key: str) -> dict[str, Any]:
        state = self.read()
        by_cwd = state.get("by_cwd")
        if isinstance(by_cwd, dict) and isinstance(item := by_cwd.get(cwd_key), dict):
            return item
        legacy = state.get("last")
        if isinstance(legacy, dict) and str(legacy.get("cwd") or "") == cwd_key:
            return legacy
        return {}

    def record(self, cwd_key: str, provider: str, mode: str, model: str) -> None:
        state = self.read()
        by_cwd = state.get("by_cwd")
        if not isinstance(by_cwd, dict):
            by_cwd = {}
        item = {
            "cwd": cwd_key,
            "provider": provider,
            "mode": mode,
            "model": model,
            "pid": self.process_id(),
            "time": self.clock(),
        }
        by_cwd[cwd_key] = item
        state["by_cwd"] = by_cwd
        state["last"] = item
        self.write(state)


def current_launch_cwd_key() -> str:
    try:
        return str(Path.cwd().resolve())
    except Exception:
        return str(Path.cwd())


def launch_mode_name(
    provider: str,
    *,
    use_native_anthropic: bool,
    anthropic_routed: bool,
) -> str:
    if use_native_anthropic:
        return "anthropic-native"
    if anthropic_routed:
        return "anthropic-routed"
    return f"router:{provider}"


def last_launch_runtime(repository: LaunchStateRepository, cwd_key: str) -> str:
    state = repository.read()
    item = repository.previous_for_cwd(cwd_key)
    if not item:
        candidate = state.get("last")
        item = candidate if isinstance(candidate, dict) else {}
    mode = str(item.get("mode") or "").strip().lower()
    if mode.startswith("codex"):
        return "codex"
    if mode.startswith("agy"):
        return "agy"
    if mode.startswith("anthropic") or mode.startswith("router:"):
        return "claude"
    return ""


def session_control_requested(passthrough: list[str]) -> bool:
    names = ("-c", "--continue", "-r", "--resume", "--session-id", "--fork-session", "--from-pr")
    return any(
        argument in names or any(argument.startswith(name + "=") for name in names)
        for argument in passthrough
    )


def should_fork_native_session(
    *,
    current_mode: str,
    passthrough: list[str],
    cwd_key: str,
    use_native_anthropic: bool,
    repository: LaunchStateRepository,
) -> tuple[bool, str]:
    if not use_native_anthropic:
        return False, ""
    if session_control_requested(passthrough):
        return False, "explicit_session_control"
    previous_mode = str(repository.previous_for_cwd(cwd_key).get("mode") or "")
    if not previous_mode or previous_mode == current_mode:
        return False, previous_mode or "no_previous_mode"
    return True, previous_mode
