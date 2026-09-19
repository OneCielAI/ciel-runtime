"""Muse Code message injection: paths, parameters, and capability rules.

Ciel Runtime injects messages into interactive runtimes in more than one way,
with more than one set of parameters - wake policy, display-only previews,
approval handling, idempotency. Muse Code can be reached through four distinct
paths, and they do not carry the same parameters:

``msp``
    A ``muse serve`` host the runtime owns, driven over the Muse Session
    Protocol (see :mod:`ciel_runtime_support.muse_msp`). This is the only path
    that can express an explicit busy disposition (``queue``/``steer``/
    ``replace``), display text, idempotent command ids, and wire-level approval
    and user-input answers.
``console``
    The interactive TUI through the terminal proxy (bracketed paste and
    submit-retry parameters), which is what interactive Muse launches use
    today.
``exec``
    A headless one-shot ``muse exec`` with its own parameter set (prompt file,
    provider/model/base-url, permission profile, JSONL events).
``session-message``
    Muse's cross-session message bus (``muse session-message send``). The body
    carries the delivery request in plain language, the receiving side
    approves, and the platform/rollout gates apply.

This module models the options once, maps them onto whichever path the caller
selects, and reports honestly when a path cannot honour an option instead of
silently degrading it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

MUSE_INJECTION_TRANSPORTS = ("msp", "console", "exec", "session-message")
MUSE_INJECTION_INTENTS = ("queue", "steer", "replace", "notify")
MUSE_INJECTION_WAKE = ("idle", "now", "notify")
# Muse's own sandbox posture flags for a `serve` host; fixed for its lifetime.
MUSE_HOST_SANDBOX_FLAGS = {
    "disable_shell": "--disable-shell",
    "disable_write": "--disable-write",
    "disable_sandbox": "--disable-sandbox",
    "trust_workspace": "--trust-workspace",
}
MUSE_SERVE_INTERACTIVE = "muse serve"


class MuseInjectionError(RuntimeError):
    """The requested path cannot deliver this message with these options."""


@dataclass(frozen=True, slots=True)
class MuseInjectionOptions:
    """Everything a caller may tune when injecting one message."""

    transport: str = "console"
    # Our channel delivery intentions, not Muse's spellings.
    intent: str = "queue"
    wake: str = "idle"
    model_visible: bool = True
    display_text: str | None = None
    approval_mode: str | None = None
    reasoning_effort: str | None = None
    sandbox: tuple[str, ...] = ()
    durable: bool = False
    workspace: str | None = None
    session: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    permission_profile: str | None = None
    command_id: str | None = None
    timeout_seconds: float = 60.0
    extras: Mapping[str, Any] = field(default_factory=dict)

    def validated(self) -> "MuseInjectionOptions":
        if self.transport not in MUSE_INJECTION_TRANSPORTS:
            raise MuseInjectionError(
                f"unknown injection transport {self.transport!r}; "
                f"known: {', '.join(MUSE_INJECTION_TRANSPORTS)}"
            )
        if self.intent not in MUSE_INJECTION_INTENTS:
            raise MuseInjectionError(
                f"unknown injection intent {self.intent!r}; "
                f"known: {', '.join(MUSE_INJECTION_INTENTS)}"
            )
        if self.wake not in MUSE_INJECTION_WAKE:
            raise MuseInjectionError(
                f"unknown wake policy {self.wake!r}; "
                f"known: {', '.join(MUSE_INJECTION_WAKE)}"
            )
        unknown = [flag for flag in self.sandbox if flag not in MUSE_HOST_SANDBOX_FLAGS]
        if unknown:
            raise MuseInjectionError(
                f"unknown sandbox option(s): {', '.join(unknown)}; "
                f"known: {', '.join(MUSE_HOST_SANDBOX_FLAGS)}"
            )
        return self


def options_from_payload(payload: Mapping[str, Any] | None) -> MuseInjectionOptions:
    """Build options from a wire/config payload, ignoring unknown keys."""

    if not payload:
        return MuseInjectionOptions()
    known = {field_name for field_name in MuseInjectionOptions.__dataclass_fields__}
    values: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    for key, value in payload.items():
        if key in known and key != "extras":
            values[key] = value
        else:
            extras[key] = value
    sandbox = values.get("sandbox")
    if isinstance(sandbox, str):
        values["sandbox"] = tuple(part.strip() for part in sandbox.split(",") if part.strip())
    elif isinstance(sandbox, (list, tuple)):
        values["sandbox"] = tuple(str(part) for part in sandbox)
    if extras:
        values["extras"] = extras
    return MuseInjectionOptions(**values).validated()


def if_busy_for_intent(intent: str) -> str | None:
    """Map a delivery intention onto the MSP ``ifBusy`` disposition."""

    if intent == "steer":
        return "steer"
    if intent == "replace":
        return "replace"
    if intent == "notify":
        return None
    return "queue"


def wake_reaches_model(wake: str, *, intent: str) -> bool:
    """Whether this wake policy lets the message reach the model's context."""

    if wake == "notify" or intent == "notify":
        return False
    return True


