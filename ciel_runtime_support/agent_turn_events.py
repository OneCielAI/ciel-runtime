"""Agent turn ends, as one ``agent.turn_ended`` event per turn.

The router's ``turn.completed`` describes one model API request, so a request
that only asked for a tool and the request that really ended the turn look the
same from outside (Walkie 34059).  The CLI process already decides when a turn
is over: Claude Code transcripts close a turn with ``end_turn`` or
``turn_duration``, Codex rollouts with ``task_complete``/``turn_aborted``, and
the Codex app-server sends ``turn/completed``.  This module turns those records
into one small event per turn -- no conversation text -- and posts it to the
router, which publishes it on ``/ca/tui``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any
import urllib.request

EVENT_KIND = "agent.turn_ended"
POST_PATH = "/ca/tui/agent-turn"
REASONS = frozenset({"end_turn", "interrupted", "error", "max_tokens"})
POST_ATTEMPTS = 5
_ID_LIMIT = 200
_RUNTIME_LIMIT = 40

# Turns these inputs open were not typed by a person.
_AUTOMATIC_PREFIXES = (
    "[ciel-runtime external channel message]",
    "[ciel-wake]",
    "<codex_internal_context",
    "<hook_prompt",
    "<task-notification>",
    "This session is being continued from a previous conversation",
)
# Codex records the turn context as user messages before the real input.
_CODEX_CONTEXT_PREFIXES = (
    "<environment_context",
    "# AGENTS.md instructions",
    "<user_instructions",
    "<permissions instructions",
    "<collaboration_mode",
)
_CLAUDE_INTERRUPT_PREFIX = "[Request interrupted by user"
_CLAUDE_CLOSING_STOPS = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "refusal": "end_turn",
    "max_tokens": "max_tokens",
}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in ("text", "input_text")
        )
    return ""


def typed_by_person(text: str) -> bool:
    """Whether a turn's opening input reads as typed by a person."""

    return not str(text or "").lstrip().startswith(_AUTOMATIC_PREFIXES)


def _iso(value: Any) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def turn_event(
    turn_id: str,
    runtime: str,
    reason: str,
    *,
    by_user_input: bool,
    ended_at: Any = None,
) -> dict[str, Any]:
    return {
        "turn_id": str(turn_id),
        "runtime": str(runtime),
        "ended_at": _iso(ended_at),
        "reason": reason if reason in REASONS else "error",
        "by_user_input": bool(by_user_input),
        # The Stop-hook self check (journal 2026-09-29) is not implemented.
        "stop_check_blocked": False,
    }


def valid_turn_event(body: Any) -> dict[str, Any] | None:
    """The event fields from a posted body, or None when it is not one."""

    if not isinstance(body, dict):
        return None
    turn_id = str(body.get("turn_id") or "").strip()
    runtime = str(body.get("runtime") or "").strip()
    reason = str(body.get("reason") or "").strip()
    if not turn_id or len(turn_id) > _ID_LIMIT or not runtime or len(runtime) > _RUNTIME_LIMIT:
        return None
    if reason not in REASONS:
        return None
    for flag in ("by_user_input", "stop_check_blocked"):
        if not isinstance(body.get(flag), bool):
            return None
    ended_at = str(body.get("ended_at") or "").strip()[:40]
    return {
        "turn_id": turn_id,
        "runtime": runtime,
        "ended_at": ended_at or _iso(None),
        "reason": reason,
        "by_user_input": body["by_user_input"],
        "stop_check_blocked": body["stop_check_blocked"],
    }


class SeenTurns:
    """Turn ids already published; a turn reported twice is published once."""

    def __init__(self, limit: int = 1000) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._limit = limit

    def first(self, turn_id: str) -> bool:
        if turn_id in self._seen:
            return False
        self._seen[turn_id] = None
        while len(self._seen) > self._limit:
            self._seen.popitem(last=False)
        return True


@dataclass(slots=True)
class _OpenTurn:
    turn_id: str
    by_user_input: bool
    error: bool = False


