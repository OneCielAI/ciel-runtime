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
from .claude_session_handoff import follow_parked_session
from .channel_message_repository import exclusive_file_lock
from .channel_transcript_repository import (
    ChannelTranscriptRepository,
    read_codex_session_meta,
)
from .native_context_recovery import parse_context_overflow
from .native_model_usage import latest_native_model_usage, model_names, request_model_key
from .oauth_routing import codex_session_key
from .process_control import pid_is_running
from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .runtime_compaction_control import CompactionScope, build_runtime_compaction_control
from .secure_json_repository import SecureJsonEffects, SecureJsonRepository

# A launch's published Claude record is re-read at most this often to follow
# /clear and /resume, which Claude Code 2.1.303 writes as a new sessionId.
PUBLISHED_RECHECK_SECONDS = 2.0

# An unproven launch identity holds external input until published idle or a
# routed request. Older clients without idle status retain this fallback: Celly held
# 6363 until the user typed "222" (2026-10-23 01:35). After this window the
# gate opens for the launch's own socket; the first request still re-binds the
# exact scope, and any request that overflows re-arms compaction admission.
REQUEST_IDENTITY_GRACE_SECONDS = 120.0

# Upstream model names the router saw answer one request alias, kept per launch
# epoch so the safeguard can read native usage recorded under those names.
NATIVE_MODEL_ALIASES_LIMIT = 8
NATIVE_MODEL_NAMES_LIMIT = 16
NATIVE_MODEL_NAME_CHARS = 128


