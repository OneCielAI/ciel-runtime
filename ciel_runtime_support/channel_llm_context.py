"""Inject pending channel messages into an LLM request context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ciel_runtime_support.channel_message_policy import message_input_transport


INTERNAL_METADATA_PREFIX = "ciel_runtime_"


class ChannelLlmInjectionDeferred(RuntimeError):
    """A wake request cannot yet cross an earlier terminal-owned message."""


def strip_internal_metadata(body: dict[str, Any]) -> dict[str, Any]:
    """Return the original body unless private metadata requires projection."""

    metadata = body.get("metadata")
    if not isinstance(metadata, dict) or not metadata:
        return body
    public_metadata = {
        key: value
        for key, value in metadata.items()
        if not str(key).startswith(INTERNAL_METADATA_PREFIX)
    }
    if len(public_metadata) == len(metadata):
        return body
    projected = dict(body)
    if public_metadata:
        projected["metadata"] = public_metadata
    else:
        projected.pop("metadata", None)
    return projected


@dataclass(frozen=True, slots=True)
class ChannelLlmContextPolicy:
    wake_request: Callable[[dict[str, Any]], bool]
    wake_message_ids: Callable[[dict[str, Any]], set[int]]
    plan_mode_active: Callable[[dict[str, Any]], bool]
    delivery_mode: Callable[[], str]
    ids_in_request: Callable[[dict[str, Any]], set[int]]
    scan_limit: Callable[[], int]
    skip_reason: Callable[[dict[str, Any]], str]
    stdin_skip_reason: Callable[[int], str]


@dataclass(frozen=True, slots=True)
class ChannelLlmContextRepository:
    lock: Callable[[], Any]
    read_cursor: Callable[[], int]
    commit_cursor: Callable[[int], None]
    read_messages: Callable[[int, int], list[dict[str, Any]]]
    superseded_ids: Callable[[list[dict[str, Any]]], set[int]]


@dataclass(frozen=True, slots=True)
class ChannelLlmContextProjection:
    remove_wake_prompt: Callable[[dict[str, Any]], dict[str, Any]]
    format_prompt: Callable[[list[dict[str, Any]]], str]


@dataclass(frozen=True, slots=True)
class ChannelLlmContextServices:
    policy: ChannelLlmContextPolicy
    repository: ChannelLlmContextRepository
    projection: ChannelLlmContextProjection
    log: Callable[[str, str], Any]


def inject_pending_channel_context(
    body: dict[str, Any],
    services: ChannelLlmContextServices,
) -> dict[str, Any]:
    policy = services.policy
    repository = services.repository
    wake_request = policy.wake_request(body)
    wake_message_ids = policy.wake_message_ids(body) if wake_request else set()
    if policy.plan_mode_active(body):
        if not wake_request:
            services.log("INFO", "channel_llm_inject_skipped reason=plan_mode_active")
            return body
        services.log("INFO", "channel_llm_inject_plan_mode_override reason=channel_wake")
    if policy.delivery_mode() != "llm":
        return body

    ids_already_in_request = policy.ids_in_request(body)
    with repository.lock():
        last_id = repository.read_cursor()
        pending: list[dict[str, Any]] = []
        max_seen = last_id
        candidates = repository.read_messages(last_id, policy.scan_limit())
        superseded_ids = repository.superseded_ids(candidates)
        deferred_tty_id = 0
        for message in candidates:
            try:
                message_id = int(message.get("id") or 0)
            except (TypeError, ValueError):
                continue
            reason = _candidate_skip_reason(
                message,
                message_id,
                ids_already_in_request,
                superseded_ids,
                policy,
            )
            if wake_request and reason == "stdin_wake_claimed":
                reason = "" if not wake_message_ids or message_id in wake_message_ids else reason
            if reason:
                if reason not in {"stdin_wake_delivered", "stdin_wake_claimed"}:
                    max_seen = max(max_seen, message_id)
                services.log(
                    "INFO",
                    f"channel_llm_inject_skipped message_id={message.get('id')} "
                    f"channel={message.get('channel')} reason={reason}",
                )
                continue
            if message_input_transport(message) != "router":
                # Transport ownership matters only after visibility/delivery
                # filtering.  A web-only self response stamped as TTY must not
                # block a later router-owned request from entering the model.
                deferred_tty_id = message_id
                services.log(
                    "INFO",
                    f"channel_llm_inject_deferred message_id={message.get('id')} "
                    f"channel={message.get('channel')} reason=input_transport_tty",
                )
                break
            pending.append(message)
            max_seen = message_id
            if not wake_request or not wake_message_ids:
                break
            if wake_message_ids.issubset({_message_id(item) for item in pending}):
                break
        if not pending:
            if max_seen != last_id:
                repository.commit_cursor(max_seen)
            if wake_request and deferred_tty_id:
                # Never strip a wake marker and send an empty user turn when
                # FIFO ownership genuinely prevents body injection.  Failing
                # the HTTP request leaves no assistant completion evidence, so
                # the bounded client retry/fallback path can recover it.
                raise ChannelLlmInjectionDeferred(
                    "Ciel Router deferred wake body injection behind "
                    f"TTY message id={deferred_tty_id}"
                )
            return services.projection.remove_wake_prompt(body) if wake_request else body

    base_body = services.projection.remove_wake_prompt(body) if wake_request else body
    messages = [message for message in base_body.get("messages", []) if isinstance(message, dict)]
    messages.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": services.projection.format_prompt(pending)}],
        }
    )
    out = dict(base_body)
    out["messages"] = messages
    ids = ",".join(str(message.get("id") or "") for message in pending)
    channels = ",".join(sorted({str(message.get("channel") or "default") for message in pending}))
    metadata = dict(out.get("metadata") if isinstance(out.get("metadata"), dict) else {})
    metadata.update(
        {
            "ciel_runtime_channel_injected": True,
            "ciel_runtime_channel_message_ids": ids,
            "ciel_runtime_channel_cursor_last_id": str(max_seen),
        }
    )
    out["metadata"] = metadata
    services.log("INFO", f"channel_llm_injected count={len(pending)} message_ids={ids} channels={channels}")
    return out


def _candidate_skip_reason(
    message: dict[str, Any],
    message_id: int,
    ids_already_in_request: set[int],
    superseded_ids: set[int],
    policy: ChannelLlmContextPolicy,
) -> str:
    if message_id in ids_already_in_request:
        return "already_in_request"
    if message_id in superseded_ids:
        return "superseded_channel_notice"
    reason = policy.skip_reason(message)
    return reason or policy.stdin_skip_reason(message_id)


def _message_id(message: dict[str, Any]) -> int:
    try:
        return int(message.get("id") or 0)
    except (TypeError, ValueError):
        return 0
