"""Interactive channel wake, transcript, and cursor-recovery bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import channel_injection, channel_llm_context
from .channel_cursor_recovery import (
    ChannelCursorRecoveryPolicy,
    ChannelCursorRecoveryPorts,
    ChannelCursorRecoveryService,
)
from .channel_compact_injection import (
    ChannelCompactInjectionService,
    ChannelCompactRequestPorts,
    ChannelCompactRuntimePorts,
)
from .channel_pending_injection import (
    ChannelInjectionIO,
    ChannelInjectionPolicy,
    ChannelInjectionPrompts,
    ChannelInjectionServices,
    ChannelInjectionState,
    ChannelInjectionWakeStore,
    inject_pending_channel_messages,
)
from .channel_replay_policy import ChannelReplaySafetyPolicy
from .channel_transcript import (
    ChannelWakeStateReader,
    ChannelWakeStateReaderPorts,
    ChannelWakeTranscriptServices,
    WakeStateEvidence,
)
from .channel_transcript_repository import ChannelTranscriptRepository
from .channel_wake_claim_repository import ChannelWakeClaimRepository
from .channel_wake_delivery_repository import ChannelWakeDeliveryRepository


@dataclass(frozen=True, slots=True)
class ChannelWakeClaimPorts:
    path: Path
    file_lock: Callable[[Path], Any]
    now: Callable[[], float]
    ttl_seconds: Callable[[], float]
    log: Callable[[str, str], None]
    delivery: ChannelWakeDeliveryRepository
    prompt_message_ids: Callable[[str], set[int]]
    prompt_reference: Callable[[str, int, list[str]], bool]


@dataclass(frozen=True, slots=True)
class ChannelWakeMessagePorts:
    content_to_text: Callable[[Any], str]
    read_messages: Callable[[int, int], list[dict[str, Any]]]
    superseded_ids: Callable[[list[dict[str, Any]]], set[int]]
    wake_request: Callable[[dict[str, Any]], bool]
    plan_mode_active: Callable[[dict[str, Any]], bool]
    delivery_mode: Callable[[], str]
    scan_limit: Callable[[], int]
    skip_reason: Callable[[dict[str, Any]], str]
    remove_wake_prompt: Callable[[dict[str, Any]], dict[str, Any]]
    format_batch_prompt: Callable[[list[dict[str, Any]]], str]


@dataclass(frozen=True, slots=True)
class ChannelWakeCursorPorts:
    lock: Callable[[], Any]
    read: Callable[[], int]
    write: Callable[[int], None]
    cache: Callable[[int], None]
    clamp_to_clear_floor: Callable[[int], int]


@dataclass(frozen=True, slots=True)
class ChannelWakeInputPorts:
    environment: dict[str, str]
    platform_enter_bytes: Callable[[], bytes]
    resolve_enter_bytes: Callable[[str | bytes | None, bytes], bytes]
    build_input_bytes: Callable[[str, bytes], bytes]
    find_executable: Callable[[str], str | None]
    run_process: Callable[..., Any]
    sleep: Callable[[float], None]
    retry_delay_seconds: Callable[[], float]
    submit_delay_seconds: Callable[[], float]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelTranscriptPorts:
    home: Path
    cache: dict[str, Any]
    scope: dict[str, Any]
    recovery_cache: dict[str, Any]
    now: Callable[[], float]
    read_tail: Callable[[Path], str]
    active_tool_call_from_text: Callable[..., bool]
    active_turn_from_text: Callable[..., bool]
    queued_age_from_text: Callable[..., float | None]
    wake_state_evidence_from_text: Callable[..., WakeStateEvidence]


@dataclass(frozen=True, slots=True)
class ChannelTranscriptPolicyPorts:
    queued_ids_from_text: Callable[[str, ChannelWakeTranscriptServices], set[int]]
    inflight_stale_seconds: Callable[[], float]
    latest_transcript: Callable[..., Path | None]
    claim_prompt: Callable[[int], str]
    prompt_references_message: Callable[..., bool]
    prompt_message_ids: Callable[[str], set[int]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelPendingStatePorts:
    active_tool_call: Callable[[], bool]
    active_turn: Callable[[], bool]
    recover_cursor: Callable[[int], int]
    pending_scan_limit: Callable[[], int]
    superseded_ids: Callable[[list[dict[str, Any]]], set[int]]
    message_is_web_chat: Callable[[dict[str, Any]], bool]
    message_skip_reason: Callable[[dict[str, Any]], str]
    event_identity_key: Callable[[dict[str, Any]], str]
    wake_state_for_message: Callable[..., str]
    queued_wake_is_stale: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ChannelPendingDeliveryPorts:
    format_llm_delivery: Callable[[list[dict[str, Any]]], str]
    format_visible_llm_delivery: Callable[[list[dict[str, Any]]], str]
    format_web_chat: Callable[[list[dict[str, Any]]], str]
    format_standard: Callable[[list[dict[str, Any]]], str]
    enter_label: Callable[[bytes], str]
    release_stale: Callable[[int, bool], None]
    mark_delivered: Callable[[int], None]
    record_prompts: Callable[[list[dict[str, Any]], str], None]
    rollback: Callable[[list[dict[str, Any]], list[int]], None]
    commit_cursor: Callable[[int | None], None]


@dataclass(frozen=True, slots=True)
class ChannelPendingIoPorts:
    inject_lock: Any
    read_messages: Callable[..., list[dict[str, Any]]]
    write_prompt: Callable[..., None]
    compact_read: Callable[[], dict[str, Any] | None]
    compact_clear: Callable[[], None]
    messages_path: Path
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelPendingPolicyPorts:
    wake_batch_limit: Callable[[], int]
    now: Callable[[], float]
    replay_ttl_seconds: Callable[[], float]
    timestamp_seconds: Callable[[dict[str, Any]], float | None]
    is_web_chat: Callable[[dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class ChannelWakeContext:
    claims: ChannelWakeClaimPorts
    messages: ChannelWakeMessagePorts
    cursor: ChannelWakeCursorPorts
    input: ChannelWakeInputPorts
    transcript: ChannelTranscriptPorts
    transcript_policy: ChannelTranscriptPolicyPorts
    pending_state: ChannelPendingStatePorts
    pending_delivery: ChannelPendingDeliveryPorts
    pending_io: ChannelPendingIoPorts
    pending_policy: ChannelPendingPolicyPorts

    def claim_repository(self) -> ChannelWakeClaimRepository:
        return ChannelWakeClaimRepository(
            path=self.claims.path,
            file_lock=self.claims.file_lock,
            now=self.claims.now,
            ttl_seconds=self.claims.ttl_seconds,
            log=self.claims.log,
        )

    def claim_prompt(self, message_id: int) -> str:
        if message_id <= 0:
            return ""
        return self.claims.delivery.prompt(message_id) or self.claim_repository().prompt(
            message_id
        )

    def claim_wake_prompt(self, message_id: int, prompt: str) -> bool:
        return self.claim_repository().claim(message_id, prompt)

    def clear_wake_claim(self, message_id: int) -> None:
        self.claim_repository().clear(message_id)

    def wake_uses_body_fallback(self, message_id: int) -> bool:
        return self.claim_repository().body_fallback(message_id)

    def mark_wake_body_fallback(self, message_id: int, reason: str) -> None:
        self.claim_repository().mark_body_fallback(message_id, reason)

    def prompt_references_message_id(
        self,
        text: str,
        message_id: int,
        prompt_texts: list[str] | tuple[str, ...] | None = None,
    ) -> bool:
        prompts = [str(item) for item in (prompt_texts or ()) if str(item or "").strip()]
        if prompt_texts is None:
            claimed_prompt = self.claim_prompt(message_id)
            if claimed_prompt:
                prompts.append(claimed_prompt)
        return self.claims.prompt_reference(text, message_id, prompts)

    def message_ids_already_in_request(self, body: dict[str, Any]) -> set[int]:
        ids: set[int] = set()
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            text = self.messages.content_to_text(message.get("content"))
            if (
                "ciel-runtime external channel message" not in text
                and "[external channel input]" not in text
            ):
                continue
            ids.update(self.claims.prompt_message_ids(text))
        return ids

    def wake_message_ids(self, body: dict[str, Any]) -> set[int]:
        if not self.messages.wake_request(body):
            return set()
        messages = [message for message in body.get("messages") or [] if isinstance(message, dict)]
        if not messages:
            return set()
        text = self.messages.content_to_text(messages[-1].get("content"))
        return self.claims.prompt_message_ids(text)

    def commit_cursor(self, last_id: int) -> None:
        self.cursor.cache(last_id)
        try:
            self.cursor.write(last_id)
        except Exception as exc:
            self.claims.log(
                "WARN",
                f"channel_llm_cursor_write_failed error={type(exc).__name__}: {exc}",
            )

    def stdin_skip_reason(self, message_id: int) -> str:
        if self.claims.delivery.is_delivered(message_id):
            return "stdin_wake_delivered"
        return (
            "stdin_wake_claimed"
            if self.transcript_policy.claim_prompt(message_id)
            else ""
        )

    def body_with_pending_messages(self, body: dict[str, Any]) -> dict[str, Any]:
        return channel_llm_context.inject_pending_channel_context(
            body,
            channel_llm_context.ChannelLlmContextServices(
                policy=channel_llm_context.ChannelLlmContextPolicy(
                    wake_request=self.messages.wake_request,
                    wake_message_ids=self.wake_message_ids,
                    plan_mode_active=self.messages.plan_mode_active,
                    delivery_mode=self.messages.delivery_mode,
                    ids_in_request=self.message_ids_already_in_request,
                    scan_limit=self.messages.scan_limit,
                    skip_reason=self.messages.skip_reason,
                    stdin_skip_reason=self.stdin_skip_reason,
                ),
                repository=channel_llm_context.ChannelLlmContextRepository(
                    lock=self.cursor.lock,
                    read_cursor=self.cursor.read,
                    commit_cursor=self.commit_cursor,
                    read_messages=self.messages.read_messages,
                    superseded_ids=self.messages.superseded_ids,
                ),
                projection=channel_llm_context.ChannelLlmContextProjection(
                    remove_wake_prompt=self.messages.remove_wake_prompt,
                    format_prompt=self.messages.format_batch_prompt,
                ),
                log=self.claims.log,
            ),
        )

    @staticmethod
    def write_all(fd: Any, data: bytes) -> None:
        writer = getattr(fd, "write", None)
        if callable(writer):
            writer(data)
            return
        import os

        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]

    def enter_bytes(self, value: str | bytes | None = None) -> bytes:
        configured = (
            self.input.environment.get("CIEL_RUNTIME_CHANNEL_WAKE_ENTER")
            if value is None
            else value
        )
        return self.input.resolve_enter_bytes(
            configured, self.input.platform_enter_bytes()
        )

    def input_bytes(self, prompt: str, enter_bytes: bytes | None = None) -> bytes:
        return self.input.build_input_bytes(prompt, self.enter_bytes(enter_bytes))

    def current_tmux_pane_text(self) -> str | None:
        from .channel_terminal_input import TmuxPaneSnapshot

        return TmuxPaneSnapshot(
            self.input.environment,
            self.input.find_executable,
            self.input.run_process,
            self.input.log,
        ).capture()

    def write_prompt(
        self,
        master_fd: int,
        prompt: str,
        enter_bytes: bytes | None = None,
        *,
        submit_retry_count: int = 1,
        confirm_submit: bool = False,
        bracketed_paste: bool = False,
        submit_delay_seconds: float | None = None,
        write_all: Callable[[Any, bytes], None] | None = None,
        snapshot: Callable[[], str | None] | None = None,
    ) -> None:
        delay = (
            self.input.submit_delay_seconds()
            if submit_delay_seconds is None
            else max(0.0, float(submit_delay_seconds))
        )
        injector = channel_injection.ChannelPromptInjector(
            sleep=self.input.sleep,
            retry_delay_seconds=self.input.retry_delay_seconds,
            snapshot=snapshot or self.current_tmux_pane_text,
            log=self.input.log,
        )
        injector.inject(
            channel_injection.CallableInputTransport(
                master_fd, write_all or self.write_all
            ),
            channel_injection.PromptInjection(
                prompt=prompt,
                policy=channel_injection.RuntimeInjectionPolicy(
                    runtime="interactive-cli",
                    clear_input=b"\x15",
                    submit_input=self.enter_bytes(enter_bytes),
                    submit_delay_seconds=delay,
                    submit_attempts=max(1, min(8, int(submit_retry_count or 1))),
                    confirm_submission=confirm_submit,
                    bracketed_paste=bracketed_paste,
                ),
            ),
        )

    def transcript_repository(self) -> ChannelTranscriptRepository:
        return ChannelTranscriptRepository(
            self.transcript.home,
            self.transcript.cache,
            self.transcript.scope,
            self.transcript.now,
        )

    def set_transcript_scope(
        self,
        runtime: str,
        *,
        started_at: float | None = None,
        codex_home: Path | None = None,
        cwd: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self.transcript_repository().set_scope(
            runtime,
            started_at=started_at,
            codex_home=codex_home,
            cwd=cwd,
            session_id=session_id,
        )

    def transcript_roots(self) -> tuple[tuple[Path, str], ...]:
        return self.transcript_repository().roots()

    def latest_transcript_path(self, ttl_seconds: float = 2.0) -> Path | None:
        return self.transcript_repository().latest(ttl_seconds)

    def transcript_services(self) -> ChannelWakeTranscriptServices:
        return ChannelWakeTranscriptServices(
            claim_prompt=self.transcript_policy.claim_prompt,
            prompt_references_message_id=self.transcript_policy.prompt_references_message,
            prompt_message_ids=self.transcript_policy.prompt_message_ids,
            now=self.transcript.now,
        )

    def queued_age_seconds_from_text(
        self,
        message_id: int,
        text: str,
        prompt_texts: list[str] | tuple[str, ...] | None = None,
        *,
        now: float | None = None,
        not_before: float | None = None,
    ) -> float | None:
        return self.transcript.queued_age_from_text(
            message_id,
            text,
            prompt_texts,
            self.transcript_services(),
            now=now,
            not_before=not_before,
        )

    def wake_state_from_text(
        self,
        message_id: int,
        text: str,
        prompt_texts: list[str] | tuple[str, ...] | None = None,
        *,
        not_before: float | None = None,
    ) -> str:
        return self.wake_state_evidence_from_text(
            message_id, text, prompt_texts, not_before=not_before
        ).state

    def wake_state_evidence_from_text(
        self,
        message_id: int,
        text: str,
        prompt_texts: list[str] | tuple[str, ...] | None = None,
        *,
        not_before: float | None = None,
    ) -> WakeStateEvidence:
        return self.transcript.wake_state_evidence_from_text(
            message_id,
            text,
            prompt_texts,
            self.transcript_services(),
            not_before=not_before,
        )

    def wake_state_reader(self) -> ChannelWakeStateReader:
        return ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                self.transcript_policy.latest_transcript,
                self.transcript.read_tail,
                self.wake_state_evidence_from_text,
                self.queued_age_seconds_from_text,
                self.transcript_policy.inflight_stale_seconds,
                self.transcript_policy.log,
            )
        )

    def active_tool_call(self) -> bool:
        path = self.transcript_policy.latest_transcript()
        text = self.transcript.read_tail(path) if path is not None else ""
        return bool(
            text
            and self.transcript.active_tool_call_from_text(
                text, not_before=self.console_started_at()
            )
        )

    def console_started_at(self) -> float | None:
        """Launch time of the console this wake path drives, if known."""

        try:
            started_at = float(self.transcript.scope.get("started_at") or 0.0)
        except (TypeError, ValueError):
            return None
        return started_at if started_at > 0 else None

    def active_turn(self) -> bool:
        path = self.transcript_policy.latest_transcript()
        if path is None:
            return bool(self.transcript.scope.get("turn_active"))
        text = self.transcript_repository().read_turn_updates(path)
        if not text:
            return bool(self.transcript.scope.get("turn_active"))
        active = bool(
            self.transcript.active_turn_from_text(
                text,
                not_before=self.console_started_at(),
                initial_active=bool(self.transcript.scope.get("turn_active")),
            )
        )
        self.transcript.scope["turn_active"] = active
        return active

    def queued_command_ids_from_text(self, text: str) -> set[int]:
        return self.transcript_policy.queued_ids_from_text(
            text, self.transcript_services()
        )

    def cursor_recovery_service(self) -> ChannelCursorRecoveryService:
        return ChannelCursorRecoveryService(
            cache=self.transcript.recovery_cache,
            policy=ChannelCursorRecoveryPolicy(),
            ports=ChannelCursorRecoveryPorts(
                latest_transcript=self.transcript_policy.latest_transcript,
                read_tail=self.transcript.read_tail,
                queued_command_ids=self.queued_command_ids_from_text,
                wake_state=self.wake_state_from_text,
                clamp_to_clear_floor=self.cursor.clamp_to_clear_floor,
                now=self.transcript.now,
                log=self.transcript_policy.log,
            ),
        )

    def pending_injection_services(self) -> ChannelInjectionServices:
        return ChannelInjectionServices(
            state=ChannelInjectionState(
                active_tool_call=self.pending_state.active_tool_call,
                active_turn=self.pending_state.active_turn,
                recover_cursor=self.pending_state.recover_cursor,
                pending_scan_limit=self.pending_state.pending_scan_limit,
                superseded_ids=self.pending_state.superseded_ids,
                message_is_web_chat=self.pending_state.message_is_web_chat,
                message_skip_reason=self.pending_state.message_skip_reason,
                event_identity_key=self.pending_state.event_identity_key,
                wake_state_for_message=self.pending_state.wake_state_for_message,
                queued_wake_is_stale=self.pending_state.queued_wake_is_stale,
            ),
            prompts=ChannelInjectionPrompts(
                llm_delivery=self.pending_delivery.format_llm_delivery,
                visible_llm_delivery=(
                    self.pending_delivery.format_visible_llm_delivery
                ),
                web_chat=self.pending_delivery.format_web_chat,
                standard=self.pending_delivery.format_standard,
                enter_bytes=self.enter_bytes,
                enter_label=self.pending_delivery.enter_label,
            ),
            wake_store=ChannelInjectionWakeStore(
                claim_for_nonblocking_scan=self.transcript_policy.claim_prompt,
                claim_prompt=self.claim_wake_prompt,
                clear_claim=self.clear_wake_claim,
                release_stale=self.pending_delivery.release_stale,
                mark_delivered=self.pending_delivery.mark_delivered,
                record_prompts=self.pending_delivery.record_prompts,
                rollback=self.pending_delivery.rollback,
                commit_cursor=self.pending_delivery.commit_cursor,
                body_fallback=self.wake_uses_body_fallback,
            ),
            io=ChannelInjectionIO(
                inject_lock=self.pending_io.inject_lock,
                read_messages=self.pending_io.read_messages,
                write_prompt=self.pending_io.write_prompt,
                log=self.pending_io.log,
            ),
            policy=ChannelInjectionPolicy(
                wake_batch_limit=self.pending_policy.wake_batch_limit,
                replay_skip_reason=ChannelReplaySafetyPolicy(
                    self.pending_policy.now,
                    self.pending_policy.replay_ttl_seconds,
                    self.pending_policy.timestamp_seconds,
                    self.pending_policy.is_web_chat,
                ).skip_reason,
            ),
        )

    def inject_pending(
        self,
        master_fd: int,
        last_id: int,
        enter_bytes: bytes | None = None,
        **options: Any,
    ) -> int:
        return inject_pending_channel_messages(
            master_fd,
            last_id,
            enter_bytes,
            services=self.pending_injection_services(),
            **options,
        )

    def inject_compact(
        self,
        master_fd: int,
        enter_bytes: bytes | None = None,
        **options: Any,
    ) -> str:
        return ChannelCompactInjectionService(
            request=ChannelCompactRequestPorts(
                read=self.pending_io.compact_read,
                clear=self.pending_io.compact_clear,
            ),
            runtime=ChannelCompactRuntimePorts(
                active_tool_call=self.pending_state.active_tool_call,
                active_turn=self.pending_state.active_turn,
                enter_bytes=self.enter_bytes,
                write_prompt=self.pending_io.write_prompt,
                enter_label=self.pending_delivery.enter_label,
            ),
            log=self.pending_io.log,
        ).inject(master_fd, enter_bytes, **options)

    def messages_file_marker(self) -> tuple[float, int]:
        try:
            stat = self.pending_io.messages_path.stat()
            return (stat.st_mtime, stat.st_size)
        except Exception:
            return (0.0, 0)
