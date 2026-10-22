"""Provider-neutral token estimate for request sizing.

ASCII averages about four characters per token, but CJK text is roughly one
token per character. Counting every character uniformly under-estimated a
Korean conversation several times over (Celly, 2026-10-22): Ciel judged the
same session far below the window the provider had already reported as
exceeded. Non-ASCII characters therefore count as one token each.
"""

from __future__ import annotations

import json
from typing import Any


def estimate_tokens(body: Any, cache: dict[int, int] | None = None) -> int:
    if cache is not None:
        body_id = id(body)
        if body_id in cache:
            return cache[body_id]
    text = json.dumps(body, ensure_ascii=False)
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    result = max(1, ascii_chars // 4 + (len(text) - ascii_chars))
    if cache is not None:
        cache[id(body)] = result
    return result


__all__ = ["estimate_tokens"]
