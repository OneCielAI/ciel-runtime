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

Failed auto-compaction (user requirement 2026-11-16, "compact 뒤에 턴이 멈춤"):
a main turn that ends with Claude Code's "Prompt is too long · automatic
compaction failed: ..." record resumes on the same schedule whoever opened
the turn (a person, a channel, a Work continuation). The resend asks to
continue; delivering it makes Claude Code compact again before the request.
A typed opener's own text is resent; otherwise a continuation line is sent.
That chain is not cancelled by a compaction boundary (its own resend causes
one) and ends when the model answers.

Every main turn (user requirement 2026-11-16, "지속되는 api 에러에 스스로
재시도를 못함, ciel runtime이 재시도를 해줘야함"): a transient API error resumes
the turn whoever opened it. A typed text opener is resent as before; any other
opener (a task notification, a Work continuation, a peer or side wake, an
image) gets the continuation line. A channel message that failed before any
answer stays with its own resend (runtime_input_retry); once that turn has
answered, a later failure is resumed here. After a resend has made progress
(an answer), a later failure in the same turn starts a new chain. A stream cut
after output that stream recovery declines (a tool block, nothing shown, busy
terminal) is handed here by the monitor (``adopt_cut``). The resend's own
queue entry (enqueue then dequeue) is not new input.
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
# Claude Code's terminal auto-compaction failure (runtime_compaction_control
# _compact_terminal_failure reads the same record shape).
AUTO_COMPACT_FAILED_PREFIX = "Prompt is too long · automatic compaction failed: "
COMPACT_RESUME_TEXT = "직전 턴이 자동 컴팩트 실패로 멈췄습니다. 컴팩트를 다시 진행한 뒤 하던 작업을 이어서 진행하세요."
API_RESUME_TEXT = "직전 턴이 일시적인 API 오류로 멈췄습니다. 하던 작업을 끊긴 지점부터 이어서 진행하세요."
# Channel deliveries: runtime_input_retry resends the message itself when its
# turn fails before an answer (channel_transcript failed state).
_CHANNEL_PREFIXES = ("[ciel-runtime external channel message]", "[ciel-wake]")
# Stream recovery reasons after which nobody else continues a cut stream; the
# resend takes it. Plan mode, a person typing, newer or queued input, a
# disabled setting or another scope are deliberate stops and stay stops.
ADOPTABLE_CUT_REASONS = frozenset({
    "tool_use_seen", "nothing_shown", "reason", "upstream_error_type", "no_router_failure",
    "terminal_busy", "terminal_stale", "terminal_unknown", "terminal_not_ready", "compaction",
})
_INTERRUPT_PREFIX = "[Request interrupted by user"
_COMMAND_PREFIXES = ("<command-name>", "<local-command-stdout>", "<local-command-caveat>", "<bash-input>",
                     "<bash-stdout>", "<bash-stderr>")


def delay_before(attempt: int) -> float | None:
    """Seconds to wait before resend ``attempt`` (1-based); None once exhausted."""

    if attempt < 1 or attempt > MAX_RESENDS:
        return None
    return FIRST_INTERVAL_SECONDS if attempt <= FIRST_COUNT else SECOND_INTERVAL_SECONDS


def auto_compact_failed(record: dict[str, Any]) -> bool:
    """Claude Code's own record for a failed automatic compaction that ended the turn."""

    if record.get("type") != "assistant" or record.get("isApiErrorMessage") is not True:
        return False
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return (isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict)
            and content[0].get("type") == "text" and isinstance(content[0].get("text"), str)
            and content[0]["text"].startswith(AUTO_COMPACT_FAILED_PREFIX))


