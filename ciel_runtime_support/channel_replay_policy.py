"""Safety policy for replaying durable channel messages into an agent terminal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelReplaySafetyPolicy:
    now: Callable[[], float]
    replay_ttl_seconds: Callable[[], float]
    timestamp_seconds: Callable[[dict[str, Any]], float | None]
    is_web_chat: Callable[[dict[str, Any]], bool]
    # A launch-scoped input (a stream recovery continuation) that no longer
    # belongs to the running session; empty when it still does.
    scoped_skip_reason: Callable[[dict[str, Any]], str] = lambda _message: ""

    def skip_reason(self, message: dict[str, Any]) -> str:
        """Expire old browser commands independently of startup cursor state.

        Browser messages are durable history, but they are also executable input.
        A cursor or launch-hook failure must never turn old history back into a
        command after a process or machine restart.
        """

        scoped = self.scoped_skip_reason(message)
        if scoped:
            return scoped
        if not self.is_web_chat(message):
            return ""
        created_at = self.timestamp_seconds(message)
        if created_at is None:
            return ""
        ttl = max(1.0, float(self.replay_ttl_seconds()))
        age = self.now() - created_at
        if age > ttl:
            return "stale_web_chat_replay"
        if age < -300.0:
            return "future_web_chat_timestamp"
        return ""


__all__ = ["ChannelReplaySafetyPolicy"]
