"""Per-credential rate-limit cooldown policy and application service."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import rate_limit_policy
from .rate_limit_repository import RateLimitRepository


RATE_LIMIT_RESET_HEADER_NAMES = (
    "x-ratelimit-reset-requests",
    "x-rate-limit-reset-requests",
    "ratelimit-reset",
    "rate-limit-reset",
    "x-ratelimit-reset",
    "x-rate-limit-reset",
)
API_KEY_COOLDOWN_MAX_SECONDS = 90_000.0
API_KEY_COOLDOWN_DEFAULT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class ApiKeyCooldownPorts:
    repository: RateLimitRepository
    rotation_name: Callable[[str, dict[str, Any]], str]
    config_keys: Callable[[str, dict[str, Any]], list[str]]
    meaningful_key: Callable[[str], bool]
    log: Callable[[str, str], None]
    now: Callable[[], float] = time.time


@dataclass(frozen=True, slots=True)
class ApiKeyCooldownService:
    ports: ApiKeyCooldownPorts

    def state_key(self, provider: str, config: dict[str, Any], key: str) -> str:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]
        return f"{self.ports.rotation_name(provider, config)}:__key__:{digest}"

    @staticmethod
    def reset_seconds(headers: Any) -> float:
        reset = rate_limit_policy.reset_seconds(
            rate_limit_policy.first_header(headers, list(RATE_LIMIT_RESET_HEADER_NAMES))
        )
        if reset is None or reset <= 0:
            reset = rate_limit_policy.retry_after_seconds(
                rate_limit_policy.first_header(headers, ["Retry-After", "retry-after"])
            )
        if reset is None or reset <= 0:
            reset = API_KEY_COOLDOWN_DEFAULT_SECONDS
        return max(1.0, min(float(reset), API_KEY_COOLDOWN_MAX_SECONDS))

    def register(
        self,
        provider: str,
        config: dict[str, Any],
        key: str,
        headers: Any,
    ) -> float:
        if not self.ports.meaningful_key(key):
            return 0.0
        reset = self.reset_seconds(headers)
        state_key = self.state_key(provider, config, key)
        self.ports.repository.register_cooldown(state_key, reset)
        self.ports.log(
            "WARN",
            f"api_key_cooldown provider={provider} "
            f"key_hash={state_key.rsplit(':', 1)[-1]} rest={reset:.0f}s",
        )
        return reset

    def cooldown_until(self, provider: str, config: dict[str, Any], key: str) -> float:
        if not self.ports.meaningful_key(key):
            return 0.0
        return self.ports.repository.cooldown_until(self.state_key(provider, config, key))

    def live_key_count(self, provider: str, config: dict[str, Any]) -> int:
        keys = self.ports.config_keys(provider, config)
        if len(keys) <= 1:
            return len(keys)
        now = self.ports.now()
        return sum(1 for key in keys if self.cooldown_until(provider, config, key) <= now)

    def has_live_key(self, provider: str, config: dict[str, Any]) -> bool:
        return self.live_key_count(provider, config) > 0

    def reset_for_router_start(self) -> int:
        removed = self.ports.repository.reset_key_cooldowns()
        if removed:
            self.ports.log(
                "INFO",
                f"api_key_cooldown_reset_on_router_start removed={removed}",
            )
        return removed


__all__ = [
    "API_KEY_COOLDOWN_DEFAULT_SECONDS",
    "API_KEY_COOLDOWN_MAX_SECONDS",
    "ApiKeyCooldownPorts",
    "ApiKeyCooldownService",
    "RATE_LIMIT_RESET_HEADER_NAMES",
]
