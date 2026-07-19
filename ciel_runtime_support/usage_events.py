"""Provider-neutral token usage events and durable sink ports."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class UsageEvent:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str = ""
    protocol: str = ""
    status: str = "completed"
    timestamp: float = 0.0

    def normalized(self, clock: Callable[[], float] = time.time) -> UsageEvent:
        return UsageEvent(
            provider=str(self.provider or "").strip(),
            model=str(self.model or "").strip(),
            input_tokens=max(0, int(self.input_tokens or 0)),
            output_tokens=max(0, int(self.output_tokens or 0)),
            request_id=str(self.request_id or "").strip(),
            protocol=str(self.protocol or "").strip(),
            status=str(self.status or "completed").strip() or "completed",
            timestamp=float(self.timestamp or clock()),
        )


class UsageEventSink(Protocol):
    def record(self, event: UsageEvent) -> None: ...


class NullUsageEventSink:
    def record(self, event: UsageEvent) -> None:
        del event


class JsonlUsageEventSink:
    """Append-only durable sink with bounded single-generation rotation."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        enabled: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._max_bytes = max(1024, int(max_bytes))
        self._enabled = enabled
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, event: UsageEvent) -> None:
        if not self._enabled():
            return
        normalized = event.normalized(self._clock)
        payload = json.dumps(asdict(normalized), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                self._path.replace(self._path.with_suffix(".jsonl.1"))
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(payload + "\n")


class CompositeUsageEventSink:
    def __init__(self, *sinks: UsageEventSink) -> None:
        self._sinks = sinks

    def record(self, event: UsageEvent) -> None:
        for sink in self._sinks:
            sink.record(event)


def summarize_usage(events: list[UsageEvent]) -> dict[tuple[str, str], dict[str, int]]:
    """Aggregate usage without coupling the domain to a dashboard or database."""

    summary: dict[tuple[str, str], dict[str, int]] = {}
    for raw in events:
        event = raw.normalized()
        key = (event.provider, event.model)
        totals = summary.setdefault(key, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        totals["requests"] += 1
        totals["input_tokens"] += event.input_tokens
        totals["output_tokens"] += event.output_tokens
    return summary