def serve_host_argv(
    executable: str | Sequence[str],
    options: MuseInjectionOptions,
    *,
    echo_delay_ms: int | None = None,
) -> list[str]:
    """Build the ``muse serve`` argv for an MSP session host."""

    argv = list(executable) if not isinstance(executable, str) else [executable]
    argv.append("serve")
    if not options.durable:
        argv.append("--no-session-log")
    for flag in options.sandbox:
        mapped = MUSE_HOST_SANDBOX_FLAGS.get(flag)
        if mapped:
            argv.append(mapped)
    if echo_delay_ms is not None:
        argv.extend(["--echo-delay-ms", str(int(echo_delay_ms))])
    return argv


def exec_argv(
    executable: str | Sequence[str],
    options: MuseInjectionOptions,
    *,
    prompt_file: str | Path,
) -> list[str]:
    """Build the headless one-shot argv for the ``exec`` path."""

    argv = list(executable) if not isinstance(executable, str) else [executable]
    argv.append("exec")
    argv.extend(["--prompt-file", str(prompt_file), "--json"])
    if options.provider:
        argv.extend(["--provider", options.provider])
    if options.model:
        argv.extend(["--model", options.model])
    if options.base_url:
        argv.extend(["--base-url", options.base_url])
    if options.reasoning_effort:
        argv.extend(["--reasoning-effort", options.reasoning_effort])
    if options.permission_profile:
        argv.extend(["--permission-profile", options.permission_profile])
    if options.workspace:
        argv.extend(["--workspace", options.workspace])
    if options.approval_mode:
        argv.extend(["--approval-mode", options.approval_mode])
    if not options.durable:
        argv.append("--no-session-log")
    return argv


def session_message_argv(
    executable: str | Sequence[str],
    options: MuseInjectionOptions,
    *,
    reply_to: str | None = None,
) -> list[str]:
    """Build the ``muse session-message send`` argv for the bus path."""

    if not options.session:
        raise MuseInjectionError("the session-message path requires a target session")
    argv = list(executable) if not isinstance(executable, str) else [executable]
    argv.extend(["session-message", "send", "--target", str(options.session), "--json"])
    if reply_to:
        argv.extend(["--in-reply-to", str(reply_to)])
    return argv


def session_message_body(message: str, options: MuseInjectionOptions) -> str:
    """Render the message with its requested delivery behaviour.

    The bus carries plain text; per Muse's documentation the sender states the
    behaviour it wants (steer the active turn, queue for the next turn, notify
    only), so the request travels as the first line of the body.
    """

    if options.intent == "steer" or (options.intent == "queue" and options.wake == "now"):
        prefix = "Steer the active turn with this message:"
    elif not wake_reaches_model(options.wake, intent=options.intent):
        prefix = "Notify only - do not add this to your model context:"
    else:
        prefix = "Queue this for your next turn:"
    return f"{prefix}\n\n{message}"


