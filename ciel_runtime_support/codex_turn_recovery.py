"""Keep routed Codex turns alive when a model only announces its intent.

Claude sessions get two safeguards against a model that says "I'll start now"
and then ends the turn: a compatibility instruction appended at launch, and
TaskList synthesis in the Anthropic stream. Neither reaches Codex — the launch
flag is Claude-only and the recovery lives in the Anthropic protocol path — so
a routed Codex session simply stops. These helpers give the Responses path the
same protection without synthesizing a tool call the Codex client never offered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


CODEX_CONTINUATION_NUDGE = (
    "Continue the work you just described in this same turn. Call the tools you "
    "need now instead of restating the plan, and report the concrete result. Only "
    "answer without tools when the task genuinely requires no tool use."
)

CODEX_EMPTY_REASONING_CONTINUATION_NUDGE = (
    "Continue the requested work now. Your previous response ended after private "
    "reasoning without visible text or a tool call. Call the next required tool, "
    "or provide the concrete final answer if no tool is needed."
)

CODEX_REPEATED_TOOL_CONTINUATION_NUDGE = (
    "The runtime stopped an exact tool call because the same call and result are "
    "already present twice in this turn. Do not repeat that call. Use the existing "
    "result, choose a different action if one is required, or provide the concrete "
    "final answer now."
)

RUNTIME_REASONING_ONLY_NOTICE_PREFIXES = (
    "[ciel-runtime] Upstream model returned reasoning without a final answer or tool call.",
    "[ciel-runtime] Upstream model exhausted its output budget during reasoning",
)

RUNTIME_EMPTY_END_TURN_NOTICE_PREFIX = (
    "[ciel-runtime] Upstream model returned an empty end_turn with no text or "
    "tool call."
)

RUNTIME_REPEATED_TOOL_NOTICE_PREFIX = (
    "[ciel-runtime] Stopped an identical completed tool call from repeating."
)

RUNTIME_CONTROL_MESSAGE_KEY = "ciel_runtime_control"
RUNTIME_REPEATED_TOOL_RECOVERY = "repeated_tool_call_recovery"

KIMI_FOLLOWUP_PROMISE_RE = re.compile(
    r"(?:겠습니다|할게요|해볼게요|하겠습니다|"
    r"i(?:'|’)ll\b[^\n]*|i\s+will\b[^\n]*|let\s+me\b[^\n]*)[.!?。！？]?\s*$",
    re.IGNORECASE,
)


def message_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def message_has_tool_use(message: dict[str, Any]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in message.get("content") or []
    )


def message_has_reasoning(message: dict[str, Any]) -> bool:
    return any(
        isinstance(block, dict)
        and block.get("type") in {"thinking", "reasoning"}
        and bool(str(block.get("thinking") or block.get("reasoning") or "").strip())
        for block in message.get("content") or []
    )


def message_has_only_reasoning_notice(message: dict[str, Any]) -> bool:
    texts = [
        str(block.get("text") or "").strip()
        for block in message.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip()
    ]
    return bool(texts) and all(
        any(text.startswith(prefix) for prefix in RUNTIME_REASONING_ONLY_NOTICE_PREFIXES)
        for text in texts
    )


def message_has_only_empty_end_turn_notice(message: dict[str, Any]) -> bool:
    """Identify the runtime's projection of a structurally empty upstream turn."""

    texts = [
        str(block.get("text") or "").strip()
        for block in message.get("content") or []
        if isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
    ]
    return bool(texts) and all(
        text.startswith(RUNTIME_EMPTY_END_TURN_NOTICE_PREFIX) for text in texts
    )


def message_has_only_repeated_tool_notice(message: dict[str, Any]) -> bool:
    """Identify the runtime's internal repeated-tool guard projection."""

    texts = [
        str(block.get("text") or "").strip()
        for block in message.get("content") or []
        if isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
    ]
    return bool(texts) and all(
        text.startswith(RUNTIME_REPEATED_TOOL_NOTICE_PREFIX) for text in texts
    )


