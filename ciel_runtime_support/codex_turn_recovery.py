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

RUNTIME_REASONING_OUTPUT_BUDGET_NOTICE_PREFIX = (
    "[ciel-runtime] Upstream model exhausted its output budget during reasoning"
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

_REASONING_RECOVERY_MIN_OUTPUT_TOKENS = 8192

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


def message_exhausted_reasoning_output_budget(message: dict[str, Any]) -> bool:
    """Match only evidence that reasoning consumed the whole output budget."""

    if not message_has_reasoning(message) or message_has_tool_use(message):
        return False
    texts = [
        str(block.get("text") or "").strip()
        for block in message.get("content") or []
        if isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
    ]
    if texts and all(
        text.startswith(RUNTIME_REASONING_OUTPUT_BUDGET_NOTICE_PREFIX)
        for text in texts
    ):
        return True
    return not texts and str(message.get("stop_reason") or "").strip().lower() in {
        "length",
        "max_tokens",
    }


def project_reasoning_output_budget_retry(
    pcfg: dict[str, Any],
    body: dict[str, Any],
    strategy: str,
    effort: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Apply a provider-approved recovery without guessing wire parameters."""

    recovery_config = dict(pcfg)
    projected = dict(body)
    metadata = projected.get("metadata")
    projected_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    internal_key = "ciel_runtime_reasoning_effort"

    if strategy == "omit":
        projected.pop("thinking", None)
        projected.pop("reasoning_effort", None)
        projected_metadata.pop(internal_key, None)
    elif strategy == "disable":
        recovery_config["effort_level"] = "none"
        recovery_config["think"] = False
        recovery_config["think_explicit"] = True
        projected["thinking"] = {"type": "disabled"}
        projected["reasoning_effort"] = "none"
        projected_metadata[internal_key] = "none"
    elif strategy == "minimum" and effort:
        recovery_config["effort_level"] = effort
        projected["thinking"] = {"type": "enabled", "effort": effort}
        projected["reasoning_effort"] = effort
        projected_metadata[internal_key] = effort
    else:
        return recovery_config, projected, "prompt_only"

    if projected_metadata:
        projected["metadata"] = projected_metadata
    elif "metadata" in projected:
        projected.pop("metadata", None)
    return recovery_config, projected, strategy


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _expand_reasoning_recovery_output_budget(
    provider: str,
    config: dict[str, Any],
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], int | None]:
    """Temporarily raise a proven-exhausted output cap for the retry.

    A reasoning-only ``length`` result is direct evidence that the active cap
    was consumed. Raise every cap participating in the provider request so a
    lower stale field cannot silently keep the retry at the exhausted value.
    The ordinary request builder still caps the result against available model
    context.
    """

    candidates = [
        _positive_int(config.get("max_output_tokens")),
        _positive_int(body.get("max_tokens")),
        _positive_int(body.get("max_output_tokens")),
    ]
    ollama_options = config.get("ollama_options")
    if isinstance(ollama_options, dict):
        candidates.append(_positive_int(ollama_options.get("num_predict")))
    current = min((value for value in candidates if value is not None), default=None)
    if current is None:
        return config, body, None

    target = max(_REASONING_RECOVERY_MIN_OUTPUT_TOKENS, current * 2)
    expanded_config = dict(config)
    expanded_body = dict(body)
    expanded_config["max_output_tokens"] = target
    if "max_tokens" in expanded_body:
        expanded_body["max_tokens"] = target
    if "max_output_tokens" in expanded_body:
        expanded_body["max_output_tokens"] = target
    if (
        str(provider or "").strip().casefold() in {"ollama", "ollama-cloud"}
        or isinstance(ollama_options, dict)
    ):
        projected_options = dict(ollama_options or {})
        projected_options["num_predict"] = target
        expanded_config["ollama_options"] = projected_options
        transient_options = [
            str(item)
            for item in expanded_config.get("ollama_transient_options") or []
            if str(item).strip()
        ]
        if "num_predict" not in transient_options:
            transient_options.append("num_predict")
        expanded_config["ollama_transient_options"] = transient_options
    return expanded_config, expanded_body, target


def prepare_provider_reasoning_output_budget_retry(
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    adapter_for: Callable[[str, dict[str, Any]], Any],
    contract_for: Callable[[str, dict[str, Any]], Any],
    resolve_model: Callable[[str, dict[str, Any], Any], str],
    select_protocol: Callable[[str, dict[str, Any], str, str], str],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve the active adapter contract and project its bounded retry."""

    adapter = adapter_for(provider, pcfg)
    contract = contract_for(provider, pcfg)
    model = resolve_model(provider, pcfg, body.get("model"))
    protocol = select_protocol(provider, pcfg, "openai_responses", model)
    strategy, effort = adapter.reasoning_output_recovery(
        contract, model, protocol, body
    )
    recovery_config, projected, projected_strategy = (
        project_reasoning_output_budget_retry(pcfg, body, strategy, effort)
    )
    recovery_config, projected, output_tokens = (
        _expand_reasoning_recovery_output_budget(
            provider, recovery_config, projected
        )
    )
    if output_tokens is not None:
        projected_metadata = projected.get("metadata")
        projected_metadata = (
            dict(projected_metadata)
            if isinstance(projected_metadata, dict)
            else {}
        )
        projected_metadata["ciel_runtime_recovery_output_tokens"] = output_tokens
        projected["metadata"] = projected_metadata
    return recovery_config, projected, projected_strategy


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


def message_has_only_runtime_stall_notice(message: dict[str, Any]) -> bool:
    """Return whether visible text contains only Ciel's own stall notices."""

    return (
        message_has_only_reasoning_notice(message)
        or message_has_only_empty_end_turn_notice(message)
        or message_has_only_repeated_tool_notice(message)
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
    prepare_reasoning_budget_retry: Callable[
        [str, dict[str, Any], dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any], str],
    ] | None = None


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
    reasoning_output_budget = (
        reasoning_only and message_exhausted_reasoning_output_budget(message)
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
        else "reasoning_output_budget"
        if reasoning_output_budget
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
        retry_body = body_with_continuation_nudge(
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
        )
        recovery_strategy = "prompt_only"
        if reasoning_output_budget and services.prepare_reasoning_budget_retry:
            try:
                recovery_config, retry_body, recovery_strategy = (
                    services.prepare_reasoning_budget_retry(
                        provider, recovery_config, retry_body
                    )
                )
            except Exception as exc:  # noqa: BLE001 - retain safe prompt-only retry
                services.log(
                    "WARN",
                    "codex_reasoning_budget_recovery_projection_failed "
                    f"provider={provider} error={type(exc).__name__}: {exc}",
                )
            services.log(
                "WARN",
                f"codex_reasoning_budget_recovery provider={provider} "
                f"model={str(body.get('model') or '-')} strategy={recovery_strategy}",
            )
        retried = services.collect_message(
            handler,
            provider,
            recovery_config,
            retry_body,
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
            message_has_only_runtime_stall_notice(retried)
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
    "RUNTIME_REASONING_OUTPUT_BUDGET_NOTICE_PREFIX",
    "CodexTurnRecoveryServices",
    "body_with_codex_compat_instructions",
    "body_with_continuation_nudge",
    "message_has_tool_use",
    "message_exhausted_reasoning_output_budget",
    "message_has_reasoning",
    "message_has_only_reasoning_notice",
    "message_has_only_empty_end_turn_notice",
    "message_has_only_repeated_tool_notice",
    "message_has_only_runtime_stall_notice",
    "kimi_message_promises_followup",
    "message_without_empty_end_turn_notice",
    "message_without_repeated_tool_notice",
    "message_without_reasoning_notice",
    "message_text",
    "project_reasoning_output_budget_retry",
    "prepare_provider_reasoning_output_budget_retry",
    "recover_preamble_only_turn",
]
