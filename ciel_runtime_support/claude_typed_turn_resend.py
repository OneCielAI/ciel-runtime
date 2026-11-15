"""Resend a person-typed Claude Code prompt whose turn ended on a transient API error.

User decision 2026-11-15: when a turn opened by input typed in the terminal
ends with a transient upstream API error, the same input is resent
automatically -- once a minute for 10 attempts, then once every 10 minutes for
10 more (20 resends in all).

Scope:

* Only a turn whose opening input reads as typed by a person
  (``agent_turn_events.typed_by_person``) starts a chain. Channel/SSE inputs
  keep their own resend (``runtime_input_retry``).
* "Transient" is ``runtime_input_retry.classify_claude_api_error`` on the
  native ``isApiErrorMessage`` record, the classifier channel resends already
  use. A stream cut after output is not transient there; stream recovery owns
  that case.

The resend is admitted through the runtime input gateway as one raw injection,
so it passes the same idle, modal, ownership and exact-scope delivery gates as
any channel input; nothing is typed directly. Its first line names the attempt
(delivery tracks prompts by exact text, so a repeated text would read as
already delivered); the original input follows unchanged.

A chain ends on:

* any newer input the person types or queues, an interrupt, compaction, or a
  session change (``cancelled``);
* a model answer to a resend (``recovered``);
* a non-transient error (``stopped``);
* the 20th failed resend (``exhausted``).

State is in memory only: a restarted launcher never resends an input it did
not see fail.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .agent_turn_events import typed_by_person
from .channel_wake_claim_repository import prompt_message_ids
from .runtime_input_retry import classify_claude_api_error

RESEND_MARKER = "[ciel-runtime 입력 재전송"
FIRST_COUNT = 10
FIRST_INTERVAL_SECONDS = 60.0
SECOND_COUNT = 10
SECOND_INTERVAL_SECONDS = 600.0
MAX_RESENDS = FIRST_COUNT + SECOND_COUNT
# The admitted resend expires from the queue after this (exact-scope gate).
NOTICE_TTL_SECONDS = 180.0
# Not seen in the transcript by then: delivery dropped it; the same attempt is
# admitted again rather than counted.
UNDELIVERED_AFTER_SECONDS = NOTICE_TTL_SECONDS + 30.0
SUBMIT_RETRY_SECONDS = 30.0
_INTERRUPT_PREFIX = "[Request interrupted by user"
_COMMAND_PREFIXES = ("<command-name>", "<local-command-stdout>", "<local-command-caveat>", "<bash-input>",
                     "<bash-stdout>", "<bash-stderr>")


def delay_before(attempt: int) -> float | None:
    """Seconds to wait before resend ``attempt`` (1-based); None once exhausted."""

    if attempt < 1 or attempt > MAX_RESENDS:
        return None
    return FIRST_INTERVAL_SECONDS if attempt <= FIRST_COUNT else SECOND_INTERVAL_SECONDS


def resend_body(attempt: int, *, error_uuid: str, text: str, scope: Any, created_at: float) -> dict[str, Any]:
    """One resend admitted like a stream-recovery continuation (exact text, exact scope)."""

    return {
        "channel": "typed-turn-resend",
        "sender_id": "ciel-runtime",
        "kind": "typed_turn_resend",
        "message": (
            f"{RESEND_MARKER} {attempt}/{MAX_RESENDS} · {error_uuid[:8]}] 직전 입력이 일시적인 API 오류로 처리되지 "
            "못해 같은 입력을 다시 보냅니다. 이미 마친 작업은 반복하지 말고 이어서 진행하세요.\n\n" + text
        ),
        "meta": {
            "raw_injection": True,
            "source": "ciel-runtime-typed-turn-resend",
            "recovery_kind": "typed_turn_resend",
            # The stream-recovery delivery gate: exact launch scope plus TTL.
            "source_kind": "stream_recovery",
            "runtime": "claude",
            "attempt": attempt,
            "max_attempts": MAX_RESENDS,
            "native_error_uuid": error_uuid,
            "recovery_scope": scope.payload(),
            "recovery_created_at": created_at,
            "recovery_ttl_seconds": NOTICE_TTL_SECONDS,
        },
    }


def _content_parts(content: Any) -> tuple[str, bool]:
    """(joined text, whether any non-text block such as an image was present)."""

    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return "", False
    text, other = [], False
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text.append(str(item.get("text") or ""))
        else:
            other = True
    return "".join(text), other


class TypedTurnResend:
    """Launcher side: watch the native transcript, resend on a transient error."""

    def __init__(
        self,
        *,
        scope: Callable[[], Any],
        submit: Callable[[dict[str, Any]], Any],
        log: Callable[[str, str], None],
        terminal_blocked: Callable[[], str],
        bound_epoch: Callable[[], str] = lambda: "",
        now: Callable[[], float] = time.time,
    ) -> None:
        self.scope, self.submit, self.log = scope, submit, log
        self.terminal_blocked, self.bound_epoch, self.now = terminal_blocked, bound_epoch, now
        self.reset()

    def reset(self) -> None:
        # Who opened the turn now running: {"typed": bool, "ours": bool, ...}.
        self._opener: dict[str, Any] | None = None
        self._chain: dict[str, Any] | None = None
        self._waiting_logged = ""

    def status(self) -> dict[str, Any] | None:
        """The active chain without the prompt text, for status surfaces and tests."""

        chain = self._chain
        if chain is None:
            return None
        return {key: chain[key] for key in ("origin", "attempts", "due", "error_uuid", "submitted_at", "delivered")}

    def _end(self, outcome: str, reason: str) -> None:
        chain, self._chain = self._chain, None
        self._waiting_logged = ""
        if chain is not None:
            self.log("INFO", f"claude_typed_resend_{outcome} reason={reason} attempts={chain['attempts']}/{MAX_RESENDS} "
                             f"origin={chain['origin'][:8]} error={chain['error_uuid'][:8]}")

    def _ours(self, text: str) -> bool:
        if RESEND_MARKER in text:
            return True
        chain = self._chain
        return bool(chain and chain.get("input_id") and chain["input_id"] in prompt_message_ids(text))

    def observe(self, record: dict[str, Any]) -> None:
        if record.get("isSidechain") is True:
            return
        kind = record.get("type")
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        if kind == "user":
            self._observe_user(record, message)
        elif kind == "assistant":
            if record.get("isApiErrorMessage") is True:
                self._observe_error(record)
            elif self._chain is not None and (self._opener or {}).get("ours"):
                self._end("recovered", "answered")
        elif kind == "queue-operation" and record.get("operation") == "enqueue":
            if self._chain is not None:
                self._end("cancelled", "queued_input")
        elif kind == "system" and record.get("subtype") == "compact_boundary":
            if self._chain is not None:
                self._end("cancelled", "compaction")

    def _observe_user(self, record: dict[str, Any], message: dict[str, Any]) -> None:
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(item, dict) and item.get("type") == "tool_result"
                                             for item in content):
            return  # Part of the running turn.
        text, non_text = _content_parts(content)
        stripped = text.lstrip()
        if stripped.startswith(_INTERRUPT_PREFIX):
            self._opener = None
            if self._chain is not None:
                self._end("cancelled", "interrupted")
            return
        if record.get("isCompactSummary") is True or stripped.startswith(_COMMAND_PREFIXES):
            return
        origin = record.get("origin")
        peer = isinstance(origin, dict) and origin.get("kind") == "peer"
        if self._ours(text):
            self._opener = {"typed": False, "ours": True}
            if self._chain is not None:
                self._chain["delivered"] = True
            return
        if record.get("isMeta") is True and not peer:
            return  # Reminders and caveats are not input.
        if peer or not typed_by_person(text):
            self._opener = {"typed": False, "ours": False}
            return
        if self._chain is not None:
            self._end("cancelled", "new_input")
        self._opener = {"typed": True, "ours": False, "uuid": str(record.get("uuid") or ""),
                        "text": text, "non_text": non_text}

    def _observe_error(self, record: dict[str, Any]) -> None:
        opener = self._opener
        if opener is None:
            return
        failure = classify_claude_api_error(record)
        error_uuid = str(record.get("uuid") or "")
        chain = self._chain
        if opener.get("ours") and chain is not None:
            if not failure.transient:
                self._end("stopped", f"not_transient:{failure.label.replace(' ', '_')}")
                return
            delay = delay_before(chain["attempts"] + 1)
            if delay is None:
                self._end("exhausted", f"last_error={failure.label.replace(' ', '_')}")
                return
            chain.update(due=self.now() + delay, error_uuid=error_uuid, submitted_at=None, delivered=False)
            self.log("INFO", f"claude_typed_resend_scheduled attempt={chain['attempts'] + 1}/{MAX_RESENDS} "
                             f"in={delay:.0f}s error={failure.label.replace(' ', '_')} origin={chain['origin'][:8]}")
            return
        if not opener.get("typed"):
            return
        subject = f"origin={str(opener.get('uuid') or '-')[:8]} error={failure.label.replace(' ', '_')}"
        if not failure.transient:
            self.log("INFO", f"claude_typed_resend_skipped reason=not_transient {subject}")
            return
        if opener.get("non_text"):
            self.log("INFO", f"claude_typed_resend_skipped reason=non_text_input {subject}")
            return
        if not str(opener.get("text") or "").strip():
            return
        delay = delay_before(1)
        self._chain = {
            "origin": str(opener.get("uuid") or error_uuid),
            "text": opener["text"],
            "session_id": str(record.get("sessionId") or ""),
            "attempts": 0,
            "due": self.now() + float(delay or FIRST_INTERVAL_SECONDS),
            "error_uuid": error_uuid,
            "submitted_at": None,
            "delivered": False,
            "input_id": 0,
        }
        self.log("INFO", f"claude_typed_resend_scheduled attempt=1/{MAX_RESENDS} in={delay:.0f}s {subject}")

    def tick(self) -> dict[str, Any] | None:
        chain = self._chain
        if chain is None:
            return None
        now = self.now()
        if chain["submitted_at"] is not None:
            if not chain["delivered"] and now - chain["submitted_at"] > UNDELIVERED_AFTER_SECONDS:
                self.log("WARN", f"claude_typed_resend_undelivered attempt={chain['attempts']}/{MAX_RESENDS} "
                                 f"origin={chain['origin'][:8]}; admitting the same attempt again")
                chain.update(attempts=chain["attempts"] - 1, submitted_at=None, due=now)
            return None
        if now < chain["due"]:
            return None
        scope = self.scope()
        bound_epoch = self.bound_epoch()
        if (scope is None or scope.runtime != "claude" or (chain["session_id"] and scope.session_id != chain["session_id"])
                or (bound_epoch and scope.epoch != bound_epoch)):
            self._end("cancelled", "scope")
            return None
        blocked = self.terminal_blocked()
        if blocked:
            # The person is typing, a turn runs or compaction holds the prompt:
            # wait, never drop the resend.
            if blocked != self._waiting_logged:
                self._waiting_logged = blocked
                self.log("INFO", f"claude_typed_resend_waiting reason={blocked} attempt={chain['attempts'] + 1}/{MAX_RESENDS}")
            return None
        attempt = chain["attempts"] + 1
        body = resend_body(attempt, error_uuid=chain["error_uuid"], text=chain["text"], scope=scope, created_at=now)
        try:
            admitted = self.submit(body)
        except Exception as exc:
            chain["due"] = now + SUBMIT_RETRY_SECONDS
            self.log("WARN", f"claude_typed_resend_submit_failed error={type(exc).__name__} attempt={attempt}/{MAX_RESENDS}")
            return None
        try:
            input_id = int((admitted or {}).get("id") or 0) if isinstance(admitted, dict) else 0
        except (TypeError, ValueError):
            input_id = 0
        self._waiting_logged = ""
        chain.update(attempts=attempt, submitted_at=now, delivered=False, input_id=input_id)
        self.log("INFO", f"claude_typed_resend_admitted session={scope.session_id} attempt={attempt}/{MAX_RESENDS} "
                         f"origin={chain['origin'][:8]} error={chain['error_uuid'][:8]} input_id={input_id or '-'}")
        return {"attempt": attempt, "limit": MAX_RESENDS, "admitted": admitted}


__all__ = ["FIRST_COUNT", "FIRST_INTERVAL_SECONDS", "MAX_RESENDS", "RESEND_MARKER", "SECOND_COUNT",
           "SECOND_INTERVAL_SECONDS", "TypedTurnResend", "delay_before", "resend_body"]
