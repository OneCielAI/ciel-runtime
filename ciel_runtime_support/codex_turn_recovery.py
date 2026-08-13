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

RUNTIME_REASONING_ONLY_NOTICE_PREFIXES = (
    "[ciel-runtime] Upstream model returned reasoning without a final answer or tool call.",
    "[ciel-runtime] Upstream model exhausted its output budget during reasoning",
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


def body_with_continuation_nudge(
    body: dict[str, Any], message: dict[str, Any], nudge: str = CODEX_CONTINUATION_NUDGE
) -> dict[str, Any]:
    """Replay the request with the stalled reply and an explicit continue turn."""

    messages = list(body.get("messages") or [])
    assistant_text = message_text(message).strip()
    if assistant_text:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": assistant_text}]})
    messages.append({"role": "user", "content": [{"type": "text", "text": nudge}]})
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
    kimi_reasoning_only = (
        (provider or "").strip().lower() == "kimi"
        and message_has_reasoning(message)
        and (not text.strip() or message_has_only_reasoning_notice(message))
    )
    if not kimi_reasoning_only and not services.should_retry(body, text, []):
        return message

    reason = "reasoning_only" if kimi_reasoning_only else "preamble_only"
    services.log(
        "WARN",
        f"codex_turn_retry provider={provider} reason={reason} "
        f"model={str(body.get('model') or '-')} chars={len(text.strip())}",
    )
    try:
        nudge = (
            CODEX_EMPTY_REASONING_CONTINUATION_NUDGE
            if kimi_reasoning_only
            else CODEX_CONTINUATION_NUDGE
        )
        retried = services.collect_message(
            handler,
            provider,
            pcfg,
            body_with_continuation_nudge(
                body,
                message_without_reasoning_notice(message) if kimi_reasoning_only else message,
                nudge,
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
    if kimi_reasoning_only:
        if not message_has_tool_use(retried) and not message_text(retried).strip():
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
    "CodexTurnRecoveryServices",
    "body_with_codex_compat_instructions",
    "body_with_continuation_nudge",
    "message_has_tool_use",
    "message_has_reasoning",
    "message_has_only_reasoning_notice",
    "message_without_reasoning_notice",
    "message_text",
    "recover_preamble_only_turn",
]
