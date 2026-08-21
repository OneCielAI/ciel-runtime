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

    def normalize_prompt(self, prompt: str) -> str: ...

    def pending_input_events(self) -> int | None: ...

    def input_snapshot(self) -> str | None: ...

    def wait_until_prompt_ready(
        self,
        previous_snapshot: str | None,
        timeout_seconds: float = 2.0,
    ) -> bool | None: ...


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
        prompt_ready_wait = bool(
            getattr(transport, "supports_prompt_ready_wait", False)
        )
        before_prompt = (
            self._submission_snapshot(transport)
            if prompt_ready_wait
            and policy.confirm_submission
            and policy.submit_attempts > 1
            else None
        )
        normalize = getattr(transport, "normalize_prompt", None)
        prompt_text = (
            str(normalize(request.prompt)) if callable(normalize) else request.prompt
        )
        prompt = prompt_text.encode("utf-8", errors="replace")
        payload = prompt
        if policy.bracketed_paste:
            payload = b"\x1b[200~" + prompt + b"\x1b[201~"

        if bool(getattr(transport, "separate_input_stages", False)):
            self._write_stage(transport, "clear", policy.clear_input, policy)
            self._write_stage(transport, "body", payload, policy)
            if policy.confirm_submission and not self._body_prefix_present(
                transport, prompt_text
            ):
                self._log(
                    "WARN",
                    "channel_input_body_verify result=missing action=rewrite-full-prompt",
                )
                self._write_stage(transport, "rewrite-clear", policy.clear_input, policy)
                self._write_stage(transport, "rewrite-body", payload, policy)
                self._body_prefix_present(transport, prompt_text)
        else:
            transport.write(policy.clear_input + payload)
            if not transport.wait_until_input_consumed(
                policy.input_drain_timeout_seconds
            ):
                self._log("WARN", "channel_input_drain_timeout")

        if prompt_ready_wait:
            self._wait_until_prompt_ready(transport, before_prompt, policy)

        if policy.submit_delay_seconds:
            self._sleep(policy.submit_delay_seconds)

        before = (
            self._submission_snapshot(transport)
            if policy.confirm_submission and policy.submit_attempts > 1
            else None
        )
        for attempt in range(policy.submit_attempts):
            if bool(getattr(transport, "separate_input_stages", False)):
                self._write_stage(
                    transport,
                    f"submit-{attempt + 1}",
                    policy.submit_input,
                    policy,
                )
            else:
                transport.write(policy.submit_input)
            if attempt >= policy.submit_attempts - 1 or not before:
                break
            retry_delay = self._retry_delay_seconds()
            if retry_delay:
                self._sleep(retry_delay)
            after = self._submission_snapshot(transport)
            if after and after != before:
                self._log("INFO", f"channel_stdin_proxy_submit_confirmed attempt={attempt + 1}")
                break

    def _submission_snapshot(self, transport: InputTransport) -> str | None:
        """Use the host snapshot when available, then the transport's own view."""

        captured = self._snapshot()
        if captured is not None:
            return captured
        snapshot = getattr(transport, "input_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def _wait_until_prompt_ready(
        self,
        transport: InputTransport,
        previous_snapshot: str | None,
        policy: RuntimeInjectionPolicy,
    ) -> None:
        if not policy.confirm_submission or policy.submit_attempts <= 1:
            return
        wait = getattr(transport, "wait_until_prompt_ready", None)
        if not callable(wait):
            return
        ready = wait(previous_snapshot, policy.input_drain_timeout_seconds)
        if ready is not None:
            self._log(
                "INFO" if ready else "WARN",
                f"channel_input_prompt_ready result={'observed' if ready else 'timeout'}",
            )

    def _write_stage(
        self,
        transport: InputTransport,
        stage: str,
        payload: bytes,
        policy: RuntimeInjectionPolicy,
    ) -> None:
        before = self._pending_events(transport)
        transport.write(payload)
        drained = transport.wait_until_input_consumed(
            policy.input_drain_timeout_seconds
        )
        after = self._pending_events(transport)
        self._log(
            "INFO" if drained else "WARN",
            f"channel_input_stage stage={stage} bytes={len(payload)} "
            f"queue_before={before if before is not None else '-'} "
            f"queue_after={after if after is not None else '-'} drained={str(drained).lower()}",
        )

    def _body_prefix_present(self, transport: InputTransport, prompt: str) -> bool:
        snapshot = getattr(transport, "input_snapshot", None)
        supported = bool(
            getattr(transport, "supports_input_snapshot", callable(snapshot))
        )
        captured = snapshot() if supported and callable(snapshot) else None
        if captured is None:
            self._log("INFO", "channel_input_body_verify result=unavailable")
            return True
        prefix = " ".join(str(prompt or "").split())[:48]
        visible = " ".join(str(captured).split())
        present = not prefix or prefix in visible
        self._log(
            "INFO" if present else "WARN",
            f"channel_input_body_verify result={'present' if present else 'missing'} "
            f"prefix_chars={len(prefix)}",
        )
        return present

    @staticmethod
    def _pending_events(transport: InputTransport) -> int | None:
        pending = getattr(transport, "pending_input_events", None)
        if not callable(pending):
            return None
        try:
            value = pending()
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None


class CallableInputTransport:
    """Compatibility adapter for existing descriptors and writer objects."""

    def __init__(self, target: object, write: Callable[[object, bytes], None]) -> None:
        self._target = target
        self._write = write
        self.separate_input_stages = bool(
            getattr(target, "separate_input_stages", False)
        )
        self.supports_input_snapshot = callable(
            getattr(target, "input_snapshot", None)
        )
        self.supports_prompt_ready_wait = callable(
            getattr(target, "wait_until_prompt_ready", None)
        )

    def write(self, data: bytes) -> None:
        self._write(self._target, data)

    def wait_until_input_consumed(self, timeout_seconds: float = 2.0) -> bool:
        wait = getattr(self._target, "wait_until_input_consumed", None)
        return bool(wait(timeout_seconds)) if callable(wait) else True

    def normalize_prompt(self, prompt: str) -> str:
        normalize = getattr(self._target, "normalize_prompt", None)
        return str(normalize(prompt)) if callable(normalize) else prompt

    def pending_input_events(self) -> int | None:
        pending = getattr(self._target, "pending_input_events", None)
        if not callable(pending):
            return None
        try:
            return int(pending())
        except (TypeError, ValueError):
            return None

    def input_snapshot(self) -> str | None:
        snapshot = getattr(self._target, "input_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def wait_until_prompt_ready(
        self,
        previous_snapshot: str | None,
        timeout_seconds: float = 2.0,
    ) -> bool | None:
        wait = getattr(self._target, "wait_until_prompt_ready", None)
        if not callable(wait):
            return None
        return bool(wait(previous_snapshot, timeout_seconds))


__all__ = [
    "CallableInputTransport",
    "ChannelPromptInjector",
    "InputTransport",
    "PromptInjection",
    "RuntimeInjectionPolicy",
]
