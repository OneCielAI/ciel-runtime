"""Continue a Claude Code turn whose streamed response broke mid-way.

Claude Code shows "API Error: Server error mid-response" and stops when a
streamed answer ends without message_stop after text was already shown; it
does not retry that request itself (owned native probe, 2026-10-20, v2.1.305).

The router records each such failure of a local main-session request in a
ledger keyed by the upstream message id.  The launcher tails the native
transcript; when the matching error record appears (its parent is the
assistant record carrying that message id) and nothing else follows it for a
quiet interval, one continuation is admitted through the runtime input
gateway.  Delivery then passes the same idle, modal and ownership gates as
any other channel input; nothing is typed directly.

A failure that encountered any tool_use block is never continued, even if
its arguments were partial or held by the relay. The native tool lifecycle
must not be replayed or supplemented by this text-only recovery.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

from . import claude_stream_recovery_settings
from .claude_compact_continuation import COMPACT_MARKER, CompactContinuationEvidence
from .channel_message_repository import exclusive_file_lock
from .channel_wake_claim_repository import prompt_message_ids
from .runtime_compaction_control import CompactionScope
from .secure_json_repository import SecureJsonEffects, SecureJsonRepository


LEDGER_NAME = "claude-stream-recovery.json"
CONFIG_KEY = claude_stream_recovery_settings.CONFIG_KEY
MAX_ATTEMPTS = claude_stream_recovery_settings.MAX_ATTEMPTS_LIMIT
# The exact words a continuation starts with; a later failure answering one
# belongs to the same recovery chain and spends the same budget.
RECOVERY_MARKER = "계속-응답중단복구"
QUIET_SECONDS = 4.0
# A terminal report older than this proves nothing about the prompt now.
TERMINAL_REPORT_MAX_AGE_SECONDS = 3.0
# A failure older than this cannot be the one an error record just reported.
FAILURE_TTL_SECONDS = 600.0
_FAILURE_LIMIT = 64
_HANDLED_LIMIT = 256
_RECENT_LIMIT = 20
_ASSISTANT_ID_LIMIT = 128

# Failure reasons that describe the transport, not the request.  An in-stream
# error event counts only with one of the transient error types below.
_TRANSIENT_REASONS = ("anthropic_stream_upstream_incomplete", "anthropic_stream_upstream_error:")
_TRANSIENT_ERROR_EVENT = "anthropic_stream_upstream_error_event"
_TRANSIENT_ERROR_TYPES = frozenset({"api_error", "overloaded_error", "server_error", "timeout_error"})


def settings(config: dict[str, Any]) -> tuple[bool, int]:
    """(enabled, attempts per chain); on with one attempt unless configured."""

    return claude_stream_recovery_settings.enabled(config), claude_stream_recovery_settings.max_attempts(config)


def ineligible_reason(failure: dict[str, Any]) -> str:
    """Why a recorded failure may not be continued; empty when it may."""

    if (
        failure.get("tool_uncertain") is not False
        or failure.get("saw_tool_use") or failure.get("emitted_tool_use")
    ):
        return "tool_use_seen"
    if not failure.get("visible_text"):
        return "nothing_shown"
    reason = str(failure.get("reason") or "")
    if reason == _TRANSIENT_ERROR_EVENT:
        if str(failure.get("upstream_error_type") or "") not in _TRANSIENT_ERROR_TYPES:
            return "upstream_error_type"
        return ""
    if not reason.startswith(_TRANSIENT_REASONS):
        return "reason"
    return ""


def stream_failure_record(metadata: dict[str, Any]) -> dict[str, Any]:
    """Ledger fields from the relay's failure metadata (flags and counts only)."""

    return {
        "message_id": str(metadata.get("upstream_message_id") or ""),
        "provider": str(metadata.get("provider") or ""),
        # Only an upstream that itself ended early is a transport failure; a
        # relay that stopped reading or lost its client is not.
        "reason": (
            str(metadata.get("failure_reason") or "")
            if metadata.get("confirmed_incomplete") is True and not metadata.get("client_disconnected")
            else "not_confirmed"
        ),
        "upstream_error_type": str(metadata.get("upstream_error_type") or ""),
        # Text Claude Code actually received; held pseudo-tool text is not counted.
        "visible_text": int(metadata.get("visible_text_len") or 0) > 0,
        # Any tool-like block or pseudo-tool text, shown or held, excludes the
        # failure. A relay that does not report it is treated as uncertain.
        "tool_uncertain": metadata.get("tool_uncertain") is not False,
        "saw_tool_use": bool(metadata.get("saw_tool_use")),
        "emitted_tool_use": bool(metadata.get("emitted_tool_use")),
    }


