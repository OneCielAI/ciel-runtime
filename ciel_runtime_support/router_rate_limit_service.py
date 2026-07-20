"""Router-wide provider rate-limit application service."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from . import rate_limit_policy
from .provider_limits import (
    RateLimitApplyPolicy,
    RateLimitApplyServices,
    RateLimitBackoffPolicy,
    RateLimitBackoffServices,
    RateLimitLearningPolicy,
    RateLimitLearningServices,
    RateLimitStateStore,
    apply_rate_limit,
    learn_rate_limit_headers,
    register_rate_limit_backoff,
)
from .rate_limit_repository import RateLimitRepository


@dataclass(frozen=True, slots=True)
class RouterRateLimitPaths:
    config_dir: Path
    state_path: Path
    lock: Lock | RLock


@dataclass(frozen=True, slots=True)
class RouterRateLimitPorts:
    current_model_id: Callable[[str, dict[str, Any]], str]
    api_key_count: Callable[[str, dict[str, Any]], int]
    positive_int: Callable[[Any], int | None]
    log: Callable[[str, str], None]
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True, slots=True)
class RouterRateLimitService:
    paths: RouterRateLimitPaths
    repository: RateLimitRepository
    ports: RouterRateLimitPorts

    def legacy_key(self, provider: str, config: dict[str, Any], model: str | None) -> str:
        return f"{provider}:{model or self.ports.current_model_id(provider, config)}"

    def configured_rpm(self, provider: str, config: dict[str, Any]) -> int | None:
        del provider
        return rate_limit_policy.configured_rpm(config, self.ports.positive_int)

    def rpm(self, provider: str, config: dict[str, Any]) -> int | None:
        rpm = self.configured_rpm(provider, config)
        return rpm if rpm and rpm > 0 else None

    @staticmethod
    def key(provider: str, config: dict[str, Any], model: str | None = None) -> str:
        del config, model
        return f"{provider}:__global__"

    def state_entry(
        self, provider: str, config: dict[str, Any], model: str | None = None
    ) -> dict[str, Any]:
        return self.repository.entry(
            self.key(provider, config, model), self.legacy_key(provider, config, model)
        )

    def effective_rpm(
        self, provider: str, config: dict[str, Any], model: str | None = None
    ) -> int | None:
        return self.repository.effective_rpm(
            self.key(provider, config, model),
            self.legacy_key(provider, config, model),
            self.configured_rpm(provider, config),
        )

    @staticmethod
    def capacity(rpm: int) -> int:
        return rate_limit_policy.capacity(rpm)

    @staticmethod
    def recent(
        timestamps: Any, now: float, window: float, *, include_future: bool
    ) -> list[float]:
        return rate_limit_policy.recent_timestamps(
            timestamps, now, window, include_future=include_future
        )

    def usage(
        self, provider: str, config: dict[str, Any], model: str | None = None
    ) -> tuple[int, int | None]:
        return self.repository.usage(
            self.key(provider, config, model),
            self.legacy_key(provider, config, model),
            self.effective_rpm(provider, config, model),
            self.recent,
        )

    def record_usage(
        self,
        provider: str,
        config: dict[str, Any],
        model: str | None,
        rpm: int | None,
    ) -> tuple[int, int | None]:
        return self.repository.record_usage(
            self.key(provider, config, model),
            self.legacy_key(provider, config, model),
            rpm,
            self.recent,
        )

    def _state_store(self) -> RateLimitStateStore:
        return RateLimitStateStore(
            CONFIG_DIR=self.paths.config_dir,
            RATE_LIMIT_STATE_PATH=self.paths.state_path,
            _RATE_LIMIT_LOCK=self.paths.lock,
            router_log=self.ports.log,
        )

    def learn_headers(
        self, provider: str, config: dict[str, Any], model: str | None, headers: Any
    ) -> None:
        learn_rate_limit_headers(
            provider,
            config,
            model,
            headers,
            services=RateLimitLearningServices(
                state_store=self._state_store(),
                policy=RateLimitLearningPolicy(
                    current_upstream_model_id=self.ports.current_model_id,
                    first_header=rate_limit_policy.first_header,
                    first_int_in_header=rate_limit_policy.first_integer,
                    provider_api_key_count=self.ports.api_key_count,
                    rate_limit_reset_seconds=rate_limit_policy.reset_seconds,
                    router_rate_limit_configured_rpm=self.configured_rpm,
                    router_rate_limit_key=self.key,
                    router_rate_limit_recent=self.recent,
                ),
            ),
        )

    def register_backoff(
        self,
        provider: str,
        config: dict[str, Any],
        model: str | None,
        retry_after: str | None = None,
    ) -> float:
        return register_rate_limit_backoff(
            provider,
            config,
            model,
            retry_after,
            services=RateLimitBackoffServices(
                state_store=self._state_store(),
                policy=RateLimitBackoffPolicy(
                    current_upstream_model_id=self.ports.current_model_id,
                    parse_retry_after_seconds=rate_limit_policy.retry_after_seconds,
                    provider_api_key_count=self.ports.api_key_count,
                    router_rate_limit_capacity=self.capacity,
                    router_rate_limit_configured_rpm=self.configured_rpm,
                    router_rate_limit_effective_rpm=self.effective_rpm,
                    router_rate_limit_key=self.key,
                    router_rate_limit_recent=self.recent,
                ),
            ),
        )

    def apply(
        self, provider: str, config: dict[str, Any], model: str | None = None
    ) -> tuple[float, int, int | None]:
        return apply_rate_limit(
            provider,
            config,
            model,
            services=RateLimitApplyServices(
                state_store=self._state_store(),
                policy=RateLimitApplyPolicy(
                    current_upstream_model_id=self.ports.current_model_id,
                    provider_api_key_count=self.ports.api_key_count,
                    record_router_rate_usage=self.record_usage,
                    router_rate_limit_capacity=self.capacity,
                    router_rate_limit_effective_rpm=self.effective_rpm,
                    router_rate_limit_key=self.key,
                    router_rate_limit_recent=self.recent,
                    wait_for_router_rate_limit_penalty=self.wait_for_penalty,
                ),
            ),
        )

    def wait_for_penalty(
        self,
        provider: str,
        config: dict[str, Any],
        model: str | None,
        rpm: int | None,
    ) -> float:
        multi_key = self.ports.api_key_count(provider, config) > 1
        waited = 0.0
        while True:
            entry = self.state_entry(provider, config, model)
            now = self.ports.now()
            try:
                penalty_until = (
                    0.0 if multi_key else float(entry.get("penalty_until") or 0.0)
                )
            except Exception:
                penalty_until = 0.0
            wait = max(0.0, penalty_until - now)
            if wait <= 0.001:
                return waited
            sleep_for = min(wait, 10.0)
            self.ports.log(
                "INFO",
                f"rate_limit_penalty_wait provider={provider} model={model or ''} "
                f"rpm={rpm if rpm is not None else 'auto'} wait={wait:.2f}s "
                f"waited={waited:.2f}s",
            )
            self.ports.sleep(sleep_for)
            waited += sleep_for


@dataclass(frozen=True, slots=True)
class RouterRateLimitApi:
    """Explicit compatibility API for late-bound router rate-limit services."""

    service_factory: Callable[[], RouterRateLimitService]

    def legacy_key(self, provider: str, pcfg: dict[str, Any], model: str | None) -> str:
        return self.service_factory().legacy_key(provider, pcfg, model)

    def configured_rpm(self, provider: str, pcfg: dict[str, Any]) -> int | None:
        return self.service_factory().configured_rpm(provider, pcfg)

    def rpm(self, provider: str, pcfg: dict[str, Any]) -> int | None:
        return self.service_factory().rpm(provider, pcfg)

    def key(self, provider: str, pcfg: dict[str, Any], model: str | None = None) -> str:
        return self.service_factory().key(provider, pcfg, model)

    def state_entry(self, provider: str, pcfg: dict[str, Any], model: str | None = None) -> dict[str, Any]:
        return self.service_factory().state_entry(provider, pcfg, model)

    def effective_rpm(self, provider: str, pcfg: dict[str, Any], model: str | None = None) -> int | None:
        return self.service_factory().effective_rpm(provider, pcfg, model)

    def capacity(self, rpm: int) -> int:
        return self.service_factory().capacity(rpm)

    def recent(self, timestamps: Any, now: float, window: float, *, include_future: bool) -> list[float]:
        return self.service_factory().recent(timestamps, now, window, include_future=include_future)

    def usage(self, provider: str, pcfg: dict[str, Any], model: str | None = None) -> tuple[int, int | None]:
        return self.service_factory().usage(provider, pcfg, model)

    def record_usage(self, provider: str, pcfg: dict[str, Any], model: str | None, rpm: int | None) -> tuple[int, int | None]:
        return self.service_factory().record_usage(provider, pcfg, model, rpm)

    def learn_headers(self, provider: str, pcfg: dict[str, Any], model: str | None, headers: Any) -> None:
        self.service_factory().learn_headers(provider, pcfg, model, headers)

    def register_backoff(self, provider: str, pcfg: dict[str, Any], model: str | None, retry_after: str | None = None) -> float:
        return self.service_factory().register_backoff(provider, pcfg, model, retry_after)

    def apply(self, provider: str, pcfg: dict[str, Any], model: str | None = None) -> tuple[float, int, int | None]:
        return self.service_factory().apply(provider, pcfg, model)

    def wait_for_penalty(self, provider: str, pcfg: dict[str, Any], model: str | None, rpm: int | None) -> float:
        return self.service_factory().wait_for_penalty(provider, pcfg, model, rpm)


__all__ = [
    "RouterRateLimitApi",
    "RouterRateLimitPaths",
    "RouterRateLimitPorts",
    "RouterRateLimitService",
]
