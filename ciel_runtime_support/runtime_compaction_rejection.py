"""Truthful local rejection that hands an over-budget turn to native compaction.

Claude Code 2.1.303 answers an HTTP 400 ``invalid_request_error`` whose
message contains "prompt is too long" with its own reactive native compaction
and then retries the original turn (local-pressure/run-01, REACTIVE_MESSAGE
verbatim).  The router may send that rejection only for the unmodified main
conversation request of the exact bound scope, before pending input, the
outbound guard or any upstream generation.

The size is a local JSON-size estimate, not a provider token count, so the
message carries no "N tokens > M" pair that Claude Code would parse as one.
Rejections are bounded per pressure cycle; past the bound, or while the
admission controller is blocked, the rejection omits the reactive phrase so
it cannot start another native compaction.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .runtime_compaction_control import CompactionScope

REACTIVE_MESSAGE = (
    "prompt is too long: the unmodified client request exceeds the configured Runtime input budget "
    "(local JSON-size estimate; not a provider token count). Native compaction is required."
)
# Must not match Claude Code's reactive trigger ("prompt is too long",
# "input is too long for requested model", "capability_rejected: prompt_too_long").
BLOCKED_MESSAGE = (
    "the unmodified client request exceeds the configured Runtime input budget "
    "(local JSON-size estimate; not a provider token count). Automatic native compaction "
    "stopped after its bounded attempts for this conversation; run /compact or /clear, "
    "or shorten the request."
)
AGENT_ID_HEADER = "x-claude-code-agent-id"
# Claude Code 2.1.303 sends main|subagent|workflow|compaction|auxiliary|side_reply
# only when gateway hint headers are enabled; absence stays unknown.
REQUEST_CLASS_HEADER = "x-claude-code-request-class"


def _positive(value: Any) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def _header(headers: Any, name: str) -> str:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return ""
    value = getter(name)
    if value is None:
        value = next((item for key, item in dict(headers).items() if str(key).lower() == name), None)
    return str(value or "").strip()


def rejection_payload(message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": "invalid_request_error", "message": message}}


@dataclass(frozen=True, slots=True)
class ReactiveRejectionPolicy:
    max_reactive_rejections: int = 2
    blocked_phases: frozenset[str] = frozenset({"blocked"})


@dataclass(frozen=True, slots=True)
class ReactiveRejectionPorts:
    # Exact bound main-conversation scope of this HTTP request, or None.
    main_scope: Callable[[str, Any, dict[str, Any]], CompactionScope | None]
    compact_kind: Callable[[dict[str, Any]], str | None]
    estimate: Callable[[Any], int]
    input_budget: Callable[[str, dict[str, Any], dict[str, Any]], int]
    # Persisted admission-control state; checked against the scope here.
    state: Callable[[CompactionScope], Mapping[str, Any] | None]
    log: Callable[[str, str], None]


class ReactiveCompactionRejection:
    def __init__(self, ports: ReactiveRejectionPorts,
                 policy: ReactiveRejectionPolicy = ReactiveRejectionPolicy()) -> None:
        self.ports = ports
        self.policy = policy
        self._lock = threading.Lock()
        # (scope key, reactive rejections since the last below-budget request)
        self._cycle: tuple[tuple[str, str, str], int] | None = None

    def reject_request(self, provider: str, config: dict[str, Any], body: dict[str, Any], runtime: str,
                       headers: Any = None) -> dict[str, Any] | None:
        """Error payload for an HTTP 400, or None to continue the request."""

        try:
            return self._decide(provider, config, body, runtime, headers)
        except (OSError, ValueError, TypeError) as exc:
            # Unknown state leaves the request to the ordinary path.
            self.ports.log("WARN", f"runtime_compaction_rejection skipped=state_unavailable runtime={runtime} "
                           f"error={type(exc).__name__}")
            return None

    def _decide(self, provider: str, config: dict[str, Any], body: dict[str, Any], runtime: str,
                headers: Any) -> dict[str, Any] | None:
        # Anthropic owns native Messages compaction; remote clients own their
        # prompt; an in-process call has no client that could compact.
        if runtime != "claude" or provider == "anthropic" or config.get(REMOTE_BRIDGE_CONFIG_MARKER):
            return None
        if headers is None or _header(headers, AGENT_ID_HEADER):
            return None
        request_class = _header(headers, REQUEST_CLASS_HEADER).lower()
        if request_class and request_class != "main":
            return None
        # A native compact request is the recovery itself, whatever its headers.
        if self.ports.compact_kind(body):
            return None
        budget = _positive(self.ports.input_budget(
            provider, config, {"max_tokens": body.get("max_tokens", body.get("max_output_tokens"))}))
        if not budget:
            return None
        scope = self.ports.main_scope(runtime, headers, body)
        if scope is None or scope.runtime != runtime:
            return None
        tokens = _positive(self.ports.estimate({key: value for key, value in body.items() if key != "max_tokens"}))
        key = (scope.runtime, scope.session_id, scope.epoch)
        with self._lock:
            if tokens < budget:
                if self._cycle and self._cycle[0] == key:
                    # The client's own raw request fits again: the cycle ended.
                    self._cycle = None
                return None
            state = self.ports.state(scope)
            phase = (str(state.get("phase") or "") if isinstance(state, Mapping)
                     and state.get("scope") == scope.payload() else "")
            count = self._cycle[1] if self._cycle and self._cycle[0] == key else 0
            blocked = phase in self.policy.blocked_phases
            if not blocked and count < self.policy.max_reactive_rejections:
                self._cycle = (key, count + 1)
        detail = (f"runtime={scope.runtime} session={scope.session_id} epoch={scope.epoch} "
                  f"local_estimate_tokens={tokens} input_budget={budget} phase={phase or 'none'}")
        if blocked or count >= self.policy.max_reactive_rejections:
            reason = "controller_blocked" if blocked else "reactive_attempts_exhausted"
            self.ports.log("WARN", f"runtime_compaction_rejection decision=blocked reason={reason} "
                           f"attempts={count}/{self.policy.max_reactive_rejections} {detail}")
            return rejection_payload(BLOCKED_MESSAGE)
        self.ports.log("INFO", f"runtime_compaction_rejection decision=reactive "
                       f"attempt={count + 1}/{self.policy.max_reactive_rejections} {detail}")
        return rejection_payload(REACTIVE_MESSAGE)


__all__ = [
    "BLOCKED_MESSAGE",
    "REACTIVE_MESSAGE",
    "ReactiveCompactionRejection",
    "ReactiveRejectionPolicy",
    "ReactiveRejectionPorts",
    "rejection_payload",
]


def build_reactive_compaction_rejection(binding: Any) -> ReactiveCompactionRejection:
    """Compose the router preflight with the existing shared scope controller."""
    return ReactiveCompactionRejection(ReactiveRejectionPorts(
        main_scope=binding._main_session, compact_kind=binding._compact_kind,
        estimate=binding.estimate, input_budget=binding.input_budget,
        state=binding._active_state, log=binding.log,
    ))
