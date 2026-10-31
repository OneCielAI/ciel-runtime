"""Deduplicate exact repeated side-effect tool calls within a bounded window."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from threading import Lock, RLock
from typing import Any, Callable


# Explicit failure text seen in tool results that were not flagged is_error.
_FAILURE_MARKERS = (
    "called with no content",
    "missing required",
    "invalid tool input",
    "tool call aborted",
    "tool call failed",
    "apply_patch failed",
    "patch failed",
)
# A result that reports a still-running, backgrounded, timed-out or aborted
# command is not a terminal success, whatever else it contains.
_NON_TERMINAL_MARKERS = (
    "process running with session id",  # Codex exec_command: process still alive
    "running in background with id",  # Claude Code Bash run_in_background
    "moved to the background",  # Claude Code Bash auto-backgrounded command
    "still running",
    "timed out",  # Codex: "command timed out after N milliseconds"
    "aborted by user",
)
_NON_TERMINAL_STATUS = re.compile(
    r"\bstatus[\"']?\s*[:=]\s*[\"']?(?:pending|running|in[_ -]progress|queued|started)\b"
)
# Any reported exit code, in the forms Codex, Claude Code and JSON metadata use:
# "Process exited with code N", "Exit code: N", "Exit code N", "exit status N",
# "exit_code": N.
_EXIT_CODE = re.compile(
    r"(?:process exited with code|exited with code|exit code|exit status|exit_code)"
    r"[\"']?\s*[:=]?\s*(-?\d+)\b"
)
# Success text of the known file-mutation tools.  Verified against the strings
# in Claude Code 2.1.316 (Write/Edit) and the installed Codex binary (apply_patch).
_MUTATION_SUCCESS_MARKERS: dict[str, tuple[str, ...]] = {
    "write": ("file created successfully at", "has been updated successfully"),
    "edit": ("has been updated successfully",),
    "apply_patch": ("success. updated the following files",),
}


@dataclass(frozen=True, slots=True)
class ToolSideEffectDedupePolicy:
    side_effect_suffixes: frozenset[str]
    ttl_seconds: float = 600.0
    # Only known file-mutation tools are guarded by default.  Arbitrary
    # shell/exec tools (Bash, shell_command, exec_command, ...) are not: running
    # the same command repeatedly with the same result is legitimate (health
    # polls, status probes), and the command string is never parsed to guess
    # whether it writes.  A caller may opt other tools in explicitly; their
    # results still need positive completion evidence (an explicit zero exit
    # code) to count.
    repeated_execution_suffixes: frozenset[str] = frozenset({"write", "edit", "apply_patch"})
    # One completed call can legitimately be retried after a client/process
    # interruption because the resumed request carries no explicit new user
    # message.  Only stop the call after the same successful result has already
    # been observed twice consecutively.  This still bounds genuine model loops
    # without treating a single historical completion as a permanent lock.
    completed_repeat_limit: int = 2


class ToolSideEffectDedupeRepository:
    def __init__(self, recent: dict[str, float], lock: Lock | RLock) -> None:
        self.recent = recent
        self._lock = lock

    def previous_or_record(self, key: str, now: float, ttl_seconds: float) -> float | None:
        with self._lock:
            expired = [name for name, seen_at in self.recent.items() if now - seen_at > ttl_seconds]
            for name in expired:
                self.recent.pop(name, None)
            previous = self.recent.get(key)
            if previous is None or now - previous > ttl_seconds:
                self.recent[key] = now
                return None
            return previous


@dataclass(frozen=True, slots=True)
class ToolSideEffectDedupePorts:
    now: Callable[[], float]
    audit: Callable[[str, dict[str, Any]], None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ToolSideEffectDedupeService:
    policy: ToolSideEffectDedupePolicy
    repository: ToolSideEffectDedupeRepository
    ports: ToolSideEffectDedupePorts

    def key(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        if not isinstance(tool_name, str) or not tool_name:
            return None
        normalized_name = tool_name.strip()
        tool_leaf = normalized_name.rsplit("__", 1)[-1].strip().lower()
        if tool_leaf not in self.policy.side_effect_suffixes:
            return None
        try:
            payload = json.dumps(
                tool_input or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
            )
        except Exception:
            payload = repr(tool_input)
        digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
        return f"{normalized_name}:{digest}"

    @staticmethod
    def _normalized_payload(value: Any) -> Any:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [ToolSideEffectDedupeService._normalized_payload(item) for item in value]
        if isinstance(value, dict):
            return {
                key: ToolSideEffectDedupeService._normalized_payload(item)
                for key, item in value.items()
            }
        return value

    @classmethod
    def _signature(cls, tool_name: str, tool_input: Any) -> str:
        payload = json.dumps(
            cls._normalized_payload(tool_input if isinstance(tool_input, dict) else {}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return f"{tool_name}:{payload}"

    def completed_repeat_count(
        self, source_body: dict[str, Any] | None, tool_name: str, tool_input: dict[str, Any]
    ) -> int:
        if not isinstance(source_body, dict):
            return 0
        tool_leaf = tool_name.rsplit("__", 1)[-1].strip().lower()
        if tool_leaf not in self.policy.repeated_execution_suffixes:
            return 0
        # Missing arguments are not an execution, much less a completed one.
        # Some OpenAI-compatible providers emit `{}` for free-form tools such as
        # apply_patch.  The client then rejects the call, but older transcripts
        # did not always mark that tool_result with is_error=true.  Counting it
        # here made Ciel stop the model's later recovery attempt as a loop.
        if not isinstance(tool_input, dict) or not tool_input:
            return 0
        target = self._signature(tool_name, tool_input)
        tool_uses: dict[str, tuple[str, str]] = {}
        completed: list[str | None] = []
        for message in source_body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            if message.get("role") == "assistant":
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = str(block.get("id") or "")
                    if tool_id:
                        name = str(block.get("name") or "")
                        tool_uses[tool_id] = (
                            self._signature(name, block.get("input")),
                            name.rsplit("__", 1)[-1].strip().lower(),
                        )
                continue
            if message.get("role") != "user":
                continue
            results = [
                block
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            runtime_control = bool(message.get("ciel_runtime_control"))
            has_new_intent = not runtime_control and (
                (isinstance(content, str) and bool(content.strip()))
                or any(
                    not isinstance(block, dict) or block.get("type") != "tool_result"
                    for block in blocks
                )
            )
            if has_new_intent:
                completed.clear()
            for block in results:
                signature, call_leaf = tool_uses.get(
                    str(block.get("tool_use_id") or ""), (None, "")
                )
                completed.append(
                    signature
                    if self._tool_result_completed_successfully(block, call_leaf)
                    else None
                )
        count = 0
        for signature in reversed(completed):
            if signature != target:
                break
            count += 1
        return count

    @staticmethod
    def _tool_result_completed_successfully(block: dict[str, Any], tool_leaf: str = "") -> bool:
        """True only with positive evidence of a terminal success.

        Unknown results (no exit code, no known success text) are not counted,
        nor are pending/running/backgrounded results or any nonzero exit code.
        """
        if block.get("is_error") or block.get("isError"):
            return False
        content = block.get("content")
        if isinstance(content, list):
            text = " ".join(
                str(item.get("text") or item.get("content") or "")
                if isinstance(item, dict)
                else str(item)
                for item in content
            )
        elif isinstance(content, dict):
            text = str(content.get("text") or content.get("content") or content)
        else:
            text = str(content or "")
        normalized = " ".join(text.lower().split())
        if not normalized:
            return False
        if any(marker in normalized for marker in _FAILURE_MARKERS):
            return False
        if any(marker in normalized for marker in _NON_TERMINAL_MARKERS):
            return False
        if _NON_TERMINAL_STATUS.search(normalized):
            return False
        exit_codes = [int(match.group(1)) for match in _EXIT_CODE.finditer(normalized)]
        if any(code != 0 for code in exit_codes):
            return False
        if exit_codes:
            return True
        return any(
            marker in normalized for marker in _MUTATION_SUCCESS_MARKERS.get(tool_leaf, ())
        )

    def should_drop(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        raw_name: str = "",
        source_body: dict[str, Any] | None = None,
    ) -> bool:
        key = self.key(tool_name, tool_input)
        if key:
            now = self.ports.now()
            previous = self.repository.previous_or_record(key, now, self.policy.ttl_seconds)
            if previous is not None:
                age = now - previous
                self.ports.audit(
                    "dropped_duplicate_side_effect_tool_call",
                    {
                        "raw_name": raw_name or tool_name,
                        "matched_name": tool_name,
                        "emitted_input": tool_input,
                        "age_seconds": round(age, 3),
                        "ttl_seconds": self.policy.ttl_seconds,
                    },
                )
                self.ports.log(
                    "WARN",
                    f"dropped duplicate side-effect tool call raw_name={raw_name or tool_name!r} "
                    f"matched_name={tool_name!r} age={age:.1f}s",
                )
                return True
        repeat_count = self.completed_repeat_count(source_body, tool_name, tool_input)
        if repeat_count < max(1, self.policy.completed_repeat_limit):
            return False
        self.ports.audit(
            "dropped_repeated_completed_tool_call",
            {
                "raw_name": raw_name or tool_name,
                "matched_name": tool_name,
                "emitted_input": tool_input,
                "completed_repeats": repeat_count,
            },
        )
        self.ports.log(
            "WARN",
            f"dropped repeated completed tool call raw_name={raw_name or tool_name!r} "
            f"matched_name={tool_name!r} completed_repeats={repeat_count}",
        )
        return True


__all__ = [
    "ToolSideEffectDedupePolicy",
    "ToolSideEffectDedupePorts",
    "ToolSideEffectDedupeRepository",
    "ToolSideEffectDedupeService",
]
