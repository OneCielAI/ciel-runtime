"""Recover Anthropic-compatible requests rejected by an upstream context limit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextOverflow:
    context_limit: int
    message_tokens: int
    completion_tokens: int


_OVERFLOW_PATTERN = re.compile(
    r"maximum context length is\s*(?P<limit>[\d,]+)\s*tokens?.*?"
    r"requested\s*(?P<requested>[\d,]+)\s*tokens?\s*\(\s*"
    r"(?P<messages>[\d,]+)\s*in the messages?,\s*"
    r"(?P<completion>[\d,]+)\s*in the completion",
    re.IGNORECASE | re.DOTALL,
)


def _integer(value: str) -> int:
    try:
        return int(value.replace(",", ""))
    except (TypeError, ValueError):
        return 0


def parse_context_overflow(raw: str | bytes | None) -> ContextOverflow | None:
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw or "")
    match = _OVERFLOW_PATTERN.search(text)
    if not match:
        return None
    limit = _integer(match.group("limit"))
    messages = _integer(match.group("messages"))
    completion = _integer(match.group("completion"))
    if limit <= 0 or messages <= 0 or completion <= 0:
        return None
    return ContextOverflow(limit, messages, completion)


def recover_output_budget(
    body: dict[str, Any],
    raw: str | bytes | None,
    *,
    reserve_tokens: int = 0,
    minimum_output_tokens: int = 256,
) -> dict[str, Any] | None:
    """Reduce only the output reservation when the prompt itself still fits."""

    overflow = parse_context_overflow(raw)
    if overflow is None:
        return None
    reserve = max(0, int(reserve_tokens or 0))
    minimum = max(1, int(minimum_output_tokens or 1))
    available = overflow.context_limit - overflow.message_tokens - reserve
    if available < minimum:
        return None
    requested = int(body.get("max_tokens") or overflow.completion_tokens)
    adjusted = min(requested, overflow.completion_tokens, available)
    if adjusted >= requested:
        return None
    recovered = dict(body)
    recovered["max_tokens"] = adjusted
    return recovered


__all__ = ["ContextOverflow", "parse_context_overflow", "recover_output_budget"]