def kimi_message_promises_followup(message: dict[str, Any]) -> bool:
    """Recognize Kimi ending a reasoning turn with an unperformed next action."""

    if not message_has_reasoning(message) or message_has_tool_use(message):
        return False
    return bool(KIMI_FOLLOWUP_PROMISE_RE.search(message_text(message).strip()))


def message_without_reasoning_notice(message: dict[str, Any]) -> dict[str, Any]:
    if not message_has_only_reasoning_notice(message):
        return message
    projected = dict(message)
    projected["content"] = [
        dict(block) if isinstance(block, dict) else block
        for block in message.get("content") or []
        if not (isinstance(block, dict) and block.get("type") == "text")
    ]
    return projected


def message_without_empty_end_turn_notice(message: dict[str, Any]) -> dict[str, Any]:
    if not message_has_only_empty_end_turn_notice(message):
        return message
    projected = dict(message)
    projected["content"] = [
        dict(block) if isinstance(block, dict) else block
        for block in message.get("content") or []
        if not (isinstance(block, dict) and block.get("type") == "text")
    ]
    return projected


def message_without_repeated_tool_notice(message: dict[str, Any]) -> dict[str, Any]:
    if not message_has_only_repeated_tool_notice(message):
        return message
    projected = dict(message)
    projected["content"] = [
        dict(block) if isinstance(block, dict) else block
        for block in message.get("content") or []
        if not (isinstance(block, dict) and block.get("type") == "text")
    ]
    return projected


def body_with_continuation_nudge(
    body: dict[str, Any],
    message: dict[str, Any],
    nudge: str = CODEX_CONTINUATION_NUDGE,
    *,
    control: str | None = None,
) -> dict[str, Any]:
    """Replay the request with the stalled reply and an explicit continue turn."""

    messages = list(body.get("messages") or [])
    assistant_text = message_text(message).strip()
    if assistant_text:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": assistant_text}]})
    continuation = {"role": "user", "content": [{"type": "text", "text": nudge}]}
    if control:
        continuation[RUNTIME_CONTROL_MESSAGE_KEY] = control
    messages.append(continuation)
    retried = dict(body)
    retried["messages"] = messages
    return retried


def body_with_codex_compat_instructions(
    body: dict[str, Any],
    compat_prompt: str,
    *,
    is_native_codex: bool,
    compat_enabled: bool,
) -> dict[str, Any]:
    """Append the routed Codex compatibility instruction to a Responses request.

    Codex rejects ``--append-system-prompt`` as a Claude-only flag, so the
    instruction has to travel in the request body. The native Codex backend is
    excluded: it serves OpenAI's own models, which do not need the nudge, and
    rewriting instructions there would only invalidate the cached prefix.
    """

    if not isinstance(body, dict) or is_native_codex or not compat_enabled:
        return body
    existing = str(body.get("instructions") or "")
    if compat_prompt in existing:
        return body
    projected = dict(body)
    projected["instructions"] = (
        f"{existing.rstrip()}\n\n{compat_prompt}" if existing.strip() else compat_prompt
    )
    return projected


@dataclass(frozen=True, slots=True)
class CodexTurnRecoveryServices:
    should_retry: Callable[[dict[str, Any], str, list[Any]], bool]
    collect_message: Callable[..., dict[str, Any]]
    log: Callable[[str, str], Any]


