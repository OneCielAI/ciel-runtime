"""CLI parameter injection for event, instruction, and memory settings.

The prelaunch menus flip `enabled` and `transport` because a keypress carries no
value.  A command line does, and an injected parameter has to mean the same
thing every time it runs, so these commands take explicit values instead:
`enabled=true`, `transport=sse`.  Boolean spellings are accepted as aliases the
way the rest of the configuration surface accepts them.

Every key not named on the command line keeps its stored value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import urllib.parse
from pathlib import PurePosixPath

from .config_value_codec import parse_bool


TRUE_WORDS = ("true", "yes", "on", "1", "enable", "enabled")
FALSE_WORDS = ("false", "no", "off", "0", "disable", "disabled")

EXTERNAL_EVENT_KEYS = (
    "enabled",
    "transport",
    "url",
    "event_types",
    "cursor_json_pointer",
    "cursor_query_parameter",
    "webhook_secret",
    "authorization",
)
REMOTE_INSTRUCTION_URL_KEYS = (
    "claude_url",
    "codex_url",
    "agy_url",
    "kimi_url",
    "grok_url",
)
REMOTE_INSTRUCTION_KEYS = (
    "enabled",
    *REMOTE_INSTRUCTION_URL_KEYS,
    "authorization",
    "timeout_seconds",
)
REMOTE_MEMORY_KEYS = (
    "enabled",
    "manifest_url",
    "authorization",
    "directory",
    "timeout_seconds",
    "max_manifest_bytes",
    "max_file_bytes",
    "max_total_bytes",
    "max_files",
)
REMOTE_MEMORY_LIMITS = {
    "timeout_seconds": (1, 30),
    "max_manifest_bytes": (1_024, 4_194_304),
    "max_file_bytes": (1_024, 16_777_216),
    "max_total_bytes": (1_024, 134_217_728),
    "max_files": (1, 2_048),
}
_SECRET_KEYS = frozenset({"webhook_secret", "authorization"})


class EventSettingsCliError(ValueError):
    """One rejected command-line parameter, reported without a traceback."""


def split_assignment(token: str, *, bare_keys: tuple[str, ...] = ()) -> tuple[str, str]:
    """Split KEY=VALUE, allowing a few action words to stand on their own."""

    key, separator, value = str(token).partition("=")
    key = key.strip().lower()
    if not separator:
        if key in bare_keys:
            return key, ""
        raise EventSettingsCliError(f"expected KEY=VALUE, received {token!r}")
    return key, value.strip()


def parse_flag(key: str, value: str) -> bool:
    """Read a boolean parameter, refusing anything that is not a known spelling.

    ``parse_bool`` falls back to a default for unknown text, which would let a
    typo silently mean "off" in an injected parameter.
    """

    normalized = value.strip().lower()
    if normalized not in {*TRUE_WORDS, *FALSE_WORDS}:
        raise EventSettingsCliError(
            f"{key} must be one of {', '.join(TRUE_WORDS)} or "
            f"{', '.join(FALSE_WORDS)}; received {value!r}"
        )
    return parse_bool(normalized)


def _validated_url(key: str, value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EventSettingsCliError(f"{key} must be an http:// or https:// URL")
    return url


@dataclass(frozen=True, slots=True)
class EventSettingsCliPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    receiver_service: Callable[[], Any]
    sync_instructions: Callable[[], list[str]]
    sync_memories: Callable[[], list[str]]
    output: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class EventSettingsCli:
    ports: EventSettingsCliPorts
    receiver_id: str = "default"

    # -- external events ---------------------------------------------------

    def external_event_values(self) -> dict[str, Any]:
        service = self.ports.receiver_service()
        current = service.receiver_configs().get(self.receiver_id, {})
        secrets = service.vault.status(self.receiver_id)
        return {
            "enabled": bool(current.get("enabled", False)),
            "transport": str(current.get("transport") or "webhook"),
            "url": str(current.get("url") or ""),
            "event_types": ",".join(
                str(value) for value in (current.get("event_types") or [])
            ),
            "cursor_json_pointer": str(current.get("cursor_json_pointer") or ""),
            "cursor_query_parameter": str(current.get("cursor_query_parameter") or ""),
            "webhook_secret": (
                "stored" if secrets.get("stored_webhook_secret") else "unset"
            ),
            "authorization": (
                "stored" if secrets.get("stored_authorization") else "unset"
            ),
        }

    def external_events(self, args: Any) -> None:
        tokens = [str(value) for value in (getattr(args, "values", None) or [])]
        if not tokens:
            self._report("external-events", self.external_event_values())
            return
        service = self.ports.receiver_service()
        current = service.receiver_configs().get(self.receiver_id, {})
        body: dict[str, Any] = {
            "enabled": bool(current.get("enabled", False)),
            "transport": str(current.get("transport") or "webhook"),
            "url": str(current.get("url") or ""),
            "event_types": list(current.get("event_types") or []),
            "cursor_json_pointer": str(current.get("cursor_json_pointer") or ""),
            "cursor_query_parameter": str(current.get("cursor_query_parameter") or ""),
        }
        changed: list[str] = []
        for token in tokens:
            key, value = split_assignment(token)
            if key not in EXTERNAL_EVENT_KEYS:
                raise EventSettingsCliError(
                    f"unsupported external event option: {key}; expected one of "
                    f"{', '.join(EXTERNAL_EVENT_KEYS)}"
                )
            if key == "enabled":
                body["enabled"] = parse_flag(key, value)
            elif key == "transport":
                transport = value.strip().lower()
                if transport not in {"webhook", "sse"}:
                    raise EventSettingsCliError("transport must be webhook or sse")
                body["transport"] = transport
            elif key == "event_types":
                body["event_types"] = [
                    part.strip() for part in value.split(",") if part.strip()
                ]
            else:
                body[key] = value
            changed.append(key)
        service.save_receiver(self.receiver_id, body)
        self._confirm("external-events", changed)

    # -- remote instructions -----------------------------------------------

    def _remote_settings(self) -> dict[str, Any]:
        value = self.ports.load_config().get("remote_instructions")
        return value if isinstance(value, dict) else {}

    def remote_instruction_values(self) -> dict[str, Any]:
        current = self._remote_settings()
        values: dict[str, Any] = {"enabled": bool(current.get("enabled", False))}
        for key in REMOTE_INSTRUCTION_URL_KEYS:
            values[key] = str(current.get(key) or "")
        values["authorization"] = "stored" if current.get("authorization") else "unset"
        values["timeout_seconds"] = current.get("timeout_seconds") or 5
        return values

    def remote_instructions(self, args: Any) -> None:
        tokens = [str(value) for value in (getattr(args, "values", None) or [])]
        if not tokens:
            self._report("remote-instructions", self.remote_instruction_values())
            return
        # Validate the whole batch before touching the configuration, so one
        # rejected parameter cannot leave the others half applied.
        remote: dict[str, Any] = {}
        changed: list[str] = []
        sync_requested = False
        for token in tokens:
            key, value = split_assignment(token, bare_keys=("sync",))
            if key == "sync":
                # An install script wants one call that stores the URLs and
                # pulls them, so `sync` runs after the batch is persisted.
                sync_requested = True
                continue
            if key not in REMOTE_INSTRUCTION_KEYS:
                raise EventSettingsCliError(
                    f"unsupported remote instruction option: {key}; expected one of "
                    f"{', '.join(REMOTE_INSTRUCTION_KEYS)}, sync"
                )
            if key == "enabled":
                remote["enabled"] = parse_flag(key, value)
            elif key == "timeout_seconds":
                try:
                    seconds = int(value.strip())
                except ValueError:
                    raise EventSettingsCliError(
                        "timeout_seconds must be a whole number from 1 to 30"
                    ) from None
                if not 1 <= seconds <= 30:
                    raise EventSettingsCliError(
                        "timeout_seconds must be a whole number from 1 to 30"
                    )
                remote[key] = seconds
            elif key in REMOTE_INSTRUCTION_URL_KEYS:
                remote[key] = _validated_url(key, value)
            else:
                remote[key] = value
            changed.append(key)
        if not changed:
            self._sync()
            return
        config = self.ports.load_config()
        stored = config.get("remote_instructions")
        if not isinstance(stored, dict):
            stored = {}
            config["remote_instructions"] = stored
        stored.update(remote)
        self.ports.save_config(config)
        self._confirm("remote-instructions", changed)
        if sync_requested:
            self._sync()

    # -- remote memory ----------------------------------------------------

    def _memory_settings(self) -> dict[str, Any]:
        value = self.ports.load_config().get("remote_memory")
        return value if isinstance(value, dict) else {}

    def remote_memory_values(self) -> dict[str, Any]:
        current = self._memory_settings()
        return {
            "enabled": bool(current.get("enabled", False)),
            "manifest_url": str(current.get("manifest_url") or ""),
            "authorization": "stored" if current.get("authorization") else "unset",
            "directory": str(current.get("directory") or "memory"),
            "timeout_seconds": current.get("timeout_seconds") or 5,
            "max_manifest_bytes": current.get("max_manifest_bytes") or 1_048_576,
            "max_file_bytes": current.get("max_file_bytes") or 4_194_304,
            "max_total_bytes": current.get("max_total_bytes") or 33_554_432,
            "max_files": current.get("max_files") or 256,
        }

    def remote_memory(self, args: Any) -> None:
        tokens = [str(value) for value in (getattr(args, "values", None) or [])]
        if not tokens:
            self._report("remote-memory", self.remote_memory_values())
            return
        updates: dict[str, Any] = {}
        changed: list[str] = []
        sync_requested = False
        for token in tokens:
            key, value = split_assignment(token, bare_keys=("sync",))
            if key == "sync":
                sync_requested = True
                continue
            if key not in REMOTE_MEMORY_KEYS:
                raise EventSettingsCliError(
                    f"unsupported remote memory option: {key}; expected one of "
                    f"{', '.join(REMOTE_MEMORY_KEYS)}, sync"
                )
            if key == "enabled":
                updates[key] = parse_flag(key, value)
            elif key == "manifest_url":
                updates[key] = _validated_url(key, value)
            elif key == "directory":
                directory = value.strip()
                if directory:
                    path = PurePosixPath(directory)
                    if (
                        "\\" in directory
                        or ":" in directory
                        or path.is_absolute()
                        or any(part in {"", ".", ".."} for part in path.parts)
                    ):
                        raise EventSettingsCliError(
                            "directory must be a portable path inside the workspace"
                        )
                updates[key] = directory
            elif key in REMOTE_MEMORY_LIMITS:
                minimum, maximum = REMOTE_MEMORY_LIMITS[key]
                try:
                    parsed = int(value.strip())
                except ValueError:
                    raise EventSettingsCliError(
                        f"{key} must be a whole number from {minimum} to {maximum}"
                    ) from None
                if not minimum <= parsed <= maximum:
                    raise EventSettingsCliError(
                        f"{key} must be a whole number from {minimum} to {maximum}"
                    )
                updates[key] = parsed
            else:
                updates[key] = value
            changed.append(key)
        if changed:
            config = self.ports.load_config()
            stored = config.get("remote_memory")
            if not isinstance(stored, dict):
                stored = {}
                config["remote_memory"] = stored
            stored.update(updates)
            self.ports.save_config(config)
            self._confirm("remote-memory", changed)
        if sync_requested:
            for line in self.ports.sync_memories() or ():
                self.ports.output(f"  {line}")

    # -- reporting ---------------------------------------------------------

    def _report(self, command: str, values: dict[str, Any]) -> None:
        self.ports.output(f"{command}:")
        for key, value in values.items():
            shown = value if str(value) else "unset"
            self.ports.output(f"  {key}={shown}")

    def _sync(self) -> None:
        for line in self.ports.sync_instructions() or ():
            self.ports.output(f"  {line}")

    def _confirm(self, command: str, changed: list[str]) -> None:
        applied = ", ".join(
            key if key not in _SECRET_KEYS else f"{key} (stored)" for key in changed
        )
        self.ports.output(f"{command} updated: {applied}")


def _guarded(handler: Callable[[Any], None]) -> Callable[[Any], None]:
    """Report a rejected parameter the way the other settings commands do.

    The receiver service raises `ValueError` for its own contract too, such as
    an SSE receiver with no URL, and none of it is worth a traceback.
    """

    def run(args: Any) -> None:
        try:
            handler(args)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None

    return run


def handlers(ports: EventSettingsCliPorts) -> tuple[Callable[[Any], None], ...]:
    """Return external-event, remote-instruction, and remote-memory handlers."""

    controller = EventSettingsCli(ports)
    return (
        _guarded(controller.external_events),
        _guarded(controller.remote_instructions),
        _guarded(controller.remote_memory),
    )


__all__ = [
    "EXTERNAL_EVENT_KEYS",
    "REMOTE_MEMORY_KEYS",
    "REMOTE_INSTRUCTION_KEYS",
    "REMOTE_INSTRUCTION_URL_KEYS",
    "EventSettingsCli",
    "EventSettingsCliError",
    "EventSettingsCliPorts",
    "handlers",
    "parse_flag",
    "split_assignment",
]
