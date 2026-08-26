"""Pending channel-message injection application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ciel_runtime_support.channel_message_policy import (
    message_input_transport,
    message_is_external_event,
    message_response_mcp,
    message_response_mode,
    web_chat_input_mode,
)


@dataclass(frozen=True, slots=True)
class ChannelInjectionState:
    active_tool_call: Callable[[], bool]
    active_turn: Callable[[], bool]
    recover_cursor: Callable[[int], int]
    pending_scan_limit: Callable[[], int]
    superseded_ids: Callable[..., set[int]]
    message_is_web_chat: Callable[..., bool]
    message_skip_reason: Callable[..., str]
    event_identity_key: Callable[..., tuple[str, ...]]
    wake_state_for_message: Callable[..., str]
    queued_wake_is_stale: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ChannelInjectionPrompts:
    llm_delivery: Callable[..., str]
    visible_llm_delivery: Callable[..., str]
    web_chat: Callable[..., str]
    standard: Callable[..., str]
    enter_bytes: Callable[..., bytes]
    enter_label: Callable[..., str]


@dataclass(frozen=True, slots=True)
class ChannelInjectionWakeStore:
    claim_for_nonblocking_scan: Callable[[int], bool]
    claim_prompt: Callable[[int, str], bool]
    clear_claim: Callable[[int], Any]
    release_stale: Callable[[int, bool], Any]
    mark_delivered: Callable[[int], bool]
    record_prompts: Callable[[list[dict[str, Any]], str], Any]
    rollback: Callable[[list[dict[str, Any]], list[int]], Any]
    commit_cursor: Callable[[int], Any]
    body_fallback: Callable[[int], bool] = lambda _message_id: False


@dataclass(frozen=True, slots=True)
class ChannelInjectionIO:
    inject_lock: Any
    read_messages: Callable[..., list[dict[str, Any]]]
    write_prompt: Callable[..., Any]
    log: Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class ChannelInjectionPolicy:
    wake_batch_limit: Callable[[], int]
    replay_skip_reason: Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ChannelInjectionServices:
    state: ChannelInjectionState
    prompts: ChannelInjectionPrompts
    wake_store: ChannelInjectionWakeStore
    io: ChannelInjectionIO
    policy: ChannelInjectionPolicy


def _message_id(message: dict[str, Any]) -> int:
    try:
        return int(message.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def inject_pending_channel_messages(
    master_fd: int,
    last_id: int,
    enter_bytes: bytes | None = None,
    *,
    web_chat_only: bool = False,
    wake_for_llm_delivery: bool = False,
    display_llm_delivery_body: bool = False,
    commit_cursor: bool = True,
    injected_message_ids: list[int] | None = None,
    submit_retry_count: int = 1,
    confirm_submit: bool = False,
    bracketed_paste: bool = False,
    submit_delay_seconds: float | None = None,
    skip_blocking_wake_states: bool = False,
    services: ChannelInjectionServices,
) -> int:
    state = services.state
    prompts = services.prompts
    wake_store = services.wake_store
    io = services.io
    with io.inject_lock:
        if state.active_tool_call():
            io.log("INFO", f"channel_stdin_proxy_deferred cursor={last_id} reason=active_tool_call")
            return last_id
        if state.active_turn():
            io.log("INFO", f"channel_stdin_proxy_deferred cursor={last_id} reason=active_turn")
            return last_id
        if not web_chat_only:
            last_id = state.recover_cursor(last_id)
        pending: list[dict[str, Any]] = []
        return_last_id = last_id
        candidates = io.read_messages(last_id, None, None, state.pending_scan_limit())
        superseded_ids = state.superseded_ids(candidates)
        batch_limit = services.policy.wake_batch_limit() if wake_for_llm_delivery else 1
        pending_batch_key: tuple[str, str, str] | None = None
        pending_uses_router = False
        seen_event_keys: set[tuple[str, ...]] = set()
        for message in candidates:
            previous_last_id = last_id
            message_id = _message_id(message)
            if message_id <= 0:
                continue
            last_id = max(last_id, message_id)
            channel = message.get("channel")
            if web_chat_only and not state.message_is_web_chat(message):
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=not_web_chat")
                continue
            replay_skip_reason = services.policy.replay_skip_reason(message)
            if replay_skip_reason:
                io.log(
                    "WARN",
                    f"channel_stdin_proxy_skipped_noise message_id={message_id} "
                    f"channel={channel} reason={replay_skip_reason}",
                )
                continue
            metadata = message.get("meta") if isinstance(message.get("meta"), dict) else {}
            requested_router = (
                message_input_transport(message) == "router"
                if "input_transport" in metadata
                else wake_for_llm_delivery
            )
            if requested_router and not wake_for_llm_delivery:
                io.log(
                    "WARN",
                    f"channel_stdin_proxy_deferred cursor={previous_last_id} message_id={message_id} "
                    f"channel={channel} reason=router_transport_unavailable",
                )
                if pending:
                    break
                return previous_last_id
            candidate_uses_router = requested_router
            if candidate_uses_router and wake_store.body_fallback(message_id):
                io.log(
                    "INFO",
                    f"channel_stdin_proxy_skipped_noise message_id={message_id} "
                    f"channel={channel} reason=stdin_wake_body_fallback",
                )
                continue
            skip_reason = state.message_skip_reason(message)
            if skip_reason:
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason={skip_reason}")
                continue
            if message_id in superseded_ids:
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=superseded_channel_notice")
                continue
            event_key = state.event_identity_key(message)
            if event_key and event_key in seen_event_keys:
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=duplicate_channel_event")
                continue
            if candidate_uses_router:
                formatter = (
                    prompts.visible_llm_delivery
                    if display_llm_delivery_body
                    else prompts.llm_delivery
                )
                message_prompt = formatter([message])
            # web_chat_only filters the candidate queue; it must not select the
            # prompt format. Mixed-channel TTY delivery still needs the private
            # one-shot Web Chat reply contract.
            elif state.message_is_web_chat(message):
                message_prompt = prompts.web_chat([message])
            else:
                message_prompt = prompts.standard([message])
            wake_state = state.wake_state_for_message(message, message_prompt)
            if wake_state == "completed":
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=stdin_wake_completed")
                continue
            if skip_blocking_wake_states and wake_state == "missing" and wake_store.claim_for_nonblocking_scan(message_id):
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=stdin_wake_claimed_continue")
                continue
            if wake_state in {"pending", "queued"}:
                if wake_state == "queued" and state.queued_wake_is_stale(message, message_prompt):
                    wake_store.release_stale(message_id, not web_chat_only)
                    io.log("WARN", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=stale_queued_wake")
                    continue
                if skip_blocking_wake_states:
                    io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=stdin_wake_{wake_state}_continue")
                    continue
                io.log("INFO", f"channel_stdin_proxy_waiting_for_turn_completion message_id={message_id} channel={channel} state={wake_state}")
                if pending:
                    break
                return previous_last_id
            response_mode = message_response_mode(message)
            response_mcp = message_response_mcp(message)
            response_key = (
                response_mode
                if response_mode != "mcp"
                else "mcp:"
                + ":".join(response_mcp.get(key, "") for key in ("server", "tool", "hint"))
            )
            batch_key_base = (
                ("web_chat", web_chat_input_mode(message) + ":" + response_key)
                if state.message_is_web_chat(message)
                else ("external_event", str((message.get("meta") or {}).get("receiver_id") or ""))
                if message_is_external_event(message)
                else ("channel", "")
            )
            batch_key = (batch_key_base[0], batch_key_base[1], "router" if candidate_uses_router else "tty")
            if pending and pending_batch_key != batch_key:
                break
            if not candidate_uses_router and not wake_store.mark_delivered(message_id):
                io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={message_id} channel={channel} reason=stdin_wake_delivered")
                continue
            pending.append(message)
            pending_batch_key = batch_key
            pending_uses_router = candidate_uses_router
            if event_key:
                seen_event_keys.add(event_key)
            if candidate_uses_router and len(pending) == 1:
                return_last_id = previous_last_id
            last_id = message_id
            if len(pending) >= batch_limit:
                break
        if not pending:
            return last_id
        if pending_uses_router:
            formatter = (
                prompts.visible_llm_delivery
                if display_llm_delivery_body
                else prompts.llm_delivery
            )
            prompt = formatter(pending)
        # Preserve the private reply contract even when the proxy accepts both
        # Web Chat and ordinary external-channel messages.
        elif all(state.message_is_web_chat(message) for message in pending):
            prompt = prompts.web_chat(pending)
        else:
            prompt = prompts.standard(pending)
        claimed_ids: list[int] = []
        if pending_uses_router:
            for message in pending:
                claim_id = _message_id(message)
                if claim_id <= 0:
                    continue
                if not wake_store.claim_prompt(claim_id, prompt):
                    io.log("INFO", f"channel_stdin_proxy_skipped_noise message_id={claim_id} channel={message.get('channel')} reason=stdin_wake_claimed")
                    return return_last_id
                claimed_ids.append(claim_id)
        submit_bytes = prompts.enter_bytes(enter_bytes)
        try:
            submitted = io.write_prompt(
                master_fd,
                prompt,
                submit_bytes,
                submit_retry_count=submit_retry_count,
                confirm_submit=confirm_submit,
                bracketed_paste=bracketed_paste,
                submit_delay_seconds=submit_delay_seconds,
            )
            if submitted is False:
                wake_store.rollback(pending, claimed_ids)
                ids = ",".join(str(message.get("id") or "") for message in pending)
                io.log(
                    "WARN",
                    "channel_stdin_proxy_deferred "
                    f"message_ids={ids} reason=prompt_not_submitted",
                )
                return return_last_id if pending_uses_router else previous_last_id
            wake_store.record_prompts(pending, prompt)
        except Exception:
            wake_store.rollback(pending, claimed_ids)
            raise
        if not web_chat_only:
            if commit_cursor:
                wake_store.commit_cursor(last_id)
            if injected_message_ids is not None:
                injected_message_ids.extend(message_id for message in pending if (message_id := _message_id(message)) > 0)
        ids = ",".join(str(message.get("id") or "") for message in pending)
        channels = ",".join(sorted({str(message.get("channel") or "default") for message in pending}))
        io.log(
            "INFO",
            f"channel_stdin_proxy_injected count={len(pending)} message_ids={ids} "
            f"channels={channels} enter={prompts.enter_label(submit_bytes)} "
            f"commit_cursor={commit_cursor} "
            f"display_body={bool(pending_uses_router and display_llm_delivery_body)}",
        )
        return return_last_id if pending_uses_router else last_id
