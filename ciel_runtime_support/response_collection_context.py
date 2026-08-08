"""Single-response collection bounded context for provider protocols."""

from __future__ import annotations

import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from .runaway_output_guard import (
    RunawayOutputPolicy,
    policy_from_env,
    trim_runaway_message_content,
)
from .ollama_thinking import INTERNAL_REASONING_EFFORT_KEY
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
class ResponseCollectionStreamPorts:
    """Optional streaming reader so a loop is cut while it is generated."""

    ollama_stream: Callable[..., dict[str, Any]] | None = None
    log: Callable[..., None] = lambda _level, _message: None


@dataclass(frozen=True, slots=True)
class ResponseCollectionContext:
    shared: ResponseCollectionServices
    anthropic: AnthropicCollectionServices
    strategies: ResponseCollectionStrategyPorts
    routing: ResponseCollectionRoutingPorts
    stream: ResponseCollectionStreamPorts = ResponseCollectionStreamPorts()

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
            stream_collect=self.stream.ollama_stream,
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
        return self.collect_without_runaway(
            collector, handler, provider, pcfg, body, upstream_model
        )

    def collect_without_runaway(
        self,
        collector: Callable[..., dict[str, Any]],
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
        upstream_model: str,
    ) -> dict[str, Any]:
        """Re-issue a looped turn instead of handing the loop to the client.

        Nothing has been written to the client at this point and no tool call
        has been executed, so a discarded attempt has no side effects -- unlike
        the streaming paths, this one can simply ask again. Sampling is
        stochastic (DeepSeek ships ``do_sample: true, temperature: 1.0``), so a
        plain retry is a genuinely different draw; the ladder then lowers
        reasoning effort, which is DeepSeek's own advice for this failure.
        """

        policy = policy_from_env(os.environ.get)
        ladder = self.effort_ladder(policy)
        attempt_body = body
        message: dict[str, Any] = {}
        trimmed: Any = None
        for attempt, _effort in enumerate(ladder):
            message = collector(handler, provider, pcfg, attempt_body)
            trimmed, verdict = trim_runaway_message_content(
                message.get("content") if isinstance(message, dict) else None, policy
            )
            if verdict is None:
                return message
            last_attempt = attempt + 1 >= len(ladder)
            self.stream.log(
                "WARN",
                f"collect_runaway_repetition provider={provider} model={upstream_model} "
                f"attempt={attempt + 1}/{len(ladder)} retry={not last_attempt} "
                f"{verdict.log_fields()}",
            )
            if last_attempt:
                break
            attempt_body = self.with_reasoning_effort(body, ladder[attempt + 1])
        return {**message, "content": trimmed, "stop_reason": "max_tokens"}

    @staticmethod
    def effort_ladder(policy: RunawayOutputPolicy) -> list[str | None]:
        """Attempts to make, and the reasoning effort each one asks for."""

        raw = os.environ.get("CIEL_RUNTIME_RUNAWAY_RETRIES")
        try:
            retries = int(str(raw).strip())
        except (TypeError, ValueError):
            retries = 2
        if not policy.enabled or not policy.recover:
            retries = 0
        retries = max(0, min(4, retries))
        # The loop happens inside reasoning, so lowering effort is the lever
        # that changes the odds; "low" turns thinking off for DeepSeek entirely.
        return ([None] + ["high", "low", "low", "low"])[: retries + 1]

    @staticmethod
    def with_reasoning_effort(body: dict[str, Any], effort: str | None) -> dict[str, Any]:
        """Ask the next attempt for less reasoning via the internal effort hint.

        Both ``OllamaThinkingPolicy`` and the DeepSeek adapter already read this
        metadata key, so one hint covers every collected protocol.
        """

        if not effort or not isinstance(body, dict):
            return body
        metadata = body.get("metadata")
        projected = dict(metadata) if isinstance(metadata, dict) else {}
        projected[INTERNAL_REASONING_EFFORT_KEY] = effort
        return {**body, "metadata": projected}

    @staticmethod
    def guard_runaway(message: dict[str, Any]) -> dict[str, Any]:
        """Trim a repetition loop out of one collected message.

        Kept as the single-shot form used by callers that cannot retry.
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
    "ResponseCollectionStreamPorts",
    "ResponseCollectionStrategyPorts",
]