def capability_for(
    transport: str,
    *,
    platform: str | None = None,
    session_message_state: str | None = None,
    has_live_session: bool = True,
    has_host_connection: bool = True,
) -> dict[str, Any]:
    """Report whether a path can deliver, and which parameters it honours.

    ``platform`` follows ``os.name``/``sys.platform`` conventions: the bus is
    unavailable on Windows, and a host connection is required to speak MSP.
    """

    transport = str(transport)
    if transport not in MUSE_INJECTION_TRANSPORTS:
        return {"supported": False, "reason": f"unknown transport {transport!r}"}
    if transport == "msp":
        if not has_host_connection:
            return {
                "supported": False,
                "reason": "no live `muse serve` host connection",
                "requires": "a MuseMspConnection (muse serve)",
            }
        return {
            "supported": True,
            "parameters": (
                "ifBusy=queue|steer|replace",
                "displayText",
                "reasoningEffort",
                "commandId (idempotent retry)",
                "approval/decide",
                "userInput/answer",
                "turn/steer with expectedTurnId",
                "turn/interrupt",
            ),
        }
    if transport == "console":
        if not has_live_session:
            return {"supported": False, "reason": "no interactive session to paste into"}
        return {
            "supported": True,
            "parameters": (
                "bracketed paste",
                "submit retries",
                "confirm submission",
                "wake on delivery",
            ),
        }
    if transport == "exec":
        if has_live_session:
            # A one-shot run is a new session; injecting into a live session
            # needs the msp or console path.
            return {
                "supported": False,
                "reason": "a live session is running; exec starts a new one",
                "alternative": "msp or console",
            }
        return {
            "supported": True,
            "parameters": (
                "prompt file",
                "provider/model/base-url",
                "permission profile",
                "workspace",
                "JSONL events",
            ),
        }
    # session-message
    if platform in {"nt", "win32", "windows"}:
        return {
            "supported": False,
            "reason": "Muse session messaging is unavailable on Windows",
        }
    if session_message_state and session_message_state != "available":
        return {
            "supported": False,
            "reason": f"session-message ingress state: {session_message_state}",
            "alternative": "msp or console",
        }
    return {
        "supported": True,
        "parameters": (
            "target session name/uuid",
            "in-reply-to token",
            "steer|queue|notify delivery (in the body)",
            "8 KiB plain-text limit",
        ),
    }


def options_from_config(
    config: Mapping[str, Any] | None,
    *,
    overlay: Mapping[str, Any] | None = None,
) -> MuseInjectionOptions:
    """Read the injection options a workspace config declares.

    ``config["muse"]["injection"]`` carries the defaults; a per-call
    ``overlay`` (an MCP tool argument or a CLI flag) wins over them. Provider
    routing defaults come from the Model API provider entry so the ``exec``
    path can be pointed at a router base URL without repeating it.
    """

    settings: dict[str, Any] = {}
    if isinstance(config, Mapping):
        section = config.get("muse")
        if isinstance(section, Mapping):
            declared = section.get("injection")
            if isinstance(declared, Mapping):
                settings.update(declared)
        providers = config.get("providers")
        meta = providers.get("meta") if isinstance(providers, Mapping) else None
        if isinstance(meta, Mapping):
            if not settings.get("model") and meta.get("current_model"):
                settings["model"] = meta.get("current_model")
            if not settings.get("base_url") and meta.get("base_url"):
                settings["base_url"] = meta.get("base_url")
    if overlay:
        settings.update(overlay)
    return options_from_payload(
        {key: value for key, value in settings.items() if value is not None}
    )


def render_turn_parts(
    message: str,
    options: MuseInjectionOptions,
) -> list[dict[str, Any]]:
    """Content parts for a turn submission, honouring display-only options."""

    parts: list[dict[str, Any]] = [{"type": "text", "text": message}]
    attachments = options.extras.get("images") if isinstance(options.extras, Mapping) else None
    if isinstance(attachments, Sequence) and not isinstance(attachments, (str, bytes)):
        for entry in attachments:
            if isinstance(entry, Mapping):
                parts.append(dict(entry))
    return parts


@dataclass(frozen=True, slots=True)
class MuseInjectionPorts:
    """Side effects the service needs; all replaceable for tests."""

    log: Callable[[str, str], Any] = lambda _level, _message: None
    platform_name: Callable[[], str] = lambda: ""
    clock: Callable[[], float] = lambda: 0.0
    connection: Callable[[MuseInjectionOptions], Any] | None = None
    console_deliver: Callable[[str, MuseInjectionOptions], bool] | None = None
    exec_runner: Callable[[Sequence[str], str, MuseInjectionOptions], int] | None = None
    session_message_runner: (
        Callable[[Sequence[str], str, MuseInjectionOptions], int] | None
    ) = None
    session_message_state: Callable[[], str] | None = None
    write_prompt_file: Callable[[str], Path] | None = None
    command_ids: Any = None


