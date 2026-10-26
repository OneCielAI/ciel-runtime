"""Bounded replay policy for Ollama turns that end without visible output.

An Ollama upstream can finish a turn cleanly (``done: true`` with
``done_reason: "stop"``) while producing no visible text and no tool call,
either nothing at all or only private reasoning.  These helpers decide when
such a terminal response may be replayed, how many replays a provider config
allows, and how the replayed wire body is built.

The policy is deliberately narrow: an incomplete stream, an output-budget stop,
an invalid payload or upstream error, a runaway-repetition stop, or any tool
intent never qualifies.  The replay body is always rebuilt from the original
request, so a failed attempt is never fed back to the model.
"""

from __future__ import annotations

import copy
from typing import Any

from .protocols.ollama_chat import decode_ollama_chat_response
from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .visible_stream_filters import strip_visible_thinking_markup


SEMANTIC_RETRIES_CONFIG_KEY = "empty_response_retries"
DEFAULT_SEMANTIC_RETRIES = 1
MAX_SEMANTIC_RETRIES = 2

REASON_EMPTY_RESPONSE = "empty_response"
REASON_REASONING_ONLY = "reasoning_only"

SEMANTIC_RETRY_INSTRUCTION = (
    "[ciel-runtime] The previous attempt at this turn ended without visible text "
    "or a tool call, so the runtime is asking again. Call the next required tool "
    "now, or give the concrete final answer if no tool is needed."
)


def configured_retries(config: Any) -> int:
    """Return the semantic replay budget for one provider config.

    Defaults to one replay and clamps to 0..2.  Only a stored integer is
    honoured; a missing value or any other type (bool, float, string, ...)
    uses the default.  A remote-bridge config never replays: the remote side
    owns its own recovery.
    """

    if not isinstance(config, dict):
        return DEFAULT_SEMANTIC_RETRIES
    if config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True:
        return 0
    value = config.get(SEMANTIC_RETRIES_CONFIG_KEY)
    if not isinstance(value, int) or isinstance(value, bool):
        return DEFAULT_SEMANTIC_RETRIES
    return max(0, min(MAX_SEMANTIC_RETRIES, value))


def exhausted_notice(attempts: int) -> str:
    """Visible text for a turn whose every attempt ended without output."""

    try:
        count = max(1, int(attempts))
    except (TypeError, ValueError, OverflowError):
        count = 1
    noun = "attempt" if count == 1 else "attempts"
    return (
        "[ciel-runtime] Upstream model returned no visible answer or tool call "
        f"after {count} {noun}; automatic retries are exhausted. "
        "Retry the request or ask me to continue."
    )


def retry_reason(
    *,
    terminal_received: bool,
    done_reason: str,
    text: str,
    thinking: str,
    tool_intent: bool,
    invalid: bool,
    runaway: bool,
) -> str:
    """Classify a finished Ollama attempt; ``""`` means do not replay.

    Only a valid terminal ``stop`` with no visible text, no tool intent, no
    payload error and no runaway stop qualifies.  ``text`` is expected to be
    the visible text after thinking-markup filtering.
    """

    if not terminal_received or invalid or runaway or tool_intent:
        return ""
    if str(done_reason or "").strip().lower() != "stop":
        return ""
    if str(text or "").strip():
        return ""
    if str(thinking or "").strip():
        return REASON_REASONING_ONLY
    return REASON_EMPTY_RESPONSE


def collected_retry_reason(data: Any) -> str:
    """Classify one complete non-streaming Ollama ``/api/chat`` response.

    Uses the raw envelope decoder only; no tool or runtime policy is applied.
    Any tool-call field, upstream error, or malformed envelope disqualifies.
    """

    if not isinstance(data, dict) or data.get("error") is not None:
        return ""
    message = data.get("message")
    if not isinstance(message, dict):
        return ""
    raw_tool_calls = message.get("tool_calls")
    try:
        decoded = decode_ollama_chat_response(data)
    except (TypeError, ValueError, OverflowError):
        return ""
    return retry_reason(
        terminal_received=data.get("done") is True,
        done_reason=decoded.done_reason,
        text=strip_visible_thinking_markup(decoded.text),
        thinking=decoded.thinking,
        tool_intent=(
            (raw_tool_calls is not None and raw_tool_calls != [])
            or bool(decoded.tool_calls)
        ),
        invalid=False,
        runaway=False,
    )


def _is_replay_instruction(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "user"
        and message.get("content") == SEMANTIC_RETRY_INSTRUCTION
    )


def replay_body(body: dict[str, Any]) -> dict[str, Any]:
    """Return an independent copy of an Ollama wire body with the replay turn.

    The input is never mutated.  Model, options, tools, and every original
    message are kept exactly; only one runtime user instruction is appended.
    A trailing instruction already present is replaced rather than repeated,
    so replaying a replay body yields the same body.
    """

    replayed = copy.deepcopy(body) if isinstance(body, dict) else {}
    messages = replayed.get("messages")
    messages = list(messages) if isinstance(messages, list) else []
    while messages and _is_replay_instruction(messages[-1]):
        messages.pop()
    messages.append({"role": "user", "content": SEMANTIC_RETRY_INSTRUCTION})
    replayed["messages"] = messages
    return replayed


__all__ = [
    "DEFAULT_SEMANTIC_RETRIES",
    "MAX_SEMANTIC_RETRIES",
    "REASON_EMPTY_RESPONSE",
    "REASON_REASONING_ONLY",
    "SEMANTIC_RETRIES_CONFIG_KEY",
    "SEMANTIC_RETRY_INSTRUCTION",
    "collected_retry_reason",
    "configured_retries",
    "exhausted_notice",
    "replay_body",
    "retry_reason",
]
