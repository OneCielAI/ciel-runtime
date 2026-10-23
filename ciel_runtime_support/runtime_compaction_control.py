"""Queue-preserving admission control around native session compaction.

Counts are the client's unmodified request observations, never fabricated
usage. Command submission and app-server acceptance are not completion:
a persisted native checkpoint and smaller next request release pressure.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from .channel_compact_request_repository import ChannelCompactRequestRepository
from .channel_message_repository import exclusive_file_lock
from .secure_json_repository import SecureJsonEffects, SecureJsonRepository


@dataclass(frozen=True, slots=True)
class CompactionScope:
    runtime: str
    session_id: str
    epoch: str

    def payload(self) -> dict[str, str]:
        return {"runtime": self.runtime, "session_id": self.session_id, "epoch": self.epoch}


@dataclass(frozen=True, slots=True)
class NativeCompactionCheckpoint:
    scope: CompactionScope
    identity: str
    persisted_at: float
    pre_tokens: int = 0
    post_tokens: int = 0
    # "manual" when the user ran /compact: that compaction has no pending turn
    # to retry, so no later request will ever measure its shrink.
    trigger: str = ""
    # Newest native prompt size written after this checkpoint in the same
    # transcript; None stays unknown rather than assumed.
    last_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCompactionPolicy:
    # Semantic work is bounded at 900s plus the final native-wire generation.
    confirmation_timeout_seconds: float = 1200.0
    retry_cooldown_seconds: float = 30.0
    max_attempts: int = 2


@dataclass(frozen=True, slots=True)
class RuntimeCompactionPorts:
    load: Callable[[], dict[str, Any] | None]
    save: Callable[[dict[str, Any]], None]
    lock: Callable[[], Any]
    scope: Callable[[], CompactionScope | None]
    checkpoint: Callable[[CompactionScope], NativeCompactionCheckpoint | None]
    queue: Callable[[dict[str, str], str], dict[str, Any] | None]
    clear: Callable[[str], Any]
    compact_kind: Callable[[dict[str, Any]], str | None]
    log: Callable[[str, str], None]
    now: Callable[[], float] = time.time


def _positive(value: Any) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def _below_capacity(tokens: int, limit: int, baseline: int = 0) -> bool:
    """A measured prompt can only prove shrink against a known capacity."""

    return bool(tokens and limit and tokens < limit and (not baseline or tokens < baseline))


def _timestamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return 0.0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# Retain only one bounded record while scanning; cache scalar proof, never the
# transcript or its replacement history. Changed files are rescanned so a
# rewrite/truncation cannot inherit proof from bytes no longer on disk.
_MAX_RECORD_BYTES = 8 * 1024 * 1024
_SCAN_CACHE_LIMIT = 4


@dataclass(frozen=True, slots=True)
class _TranscriptScan:
    signature: tuple[int, int, int, int, int]
    checkpoint: NativeCompactionCheckpoint | None


_scan_cache: OrderedDict[tuple[str, CompactionScope, int], _TranscriptScan] = OrderedDict()
_scan_lock = threading.Lock()


def _read_jsonl(stream: BinaryIO, max_record_bytes: int):
    """Yield complete bounded records, draining an oversized line whole."""

    while True:
        line = stream.readline(max_record_bytes + 1)
        if not line:
            return
        if len(line) > max_record_bytes:
            while not line.endswith(b"\n"):
                line = stream.readline(256 * 1024)
                if not line:
                    return
            continue
        if not line.endswith(b"\n"):
            return
        yield line.rstrip(b"\r\n")


def _checkpoint_record(record: Any, scope: CompactionScope, path: Path) -> tuple[float, int, int, str] | None:
    if not isinstance(record, dict) or record.get("isSidechain") is True:
        return None
    pre_tokens = post_tokens = 0
    trigger = ""
    if scope.runtime == "claude":
        if (record.get("type") != "system" or record.get("subtype") != "compact_boundary"
                or record.get("sessionId") != scope.session_id):
            return None
        metadata = record.get("compactMetadata")
        if not isinstance(metadata, dict) or not record.get("uuid"):
            return None
        pre_tokens = _positive(metadata.get("preTokens"))
        post_tokens = _positive(metadata.get("postTokens"))
        trigger = str(metadata.get("trigger") or "")
    elif scope.runtime in {"codex", "codex-app-server", "codex-desktop"}:
        payload = record.get("payload")
        if record.get("type") != "compacted" or not isinstance(payload, dict):
            return None
        if not isinstance(payload.get("replacement_history"), list) or not payload["replacement_history"]:
            return None
        usage = payload.get("latest_token_usage_record")
        thread_id = usage.get("thread_id") if isinstance(usage, dict) else None
        # Codex compacted records have no top-level session id.
        if thread_id and thread_id != scope.session_id:
            return None
        if not thread_id and not path.stem.endswith(scope.session_id):
            return None
    else:
        return None
    persisted_at = _timestamp(record.get("timestamp"))
    return (persisted_at, pre_tokens, post_tokens, trigger) if persisted_at > 0 else None


def _input_tokens_record(record: Any, scope: CompactionScope) -> int | None:
    """The prompt size this record reports for the same conversation.

    Claude records input + cache read + cache creation; Codex token_count
    events carry last_token_usage.input_tokens.  Only another process's
    records for this exact session qualify; the file itself binds Codex.
    """

    if not isinstance(record, dict) or record.get("isSidechain") is True:
        return None
    if scope.runtime == "claude":
        if record.get("type") != "assistant" or record.get("sessionId") != scope.session_id:
            return None
        message = record.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict) or not _nonnegative_int(usage.get("input_tokens")):
            return None
        values = [usage.get(key, 0) for key in
                  ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
        if not all(_nonnegative_int(value) for value in values):
            return None
        return sum(values) or None
    if scope.runtime in {"codex", "codex-app-server", "codex-desktop"}:
        payload = record.get("payload")
        if record.get("type") != "event_msg" or not isinstance(payload, dict):
            return None
        if payload.get("type") != "token_count":
            return None
        info = payload.get("info")
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
        value = usage.get("input_tokens") if isinstance(usage, dict) else None
        return value if _nonnegative_int(value) else None
    return None


def native_checkpoint_from_transcript(
    path: Path | None, scope: CompactionScope, *, max_bytes: int = _MAX_RECORD_BYTES
) -> NativeCompactionCheckpoint | None:
    """Scan the exact native transcript with bounded per-record memory.

    ``max_bytes`` caps each JSON record, not the file tail. A 600 KiB native
    checkpoint and checkpoints followed by arbitrarily much later history are
    visible. Unchanged file signatures reuse only scalar checkpoint evidence.
    No latest-file discovery or assistant/tool marker matching is performed.
    """

    if path is None or not scope.session_id or not scope.epoch:
        return None
    cap = max(1, min(int(max_bytes), _MAX_RECORD_BYTES))
    key = (str(path), scope, cap)
    try:
        with _scan_lock, path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            previous = _scan_cache.get(key)
            if previous is not None and previous.signature == signature:
                _scan_cache.move_to_end(key)
                return previous.checkpoint
            newest = None
            for line in _read_jsonl(stream, cap):
                try:
                    record = json.loads(line)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    continue
                found = _checkpoint_record(record, scope, path)
                if found is not None:
                    newest = NativeCompactionCheckpoint(
                        scope, hashlib.sha256(line).hexdigest(), found[0],
                        pre_tokens=found[1], post_tokens=found[2], trigger=found[3])
                    continue
                # Usage must follow a matching checkpoint physically and carry
                # a valid timestamp at or after that boundary. Older usage is
                # never promoted into proof merely because it was appended.
                if (newest is not None and isinstance(record, dict)
                        and _timestamp(record.get("timestamp")) >= newest.persisted_at):
                    tokens = _input_tokens_record(record, scope)
                    if tokens is not None:
                        newest = replace(newest, last_input_tokens=tokens)
            after = os.fstat(stream.fileno())
            after_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            if after_signature != signature:
                return None
            _scan_cache[key] = _TranscriptScan(signature, newest)
            _scan_cache.move_to_end(key)
            while len(_scan_cache) > _SCAN_CACHE_LIMIT:
                _scan_cache.popitem(last=False)
            return newest
    except OSError:
        return None


class RuntimeCompactionControl:
    def __init__(self, ports: RuntimeCompactionPorts,
                 policy: RuntimeCompactionPolicy = RuntimeCompactionPolicy()) -> None:
        self.ports = ports
        self.policy = policy

    def _read(self, scope: CompactionScope) -> dict[str, Any]:
        value = self.ports.load()
        if value is None:
            raise OSError("compaction admission state is unreadable")
        if value.get("scope") != scope.payload():
            return {}
        if value.get("phase") not in {"needed", "queued", "dispatching", "awaiting_checkpoint",
                                      "compacting", "awaiting_shrink", "blocked", "retry_wait", "verified"}:
            raise ValueError("invalid compaction control phase")
        return dict(value)

    def _start(self, scope: CompactionScope, raw_tokens: int, limit: int,
               *, pressure: bool) -> dict[str, Any]:
        now = self.ports.now()
        checkpoint = self.ports.checkpoint(scope)
        return {
            "scope": scope.payload(), "phase": "needed", "pressure": pressure,
            "baseline_tokens": raw_tokens, "context_limit": limit,
            "requested_at": now, "deadline": now + self.policy.confirmation_timeout_seconds,
            "baseline_checkpoint_id": checkpoint.identity if checkpoint else "",
            "checkpoint_id": "", "request_id": "", "attempts": 0,
        }

    def _write(self, state: dict[str, Any], event: str = "", detail: str = "") -> None:
        self.ports.save(state)
        if event:
            self.ports.log(
                "INFO", f"runtime_compaction_control event={event} phase={state.get('phase')} "
                f"runtime={state['scope']['runtime']} session={state['scope']['session_id']} "
                f"epoch={state['scope']['epoch']} attempts={state.get('attempts', 0)}"
                f"{' ' + detail if detail else ''}",
            )

    def _refresh(self, state: dict[str, Any], scope: CompactionScope) -> None:
        if not state or state.get("phase") == "verified":
            return
        now = self.ports.now()
        checkpoint = self.ports.checkpoint(scope)
        if (checkpoint and checkpoint.scope == scope
                and checkpoint.identity != state.get("baseline_checkpoint_id")
                and checkpoint.persisted_at >= float(state.get("requested_at") or 0)
                and checkpoint.identity != state.get("checkpoint_id")):
            state.update({"checkpoint_id": checkpoint.identity, "phase": "awaiting_shrink"})
            self._write(state, "native_checkpoint_observed")
        if (state.get("phase") == "awaiting_shrink" and checkpoint and checkpoint.scope == scope
                and checkpoint.identity == state.get("checkpoint_id")
                and checkpoint.identity != state.get("baseline_checkpoint_id")
                and checkpoint.persisted_at >= float(state.get("requested_at") or 0)):
            # The phase and its evidence are durable, so the release decision
            # is re-evaluated on every refresh rather than only on the write
            # that observed the checkpoint: a restart between those two writes
            # must not hold admission forever.
            self._release(state, checkpoint)
        if state.get("phase") in {"dispatching", "awaiting_checkpoint", "compacting"}:
            if now >= float(state.get("deadline") or 0):
                # An uncertain submission cannot authorize another compact.
                state.update({"phase": "blocked", "failure": "checkpoint_unconfirmed"})
                self._write(state, "confirmation_timeout")
        if state.get("phase") == "queued" and now >= float(state.get("deadline") or 0):
            if state.get("request_id"):
                self.ports.clear(str(state["request_id"]))
            self._retry(state, "request_expired_before_dispatch")
        elif state.get("phase") == "queued":
            # A manual session action may have replaced our automatic slot.
            # Re-reservation does not overwrite it; once it is consumed a new
            # automatic id can be reserved without waiting for the old TTL.
            request = self.ports.queue(scope.payload(), "full-history context pressure")
            if request is None:
                state.update({"phase": "needed", "request_id": ""})
                self._write(state, "manual_action_has_priority")
            elif str(request.get("id") or "") != state.get("request_id"):
                state["request_id"] = str(request.get("id") or "")
                state["deadline"] = min(float(request.get("expires_at") or now + self.policy.confirmation_timeout_seconds),
                                        now + self.policy.confirmation_timeout_seconds)
                self._write(state, "compact_slot_reacquired")
        if state.get("phase") == "retry_wait" and now >= float(state.get("cooldown_until") or 0):
            state["phase"] = "needed"
        if state.get("phase") == "needed":
            request = self.ports.queue(scope.payload(), "full-history context pressure")
            if request:
                state.update({
                    "phase": "queued", "request_id": str(request.get("id") or ""),
                    "attempts": _positive(state.get("attempts")) + 1,
                    "deadline": min(float(request.get("expires_at") or now + self.policy.confirmation_timeout_seconds),
                                    now + self.policy.confirmation_timeout_seconds),
                })
                self._write(state, "compact_queued")
            else:
                # Manual /new or /goal retains ownership of the single slot.
                self._write(state)

    def observe_request(self, body: dict[str, Any], raw_tokens: int, context_limit: int,
                        *, scope: CompactionScope | None = None,
                        recovery_required: bool = False) -> None:
        active_scope = self.ports.scope()
        scope = scope or active_scope
        if scope != active_scope:
            return
        if scope is None or not scope.session_id or not scope.epoch:
            return
        raw_tokens, context_limit = _positive(raw_tokens), _positive(context_limit)
        native = bool(self.ports.compact_kind(body))
        if not native and not recovery_required and (not raw_tokens or not context_limit):
            return
        with self.ports.lock():
            state = self._read(scope)
            active = bool(state and state.get("phase") != "verified")
            pressure = recovery_required or bool(context_limit and raw_tokens >= context_limit)
            if not active:
                if not pressure and not native:
                    return
                state = self._start(scope, raw_tokens, context_limit, pressure=pressure)
                if native:
                    state.update({"phase": "compacting", "attempts": 1})
                self._write(state, "admission_paused")
            elif native:
                if state.get("phase") == "queued" and state.get("request_id"):
                    self.ports.clear(str(state["request_id"]))
                    state["request_id"] = ""
                if state.get("phase") not in {"queued", "dispatching", "awaiting_checkpoint", "compacting"}:
                    # A client-started (reactive or manual) compaction is an
                    # attempt too; otherwise non-shrinking cycles never end.
                    # One that supersedes our queued command uses its count.
                    state["attempts"] = _positive(state.get("attempts")) + 1
                state["phase"] = "compacting"
                state["deadline"] = self.ports.now() + self.policy.confirmation_timeout_seconds
                state["baseline_tokens"] = max(raw_tokens, _positive(state.get("baseline_tokens")))
                # A new manual/native compaction must not reuse proof from an
                # earlier failed attempt of the same recovery epoch.
                current_checkpoint = self.ports.checkpoint(scope)
                state["baseline_checkpoint_id"] = current_checkpoint.identity if current_checkpoint else ""
                state["checkpoint_id"] = ""
                self._write(state, "native_compact_started")
            if native:
                # A new compaction cannot reuse an earlier cycle's request.
                state.pop("shrink_observed", None)
            if context_limit and not _positive(state.get("context_limit")):
                # A manual dispatch starts before a request reveals capacity.
                # Persist that real bound, never infer one from native usage.
                state["context_limit"] = context_limit
                self._write(state)
            baseline = _positive(state.get("baseline_tokens"))
            shrunk = bool(not native and not recovery_required
                          and _below_capacity(raw_tokens, context_limit, baseline))
            if shrunk and state.get("phase") in {"dispatching", "awaiting_checkpoint", "compacting"}:
                state["shrink_observed"] = {"tokens": raw_tokens, "at": self.ports.now()}
                self._write(state, "smaller_request_before_checkpoint")
            self._refresh(state, scope)
            if native or state.get("phase") != "awaiting_shrink":
                return
            if shrunk:
                self._verify(state, raw_tokens)
            elif recovery_required or (context_limit and raw_tokens >= context_limit):
                state["baseline_tokens"] = max(raw_tokens, baseline)
                self._not_shrunk(state, "next_request_not_below_budget")

    def _release(self, state: dict[str, Any], checkpoint: NativeCompactionCheckpoint) -> None:
        """Decide whether a stored checkpoint cycle may release admission.

        Every input here was written under the new-checkpoint gate (exact
        session, identity, timestamp at or after the request), so this only
        re-applies the shrink rules to durable evidence.
        """

        limit = _positive(state.get("context_limit"))
        if checkpoint.post_tokens and limit and checkpoint.post_tokens >= limit:
            # The client measured its own compacted history as still full.
            self._not_shrunk(state, "checkpoint_not_below_budget")
            return
        baseline = _positive(state.get("baseline_tokens"))
        observed = state.get("shrink_observed")
        if isinstance(observed, dict):
            # Claude Code 2.1.303 persists the boundary only after its
            # automatic retry completes; that retry was the smaller raw
            # request of this same compaction cycle.
            tokens = _positive(observed.get("tokens"))
            if _below_capacity(tokens, limit, baseline):
                self._verify(state, tokens, evidence="next_request_shrink")
                return
        if checkpoint.trigger == "manual" and _below_capacity(checkpoint.post_tokens, limit, baseline):
            # A user-run /compact has no pending turn to retry, so no later
            # request will ever measure it: holding here stranded every
            # external delivery until someone typed (Regina, 2026-10-23:
            # 716,750 window, boundary postTokens 15,117, nothing after).
            # The boundary is the client's own measurement of its compacted
            # history; a request that still overflows re-arms this state.
            self._verify(state, checkpoint.post_tokens, evidence="manual_checkpoint_post_tokens")
            return
        # The native client's own newest prompt size after this checkpoint is
        # the same measurement as the next raw request, for a client that
        # sends without passing through the router.
        tokens = checkpoint.last_input_tokens
        if tokens is not None and _below_capacity(tokens, limit, baseline):
            self._verify(state, tokens, evidence="post_checkpoint_prompt")

    def _verify(self, state: dict[str, Any], raw_tokens: int, *, evidence: str = "") -> None:
        if state.get("request_id"):
            self.ports.clear(str(state["request_id"]))
        state.update({"phase": "verified", "verified_at": self.ports.now(), "next_raw_tokens": raw_tokens})
        self._write(state, "checkpoint_and_history_shrink_verified", f"evidence={evidence}" if evidence else "")

    def _not_shrunk(self, state: dict[str, Any], reason: str) -> None:
        # A checkpoint that did not shrink is spent proof: the next attempt
        # needs a newer one, and attempts stay bounded.
        state.pop("shrink_observed", None)
        state.update({"baseline_checkpoint_id": state.get("checkpoint_id") or "",
                      "checkpoint_id": "", "requested_at": self.ports.now()})
        self._retry(state, reason)

    def external_admission_paused(self) -> bool:
        try:
            scope = self.ports.scope()
            if scope is None:
                return False
            with self.ports.lock():
                state = self._read(scope)
                self._refresh(state, scope)
                return bool(state and state.get("phase") != "verified")
        except (OSError, ValueError, TypeError) as exc:
            self.ports.log("WARN", f"runtime_compaction_control admission=paused reason=state_unavailable error={type(exc).__name__}")
            return True

    def context_pause_reason(self, body: dict[str, Any]) -> str:
        if self.ports.compact_kind(body):
            return "native_compaction"
        return "context_recovery" if self.external_admission_paused() else ""

    def command_ready(self, request: dict[str, Any]) -> bool:
        if request.get("action", "compact") != "compact":
            return True
        if not request.get("id"):
            return request.get("automatic") is not True
        scope = self.ports.scope()
        if scope is None:
            return request.get("automatic") is not True
        with self.ports.lock():
            state = self._read(scope)
            automatic = request.get("automatic") is True
            if automatic:
                if request.get("scope") != scope.payload():
                    self.ports.clear(str(request.get("id") or ""))
                    return False
                self._refresh(state, scope)
                if (state.get("phase") != "queued"
                        or state.get("request_id") != str(request.get("id") or "")):
                    if state.get("phase") in {"verified", "awaiting_shrink", "compacting", "blocked"}:
                        self.ports.clear(str(request.get("id") or ""))
                    return False
            elif state.get("request_id") == str(request.get("id") or ""):
                # Two consumers may have read the same single slot. A manual
                # retry is a new request id, never a second dispatch of this id.
                return False
            elif not state or state.get("phase") == "verified":
                state = self._start(scope, 0, 0, pressure=False)
            if not automatic:
                checkpoint = self.ports.checkpoint(scope)
                state["baseline_checkpoint_id"] = checkpoint.identity if checkpoint else ""
                state["checkpoint_id"] = ""
                state["requested_at"] = self.ports.now()
            state.pop("shrink_observed", None)
            state.update({"phase": "dispatching", "request_id": str(request.get("id") or ""),
                          "deadline": self.ports.now() + self.policy.confirmation_timeout_seconds})
            self._write(state, "command_dispatch_started")
            return True

    def command_submitted(self, request: dict[str, Any]) -> None:
        self._command_result(request, "awaiting_checkpoint", "command_submitted")

    def command_failed(self, request: dict[str, Any], reason: str, *, uncertain: bool = False) -> None:
        self._command_result(request, "blocked" if uncertain else "retry_wait", reason)

    def _command_result(self, request: dict[str, Any], phase: str, reason: str) -> None:
        if request.get("action", "compact") != "compact":
            return
        scope = self.ports.scope()
        if scope is None:
            return
        with self.ports.lock():
            state = self._read(scope)
            if not state or state.get("request_id") != str(request.get("id") or ""):
                return
            if state.get("phase") in {"awaiting_shrink", "verified", "compacting"}:
                return
            if phase == "retry_wait":
                self.ports.clear(str(request.get("id") or ""))
                if request.get("automatic") is True:
                    self._retry(state, reason)
                else:
                    state.update({"phase": "blocked", "failure": reason})
                    self._write(state, reason)
            else:
                state.update({"phase": phase, "failure": reason})
                self._write(state, reason)

    def _retry(self, state: dict[str, Any], reason: str) -> None:
        state.update({"failure": reason, "request_id": ""})
        if _positive(state.get("attempts")) >= self.policy.max_attempts:
            state["phase"] = "blocked"
        else:
            state.update({"phase": "retry_wait", "cooldown_until": self.ports.now() + self.policy.retry_cooldown_seconds})
        self._write(state, reason)


def build_runtime_compaction_control(
    state_path: Path,
    *,
    scope: Callable[[], CompactionScope | None],
    transcript_path: Callable[[], Path | None],
    requests: ChannelCompactRequestRepository,
    compact_kind: Callable[[dict[str, Any]], str | None],
    log: Callable[[str, str], None],
    policy: RuntimeCompactionPolicy = RuntimeCompactionPolicy(),
) -> RuntimeCompactionControl:
    """Compose private atomic state shared by the router and native consumer."""

    state = SecureJsonRepository(state_path, SecureJsonEffects(log=log))

    def load_state() -> dict[str, Any] | None:
        # An unreadable file used to pause external delivery forever, with no
        # request able to repair it. Move it aside once; the next observed
        # request then starts a new cycle from its own measured tokens, so a
        # full context still pauses admission until shrink is proven.
        value = state.load("runtime_compaction_control")
        if value is None:
            state.quarantine("runtime_compaction_control")
            return {}
        return value

    return RuntimeCompactionControl(
        RuntimeCompactionPorts(
            load=load_state,
            save=lambda value: state.save(value, "runtime_compaction_control"),
            lock=lambda: exclusive_file_lock(state_path),
            scope=scope,
            checkpoint=lambda current: native_checkpoint_from_transcript(transcript_path(), current),
            queue=requests.queue_automatic,
            clear=requests.clear,
            compact_kind=compact_kind,
            log=log,
        ),
        policy,
    )
