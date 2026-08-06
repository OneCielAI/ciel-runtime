"""Incremental cache-usage observation for native Responses passthrough."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class ResponsesUsageObserver:
    """Observe final usage without altering or delaying streamed response bytes."""

    _line_buffer: str = ""
    _raw: str = ""
    _usage: dict[str, int] | None = None

    def feed(self, chunk: bytes) -> None:
        text = chunk.decode("utf-8", errors="ignore")
        if len(self._raw) < 1_048_576:
            self._raw = (self._raw + text)[-1_048_576:]
        self._line_buffer += text
        lines = self._line_buffer.split("\n")
        self._line_buffer = lines.pop()
        for line in lines:
            payload = line.strip()
            if payload.startswith("data:"):
                payload = payload[5:].strip()
            self._observe_json(payload)

    def finish(self) -> dict[str, int]:
        self._observe_json(self._line_buffer.strip())
        self._observe_json(self._raw.strip())
        return dict(self._usage or {})

    def _observe_json(self, text: str) -> None:
        if not text or text == "[DONE]":
            return
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, Mapping):
            return
        usage = payload.get("usage")
        response = payload.get("response")
        if not isinstance(usage, Mapping) and isinstance(response, Mapping):
            usage = response.get("usage")
        if not isinstance(usage, Mapping):
            return
        details = usage.get("input_tokens_details")
        details = details if isinstance(details, Mapping) else {}
        input_tokens = _positive_int(usage.get("input_tokens"))
        output_tokens = _positive_int(usage.get("output_tokens"))
        cache_read = (
            _positive_int(details.get("cached_tokens"))
            or _positive_int(usage.get("cache_read_input_tokens"))
        )
        cache_creation = (
            _positive_int(details.get("cache_write_tokens"))
            or _positive_int(usage.get("cache_creation_input_tokens"))
        )
        if not any((input_tokens, output_tokens, cache_read, cache_creation)):
            return
        self._usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "uncached_input_tokens": max(
                0, input_tokens - cache_read - cache_creation
            ),
        }


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = ["ResponsesUsageObserver"]
