"""Conservative, attachment-local evidence for automatic compact continuation.

No transcript text is persisted. A human task must have been observed here;
summary text, peer messages and a quiet terminal cannot establish authority.
Terminal snapshots have no cancel generation: observed manual/unknown input
withdraws authority for the current task even after a later idle snapshot.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime
from typing import Any

from .agent_turn_events import typed_by_person
from .runtime_compaction_control import CompactionScope


COMPACT_MARKER = "Auto-compact continuation"
_NONPLAN_MODES = frozenset({"default", "acceptEdits", "bypassPermissions", "dontAsk", "auto"})


class CompactContinuationEvidence:
    def __init__(self) -> None:
        self.task_scope: CompactionScope | None = None
        self.task_open = False
        self.task_watermark = -1
        self.tools: set[str] = set()
        self.tools_unknown = False
        self.operator_input = False
        self.nonplan = False
        self.mode_scope: CompactionScope | None = None
        self.generation: int | None = None
        self.latest_terminal: dict[str, Any] = {}
        self.task_accepted_at = 0.0

    def terminal(self, snapshot: dict[str, Any]) -> None:
        self.latest_terminal = dict(snapshot)
        generation = snapshot.get("operator_generation")
        if self.task_open and isinstance(generation, int) and not isinstance(generation, bool):
            try:
                input_at = float(snapshot.get("operator_input_at") or 0)
            except (ValueError, TypeError, OverflowError):
                self.operator_input = True
                return
            if self.task_accepted_at > 0 and 0 <= input_at <= self.task_accepted_at:
                self.generation = generation
            elif generation != self.generation:
                self.operator_input = True
            return
        if self.task_open and (snapshot.get("manual_input") is not False
                               or snapshot.get("manual_input_known") is not True):
            self.operator_input = True

    def terminal_for_compact(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if self.generation is None or snapshot.get("operator_generation") != self.generation or self.operator_input:
            return snapshot
        # POSIX's historical manual_input flag means any input ever. A newer
        # accepted human record acknowledges its generation; only later input
        # invalidates this task. Stream-error recovery keeps its old gate.
        snapshot = dict(snapshot)
        snapshot["manual_input"] = False
        snapshot["ready"] = bool(snapshot.get("input_ready") and not snapshot.get("busy")
                                 and not snapshot.get("compaction") and not snapshot.get("pending"))
        return snapshot

    def observe(self, record: dict[str, Any], scope: CompactionScope | None, text: str, watermark: int) -> None:
        if scope is None or scope.runtime != "claude" or record.get("sessionId") != scope.session_id:
            return
        kind = record.get("type")
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if kind == "permission-mode":
            self.nonplan = record.get("permissionMode") in _NONPLAN_MODES
            self.mode_scope = scope
        elif kind == "user":
            results = [item for item in blocks if isinstance(item, dict) and item.get("type") == "tool_result"]
            if results:
                for item in results:
                    identity = item.get("tool_use_id")
                    if not isinstance(identity, str) or identity not in self.tools:
                        self.tools_unknown = True
                    else:
                        self.tools.discard(identity)
                return
            # Native compaction bookkeeping preserves existing authority but
            # must never create a new task or cancel the post-boundary waiter.
            if record.get("isCompactSummary"):
                return
            origin = record.get("origin")
            is_human_origin = isinstance(origin, dict) and origin.get("kind") == "human"
            if (record.get("isMeta") or record.get("isSynthetic") or record.get("isCompactSummary")
                    or (origin is not None and not is_human_origin) or record.get("toolUseResult") is not None
                    or not record.get("uuid") or not text.strip() or not typed_by_person(text)
                    or text.lstrip().startswith(("/", "<", "[Request interrupted by user"))
                    or COMPACT_MARKER in text or "계속-응답중단복구" in text):
                self.task_open = False
                return
            self.task_scope, self.task_open = scope, True
            self.task_watermark = watermark
            self.operator_input = False
            self.generation = None
            try:
                self.task_accepted_at = datetime.fromisoformat(str(record.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError, OverflowError):
                self.task_accepted_at = 0.0
            latest = self.latest_terminal
            generation = latest.get("operator_generation")
            if isinstance(generation, int) and not isinstance(generation, bool):
                try:
                    accepted_at = datetime.fromisoformat(str(record.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
                    input_at = float(latest.get("operator_input_at") or 0)
                    if 0 < accepted_at and input_at <= accepted_at:
                        self.generation = generation
                    else:
                        self.operator_input = True
                except (ValueError, TypeError, OverflowError):
                    self.operator_input = True
            # A new prompt never proves an earlier tool settled.
        elif kind == "assistant":
            for item in blocks:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    name = str(item.get("name") or "").lower()
                    inputs = item.get("input") if isinstance(item.get("input"), dict) else {}
                    if (name in {"compact", "compact_session", "mcp__ciel-runtime-router__compact_session"}
                            or str(inputs.get("command") or "").lstrip().startswith("/compact")):
                        self.task_open = False
                    identity = item.get("id")
                    if isinstance(identity, str) and identity and len(self.tools) < 128:
                        self.tools.add(identity)
                    else:
                        self.tools_unknown = True
            if record.get("isApiErrorMessage") or message.get("stop_reason") in {
                "end_turn", "max_tokens", "stop_sequence", "refusal",
            }:
                self.task_open = False
        elif kind == "system" and record.get("subtype") in {"turn_duration", "local_command"}:
            self.task_open = False
        elif kind == "queue-operation" and record.get("operation") == "enqueue":
            self.task_open = False

    def candidate(self, record: dict[str, Any], scope: CompactionScope | None,
                  bound_epoch: str, now: float, watermark: int) -> tuple[dict[str, Any], str]:
        identity = record.get("uuid")
        key = "compact:" + sha256(f"{scope!r}:{identity}".encode()).hexdigest()
        pending = {"kind": "compact_resume", "error_uuid": key, "message_id": "",
                   "checkpoint_uuid": identity, "scope": scope,
                   "session_id": str(record.get("sessionId") or ""),
                   "seen_at": now, "input_watermark": watermark, "continues_chain": False}
        metadata = record.get("compactMetadata")
        if not isinstance(metadata, dict) or metadata.get("trigger") != "auto":
            self.task_open = False
            return pending, "compact_not_auto"
        pre, post = metadata.get("preTokens"), metadata.get("postTokens")
        if (not isinstance(pre, int) or isinstance(pre, bool) or not isinstance(post, int)
                or isinstance(post, bool) or not 0 < post < pre):
            return pending, "compact_shrink_unknown"
        if (scope is None or scope.runtime != "claude" or not scope.session_id or not scope.epoch
                or not bound_epoch or scope.epoch != bound_epoch
                or record.get("sessionId") != scope.session_id
                or not isinstance(identity, str) or not identity):
            return pending, "scope"
        if not self.task_open or self.task_scope != scope:
            return pending, "compact_no_observed_task"
        if watermark < 0 or self.task_watermark < 0 or watermark != self.task_watermark:
            return pending, "newer_input"
        if not self.nonplan or self.mode_scope != scope:
            return pending, "compact_permission_mode_unknown_or_plan"
        if self.tools or self.tools_unknown:
            return pending, "compact_tool_pending"
        if self.operator_input:
            return pending, "compact_operator_input"
        return pending, ""