class MuseInjectionService:
    """Deliver one message through the path and parameters the caller chose."""

    def __init__(self, ports: MuseInjectionPorts) -> None:
        self._ports = ports
        # Command ids the host already applied. Re-using one for a NEW delivery
        # is rejected live as `-32030 command_id_conflict` (2026-09-19), so a
        # key seen here rotates to its next generation; only an unacknowledged
        # attempt may retry with the id it minted.
        self._applied: set[str] = set()
        self._applied_lock = threading.Lock()

    # -- capability --------------------------------------------------------

    def capability(
        self,
        options: MuseInjectionOptions,
        *,
        has_live_session: bool = True,
        has_host_connection: bool | None = None,
    ) -> dict[str, Any]:
        options = options.validated()
        if has_host_connection is None:
            has_host_connection = self._ports.connection is not None
        state = None
        if options.transport == "session-message" and self._ports.session_message_state:
            try:
                state = self._ports.session_message_state()
            except Exception as exc:  # noqa: BLE001 - capability probing must not raise
                state = f"probe failed: {type(exc).__name__}"
        report = capability_for(
            options.transport,
            platform=self._ports.platform_name(),
            session_message_state=state,
            has_live_session=has_live_session,
            has_host_connection=bool(has_host_connection),
        )
        if report.get("supported") and not wake_reaches_model(
            options.wake, intent=options.intent
        ):
            if options.transport == "msp":
                return {
                    "supported": False,
                    "reason": (
                        "the MSP turn API has no display-only submission; "
                        "notify-only needs the session-message path"
                    ),
                    "alternative": "session-message",
                }
        return report

    # -- delivery ----------------------------------------------------------

    def deliver(
        self,
        message: str,
        options: MuseInjectionOptions | None = None,
        *,
        session_id: str | None = None,
        command_key: str | None = None,
        active_turn_id: str | None = None,
        has_live_session: bool = True,
    ) -> dict[str, Any]:
        options = (options or MuseInjectionOptions()).validated()
        report = self.capability(options, has_live_session=has_live_session)
        if not report.get("supported"):
            raise MuseInjectionError(
                f"{options.transport} cannot deliver this message: {report.get('reason')}"
            )
        if options.transport == "msp":
            return self._deliver_msp(
                message,
                options,
                session_id=session_id,
                command_key=command_key,
                active_turn_id=active_turn_id,
            )
        if options.transport == "console":
            return self._deliver_console(message, options)
        if options.transport == "exec":
            return self._deliver_exec(message, options)
        return self._deliver_session_message(message, options)

    # -- per-path implementations -----------------------------------------

    def _command_id(self, key: str) -> tuple[str, bool]:
        """Return (command id, reuse flag); a fresh id after an applied one."""

        minted = self._ports.command_ids
        with self._applied_lock:
            applied = key in self._applied
        if minted is not None:
            return minted.for_key(key, fresh=applied), applied
        from .muse_msp import muse_command_id

        return muse_command_id(seed=f"{key}#{int(applied)}"), applied

    def _mark_applied(self, key: str) -> None:
        with self._applied_lock:
            self._applied.add(key)
            if len(self._applied) > 4096:
                self._applied.clear()
                self._applied.add(key)

    def _deliver_msp(
        self,
        message: str,
        options: MuseInjectionOptions,
        *,
        session_id: str | None,
        command_key: str | None,
        active_turn_id: str | None,
    ) -> dict[str, Any]:
        if self._ports.connection is None:
            raise MuseInjectionError("no MSP host connection is configured")
        target = session_id or options.session
        if not target:
            raise MuseInjectionError("the msp path requires a target session id")
        connection = self._ports.connection(options)
        key = command_key or options.command_id or f"turn:{target}:{message[:64]}"
        if options.command_id:
            command_id, reused = options.command_id, True
        else:
            command_id, reused = self._command_id(key)
        parts = render_turn_parts(message, options)
        disposition = if_busy_for_intent(options.intent)
        if options.intent == "steer" and active_turn_id:
            result = connection.turn_steer(
                command_id=command_id,
                session_id=target,
                expected_turn_id=active_turn_id,
                parts=parts,
                reasoning_effort=options.reasoning_effort,
                timeout=options.timeout_seconds,
            )
            self._mark_applied(key)
            return {
                "transport": "msp",
                "session_id": target,
                "command_id": command_id,
                "command_reused": reused,
                "method": "turn/steer",
                "turn_id": result.get("turnId") or active_turn_id,
                "status": result.get("status") or "accepted",
                "disposition": "steer",
                "raw": result,
            }
        result = connection.turn_start(
            command_id=command_id,
            session_id=target,
            parts=parts,
            display_text=options.display_text,
            if_busy=disposition,
            reasoning_effort=options.reasoning_effort,
            timeout=options.timeout_seconds,
        )
        self._mark_applied(key)
        return {
            "transport": "msp",
            "session_id": target,
            "command_id": command_id,
            "command_reused": reused,
            "method": "turn/start",
            "turn_id": result.get("turnId"),
            "status": result.get("status"),
            "disposition": result.get("disposition") or disposition,
            "started_new_turn": result.get("startedNewTurn"),
            "raw": result,
        }

    def _deliver_console(
        self, message: str, options: MuseInjectionOptions
    ) -> dict[str, Any]:
        runner = self._ports.console_deliver
        if runner is None:
            raise MuseInjectionError("no console delivery port is configured")
        delivered = bool(runner(message, options))
        return {
            "transport": "console",
            "delivered": delivered,
            "wake": options.wake,
            "display_text": options.display_text,
        }

    def _deliver_exec(self, message: str, options: MuseInjectionOptions) -> dict[str, Any]:
        runner = self._ports.exec_runner
        writer = self._ports.write_prompt_file
        if runner is None or writer is None:
            raise MuseInjectionError("no headless exec port is configured")
        prompt_file = writer(message)
        argv = exec_argv(self._executable(options), options, prompt_file=prompt_file)
        returncode = int(runner(argv, message, options))
        return {
            "transport": "exec",
            "argv": argv,
            "prompt_file": str(prompt_file),
            "returncode": returncode,
        }

    def _deliver_session_message(
        self, message: str, options: MuseInjectionOptions
    ) -> dict[str, Any]:
        runner = self._ports.session_message_runner
        if runner is None:
            raise MuseInjectionError("no session-message port is configured")
        argv = session_message_argv(self._executable(options), options)
        body = session_message_body(message, options)
        returncode = int(runner(argv, body, options))
        return {
            "transport": "session-message",
            "argv": argv,
            "target": options.session,
            "intent": options.intent,
            "wake": options.wake,
            "returncode": returncode,
        }

    def _executable(self, options: MuseInjectionOptions) -> Any:
        executable = options.extras.get("executable") if options.extras else None
        if isinstance(executable, (str, list, tuple)):
            return executable
        return "muse"


