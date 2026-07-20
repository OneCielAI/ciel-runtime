"""Runtime-specific provider endpoint selection policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderLaunchEndpointGroups:
    native_runtimes: frozenset[str]
    auto_detect: frozenset[str]
    claude_anthropic: frozenset[str]
    codex_openai: frozenset[str]
    model_specific: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProviderLaunchEndpointQueries:
    detect_native_compat: Callable[
        [str, dict[str, Any]], tuple[bool | None, str]
    ]
    endpoint_kind: Callable[[str, str, dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ProviderLaunchEndpointPolicy:
    groups: ProviderLaunchEndpointGroups
    query: ProviderLaunchEndpointQueries

    def preferred_native_compat(
        self,
        runtime: str,
        provider: str,
        config: dict[str, Any],
    ) -> tuple[bool | None, str]:
        runtime = str(runtime or "").strip().casefold()
        if provider in self.groups.native_runtimes:
            return None, ""
        if runtime == "claude":
            if provider in self.groups.auto_detect:
                return self.query.detect_native_compat(provider, config)
            if provider in self.groups.claude_anthropic:
                return (
                    True,
                    "Claude Code prefers the provider's Anthropic Messages "
                    "compatible endpoint",
                )
            if provider in self.groups.model_specific:
                endpoint = self.query.endpoint_kind(
                    provider,
                    str(config.get("current_model") or ""),
                    config,
                )
                if endpoint == "anthropic-messages":
                    return (
                        True,
                        "Claude Code prefers the model's Anthropic Messages "
                        "endpoint",
                    )
                if endpoint == "openai-chat":
                    return False, "selected model uses an OpenAI Chat endpoint"
            return None, ""
        if runtime in {"codex", "codex-app-server"}:
            if provider in self.groups.model_specific:
                endpoint = self.query.endpoint_kind(
                    provider,
                    str(config.get("current_model") or ""),
                    config,
                )
                if endpoint == "openai-chat":
                    return (
                        False,
                        "Codex prefers the model's OpenAI Chat compatible "
                        "endpoint",
                    )
                return None, ""
            if provider in self.groups.codex_openai:
                return (
                    False,
                    "Codex prefers OpenAI Chat compatible upstream routing",
                )
        return None, ""
