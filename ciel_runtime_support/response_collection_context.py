"""Single-response collection bounded context for provider protocols."""

from __future__ import annotations

import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from .runaway_output_guard import policy_from_env, trim_runaway_message_content
from .response_collection import (
    AnthropicCollectionServices,
    ChatCollectionStrategy,
    ResponseCollectionServices,
    collect_anthropic_message_for_responses,
    collect_chat_message_for_responses,
)


@dataclass(frozen=True, slots=True)
class ResponseCollectionStrategyPorts:
    ollama_request: Callable[..., dict[str, Any]]
    ollama_decode: Callable[..., dict[str, Any]]
    ollama_timeout: Callable[..., float]
    openai_request: Callable[..., dict[str, Any]]
    openai_decode: Callable[..., dict[str, Any]]
    openai_timeout: Callable[..., float]
    upstream_model: Callable[..., str]


@dataclass(frozen=True, slots=True)
class ResponseCollectionRoutingPorts:
    resolve_model: Callable[..., str]
    select_protocol: Callable[..., str]
    provider_labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class ResponseCollectionContext:
    shared: ResponseCollectionServices
    anthropic: AnthropicCollectionServices
    strategies: ResponseCollectionStrategyPorts
    routing: ResponseCollectionRoutingPorts

    @staticmethod
    def identity_upstream_model(
        provider: str,
        pcfg: dict[str, Any],
        model: str,
    ) -> str:
        del provider, pcfg
        return model

    def build_ollama_request(
        self,
        provider: str,
        model: str,
        body: dict[str, Any],
        pcfg: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        return self.strategies.ollama_request(
            model,
            body,
            pcfg,
            stream=stream,
            provider=provider,
        )

    def collect_ollama(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        strategy = ChatCollectionStrategy(
            operation="ollama_chat",
            build_request=self.build_ollama_request,
            decode_response=self.strategies.ollama_decode,
            request_timeout_seconds=self.strategies.ollama_timeout,
            normalize_upstream_model=self.identity_upstream_model,
            skip_rate_limit_during_compatibility_test=True,
        )
        return collect_chat_message_for_responses(
            handler,
            provider,
            pcfg,
            body,
            strategy=strategy,
            services=self.shared,
        )

    def collect_openai_chat(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        strategy = ChatCollectionStrategy(
            operation="openai_chat",
            build_request=self.strategies.openai_request,
            decode_response=self.strategies.openai_decode,
            request_timeout_seconds=self.strategies.openai_timeout,
            normalize_upstream_model=self.strategies.upstream_model,
        )
        return collect_chat_message_for_responses(
            handler,
            provider,
            pcfg,
            body,
            strategy=strategy,
            services=self.shared,
        )

    def collect_anthropic(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return collect_anthropic_message_for_responses(
            handler,
            provider,
            pcfg,
            body,
            services=self.anthropic,
        )

    def collect(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        upstream_model = self.routing.resolve_model(
            provider, pcfg, body.get("model")
        )
        protocol = self.routing.select_protocol(
            provider,
            pcfg,
            "openai_responses",
            upstream_model,
        )
        collectors = {
            "ollama_chat": self.collect_ollama,
            "openai_chat": self.collect_openai_chat,
            "anthropic_messages": self.collect_anthropic,
        }
        collector = collectors.get(protocol)
        if collector is None:
            provider_label = self.routing.provider_labels.get(provider, provider)
            endpoint_family = protocol.replace("_", "-")
            raise RuntimeError(
                f"{provider_label} model {upstream_model!r} uses the "
                f"{endpoint_family} endpoint family. ciel-runtime currently routes "
                f"{provider_label} /v1/messages and /v1/chat/completions models."
            )
        return self.guard_runaway(collector(handler, provider, pcfg, body))

    @staticmethod
    def guard_runaway(message: dict[str, Any]) -> dict[str, Any]:
        """Cut a repetition loop out of a collected message before it is relayed.

        Collection is the Codex-facing path: there is no stream to abort, but
        the repeated block must not reach the client or the next request's
        replayed history.
        """

        if not isinstance(message, dict):
            return message
        content, verdict = trim_runaway_message_content(
            message.get("content"), policy_from_env(os.environ.get)
        )
        if verdict is None:
            return message
        return {**message, "content": content, "stop_reason": "max_tokens"}


@dataclass(frozen=True, slots=True)
class ResponseCollectionCompatibilityApi:
    context: Callable[[], ResponseCollectionContext]

    def services(self) -> ResponseCollectionServices:
        return self.context().shared

    def identity_upstream_model(self, *args: Any, **kwargs: Any) -> str:
        return self.context().identity_upstream_model(*args, **kwargs)

    def build_ollama_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().build_ollama_request(*args, **kwargs)

    def collect_ollama(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().collect_ollama(*args, **kwargs)

    def collect_openai_chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().collect_openai_chat(*args, **kwargs)

    def collect_anthropic(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().collect_anthropic(*args, **kwargs)

    def collect(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().collect(*args, **kwargs)


__all__ = [
    "ResponseCollectionCompatibilityApi",
    "ResponseCollectionContext",
    "ResponseCollectionRoutingPorts",
    "ResponseCollectionStrategyPorts",
]
