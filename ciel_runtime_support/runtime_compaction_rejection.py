"""Native compaction ownership at the legacy router preflight boundary.

The router no longer synthesizes prompt-too-long errors from local size
estimates. The callable/ports remain compatible with the request assembly;
confirmed provider overflow still uses the existing bounded recovery path.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

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
    """Compatibility port: native clients own proactive compaction timing.

    Neither JSON/4 nor a previous request's usage proves that this request
    exceeds the current model context. Real upstream rejections continue
    through the router's existing bounded recovery path.
    """

    def __init__(self, ports: ReactiveRejectionPorts,
                 policy: ReactiveRejectionPolicy = ReactiveRejectionPolicy()) -> None:
        self.ports = ports
        self.policy = policy

    def reject_request(self, provider: str, config: dict[str, Any], body: dict[str, Any], runtime: str,
                       headers: Any = None) -> dict[str, Any] | None:
        return None


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