def _positive(value: Any) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def _identity_expired(value: dict[str, Any], now: float) -> bool:
    """A held launch identity older than the grace window no longer blocks."""

    try:
        since = float(value.get("identity_since") or value.get("started_at") or 0)
    except (ValueError, TypeError, OverflowError):
        return False
    return since > 0 and now - since >= REQUEST_IDENTITY_GRACE_SECONDS


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
                 log: Callable[[str, str], None], *, gate_config: Callable[[], dict[str, Any]] = lambda: {}) -> None:
        self.home, self.builder, self.estimate, self.config, self.log = home, builder, estimate, config, log
        self.repository = SecureJsonRepository(root / "compaction-scope.json", SecureJsonEffects(log=log))
        self.gate_config = gate_config
        self.gate_snapshot = SecureJsonRepository(root / "sse-gate-status.json", SecureJsonEffects(log=log))
        self.safeguard_checked = 0.0
        self.cache: dict[str, Any] = {}
        self.seeded = ""
        self.pending_logged = ""
        self.bound_epoch = ""
        self.published_checked: tuple[str, float] = ("", 0.0)
        self.native_seen: set[tuple[str, str, str, str]] = set()
        self.control = build_runtime_compaction_control(
            root / "compaction-admission.json", scope=self.scope,
            transcript_path=self.transcript_path, requests=requests,
            compact_kind=self._compact_kind, log=log,
        )

    def _compact_kind(self, body: dict[str, Any]) -> str | None:
        return self.builder().budget.compact_kind(body)

    def bind(self, runtime: str, **options: Any) -> None:
        started_at = options.get("started_at") or time.time()
        value = {"runtime": runtime, "epoch": uuid.uuid4().hex,
                 "started_at": started_at, "identity_since": started_at,
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

    def _update(self, epoch: str, *, expected_session: str | None = None, **fields: Any) -> dict[str, Any]:
        # Reads take the short scope lock too: on Windows an open reader makes
        # the writer's atomic replace fail (measured: 2194 failed saves in 3 s).
        with exclusive_file_lock(self.repository.path):
            value = self.repository.load("compaction_scope")
            if value is None:
                raise OSError("compaction scope is unreadable")
            if (fields and value.get("epoch") == epoch
                    and (expected_session is None or value.get("session_id") == expected_session)):
                value.update(fields)
                self.repository.save(value, "compaction_scope")
            return value

    def _published_claude_record(self, value: dict[str, Any]) -> dict[str, Any]:
        """The session Claude Code publishes for this launch's exact socket.

        The record must name the launch's messaging socket and cwd, carry a
        process identity, and start after the launch (an earlier process on a
        reused path cannot qualify).  Two qualifying records prove neither.
        """

        target = str(value.get("messaging_socket_path") or "")
        cwd = ChannelTranscriptRepository._normalized_cwd(value.get("cwd"))
        if not target or not cwd:
            return {}
        directory = Path(value.get("claude_config_dir") or self.home / ".claude") / "sessions"
        candidates = []
        try:
            paths = list(directory.glob("*.json"))
        except OSError:
            return {}
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
                    candidates.append(record)
            except (OSError, ValueError, TypeError, UnicodeError):
                continue
        if len(candidates) != 1:
            # Once a published identity was observed, a transient missing or
            # unreadable record cannot demote it to legacy socket delivery.
            return {"handoff_pending": True} if (candidates or value.get("delivery_socket_path")) else {}
        handoff = follow_parked_session(directory, candidates[0])
        return {"handoff_pending": True} if handoff.pending else handoff.record

    def _follow_published(self, value: dict[str, Any]) -> dict[str, Any]:
        epoch, now = value["epoch"], time.time()
        checked_epoch, checked_at = self.published_checked
        if value.get("identity_ready") and checked_epoch == epoch and now - checked_at < PUBLISHED_RECHECK_SECONDS:
            return value
        self.published_checked = (epoch, now)
        record = self._published_claude_record(value)
        session_id = str(record.get("sessionId") or "").strip()
        ready = None
        if session_id:
            peer = {"session_id": session_id, "pid": record["pid"], "proc_start": record["procStart"]}
            previous = value.get("identity_ready")
            if isinstance(previous, dict) and all(previous.get(key) == item for key, item in peer.items()):
                # Readiness establishes identity once; busy turns do not make
                # that same native process provisional again.
                ready = previous
            else:
                try:
                    stamp = record.get("statusUpdatedAt")
                    if (record.get("status") == "idle" and not isinstance(stamp, bool)
                            and float(record["startedAt"]) / 1000 - 1 <= float(stamp) / 1000 <= now + 1
                            and float(stamp) / 1000 >= float(value["started_at"]) - 1):
                        ready = {**peer, "at": float(stamp) / 1000}
                except (ValueError, TypeError, OverflowError):
                    pass
        with exclusive_file_lock(self.repository.path):
            current = self.repository.load("compaction_scope")
            if current is None:
                raise OSError("compaction scope is unreadable")
            # A scan cannot overwrite a newer launch or a concurrent retarget.
            fields = ("epoch", "session_id", "messaging_socket_path", "identity_since", "identity_ready", "request_observed", "handoff_pending")
            if any(current.get(key) != value.get(key) for key in fields):
                return current
            switched = bool(session_id and session_id != current.get("session_id"))
            if switched:
                current.update({"session_id": session_id, "path": "", "seeded": False,
                                "request_observed": False, "identity_since": now})
            pending = record.get("handoff_pending") is True
            changed = (current.get("identity_ready") != ready or bool(current.get("handoff_pending")) != pending
                       or (record.get("messagingSocketPath") and current.get("delivery_socket_path") != record["messagingSocketPath"]))
            current["handoff_pending"] = pending
            if record.get("messagingSocketPath"):
                current["delivery_socket_path"] = str(record["messagingSocketPath"])
            if ready:
                current["identity_ready"] = ready
            else:
                current.pop("identity_ready", None)
            if switched or changed:
                self.repository.save(current, "compaction_scope")
        if switched:
            self.log("INFO", f"runtime_compaction_scope_bound runtime=claude session={session_id} via=socket")
        if ready and changed:
            self.log("INFO", f"runtime_compaction_scope identity=ready reason=published_idle runtime=claude session={session_id}")
        return current

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
            # Proof belongs to one conversation: path and seeding start over,
            # and the new identity gets its own grace window.
            value.pop("identity_ready", None)
            value.update({"session_id": session_id, "path": "", "seeded": False,
                          "request_observed": False, "identity_since": time.time()})
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

    def _admission_state(self) -> dict[str, Any] | None:
        # Never called while the admission lock is held; see _update.
        with self.control.ports.lock():
            return self.control.ports.load()

    def _active_state(self, scope: CompactionScope | None) -> dict[str, Any]:
        state = self._admission_state() if scope else None
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

    def observe_native_model(self, scope: CompactionScope | None, request_model: Any, native_model: Any) -> None:
        """Remember that a main-session request for an alias was answered by another model name.

        The relay calls this with message_start.message.model. Only the bound
        launch epoch and session accept it; it never affects the response.
        """

        try:
            key = request_model_key(request_model)[:NATIVE_MODEL_NAME_CHARS]
            native = str(native_model or "").strip()[:NATIVE_MODEL_NAME_CHARS].casefold()
            if scope is None or not key or not native or native == key:
                return
            seen = (scope.epoch, scope.session_id, key, native)
            if seen in self.native_seen:
                return
            with exclusive_file_lock(self.repository.path):
                value = self.repository.load("compaction_scope")
                if (value is None or value.get("epoch") != scope.epoch
                        or value.get("session_id") != scope.session_id):
                    return
                stored = value.get("native_models") if isinstance(value.get("native_models"), dict) else {}
                names = [str(name) for name in stored.get(key) or [] if isinstance(name, str)]
                if native not in names:
                    names = [*names, native][-NATIVE_MODEL_NAMES_LIMIT:]
                    stored = {k: v for k, v in stored.items() if k != key}
                    stored = dict(list(stored.items())[-(NATIVE_MODEL_ALIASES_LIMIT - 1):])
                    stored[key] = names
                    value["native_models"] = stored
                    self.repository.save(value, "compaction_scope")
            self.native_seen.add(seen)
            self.log("INFO", f"runtime_compaction_native_model request_model={key} native_model={native}")
        except (OSError, ValueError, TypeError) as exc:
            # An observation must never fail the response it describes.
            self.log("WARN", f"runtime_compaction_native_model_failed error={type(exc).__name__}")

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
        self._update(scope.epoch, expected_session=scope.session_id, request_observed=True)
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
        self.control.observe_request(body, overflow.message_tokens, limit, scope=scope, recovery_required=True)

    def _admission_paused(self) -> bool:
        """Strict gate for router-side append: paused until shrink is verified."""

        try:
            scope = self.scope()
            if scope is None:
                return self._identity_pending()
            value = self._update("")
            if value.get("handoff_pending") or self._request_identity_pending(value):
                return True
            if self.seeded != f"{scope.epoch}:{scope.session_id}":
                self._seed(scope)
            self._observe_safeguard(scope)
        except (OSError, ValueError, TypeError) as exc:
            self.log("WARN", f"runtime_compaction_scope admission=paused reason=state_unavailable error={type(exc).__name__}")
            return True
        return self.control.external_admission_paused()

    def _observe_safeguard(self, scope, force=False):
        now = time.monotonic()
        if not force and now - self.safeguard_checked < 2:
            return
        self.safeguard_checked = now
        provider, config = self.config()
        value = self._update(str())
        model = str(config.get('current_model') or str()).strip()
        enabled = (provider == 'cielairouter' and scope.runtime == 'claude'
                   and config.get('compact_safeguard') is not False
                   and not config.get(REMOTE_BRIDGE_CONFIG_MARKER))
        limit = self.builder().budget.context_limit(provider, config) if enabled else 0
        since = max(float(value.get('started_at') or 0), float(value.get('identity_since') or 0))
        extra = set()
        observed = value.get('native_models') if value.get('epoch') == scope.epoch else None
        if enabled and isinstance(observed, dict) and value.get('session_id') == scope.session_id:
            names = model_names(model)
            for key, natives in observed.items():
                if key in names and isinstance(natives, list):
                    extra.update(name for name in natives if isinstance(name, str))
        sample = latest_native_model_usage(self.transcript_path(), scope, since, model, extra) if enabled else None
        def validate():
            current_provider, current = self.config()
            return (current_provider == provider
                    and str(current.get('current_model') or '').strip() == model
                    and current.get('compact_safeguard') == config.get('compact_safeguard')
                    and not current.get(REMOTE_BRIDGE_CONFIG_MARKER)
                    and (not enabled or self.builder().budget.context_limit(current_provider, current) == limit))
        self.control.observe_proactive(scope, sample, _positive(limit), model.casefold(), validate=validate)

    def _request_identity_pending(self, value: dict[str, Any]) -> bool:
        """Hold external input until this launch proves its routed session.

        Claude publishes a session id from the socket before it necessarily
        completes the first resumed turn. The first published id can be
        replaced, so a socket-only identity is provisional: injecting an event
        against it can observe the wrong compaction scope and race the actual
        request past a full context window (Celly, 2026-10-22 11:22/11:35).
        Claude's exact published idle record establishes readiness without a
        user turn. The first routed request is independent proof. Older clients
        without a readiness record retain the compatibility deadline after
        ``REQUEST_IDENTITY_GRACE_SECONDS`` from this identity binding.
        """

        if (value.get("runtime") != "claude"
                or not value.get("messaging_socket_path")
                or value.get("request_observed") is True
                or value.get("identity_ready")):
            return False
        pid = _positive(value.get("launcher_pid"))
        running = bool(pid and pid_is_running(pid))
        held = running and not _identity_expired(value, time.time())
        marker = f"request:{value.get('epoch')}:{value.get('session_id')}:{held}"
        if self.pending_logged != marker:
            self.pending_logged = marker
            reason = "request_identity_pending" if held else (
                "request_identity_expired" if running else "request_identity_unavailable")
            self.log("INFO" if held else "WARN", "runtime_compaction_scope "
                     f"admission={'paused' if held else 'ungated'} "
                     f"reason={reason} runtime=claude")
        return held

    def _identity_pending(self) -> bool:
        """A live supported launch whose exact native identity is not yet known."""

        value = self._update("")
        if value.get("runtime") not in {"claude", "codex"} or not value.get("epoch"):
            return False
        if value.get("handoff_pending"):
            return True
        pid = _positive(value.get("launcher_pid"))
        sources = bool(value.get("messaging_socket_path")) or value.get("request_identity") is not False
        running = bool(pid and pid_is_running(pid))
        held = bool(sources and running and not _identity_expired(value, time.time()))
        marker = f"{value['epoch']}:{held}"
        if self.pending_logged != marker:
            self.pending_logged = marker
            reason = "identity_pending" if held else (
                "identity_expired" if sources and running else "identity_unavailable")
            self.log("INFO" if held else "WARN", f"runtime_compaction_scope admission={'paused' if held else 'ungated'} "
                     f"reason={reason} runtime={value['runtime']}")
        return held

    def external_admission_paused(self) -> bool:
        """Gate active compact recovery, with distinct success and failure release.

        A checkpoint plus real shrink verifies success. Terminal failure or
        confirmation expiry releases only the compact gate, retaining failed
        status; new external input may trigger the native client's compact.
        Unproven launch identity and unreadable state remain separate holds.
        """
        return self.gate_status()["paused"]

    def gate_status(self) -> dict[str, Any]:
        from .sse_gate import enabled
        try:
            mode = enabled(self.gate_config())
            reason = self._raw_admission_reason()
        except (OSError, ValueError, TypeError):
            mode, reason = True, "state_unavailable"
        bypass = not mode and reason in {"context_recovery", "compaction_checkpoint_pending",
                                         "compaction_shrink_pending", "compaction_retry_wait"}
        paused = bool(reason and not bypass)
        value = {"enabled": mode, "paused": paused, "state": "held" if paused else "bypassed" if bypass else "open",
                 "reason": reason, "updated_at": time.time(), "observer_pid": os.getpid()}
        # Display evidence only: a failed status write never changes admission.
        try:
            previous = self.gate_snapshot.load("sse_gate_status") or {}
            changed = any(previous.get(key) != value[key] for key in ("enabled", "paused", "state", "reason"))
            expired = value["updated_at"] - float(previous.get("updated_at") or 0) >= 5
            if changed or expired:
                self.gate_snapshot.save(value, "sse_gate_status")
        except (OSError, ValueError, TypeError):
            pass
        return value

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

    def admission_reason(self) -> str:
        state = self.gate_status()
        return state["reason"] if state["paused"] else ""

    def _raw_admission_reason(self) -> str:
        try:
            if not self._admission_paused():
                return ""
            value = self._update("")
            if value.get("handoff_pending"):
                return "native_handoff_pending"
            if self.scope() is None or self._request_identity_pending(value):
                return "identity_pending"
            return self.control.admission_reason() or "context_recovery"
        except (OSError, ValueError, TypeError):
            return "state_unavailable"

    def context_pause_reason(self, body: dict[str, Any]) -> str:
        if self._compact_kind(body):
            return "native_compaction"
        return self.admission_reason()

    def recovery_status(self) -> dict[str, Any]:
        try:
            scope = self.scope()
            state = self._admission_state() if scope is not None else None
            if not isinstance(state, dict) or not state:
                reason = self.control.admission_reason()
                return {"phase": "unknown", "failure": reason} if reason else {}
            if state.get("unresolved_scope") is True:
                return {"phase": "unknown", "failure": "state_unavailable"}
            if state.get("scope") != scope.payload():
                if state.get("phase") in {None, "verified"}:
                    return {}
                # Diagnostic only: a state an earlier session scope left
                # unfinished never gates this scope and never opens it.
                stored = state.get("scope") if isinstance(state.get("scope"), dict) else {}
                return {"phase": "stale_scope", "failure":
                        f"earlier_{stored.get('runtime') or '-'}_scope_{state.get('phase')}_not_gating",
                        "failure_at": state.get("failure_at") or state.get("requested_at"),
                        "checkpoint_observed_at": state.get("checkpoint_observed_at"),
                        "deadline": state.get("deadline")}
            return {key: state.get(key) for key in (
                "phase", "failure", "failure_at", "checkpoint_observed_at", "deadline", "admission_released_at")}
        except (OSError, ValueError, TypeError):
            return {"phase": "unknown", "failure": "state_unavailable"}

    def compact_dispatch(self) -> dict[str, Any]:
        """Exact confirmed runtime dispatch for a continuation observer."""
        scope = self.scope()
        state = self._admission_state() if scope is not None else None
        if not isinstance(state, dict) or state.get("scope") != scope.payload():
            return {}
        dispatch = state.get("dispatch")
        if not isinstance(dispatch, dict):
            return {}
        return {**dispatch, "verified_at": state.get("verified_at")}

    def command_ready(self, request: dict[str, Any]) -> bool:
        if request.get('automatic') is True:
            scope = self.scope()
            if scope is not None:
                self._observe_safeguard(scope, force=True)
        return self.control.command_ready(request)

    def command_submitted(self, request: dict[str, Any]) -> None:
        self.control.command_submitted(request)

    def command_failed(self, request: dict[str, Any], reason: str, *, uncertain: bool = False) -> None:
        self.control.command_failed(request, reason, uncertain=uncertain)