@dataclass(slots=True)
class TranscriptTurnTracker:
    """Follows one transcript's records and reports each turn that ends."""

    runtime: str
    _open: _OpenTurn | None = None
    # Claude: the last input that could have opened the next turn.
    _opener: tuple[str, bool] | None = None
    _codex_input_seen: bool = False
    _closed_message: str = ""
    _reported: list[dict[str, Any]] = field(default_factory=list)

    def feed(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        self._reported = []
        if record.get("type") in ("event_msg", "response_item", "turn_context", "session_meta"):
            self._codex(record)
        else:
            self._claude(record)
        return self._reported

    def _end(self, reason: str, ended_at: Any, turn: _OpenTurn | None = None) -> None:
        turn = turn or self._open
        self._open = None
        if turn is None:
            return
        self._reported.append(
            turn_event(turn.turn_id, self.runtime, reason, by_user_input=turn.by_user_input, ended_at=ended_at)
        )

    # Codex rollout: task_started / task_complete / turn_aborted carry the turn id.
    def _codex(self, record: dict[str, Any]) -> None:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        kind = record.get("type")
        event = str(payload.get("type") or "")
        ended_at = record.get("timestamp")
        if kind == "event_msg" and event in ("task_started", "turn_started"):
            turn_id = str(payload.get("turn_id") or "")
            # Undecided until the turn's first real input arrives.
            self._open = _OpenTurn(turn_id, by_user_input=False) if turn_id else None
            self._codex_input_seen = False
            return
        if kind == "response_item" and event == "message" and payload.get("role") == "user":
            text = _text(payload.get("content")).lstrip()
            if self._open is None or self._codex_input_seen or text.startswith(_CODEX_CONTEXT_PREFIXES):
                return
            self._codex_input_seen = True
            self._open.by_user_input = typed_by_person(text)
            return
        if kind == "response_item" and event == "message" and payload.get("role") == "assistant":
            if self._open is not None:
                self._open.error = False
            return
        if kind == "event_msg" and event == "error":
            if self._open is not None:
                self._open.error = True
            return
        if kind == "event_msg" and event in ("task_complete", "turn_complete", "turn_aborted"):
            turn_id = str(payload.get("turn_id") or "")
            turn = self._open
            if turn is None or (turn_id and turn.turn_id != turn_id):
                # Started before this tail began: who opened it is unknown.
                turn = _OpenTurn(turn_id, by_user_input=False) if turn_id else None
            if event == "turn_aborted":
                reason = "interrupted" if str(payload.get("reason") or "") == "interrupted" else "error"
            else:
                reason = "error" if turn is not None and turn.error else "end_turn"
            self._end(reason, ended_at, turn)

    # Claude Code transcript: a turn is open from the model's first answer to
    # an end_turn / max_tokens answer, an interrupt, an API error, or the
    # turn_duration record.
    def _claude(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        ended_at = record.get("timestamp")
        if kind == "user":
            content = message.get("content")
            text = _text(content).lstrip()
            if text.startswith(_CLAUDE_INTERRUPT_PREFIX):
                self._ensure_open(record)
                self._end("interrupted", ended_at)
                return
            if isinstance(content, list) and any(
                isinstance(item, dict) and item.get("type") == "tool_result" for item in content
            ):
                return
            if record.get("isCompactSummary") is True or text.startswith(("<command-name>", "<local-command-stdout>", "<local-command-caveat>")):
                return
            if self._open is not None:
                return
            origin = record.get("origin")
            automatic = (
                record.get("isMeta") is True
                or (isinstance(origin, dict) and origin.get("kind") == "peer")
                or not typed_by_person(text)
            )
            self._opener = (str(record.get("uuid") or ""), not automatic)
            return
        if kind == "assistant":
            message_id = str(message.get("id") or "")
            if self._open is None and message_id and message_id == self._closed_message:
                # Another content block of the answer that just closed the turn.
                return
            self._ensure_open(record)
            if record.get("isApiErrorMessage") is True:
                self._end("error", ended_at)
                return
            stop = str(message.get("stop_reason") or "").strip().lower()
            if stop in _CLAUDE_CLOSING_STOPS:
                self._closed_message = message_id
                self._end(_CLAUDE_CLOSING_STOPS[stop], ended_at)
            return
        if kind == "system" and record.get("subtype") == "turn_duration":
            self._end("end_turn", ended_at)

    def _ensure_open(self, record: dict[str, Any]) -> None:
        if self._open is not None:
            return
        opener_id, typed = self._opener or ("", False)
        self._opener = None
        self._open = _OpenTurn(opener_id or str(record.get("uuid") or ""), by_user_input=typed)


@dataclass(slots=True)
class AppServerTurnTracker:
    """Turn ends from Codex app-server notifications."""

    runtime: str
    _first_input: dict[str, bool] = field(default_factory=dict)

    def note_input(self, turn_id: str, text: str) -> None:
        stripped = str(text or "").lstrip()
        if turn_id and turn_id not in self._first_input and not stripped.startswith(_CODEX_CONTEXT_PREFIXES):
            self._first_input[turn_id] = typed_by_person(stripped)

    def completed(self, turn: dict[str, Any], *, started_by_ciel: bool) -> dict[str, Any] | None:
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            return None
        typed = self._first_input.pop(turn_id, None)
        status = str(turn.get("status") or "")
        reason = {"completed": "end_turn", "interrupted": "interrupted"}.get(status, "error")
        return turn_event(
            turn_id,
            self.runtime,
            reason,
            by_user_input=False if started_by_ciel else bool(typed),
        )


def user_input_text(params: dict[str, Any]) -> tuple[str, str]:
    """(turn id, text) of an app-server ``item/*`` user message, else empty."""

    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    if item.get("type") != "userMessage":
        return "", ""
    turn_id = str(params.get("turnId") or "")
    return turn_id, _text(item.get("content"))


@dataclass(slots=True)
class RouterTurnPoster:
    """Posts turn ends to the local router; keeps unsent ones for a few tries."""

    base_url: Callable[[], str]
    log: Callable[[str, str], Any]
    urlopen: Callable[..., Any] = urllib.request.urlopen
    timeout: float = 2.0
    _pending: list[tuple[dict[str, Any], int]] = field(default_factory=list)

    def __call__(self, event: dict[str, Any]) -> None:
        self._pending.append((dict(event), 0))
        self.flush()

    def flush(self) -> None:
        pending, self._pending = self._pending, []
        for event, tries in pending:
            if self._send(event):
                continue
            if tries + 1 < POST_ATTEMPTS:
                self._pending.append((event, tries + 1))
            else:
                self.log("WARN", f"agent_turn_event_dropped turn={event.get('turn_id')} reason=router_unreachable")

    def _send(self, event: dict[str, Any]) -> bool:
        request = urllib.request.Request(
            self.base_url().rstrip("/") + POST_PATH,
            data=json.dumps(event).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=self.timeout) as response:
                return 200 <= int(getattr(response, "status", 200)) < 300
        except Exception:
            return False


__all__ = [
    "AppServerTurnTracker",
    "EVENT_KIND",
    "POST_PATH",
    "RouterTurnPoster",
    "SeenTurns",
    "TranscriptTurnTracker",
    "turn_event",
    "typed_by_person",
    "user_input_text",
    "valid_turn_event",
]