def resend_body(attempt: int, *, error_uuid: str, text: str, scope: Any, created_at: float,
                compaction: bool = False) -> dict[str, Any]:
    """One resend admitted like a stream-recovery continuation (exact text, exact scope)."""

    lead = ("직전 턴이 자동 컴팩트 실패로 멈춰 다시 보냅니다." if compaction
            else "직전 턴이 일시적인 API 오류로 멈춰 이어서 진행을 요청합니다." if text == API_RESUME_TEXT
            else "직전 입력이 일시적인 API 오류로 처리되지 못해 같은 입력을 다시 보냅니다.")
    return {
        "channel": "typed-turn-resend",
        "sender_id": "ciel-runtime",
        "kind": "typed_turn_resend",
        "message": (
            f"{RESEND_MARKER} {attempt}/{MAX_RESENDS} · {error_uuid[:8]}] {lead} "
            "이미 마친 작업은 반복하지 말고 이어서 진행하세요.\n\n" + text
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
        # Who opened the turn now running: {"typed": bool, "ours": bool,
        # "channel": bool, "answered": bool, "text": str, ...}.
        self._opener: dict[str, Any] | None = None
        self._chain: dict[str, Any] | None = None
        self._waiting_logged = ""
        # The last stream cut after output this turn saw; stream recovery may
        # hand it over (adopt_cut) when it does not continue it itself.
        self._cut: dict[str, Any] | None = None

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
                return
            self._cut = None
            if self._opener is not None:
                self._opener["answered"] = True
            if self._chain is not None and (self._opener or {}).get("ours"):
                self._end("recovered", "answered")
        elif kind == "queue-operation" and record.get("operation") == "enqueue":
            self._cut = None
            chain = self._chain
            if chain is not None and chain.get("submitted_at") is not None and not chain.get("delivered"):
                # Claude Code queues an admitted resend before it runs it
                # (enqueue, dequeue 11 ms later; 2026-11-16 17:43:11Z). That is
                # this chain's own input, not a person's: the chain stays.
                return
            if chain is not None:
                self._end("cancelled", "queued_input")
        elif kind == "system" and record.get("subtype") == "compact_boundary":
            # A failed-compaction chain's own resend makes Claude Code compact
            # again; that boundary is the recovery path, not a reason to stop.
            if self._chain is not None and not self._chain.get("compaction"):
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
            self._cut = None
            if self._chain is not None:
                self._end("cancelled", "interrupted")
            return
        if record.get("isCompactSummary") is True or stripped.startswith(_COMMAND_PREFIXES):
            return
        origin = record.get("origin")
        peer = isinstance(origin, dict) and origin.get("kind") == "peer"
        if self._ours(text):
            # The text a later failure in this resumed turn sends again.
            previous = str((self._chain or {}).get("text") or (self._opener or {}).get("text") or API_RESUME_TEXT)
            self._opener = {"typed": False, "ours": True, "uuid": str(record.get("uuid") or ""), "text": previous}
            self._cut = None
            if self._chain is not None:
                self._chain["delivered"] = True
            return
        if record.get("isMeta") is True and not peer:
            return  # Reminders and caveats are not input.
        self._cut = None
        if peer or not typed_by_person(text):
            self._opener = {"typed": False, "ours": False, "uuid": str(record.get("uuid") or ""),
                            "channel": stripped.startswith(_CHANNEL_PREFIXES)}
            return
        if self._chain is not None:
            self._end("cancelled", "new_input")
        self._opener = {"typed": True, "ours": False, "uuid": str(record.get("uuid") or ""),
                        "text": text, "non_text": non_text}

    def _observe_compaction_failure(self, record: dict[str, Any]) -> None:
        error_uuid = str(record.get("uuid") or "")
        chain = self._chain
        if chain is not None:
            delay = delay_before(chain["attempts"] + 1)
            if delay is None:
                self._end("exhausted", "last_error=auto_compact_failed")
                return
            chain.update(due=self.now() + delay, error_uuid=error_uuid, submitted_at=None, delivered=False,
                         compaction=True)
            self.log("INFO", f"claude_typed_resend_scheduled attempt={chain['attempts'] + 1}/{MAX_RESENDS} "
                             f"in={delay:.0f}s error=auto_compact_failed origin={chain['origin'][:8]}")
            return
        opener = self._opener or {}
        typed_text = str(opener.get("text") or "") if opener.get("typed") and not opener.get("non_text") else ""
        delay = delay_before(1)
        self._chain = {
            "origin": str(opener.get("uuid") or error_uuid),
            "text": typed_text if typed_text.strip() else COMPACT_RESUME_TEXT,
            "session_id": str(record.get("sessionId") or ""),
            "attempts": 0,
            "due": self.now() + float(delay or FIRST_INTERVAL_SECONDS),
            "error_uuid": error_uuid,
            "submitted_at": None,
            "delivered": False,
            "input_id": 0,
            "compaction": True,
        }
        self.log("INFO", f"claude_typed_resend_scheduled attempt=1/{MAX_RESENDS} in={delay:.0f}s "
                         f"origin={self._chain['origin'][:8]} error=auto_compact_failed")

    def _resume_text(self, opener: dict[str, Any]) -> str:
        """What a new chain sends: a typed text opener itself, else the continuation line."""

        text = str(opener.get("text") or "")
        if opener.get("ours"):
            return text if text.strip() else API_RESUME_TEXT
        if opener.get("typed") and not opener.get("non_text") and text.strip():
            return text
        return API_RESUME_TEXT

    def _start(self, record: dict[str, Any], opener: dict[str, Any], label: str) -> None:
        error_uuid = str(record.get("uuid") or "")
        delay = delay_before(1)
        self._chain = {
            "origin": str(opener.get("uuid") or error_uuid),
            "text": self._resume_text(opener),
            "session_id": str(record.get("sessionId") or ""),
            "attempts": 0,
            "due": self.now() + float(delay or FIRST_INTERVAL_SECONDS),
            "error_uuid": error_uuid,
            "submitted_at": None,
            "delivered": False,
            "input_id": 0,
        }
        self.log("INFO", f"claude_typed_resend_scheduled attempt=1/{MAX_RESENDS} in={delay:.0f}s "
                         f"origin={self._chain['origin'][:8]} error={label}")

    def adopt_cut(self, error_uuid: str, reason: str) -> bool:
        """Take a stream cut after output that stream recovery will not continue."""

        cut = self._cut
        if (reason not in ADOPTABLE_CUT_REASONS or cut is None or cut["record"].get("uuid") != error_uuid
                or self._chain is not None):
            return False
        self._cut = None
        self._start(cut["record"], cut["opener"], f"stream_cut_after_output:{reason}")
        return True

    def _observe_error(self, record: dict[str, Any]) -> None:
        if auto_compact_failed(record):
            self._observe_compaction_failure(record)
            return
        opener = self._opener
        if opener is None:
            return
        failure = classify_claude_api_error(record)
        error_uuid = str(record.get("uuid") or "")
        label = failure.label.replace(' ', '_')
        chain = self._chain
        if opener.get("ours") and chain is not None:
            if failure.kind == "stream_cut_after_output":
                # Stream recovery decides first; it hands the cut over when it
                # will not continue it (adopt_cut).
                self._cut = {"record": record, "opener": dict(opener)}
                self._end("stopped", "stream_cut_after_output")
                return
            if not failure.transient:
                self._end("stopped", f"not_transient:{label}")
                return
            delay = delay_before(chain["attempts"] + 1)
            if delay is None:
                self._end("exhausted", f"last_error={label}")
                return
            chain.update(due=self.now() + delay, error_uuid=error_uuid, submitted_at=None, delivered=False)
            self.log("INFO", f"claude_typed_resend_scheduled attempt={chain['attempts'] + 1}/{MAX_RESENDS} "
                             f"in={delay:.0f}s error={label} origin={chain['origin'][:8]}")
            return
        subject = f"origin={str(opener.get('uuid') or '-')[:8]} error={label}"
        if failure.kind == "stream_cut_after_output":
            self._cut = {"record": record, "opener": dict(opener)}
            self.log("INFO", f"claude_typed_resend_deferred reason=stream_recovery_first {subject}")
            return
        if not failure.transient:
            self.log("INFO", f"claude_typed_resend_skipped reason=not_transient {subject}")
            return
        if opener.get("channel") and not opener.get("answered"):
            # The channel message's own resend owns a turn that never answered.
            self.log("INFO", f"claude_typed_resend_skipped reason=channel_input_retry {subject}")
            return
        self._start(record, opener, label)

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
        body = resend_body(attempt, error_uuid=chain["error_uuid"], text=chain["text"], scope=scope, created_at=now,
                           compaction=bool(chain.get("compaction")))
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
