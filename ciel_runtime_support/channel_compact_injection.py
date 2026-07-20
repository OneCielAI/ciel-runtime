"""Application service for injecting queued channel compaction commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelCompactRequestPorts:
    read: Callable[[], dict[str, Any] | None]
    clear: Callable[[str | None], None]


@dataclass(frozen=True, slots=True)
class ChannelCompactRuntimePorts:
    active_tool_call: Callable[[], bool]
    active_turn: Callable[[], bool]
    enter_bytes: Callable[[bytes | None], bytes]
    write_prompt: Callable[..., None]
    enter_label: Callable[[bytes], str]


@dataclass(frozen=True, slots=True)
class ChannelCompactInjectionService:
    request: ChannelCompactRequestPorts
    runtime: ChannelCompactRuntimePorts
    log: Callable[[str, str], None]

    def inject(
        self,
        writer: Any,
        enter_bytes: bytes | None = None,
        *,
        log_defer: bool = True,
        submit_retry_count: int = 1,
        confirm_submit: bool = False,
        bracketed_paste: bool = False,
        submit_delay_seconds: float | None = None,
    ) -> str:
        request = self.request.read()
        if not request:
            return "none"
        request_id = str(request.get("id") or "")
        if self.runtime.active_tool_call():
            self._log_deferred(request_id, "active_tool_call", log_defer)
            return "deferred"
        if self.runtime.active_turn():
            self._log_deferred(request_id, "active_turn", log_defer)
            return "deferred"

        command = str(request.get("command") or "/compact").strip() or "/compact"
        if command != "/compact":
            command = "/compact"
        submit_bytes = self.runtime.enter_bytes(enter_bytes)
        self.runtime.write_prompt(
            writer,
            command,
            submit_bytes,
            submit_retry_count=submit_retry_count,
            confirm_submit=confirm_submit,
            bracketed_paste=bracketed_paste,
            submit_delay_seconds=submit_delay_seconds,
        )
        self.request.clear(request_id or None)
        self.log(
            "INFO",
            f"channel_compact_request_injected id={request_id or '-'} "
            f"enter={self.runtime.enter_label(submit_bytes)}",
        )
        return "injected"

    def _log_deferred(
        self, request_id: str, reason: str, enabled: bool
    ) -> None:
        if enabled:
            self.log(
                "INFO",
                f"channel_compact_request_deferred id={request_id or '-'} "
                f"reason={reason}",
            )