RECOVERY_NOTICE_TTL_SECONDS = 120.0


def recovery_notice_body(
    attempt: int,
    limit: int,
    *,
    error_uuid: str,
    message_id: str,
    scope: CompactionScope | None = None,
    created_at: float | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    """One continuation, admitted like the restart notice (exact text).

    Channel delivery tracks a raw injection by its exact prompt text, so each
    continuation names its error: a repeated text was taken for the earlier,
    already delivered one and held as queued (owned E2E 211930).
    """

    return {
        "channel": "stream-recovery",
        "sender_id": "ciel-runtime",
        "kind": "compact_resume" if compact else "stream_recovery",
        "message": (
            f"{COMPACT_MARKER} (1/1 · {error_uuid}): Continue the previously authorized unfinished work "
            "after automatic context compaction. Do not repeat completed work or expand the user-approved scope."
        ) if compact else (
            f"{RECOVERY_MARKER} ({attempt}/{limit} · {error_uuid[:8]}): 직전 응답이 업스트림 서버 오류로 중간에 끊겼습니다. "
            "이미 출력했거나 실행을 마친 작업은 반복하지 말고, 끊긴 지점부터 남은 작업을 이어서 진행하세요."
        ),
        "meta": {
            "raw_injection": True,
            "source": "ciel-runtime-compact-resume" if compact else "ciel-runtime-stream-recovery",
            "recovery_kind": "compact_resume" if compact else "stream_recovery",
            # Preserve the existing recovery queue and exact-scope delivery gates.
            "source_kind": "stream_recovery",
            "runtime": "claude",
            "attempt": attempt,
            "max_attempts": limit,
            "native_error_uuid": error_uuid,
            **({"compact_checkpoint_claim": error_uuid} if compact else {}),
            "upstream_message_id": message_id,
            **({"recovery_scope": scope.payload()} if scope is not None else {}),
            "recovery_created_at": time.time() if created_at is None else created_at,
            "recovery_ttl_seconds": RECOVERY_NOTICE_TTL_SECONDS,
        },
    }


def recovery_notice_skip_reason(message: dict[str, Any], scope: CompactionScope | None, now: float) -> str:
    """Why a stream recovery notice may no longer be delivered; empty when it may."""

    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    if meta.get("source_kind") != "stream_recovery":
        return ""
    if scope is None or meta.get("recovery_scope") != scope.payload():
        return "stream_recovery_scope_changed"
    try:
        created_at = float(meta.get("recovery_created_at"))
        ttl = float(meta.get("recovery_ttl_seconds") or RECOVERY_NOTICE_TTL_SECONDS)
    except (TypeError, ValueError):
        return "stream_recovery_unbound"
    if now - created_at > ttl or created_at - now > 30.0:
        return "stream_recovery_expired"
    return ""


class StreamRecoveryLedger:
    """Failures the router saw and continuations the launcher admitted.

    Shared by the router and launcher processes of one router instance;
    every change happens under the file lock.
    """

    def __init__(self, path: Path, log: Callable[[str, str], None], now: Callable[[], float] = time.time) -> None:
        self.repository = SecureJsonRepository(path, SecureJsonEffects(log=log))
        self.log = log
        self.now = now

    def _change(self, purpose: str, change: Callable[[dict[str, Any]], Any]) -> Any:
        with exclusive_file_lock(self.repository.path):
            value = self.repository.load(purpose)
            if value is None:
                raise OSError("stream recovery ledger is unreadable")
            for key in ("failures", "handled", "chains"):
                if not isinstance(value.get(key), dict):
                    value[key] = {}
            if not isinstance(value.get("recent"), list):
                value["recent"] = []
            result = change(value)
            self.repository.save(value, purpose)
            return result

    def record_failure(self, scope: CompactionScope, failure: dict[str, Any]) -> None:
        message_id = str(failure.get("message_id") or "")
        if not message_id:
            return
        entry = {
            **scope.payload(),
            "message_id": message_id,
            "failed_at": self.now(),
            **{key: failure.get(key) for key in (
                "provider", "reason", "upstream_error_type", "visible_text",
                "tool_uncertain", "saw_tool_use", "emitted_tool_use",
            )},
        }

        def change(value: dict[str, Any]) -> None:
            failures = value["failures"]
            failures[message_id] = entry
            for stale in sorted(failures, key=lambda key: float(failures[key].get("failed_at") or 0))[:-_FAILURE_LIMIT]:
                failures.pop(stale, None)

        self._change("stream_recovery_failure", change)

    def failure(self, scope: CompactionScope, message_id: str) -> dict[str, Any] | None:
        value = self.repository.load("stream_recovery_lookup")
        failures = value.get("failures") if isinstance(value, dict) else None
        entry = failures.get(message_id) if isinstance(failures, dict) else None
        if not isinstance(entry, dict):
            return None
        if {key: entry.get(key) for key in ("runtime", "session_id", "epoch")} != scope.payload():
            return None
        if self.now() - float(entry.get("failed_at") or 0) > FAILURE_TTL_SECONDS:
            return None
        return entry

    def claim(self, scope: CompactionScope, error_uuid: str, *, continues_chain: bool, limit: int,
              checkpoint: bool = False) -> int | None:
        """The attempt number to admit for this error record, or None.

        An error record is claimed once.  A failure answering a continuation
        spends that chain's budget; anything else opens a new chain.
        """

        def change(value: dict[str, Any]) -> int | None:
            handled, chains = value["handled"], value["chains"]
            if error_uuid in handled:
                return None
            chain = chains.get(scope.session_id)
            spent = 0
            if not checkpoint and continues_chain and isinstance(chain, dict) and chain.get("epoch") == scope.epoch:
                spent = int(chain.get("attempts") or 0)
            if spent >= limit:
                handled[error_uuid] = {"status": "exhausted", "at": self.now(), "attempts": spent}
                self._remember(value, error_uuid, "exhausted", f"attempts={spent}/{limit}")
                result = None
            else:
                if not checkpoint:
                    chains[scope.session_id] = {"epoch": scope.epoch, "attempts": spent + 1}
                handled[error_uuid] = {"status": "admitted", "at": self.now(), "attempt": spent + 1}
                self._remember(value, error_uuid, "admitted", f"attempt={spent + 1}/{limit}")
                result = spent + 1
            for stale in sorted(handled, key=lambda key: float(handled[key].get("at") or 0))[:-_HANDLED_LIMIT]:
                handled.pop(stale, None)
            return result

        return self._change("stream_recovery_claim", change)

    def settle(self, error_uuid: str, status: str) -> None:
        def change(value: dict[str, Any]) -> None:
            entry = value["handled"].get(error_uuid)
            if isinstance(entry, dict):
                entry["status"] = status
            self._remember(value, error_uuid, status, "")

        self._change("stream_recovery_settle", change)

    def _remember(self, value: dict[str, Any], error_uuid: str, status: str, reason: str) -> None:
        value["recent"] = [*value["recent"], {"at": self.now(), "error": error_uuid, "status": status, "reason": reason}][-_RECENT_LIMIT:]

    def note(self, error_uuid: str, status: str, reason: str) -> None:
        """Keep a decision that admitted nothing, for the settings panel."""

        try:
            self._change("stream_recovery_note", lambda value: self._remember(value, error_uuid, status, reason))
        except OSError as exc:
            self.log("WARN", f"claude_stream_recovery_note_failed error={type(exc).__name__}")

    def recent(self) -> list[dict[str, Any]]:
        """The last decisions, oldest first (admitted, exhausted, skipped, cancelled)."""

        value = self.repository.load("stream_recovery_status") or {}
        recent = value.get("recent")
        return [entry for entry in recent if isinstance(entry, dict)] if isinstance(recent, list) else []


@dataclass(frozen=True, slots=True)
class ClaudeStreamFailureRecorder:
    """Router side: remember one broken local main-session stream.

    The scope is the one the request started in.  It is recorded only while
    the launch still holds it: a failure that ends after /clear, a relaunch
    or a session switch belongs to no current conversation.
    """

    ledger: StreamRecoveryLedger
    current_scope: Callable[[], CompactionScope | None]
    log: Callable[[str, str], None]

    def __call__(self, scope: CompactionScope | None, metadata: dict[str, Any]) -> None:
        try:
            failure = stream_failure_record(metadata)
            if scope is None or scope.runtime != "claude":
                self.log("INFO", f"claude_stream_recovery_failure_skipped reason=not_main_session message={failure.get('message_id') or '-'}")
                return
            if self.current_scope() != scope:
                self.log("INFO", f"claude_stream_recovery_failure_skipped reason=scope_changed message={failure.get('message_id') or '-'}")
                return
            if not failure.get("message_id"):
                self.log("INFO", "claude_stream_recovery_failure_skipped reason=no_message_id")
                return
            self.ledger.record_failure(scope, failure)
            self.log(
                "INFO",
                "claude_stream_recovery_failure_recorded "
                f"session={scope.session_id} message={failure.get('message_id')} reason={failure.get('reason') or '-'} "
                f"eligible={not ineligible_reason(failure)} excluded={ineligible_reason(failure) or '-'}",
            )
        except (OSError, ValueError, TypeError) as exc:
            # An observation must never fail the client request it describes.
            self.log("WARN", f"claude_stream_recovery_failure_record_failed error={type(exc).__name__}")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


class ClaudeStreamRecoveryMonitor:
    """Launcher side: match native error records to router failures, then continue."""

    def __init__(
        self,
        ledger: StreamRecoveryLedger,
        load_config: Callable[[], dict[str, Any]],
        scope: Callable[[], CompactionScope | None],
        submit: Callable[[dict[str, Any]], Any],
        log: Callable[[str, str], None],
        now: Callable[[], float] = time.time,
        quiet_seconds: float = QUIET_SECONDS,
        input_watermark: Callable[[], int] = lambda: 0,
        bound_epoch: Callable[[], str] = lambda: "",
    ) -> None:
        self.ledger, self.load_config, self.scope, self.submit, self.log = ledger, load_config, scope, submit, log
        self.now, self.quiet_seconds, self.input_watermark = now, quiet_seconds, input_watermark
        self.bound_epoch = bound_epoch
        # The terminal proxy's last report: ready/manual_input/busy/compaction/
        # pending flags plus its time.  None until one arrives.
        self._terminal: dict[str, Any] | None = None
        self.reset()

    def set_terminal_admission(self, snapshot: dict[str, Any]) -> None:
        """Record the terminal proxy's view of whether input may be added now."""

        self._terminal = dict(snapshot)
        self._compact.terminal(snapshot)
        if self._compact.operator_input and self._pending and self._pending.get("kind") == "compact_resume":
            self._cancel("compact_operator_input")

    def reset(self) -> None:
        self._compact = CompactContinuationEvidence()
        self._assistant_ids: OrderedDict[str, str] = OrderedDict()
        self._last_prompt_recovery = False
        # Unknown until this attachment sees a permission-mode record; a
        # session already in plan mode must never get a continuation.
        self._plan_mode: bool | None = None
        self._pending: dict[str, Any] | None = None
        # The runtime input id of the last continuation admitted.  Router
        # delivery records only a "[ciel-wake] pending_ids=..." marker, not the
        # continuation text, so the chain is also recognized by that id.
        self._chain_input_id = 0

    def _cancel(self, reason: str) -> None:
        if self._pending is not None:
            self.log("INFO", f"claude_stream_recovery_cancelled reason={reason} error={self._pending['error_uuid']}")
            self.ledger.note(self._pending["error_uuid"], "cancelled", reason)
            self._pending = None

    def _terminal_blocked(self) -> str:
        """Why the terminal cannot take a continuation now; empty when it can.

        A missing or stale report fails closed: nothing proves the prompt is
        idle without a dialog, draft or queued input.
        """

        snapshot = self._terminal
        if not isinstance(snapshot, dict):
            return "terminal_unknown"
        try:
            age = self.now() - float(snapshot.get("at") or 0)
        except (TypeError, ValueError):
            return "terminal_unknown"
        if age > TERMINAL_REPORT_MAX_AGE_SECONDS:
            return "terminal_stale"
        for key, reason in (
            ("manual_input", "manual_input"),
            ("busy", "terminal_busy"),
            ("compaction", "compaction"),
            ("pending", "pending_input"),
        ):
            if snapshot.get(key) is not False:
                return reason
        if snapshot.get("ready") is not True:
            return "terminal_not_ready"
        return ""

    def _watermark(self) -> int:
        try:
            return int(self.input_watermark())
        except (OSError, TypeError, ValueError):
            # An unreadable queue cannot prove that no newer input exists.
            return -1

    def _skip(self, pending: dict[str, Any], reason: str, subject: str) -> None:
        self.log("INFO", f"claude_stream_recovery_skipped reason={reason} {subject}")
        self.ledger.note(pending["error_uuid"], "skipped", reason)

    def observe(self, record: dict[str, Any]) -> None:
        if record.get("isSidechain") is True:
            return
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "compact_boundary":
            self._cancel("compaction")
            pending, reason = self._compact.candidate(
                record, self.scope(), self.bound_epoch(), self.now(), self._watermark()
            )
            if reason:
                self._skip(pending, reason, f"checkpoint={record.get('uuid') or '-'}")
            else:
                self._pending = pending
            return
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        self._compact.observe(record, self.scope(), _text(message.get("content")), self._watermark())
        if self._pending and self._pending.get("kind") == "compact_resume":
            # Summary/attachments/permission bookkeeping follow every native
            # boundary; only actual work or new input invalidates resumption.
            bookkeeping = (kind in {"attachment", "permission-mode", "mode", "last-prompt", "atis-latch",
                                    "file-history-snapshot", "cost-state"}
                           or kind == "user" and record.get("isCompactSummary") is True)
            if bookkeeping:
                if kind == "permission-mode" and record.get("permissionMode") == "plan":
                    self._cancel("plan_mode")
                return
            self._cancel("native_continued")
        if kind == "assistant":
            if record.get("isApiErrorMessage") is True:
                self._observe_error(record)
                return
            uuid, message_id = str(record.get("uuid") or ""), str(message.get("id") or "")
            if uuid and message_id:
                self._assistant_ids[uuid] = message_id
                while len(self._assistant_ids) > _ASSISTANT_ID_LIMIT:
                    self._assistant_ids.popitem(last=False)
            self._cancel("native_continued")
        elif kind == "user":
            # Input over the session socket (a continuation included) is a
            # peer message Claude Code records as meta; other meta records
            # (reminders, command caveats) are not input.
            origin = record.get("origin")
            if record.get("isMeta") is True and not (isinstance(origin, dict) and origin.get("kind") == "peer"):
                return
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(item, dict) and item.get("type") == "tool_result" for item in content
            ):
                self._cancel("native_tool_result")
                return
            text = _text(content)
            self._last_prompt_recovery = RECOVERY_MARKER in text or (
                self._chain_input_id > 0 and self._chain_input_id in prompt_message_ids(text)
            )
            self._cancel("new_input")
        elif kind == "queue-operation" and record.get("operation") == "enqueue":
            # Input typed or delivered while the error was showing.
            self._cancel("queued_input")
        elif kind == "permission-mode":
            self._plan_mode = record.get("permissionMode") == "plan"
            if self._plan_mode:
                self._cancel("plan_mode")
        elif kind == "system" and record.get("subtype") == "compact_boundary":
            self._cancel("compaction")

    def _observe_error(self, record: dict[str, Any]) -> None:
        # Another error replaces a candidate that never got quiet.
        self._cancel("superseded_error")
        error_uuid = str(record.get("uuid") or "")
        message_id = self._assistant_ids.get(str(record.get("parentUuid") or ""), "")
        if record.get("truncatedAfterOutput") is not True or not error_uuid or not message_id:
            return
        if self._plan_mode is not False:
            reason = "plan_mode" if self._plan_mode else "permission_mode_unknown"
            self._skip({"error_uuid": error_uuid}, reason, f"error={error_uuid}")
            return
        self._pending = {
            "error_uuid": error_uuid,
            "message_id": message_id,
            "session_id": str(record.get("sessionId") or ""),
            "continues_chain": self._last_prompt_recovery,
            "seen_at": self.now(),
            # Runtime input admitted after this point is newer work; the
            # continuation must not reach Claude Code in front of it.
            "input_watermark": self._watermark(),
        }

    def tick(self) -> dict[str, Any] | None:
        pending = self._pending
        if pending is None or self.now() - pending["seen_at"] < self.quiet_seconds:
            return None
        self._pending = None
        config = self.load_config()
        compact = pending.get("kind") == "compact_resume"
        enabled, limit = settings(config)
        if compact:
            if self.now() - pending["seen_at"] > RECOVERY_NOTICE_TTL_SECONDS:
                self._skip(pending, "compact_expired", f"checkpoint={pending['error_uuid']}")
                return None
            enabled, limit = claude_stream_recovery_settings.compact_resume(config), 1
        if not enabled:
            self._skip(pending, "disabled", f"error={pending['error_uuid']}")
            return None
        scope = self.scope()
        bound_epoch = self.bound_epoch()
        if (
            scope is None or scope.runtime != "claude" or scope.session_id != pending["session_id"]
            or (bound_epoch and scope.epoch != bound_epoch)
        ):
            self._skip(pending, "scope", f"error={pending['error_uuid']}")
            return None
        if compact and scope != pending["scope"]:
            self._skip(pending, "scope", f"checkpoint={pending['error_uuid']}")
            return None
        watermark = self._watermark()
        if watermark < 0 or watermark != pending["input_watermark"]:
            self._skip(pending, "newer_input", f"error={pending['error_uuid']}")
            return None
        original_terminal = self._terminal
        if compact and isinstance(original_terminal, dict):
            self._terminal = self._compact.terminal_for_compact(original_terminal)
        blocked = self._terminal_blocked()
        self._terminal = original_terminal
        if blocked:
            if compact and blocked in {"terminal_busy", "compaction", "terminal_stale", "terminal_unknown", "terminal_not_ready"}:
                # Checkpoint persistence can precede the terminal's refreshed
                # idle report. Keep this bounded candidate until its TTL,
                # rather than permanently dropping it at the first busy poll.
                self._pending = pending
                return None
            self._skip(pending, blocked, f"error={pending['error_uuid']}")
            return None
        if compact:
            snapshot = self._terminal or {}
            if (snapshot.get("manual_input_known") is not True or self._compact.operator_input
                    or not isinstance(snapshot.get("at"), (float, int))
                    or not pending["seen_at"] <= snapshot["at"] <= self.now()):
                self._skip(pending, "compact_terminal_unproven", f"checkpoint={pending['error_uuid']}")
                return None
            return self._submit_compact(scope, pending)
        failure = self.ledger.failure(scope, pending["message_id"])
        if failure is None:
            self._skip(pending, "no_router_failure", f"message={pending['message_id']}")
            return None
        excluded = ineligible_reason(failure)
        if excluded:
            self._skip(pending, excluded, f"message={pending['message_id']}")
            return None
        attempt = self.ledger.claim(
            scope, pending["error_uuid"], continues_chain=pending["continues_chain"], limit=limit
        )
        if attempt is None:
            self.log("WARN", f"claude_stream_recovery_skipped reason=claimed_or_exhausted error={pending['error_uuid']} limit={limit}")
            return None
        body = recovery_notice_body(
            attempt, limit, error_uuid=pending["error_uuid"], message_id=pending["message_id"],
            scope=scope, created_at=self.now(),
        )
        try:
            admitted = self.submit(body)
        except Exception as exc:
            self.ledger.settle(pending["error_uuid"], "submit_failed")
            self.log("WARN", f"claude_stream_recovery_submit_failed error={type(exc).__name__} native_error={pending['error_uuid']}")
            return None
        try:
            self._chain_input_id = int((admitted or {}).get("id") or 0) if isinstance(admitted, dict) else 0
        except (TypeError, ValueError):
            self._chain_input_id = 0
        self.log(
            "INFO",
            "claude_stream_recovery_admitted "
            f"session={scope.session_id} attempt={attempt}/{limit} message={pending['message_id']} "
            f"native_error={pending['error_uuid']} input_id={(admitted or {}).get('id') if isinstance(admitted, dict) else '-'}",
        )
        return {"attempt": attempt, "limit": limit, "admitted": admitted}

    def _submit_compact(self, scope: CompactionScope, pending: dict[str, Any]) -> dict[str, Any] | None:
        attempt = self.ledger.claim(scope, pending["error_uuid"], continues_chain=False, limit=1, checkpoint=True)
        if attempt is None:
            return None
        body = recovery_notice_body(1, 1, error_uuid=pending["error_uuid"], message_id="",
                                    scope=scope, created_at=self.now(), compact=True)
        try:
            admitted = self.submit(body)
        except Exception as exc:
            self.ledger.settle(pending["error_uuid"], "submit_failed")
            self.log("WARN", f"claude_compact_resume_submit_failed error={type(exc).__name__}")
            return None
        self.log("INFO", f"claude_compact_resume_admitted session={scope.session_id} checkpoint={pending['checkpoint_uuid']}")
        return {"attempt": 1, "limit": 1, "kind": "compact_resume", "admitted": admitted}