def recover_preamble_only_turn(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    message: dict[str, Any],
    services: CodexTurnRecoveryServices,
) -> dict[str, Any]:
    """Retry once when the model announced work but called no tool.

    Bounded to a single extra upstream call. The retry only wins if it produces
    a tool call; prose answers keep the original reply so a model that legitimately
    responds without tools is never overridden or duplicated.
    """

    if not isinstance(message, dict) or message_has_tool_use(message):
        return message
    text = message_text(message)
    empty_end_turn = message_has_only_empty_end_turn_notice(message)
    repeated_tool_guard = message_has_only_repeated_tool_notice(message)
    reasoning_only = (
        message_has_reasoning(message)
        and (not text.strip() or message_has_only_reasoning_notice(message))
    )
    kimi_promised_followup = (
        (provider or "").strip().lower() == "kimi"
        and kimi_message_promises_followup(message)
        and services.should_retry(body, text.replace("`", ""), [])
    )
    if (
        not empty_end_turn
        and not repeated_tool_guard
        and not reasoning_only
        and not kimi_promised_followup
        and not services.should_retry(body, text, [])
    ):
        return message

    reason = (
        "empty_end_turn"
        if empty_end_turn
        else "repeated_tool_call"
        if repeated_tool_guard
        else "reasoning_only"
        if reasoning_only
        else "promised_followup"
        if kimi_promised_followup
        else "preamble_only"
    )
    services.log(
        "WARN",
        f"codex_turn_retry provider={provider} reason={reason} "
        f"model={str(body.get('model') or '-')} chars={len(text.strip())}",
    )
    try:
        nudge = (
            CODEX_REPEATED_TOOL_CONTINUATION_NUDGE
            if repeated_tool_guard
            else
            CODEX_EMPTY_REASONING_CONTINUATION_NUDGE
            if empty_end_turn or reasoning_only
            else CODEX_CONTINUATION_NUDGE
        )
        recovery_config = dict(pcfg)
        if (provider or "").strip().lower() == "kimi":
            recovery_config["gateway_retries"] = 0
        retried = services.collect_message(
            handler,
            provider,
            recovery_config,
            body_with_continuation_nudge(
                body,
                message_without_repeated_tool_notice(message)
                if repeated_tool_guard
                else message_without_empty_end_turn_notice(message)
                if empty_end_turn
                else message_without_reasoning_notice(message)
                if reasoning_only
                else message,
                nudge,
                control=(RUNTIME_REPEATED_TOOL_RECOVERY if repeated_tool_guard else None),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - recovery must never fail the turn
        services.log(
            "WARN",
            f"codex_preamble_only_turn_retry_failed error={type(exc).__name__}: {exc}",
        )
        return message
    if not isinstance(retried, dict):
        return message
    if empty_end_turn or reasoning_only or repeated_tool_guard:
        if (
            message_has_only_repeated_tool_notice(retried)
            or (not message_has_tool_use(retried) and not message_text(retried).strip())
        ):
            return message
        return retried
    if not message_has_tool_use(retried):
        return message
    return _merged(message, retried)


def _merged(original: dict[str, Any], retried: dict[str, Any]) -> dict[str, Any]:
    """Keep the announcement, then the work it promised."""

    preamble = message_text(original).strip()
    blocks = list(retried.get("content") or [])
    if preamble:
        retried_text = re.sub(r"\s+", " ", message_text(retried)).strip()
        if re.sub(r"\s+", " ", preamble) not in retried_text:
            blocks = [{"type": "text", "text": preamble}, *blocks]
    merged = dict(retried)
    merged["content"] = blocks
    return merged


__all__ = [
    "CODEX_CONTINUATION_NUDGE",
    "CODEX_EMPTY_REASONING_CONTINUATION_NUDGE",
    "CODEX_REPEATED_TOOL_CONTINUATION_NUDGE",
    "RUNTIME_EMPTY_END_TURN_NOTICE_PREFIX",
    "CodexTurnRecoveryServices",
    "body_with_codex_compat_instructions",
    "body_with_continuation_nudge",
    "message_has_tool_use",
    "message_has_reasoning",
    "message_has_only_reasoning_notice",
    "message_has_only_empty_end_turn_notice",
    "message_has_only_repeated_tool_notice",
    "kimi_message_promises_followup",
    "message_without_empty_end_turn_notice",
    "message_without_repeated_tool_notice",
    "message_without_reasoning_notice",
    "message_text",
    "recover_preamble_only_turn",
]
