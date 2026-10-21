"""Bind native compaction admission to a private cross-process launch identity.

The launcher persists runtime, explicit native session id and a fresh epoch
under the router instance directory; the router and channel consumers resolve
that same file.  Identity comes from the launch, never from the newest
workspace transcript. Fresh launches resolve their exact published socket or
main-request identity before external input can enter.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .claude_session_socket import canonical_socket_path
from .channel_message_repository import exclusive_file_lock
from .channel_transcript_repository import (
    ChannelTranscriptRepository,
    read_codex_session_meta,
)
from .native_context_recovery import parse_context_overflow
from .oauth_routing import codex_session_key
from .process_control import pid_is_running
from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .runtime_compaction_control import CompactionScope, build_runtime_compaction_control
from .secure_json_repository import SecureJsonEffects, SecureJsonRepository

# A launch's published Claude record is re-read at most this often to follow
# /clear and /resume, which Claude Code 2.1.303 writes as a new sessionId.
PUBLISHED_RECHECK_SECONDS = 2.0


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


def _usage_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _records(path: Path, limit: int = 512 * 1024) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            data = stream.read(limit)
    except OSError:
        return []
    if size > limit:
        # Never parse a partial first record of a bounded tail.
        data = data.partition(bytes([10]))[2]
    records = []
    for line in data.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict) and record.get("isSidechain") is not True:
            records.append(record)
    return records


def native_input_tokens(path: Path, scope: CompactionScope) -> int | None:
    """Prompt size the native client last reported; None stays unknown.

    Claude records the prompt as input + cache read + cache creation tokens;
    Codex token_count events carry last_token_usage.input_tokens.  A newer
    native checkpoint supersedes earlier usage, so it resets to unknown.
    """

    latest = None
    for record in _records(path):
        payload = record.get("payload")
        if scope.runtime == "claude":
            if record.get("sessionId") != scope.session_id:
                continue
            if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                latest = None
                continue
            message = record.get("message")
            usage = message.get("usage") if record.get("type") == "assistant" and isinstance(message, dict) else None
            if not isinstance(usage, dict) or not _usage_int(usage.get("input_tokens")):
                continue
            values = [usage.get(key, 0) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
            if all(_usage_int(value) for value in values):
                latest = sum(values)
        elif scope.runtime == "codex":
            if record.get("type") == "compacted":
                latest = None
                continue
            if record.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            usage = info.get("last_token_usage") if isinstance(info, dict) else None
            value = usage.get("input_tokens") if isinstance(usage, dict) else None
            if _usage_int(value):
                latest = value
    return latest


class RuntimeCompactionBinding:
    """One scope and admission state shared by launcher, router and consumers."""

    def __init__(self, root: Path, home: Path, requests: Any, builder: Callable[[], Any],
                 estimate: Callable[[Any], int], config: Callable[[], tuple[str, dict[str, Any]]],
                 log: Callable[[str, str], None]) -> None:
        self.home, self.builder, self.estimate, self.config, self.log = home, builder, estimate, config, log
        self.repository = SecureJsonRepository(root / "compaction-scope.json", SecureJsonEffects(log=log))
        self.seed_lock_path = root / "compaction-seed"
        self.cache: dict[str, Any] = {}
        self.seeded = ""
        self.pending_logged = ""
        self.bound_epoch = ""
        self.published_checked: tuple[str, float] = ("", 0.0)
        self.control = build_runtime_compaction_control(
            root / "compaction-admission.json", scope=self.scope,
            transcript_path=self.transcript_path, requests=requests,
            compact_kind=self._compact_kind, log=log,
        )

    def _compact_kind(self, body: dict[str, Any]) -> str | None:
        return self.builder().budget.compact_kind(body)

    def bind(self, runtime: str, **options: Any) -> None:
        value = {"runtime": runtime, "epoch": uuid.uuid4().hex,
                 "started_at": options.get("started_at") or time.time(),
                 "session_id": str(options.get("session_id") or "").strip(),
                 "cwd": str(options.get("cwd") or ""),
                 "codex_home": str(options.get("codex_home") or ""), "path": "",
                 "messaging_socket_path": str(options.get("messaging_socket_path") or ""),
                 "owned_pid": _positive(options.get("owned_pid")), "launcher_pid": os.getpid(),
                 # Claude Code publishes <pid>.json under the config dir it inherits.
                 "claude_config_dir": os.environ.get("CLAUDE_CONFIG_DIR") or str(self.home / ".claude"),
                 # False only when the launcher knows requests bypass this router.
                 "request_identity": options.get("request_identity") is not False}
        try:
            with exclusive_file_lock(self.repository.path):
                self.repository.save(value, "compaction_scope")
            self.bound_epoch = value["epoch"]
        except OSError as exc:
            # The launch proceeds; consumers fail closed on unreadable state.
            self.log("WARN", f"runtime_compaction_scope_bind_failed runtime={runtime} error={type(exc).__name__}")

    def _update(self, epoch: str, **fields: Any) -> dict[str, Any]:
        with exclusive_file_lock(self.repository.path):
            value = self.repository.load("compaction_scope")
            if value is None:
                raise OSError("compaction scope is unreadable")
            if fields and value.get("epoch") == epoch:
                value.update(fields)
                self.repository.save(value, "compaction_scope")
            return value

    def _published_claude_session(self, value: dict[str, Any]) -> str:
        """The session Claude Code publishes for this launch's exact socket.

        The record must name the launch's messaging socket and cwd, carry a
        process identity, and start after the launch (an earlier process on a
        reused path cannot qualify).  Two qualifying records prove neither.
        """

        target = str(value.get("messaging_socket_path") or "")
        cwd = ChannelTranscriptRepository._normalized_cwd(value.get("cwd"))
        if not target or not cwd:
            return ""
        directory = Path(value.get("claude_config_dir") or self.home / ".claude") / "sessions"
        candidates = []
        try:
            paths = list(directory.glob("*.json"))
        except OSError:
            return ""
        for path in paths:
            try:
                if path.stat().st_size > 64 * 1024:
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    continue
                pid = _positive(record.get("pid"))
                if not pid or path.stem != str(pid) or not record.get("procStart"):
                    continue
                if value.get("owned_pid") and pid != value["owned_pid"]:
                    continue
                if float(record.get("startedAt") or 0) / 1000 < float(value["started_at"]) - 1:
                    continue
                if ChannelTranscriptRepository._normalized_cwd(record.get("cwd")) != cwd:
                    continue
                published = str(record.get("messagingSocketPath") or "")
                session_id = str(record.get("sessionId") or "").strip()
                if published and session_id and canonical_socket_path(published) == canonical_socket_path(target):
                    candidates.append(session_id)
            except (OSError, ValueError, TypeError, UnicodeError):
                continue
        return candidates[0] if len(candidates) == 1 else ""

    def _follow_published(self, value: dict[str, Any]) -> dict[str, Any]:
        epoch, now = value["epoch"], time.time()
        checked_epoch, checked_at = self.published_checked
        if value.get("session_id") and checked_epoch == epoch and now - checked_at < PUBLISHED_RECHECK_SECONDS:
            return value
        self.published_checked = (epoch, now)
        session_id = self._published_claude_session(value)
        if session_id and session_id != value.get("session_id") and self._switch(epoch, "claude", session_id, "socket"):
            return self._update("")
        return value

    def _switch(self, epoch: str, runtime: str, session_id: str, via: str, *, unbound_only: bool = False) -> bool:
        """Retarget one launch epoch to an exactly observed native session."""

        with exclusive_file_lock(self.repository.path):
            value = self.repository.load("compaction_scope")
            if not value or value.get("epoch") != epoch or value.get("runtime") != runtime:
                return False
            if value.get("session_id") == session_id:
                return True
            if unbound_only and value.get("session_id"):
                return False
            # Proof belongs to one conversation: path and seeding start over.
            value.update({"session_id": session_id, "path": "", "seeded": False, "request_observed": False})
            self.repository.save(value, "compaction_scope")
        self.log("INFO", f"runtime_compaction_scope_bound runtime={runtime} session={session_id} via={via}")
        return True

    def _bind_request_identity(self, runtime: str, headers: Any, body: dict[str, Any]) -> None:
        # HTTP identity only: missing headers and subagents cannot select a
        # launch.  A declared socket is the stronger ownership boundary: its
        # header can only prompt an earlier re-read of the published record.
        if headers is None or _header(headers, "x-claude-code-agent-id"):
            return
        value = self._update("")
        if value.get("runtime") != runtime or not value.get("epoch"):
            return
        session_id = (_header(headers, "x-claude-code-session-id") if runtime == "claude"
                      else codex_session_key(headers, body))
        if not session_id or session_id == "default" or session_id == value.get("session_id"):
            return
        if value.get("messaging_socket_path"):
            self.published_checked = ("", 0.0)
        elif not value.get("session_id") and value.get("request_identity") is not False:
            self._switch(value["epoch"], runtime, session_id, "request", unbound_only=True)

    def bind_native_session(self, runtime: str, session_id: str) -> bool:
        """Follow a native thread the launching process itself observed.

        Only the epoch this process bound may change; a stale launcher or a
        process that never bound cannot retarget a newer launch.
        """

        session_id = str(session_id or "").strip()
        if not session_id or not self.bound_epoch:
            return False
        try:
            return self._switch(self.bound_epoch, runtime, session_id, "thread")
        except OSError as exc:
            self.log("WARN", f"runtime_compaction_scope_thread_bind_failed error={type(exc).__name__}")
            return False

    def _resolve(self) -> dict[str, Any]:
        value = self._update("")
        if value.get("runtime") not in {"claude", "codex"} or not value.get("epoch"):
            return {}
        if value["runtime"] == "claude" and value.get("messaging_socket_path"):
            value = self._follow_published(value)
        if value.get("runtime") not in {"claude", "codex"} or not value.get("session_id"):
            return {}
        identity = (value["epoch"], value["session_id"])
        if self.cache.get("identity") != identity:
            # A transcript scan is valid for one exact session of one epoch.
            self.cache.clear()
            self.cache.update({"identity": identity, "checked_at": 0.0, "path": None})
        scope = {"runtime": value["runtime"], "started_at": value["started_at"], "session_id": value["session_id"],
                 "cwd": Path(value["cwd"]) if value.get("cwd") else None,
                 "codex_home": Path(value["codex_home"]) if value.get("codex_home") else None,
                 "claude_config_dir": value.get("claude_config_dir")}
        repository = ChannelTranscriptRepository(self.home, self.cache, scope, time.time)
        # Retain the exact selected path, but revalidate native identity so a
        # replaced file cannot supply proof under an earlier launch binding.
        path = Path(value["path"]) if value.get("path") else repository.latest()
        if path is None:
            return value
        if self._transcript_session(path, value["runtime"]) != value["session_id"]:
            # A replaced or vanished file supplies no proof; the launch's
            # identity stays exact and the next scan selects again.
            return self._update(value["epoch"], path="") if value.get("path") else value
        return value if value.get("path") else self._update(value["epoch"], path=str(path))

    @staticmethod
    def _transcript_session(path: Path, runtime: str) -> str:
        """The session a transcript's own native metadata names."""

        if runtime == "claude":
            session_id = ChannelTranscriptRepository._claude_session_identity(path)[2]
            return session_id if any(record.get("sessionId") == session_id for record in _records(path, 64 * 1024)) else ""
        # A large ``base_instructions`` payload makes the first physical line
        # longer than any bounded readline; the shared reader validates the
        # record and returns only its exact identity scalars.
        meta = read_codex_session_meta(path)
        if meta is None or meta.record_type != "session_meta" or meta.is_sidechain:
            return ""
        return meta.identity

    def scope(self) -> CompactionScope | None:
        value = self._resolve()
        return CompactionScope(value["runtime"], value["session_id"], value["epoch"]) if value.get("session_id") else None

    def transcript_path(self) -> Path | None:
        value = self._resolve()
        return Path(value["path"]) if value.get("path") else None

    def input_budget(self, provider: str, config: dict[str, Any], body: dict[str, Any]) -> int:
        """The input budget ProviderRequestBuilder.cap_anthropic_body enforces."""

        budget = self.builder().budget
        limit = (budget.context_limit(provider, config) or budget.positive_int(config.get("max_model_len"))
                 or budget.positive_int(config.get("context_window")) or (32768 if provider == "vllm" else 0))
        if not limit:
            return 0
        configured = budget.configured_output(config, body)
        output = (budget.cap_output_ratio(provider, config, configured) or budget.positive_int(body.get("max_tokens"))
                  or configured or 4096)
        return max(8192, limit - output - budget.reserve(config, limit))

    def _active_state(self, scope: CompactionScope | None) -> dict[str, Any]:
        state = self.control.ports.load() if scope else None
        if not state or state.get("scope") != scope.payload() or state.get("phase") == "verified":
            return {}
        return state

    def _main_session(self, runtime: str, headers: Any, body: dict[str, Any]) -> CompactionScope | None:
        """The bound scope when this request belongs to its main conversation.

        Claude Code tags subagent requests with x-claude-code-agent-id under
        the parent's session id; another session id is another conversation.
        An HTTP request without native identity stays unknown and cannot
        prove shrink.  None denotes a trusted in-process observation.
        """

        self._bind_request_identity(runtime, headers, body)
        scope = self.scope()
        if scope is None or scope.runtime != runtime:
            return None
        if runtime == "claude":
            if _header(headers, "x-claude-code-agent-id"):
                return None
            session = _header(headers, "x-claude-code-session-id")
        else:
            session = codex_session_key(headers, body)
            session = "" if session == "default" else session
        # None is reserved for trusted in-process observations; HTTP callbacks
        # always pass the header container, even when it has no session key.
        return scope if headers is None or session == scope.session_id else None

    def request_scope(self, runtime: str, headers: Any, body: dict[str, Any]) -> CompactionScope | None:
        """The bound scope when this HTTP request belongs to its main conversation."""

        try:
            return self._main_session(runtime, headers, body)
        except (OSError, ValueError, TypeError) as exc:
            self.log("WARN", f"runtime_compaction_request_scope_failed runtime={runtime} error={type(exc).__name__}")
            return None

    def observe_request(self, provider: str, config: dict[str, Any], body: dict[str, Any], runtime: str,
                        headers: Any = None) -> None:
        try:
            self._observe_request(provider, config, body, runtime, headers)
        except (OSError, ValueError, TypeError) as exc:
            # An observation must never fail the client request it describes.
            self.log("WARN", f"runtime_compaction_observe_failed runtime={runtime} error={type(exc).__name__}")

    def _observe_request(self, provider: str, config: dict[str, Any], body: dict[str, Any], runtime: str,
                         headers: Any) -> None:
        if config.get(REMOTE_BRIDGE_CONFIG_MARKER):
            return
        scope = self._main_session(runtime, headers, body)
        if scope is None:
            return
        native = self._compact_kind(body)
        tokens = self.estimate({key: value for key, value in body.items() if key != "max_tokens"})
        # A raw Responses body names its output budget max_output_tokens.
        limit = self.input_budget(provider, config, {"max_tokens": body.get("max_tokens", body.get("max_output_tokens"))})
        with exclusive_file_lock(self.seed_lock_path):
            self._update(scope.epoch, request_observed=True)
            active = self._active_state(scope)
            # Native clients own proactive timing. JSON/4 is not tokenizer
            # evidence and the Runtime input reserve is not the CLI threshold.
            # Keep observing native checkpoints and an existing recovery cycle,
            # but only a confirmed upstream overflow may start pressure here.
            if not active and not native:
                return
            if active and not limit:
                # Unknown capacity: the observed compact request size is the
                # only bound a real next request can be proven smaller than.
                limit = _positive(active.get("baseline_tokens"))
            self.control.observe_request(body, tokens, limit, scope=scope)
            after = self._active_state(scope)
            if not native and after.get("phase") == "awaiting_shrink":
                # The real next request did not shrink: stop admitting probes.
                self._update(scope.epoch, failed_probe=after.get("checkpoint_id", ""))

    def observe_failure(self, provider: str, config: dict[str, Any], body: dict[str, Any], raw: Any, runtime: str,
                        headers: Any = None) -> None:
        try:
            self._observe_failure(provider, config, body, raw, runtime, headers)
        except (OSError, ValueError, TypeError) as exc:
            self.log("WARN", f"runtime_compaction_failure_observe_failed runtime={runtime} error={type(exc).__name__}")

    def _observe_failure(self, provider: str, config: dict[str, Any], body: dict[str, Any], raw: Any, runtime: str,
                         headers: Any) -> None:
        if config.get(REMOTE_BRIDGE_CONFIG_MARKER) or (provider == "anthropic" and runtime == "claude"):
            return
        overflow = parse_context_overflow(raw)
        scope = self._main_session(runtime, headers, body) if overflow is not None else None
        if overflow is None or scope is None:
            return
        # Only the provider's own input count proves overflow.  A rejection
        # whose input still fits belongs to output-budget recovery.
        limit = max(1, overflow.context_limit - self.builder().budget.reserve(config, overflow.context_limit))
        if overflow.message_tokens < limit:
            return
        with exclusive_file_lock(self.seed_lock_path):
            self.control.observe_request(body, overflow.message_tokens, limit, scope=scope, recovery_required=True)

    def _admission_paused(self) -> bool:
        """Strict gate for router-side append: paused until shrink is verified."""

        try:
            with exclusive_file_lock(self.seed_lock_path):
                scope = self.scope()
                if scope is None:
                    return self._identity_pending()
                if self.seeded != f"{scope.epoch}:{scope.session_id}":
                    self._seed(scope)
        except (OSError, ValueError, TypeError) as exc:
            self.log("WARN", f"runtime_compaction_scope admission=paused reason=state_unavailable error={type(exc).__name__}")
            return True
        return self.control.external_admission_paused()

    def _identity_pending(self) -> bool:
        """A live supported launch whose exact native identity is not yet known."""

        value = self._update("")
        if value.get("runtime") not in {"claude", "codex"} or not value.get("epoch"):
            return False
        pid = _positive(value.get("launcher_pid"))
        sources = bool(value.get("messaging_socket_path")) or value.get("request_identity") is not False
        held = bool(sources and pid and pid_is_running(pid))
        if self.pending_logged != value["epoch"]:
            self.pending_logged = value["epoch"]
            self.log("INFO" if held else "WARN", f"runtime_compaction_scope admission={'paused' if held else 'ungated'} "
                     f"reason={'identity_pending' if held else 'identity_unavailable'} runtime={value['runtime']}")
        return held

    def external_admission_paused(self) -> bool:
        """Hold external deliveries until checkpoint AND raw shrink are proven.

        A manual compact may wait here for the next user/native request. An
        external SSE delivery must not be used as a recovery probe.
        """
        return self._admission_paused()

    def _seed(self, scope: CompactionScope) -> None:
        value = self._update(scope.epoch)
        if value.get("epoch") != scope.epoch or value.get("session_id") != scope.session_id:
            return
        if value.get("request_observed") or value.get("seeded"):
            self.seeded = f"{scope.epoch}:{scope.session_id}"
            return
        # Retried until the native transcript exists, before external input.
        path = self.transcript_path()
        if path is None:
            return
        self.seeded = f"{scope.epoch}:{scope.session_id}"
        try:
            provider, config = self.config()
        except Exception as exc:  # noqa: BLE001 - seeding is optional evidence
            self.log("WARN", f"runtime_compaction_seed_skipped reason=config_unavailable error={type(exc).__name__}")
            return
        if provider == "anthropic" and scope.runtime == "claude":
            return
        self._update(scope.epoch, seeded=True)
        tokens = native_input_tokens(path, scope)
        budget = self.input_budget(provider, config, {})
        self.log("INFO", f"runtime_compaction_seed runtime={scope.runtime} session={scope.session_id} "
                 f"native_input_tokens={'unknown' if tokens is None else tokens} input_budget={budget or 'unknown'}")
        # Transcript usage predates this launch and is not tied to the current
        # provider/model. It is diagnostic only, never proactive pressure. The
        # native CLI applies its own threshold to the resumed conversation.

    def context_pause_reason(self, body: dict[str, Any]) -> str:
        if self._compact_kind(body):
            return "native_compaction"
        if not self._admission_paused():
            return ""
        return "context_recovery" if self.scope() is not None else "identity_pending"

    def command_ready(self, request: dict[str, Any]) -> bool:
        return self.control.command_ready(request)

    def command_submitted(self, request: dict[str, Any]) -> None:
        self.control.command_submitted(request)

    def command_failed(self, request: dict[str, Any], reason: str, *, uncertain: bool = False) -> None:
        self.control.command_failed(request, reason, uncertain=uncertain)
