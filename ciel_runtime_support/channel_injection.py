"""Runtime-neutral contracts for injecting channel prompts into interactive CLIs.

This module owns runtime input policy and terminal delivery orchestration. It
does not know about SSE, MCP, cursors, transcripts, or subprocess lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class InputTransport(Protocol):
    """Input port implemented by PTY and Windows Console adapters."""

    def write(self, data: bytes) -> None: ...

    def wait_until_input_consumed(self, timeout_seconds: float = 2.0) -> bool: ...


@dataclass(frozen=True)
class RuntimeInjectionPolicy:
    """Runtime interaction semantics independent of the host platform."""

    runtime: str
    clear_input: bytes
    submit_input: bytes
    submit_delay_seconds: float
    submit_attempts: int = 1
    confirm_submission: bool = False
    bracketed_paste: bool = False
    input_drain_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.runtime.strip():
            raise ValueError("runtime is required")
        if not self.submit_input:
            raise ValueError("submit_input is required")
        if self.submit_delay_seconds < 0:
            raise ValueError("submit_delay_seconds cannot be negative")
        if not 1 <= self.submit_attempts <= 8:
            raise ValueError("submit_attempts must be between 1 and 8")


@dataclass(frozen=True)
class PromptInjection:
    prompt: str
    policy: RuntimeInjectionPolicy


class ChannelPromptInjector:
    """Coordinates an input transport using an explicit runtime policy."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], None],
        retry_delay_seconds: Callable[[], float],
        snapshot: Callable[[], str | None],
        log: Callable[[str, str], None],
    ) -> None:
        self._sleep = sleep
        self._retry_delay_seconds = retry_delay_seconds
        self._snapshot = snapshot
        self._log = log

    def inject(self, transport: InputTransport, request: PromptInjection) -> None:
        policy = request.policy
        prompt = request.prompt.encode("utf-8", errors="replace")
        payload = policy.clear_input + prompt
        if policy.bracketed_paste:
            payload = policy.clear_input + b"\x1b[200~" + prompt + b"\x1b[201~"
        transport.write(payload)

        if not transport.wait_until_input_consumed(policy.input_drain_timeout_seconds):
            self._log("WARN", "channel_input_drain_timeout")
        if policy.submit_delay_seconds:
            self._sleep(policy.submit_delay_seconds)

        before = self._snapshot() if policy.confirm_submission and policy.submit_attempts > 1 else None
        for attempt in range(policy.submit_attempts):
            transport.write(policy.submit_input)
            if attempt >= policy.submit_attempts - 1 or not before:
                break
            retry_delay = self._retry_delay_seconds()
            if retry_delay:
                self._sleep(retry_delay)
            after = self._snapshot()
            if after and after != before:
                self._log("INFO", f"channel_stdin_proxy_submit_confirmed attempt={attempt + 1}")
                break


class CallableInputTransport:
    """Compatibility adapter for existing descriptors and writer objects."""

    def __init__(self, target: object, write: Callable[[object, bytes], None]) -> None:
        self._target = target
        self._write = write

    def write(self, data: bytes) -> None:
        self._write(self._target, data)

    def wait_until_input_consumed(self, timeout_seconds: float = 2.0) -> bool:
        wait = getattr(self._target, "wait_until_input_consumed", None)
        return bool(wait(timeout_seconds)) if callable(wait) else True


__all__ = [
    "CallableInputTransport",
    "ChannelPromptInjector",
    "InputTransport",
    "PromptInjection",
    "RuntimeInjectionPolicy",
]