class ClaudeStreamRecovery:
    """One router instance's ledger with its router recorder and launcher monitor."""

    def __init__(
        self,
        instance_dir: Path,
        log: Callable[[str, str], None],
        load_config: Callable[[], dict[str, Any]],
        binding: Any,
        submit: Callable[[dict[str, Any]], Any],
        input_watermark: Callable[[], int] = lambda: 0,
    ) -> None:
        self.ledger = StreamRecoveryLedger(instance_dir / LEDGER_NAME, log)
        self.recorder = ClaudeStreamFailureRecorder(self.ledger, binding.scope, log)
        self.monitor = ClaudeStreamRecoveryMonitor(
            self.ledger, load_config, binding.scope, submit, log, input_watermark=input_watermark,
            bound_epoch=lambda: str(getattr(binding, "bound_epoch", "") or ""),
        )
        self.binding = binding

    def request_scope(self, headers: Any, body: dict[str, Any]) -> CompactionScope | None:
        """The main-session scope a streamed request starts in, read at its start."""

        return self.binding.request_scope("claude", headers, body)

    def delivery_skip_reason(self, message: dict[str, Any]) -> str:
        """Expire a queued continuation outside its exact launch scope."""

        return recovery_notice_skip_reason(message, self.binding.scope(), time.time())


__all__ = [
    "CONFIG_KEY",
    "ClaudeStreamRecovery",
    "ClaudeStreamFailureRecorder",
    "ClaudeStreamRecoveryMonitor",
    "LEDGER_NAME",
    "RECOVERY_MARKER",
    "StreamRecoveryLedger",
    "ineligible_reason",
    "recovery_notice_body",
    "recovery_notice_skip_reason",
    "settings",
    "stream_failure_record",
]
