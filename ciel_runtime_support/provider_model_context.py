"""Provider model context-capacity and output-budget bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .architecture import ProviderContextPolicy
from .provider_context import ProviderContextServices


@dataclass(frozen=True, slots=True)
class ProviderModelContextQueries:
    context_policy: Callable[[str, dict[str, Any]], ProviderContextPolicy]
    context_limit: Callable[[str, dict[str, Any]], int | None]
    positive_int: Callable[[Any], int | None]
    format_context: Callable[[int | None], str]


@dataclass(frozen=True, slots=True)
class ProviderModelContextAlgorithms:
    resolve_capacity: Callable[..., int | None]
    apply_capacity_cap: Callable[..., list[str]]
    resolve_small_output_cap: Callable[..., int | None]
    apply_output_token_cap: Callable[..., int | None]
    apply_output_context_cap: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class ProviderModelContext:
    services: ProviderContextServices
    queries: ProviderModelContextQueries
    algorithms: ProviderModelContextAlgorithms

    def capacity(self, provider: str, config: dict[str, Any]) -> int | None:
        return self.algorithms.resolve_capacity(
            provider,
            config,
            self.queries.context_policy(provider, config),
            self.services,
        )

    def cap_context(self, provider: str, config: dict[str, Any]) -> list[str]:
        return self.algorithms.apply_capacity_cap(
            config,
            self.capacity(provider, config),
            self.queries.context_policy(provider, config),
            positive_int=self.queries.positive_int,
        )

    def small_output_cap(self, context_window: int | None) -> int | None:
        return self.algorithms.resolve_small_output_cap(
            context_window, positive_int=self.queries.positive_int
        )

    def cap_output_tokens(
        self, provider: str, config: dict[str, Any], configured: int | None
    ) -> int | None:
        return self.algorithms.apply_output_token_cap(
            configured,
            self.queries.context_policy(provider, config),
            self.queries.context_limit(provider, config),
            positive_int=self.queries.positive_int,
        )

    def cap_output_settings(
        self, provider: str, config: dict[str, Any]
    ) -> list[str]:
        return self.algorithms.apply_output_context_cap(
            config,
            self.queries.context_policy(provider, config),
            self.queries.context_limit(provider, config),
            positive_int=self.queries.positive_int,
            format_context=self.queries.format_context,
        )


@dataclass(frozen=True, slots=True)
class ProviderModelContextCompatibilityApi:
    context: Callable[[], ProviderModelContext]

    def capacity(self, provider: str, config: dict[str, Any]) -> int | None:
        return self.context().capacity(provider, config)

    def cap_context(self, provider: str, config: dict[str, Any]) -> list[str]:
        return self.context().cap_context(provider, config)

    def small_output_cap(self, context_window: int | None) -> int | None:
        return self.context().small_output_cap(context_window)

    def cap_output_tokens(
        self, provider: str, config: dict[str, Any], configured: int | None
    ) -> int | None:
        return self.context().cap_output_tokens(provider, config, configured)

    def cap_output_settings(
        self, provider: str, config: dict[str, Any]
    ) -> list[str]:
        return self.context().cap_output_settings(provider, config)


__all__ = [
    "ProviderModelContext",
    "ProviderModelContextAlgorithms",
    "ProviderModelContextCompatibilityApi",
    "ProviderModelContextQueries",
]