def options_with(
    options: MuseInjectionOptions, **changes: Any
) -> MuseInjectionOptions:
    """Return a copy of ``options`` with validated changes applied."""

    return replace(options, **changes).validated()


def session_message_state_from_probe(output: str) -> str:
    """Read the ingress state from ``muse session-message list --json``."""

    try:
        payload = json.loads(str(output or "").strip().splitlines()[-1])
    except (TypeError, ValueError, IndexError):
        return "unknown"
    if not isinstance(payload, Mapping):
        return "unknown"
    status = str(payload.get("status") or "").strip()
    error_code = str(payload.get("error_code") or "").strip()
    if status == "available":
        return "available"
    if error_code:
        return error_code
    return status or "unknown"


__all__ = [
    "MUSE_HOST_SANDBOX_FLAGS",
    "MUSE_INJECTION_INTENTS",
    "MUSE_INJECTION_TRANSPORTS",
    "MUSE_INJECTION_WAKE",
    "MUSE_SERVE_INTERACTIVE",
    "MuseInjectionError",
    "MuseInjectionOptions",
    "MuseInjectionPorts",
    "MuseInjectionService",
    "capability_for",
    "exec_argv",
    "if_busy_for_intent",
    "options_from_config",
    "options_from_payload",
    "options_with",
    "render_turn_parts",
    "serve_host_argv",
    "session_message_argv",
    "session_message_body",
    "session_message_state_from_probe",
    "wake_reaches_model",
]
