"""Reusable Server-Sent Events stream parser and dispatch loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class SseStreamServices:
    should_continue: Callable[[], bool]
    dispatch: Callable[[str, list[str], str | None], Any]
    invalid_retry: Callable[[str], Any]


@dataclass(slots=True)
class SseRetryState:
    seconds: float


def consume_sse_stream(
    response: Any,
    retry: SseRetryState,
    end_message: str,
    services: SseStreamServices,
) -> None:
    event_name = "message"
    event_id: str | None = None
    data_lines: list[str] = []
    while services.should_continue():
        raw = response.readline()
        if raw == b"":
            raise ConnectionError(end_message)
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                services.dispatch(event_name, data_lines, event_id)
            event_name = "message"
            event_id = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            event_id = value
        elif field == "retry":
            try:
                retry.seconds = max(1.0, min(60.0, int(value) / 1000.0))
            except (TypeError, ValueError):
                services.invalid_retry(value)
