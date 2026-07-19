"""Application clients for Advisor review and refinement provider calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class AdvisorClientPolicy:
    model_enabled: Callable[[dict[str, Any]], str]
    provider_supported: Callable[[str], bool]
    upstream_model: Callable[[str, str], str]
    provider_kind: Callable[..., str]
    request: Callable[..., dict[str, Any]]
    endpoint: Callable[[str, dict[str, Any]], str]
    response_text: Callable[[str, Any], str]


@dataclass(frozen=True, slots=True)
class AdvisorClientIO:
    apply_rate_limit: Callable[..., None]
    write_activity: Callable[..., None]
    estimate_tokens: Callable[[Any], int]
    post_json: Callable[..., Any]
    headers: Callable[..., dict[str, str]]
    provider_timeout: Callable[[dict[str, Any]], float]
    ollama_timeout: Callable[[dict[str, Any]], float]
    log: Callable[[str, str], None]


class AdvisorClient:
    def __init__(self, policy: AdvisorClientPolicy, io: AdvisorClientIO) -> None:
        self.policy = policy
        self.io = io

    def review(
        self,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        focus: str = "",
        inbound_headers: Any | None = None,
        *,
        allow_rate_limit_wait: bool = True,
        retry_rate_limits: bool = True,
        raise_errors: bool = False,
    ) -> str:
        advisor_model = self.policy.model_enabled(config)
        if not advisor_model or not self.policy.provider_supported(provider):
            return ""
        upstream_model = self.policy.upstream_model(provider, advisor_model)
        request_body = self.policy.request(
            provider, advisor_model, body, config, focus_override=focus
        )
        try:
            if allow_rate_limit_wait:
                self.io.apply_rate_limit(provider, config, upstream_model)
            self.io.write_activity(
                "advisor",
                provider,
                upstream_model,
                tokens=self.io.estimate_tokens(request_body),
            )
            kind = self.policy.provider_kind(provider)
            headers = (
                self.io.headers(provider, config, inbound_headers)
                if kind == "anthropic"
                else self.io.headers(provider, config)
            )
            timeout = (
                self.io.ollama_timeout(config)
                if kind == "ollama"
                else self.io.provider_timeout(config)
            )
            data = self.io.post_json(
                self.policy.endpoint(provider, config),
                request_body,
                headers,
                timeout,
                provider,
                config,
                upstream_model,
                None,
                retry_rate_limits=retry_rate_limits,
            )
            text = self.policy.response_text(provider, data)
            if text:
                self.io.log(
                    "INFO",
                    f"advisor_feedback provider={provider} "
                    f"advisor_model={upstream_model} chars={len(text)}",
                )
            return text
        except Exception as exc:
            self.io.log(
                "WARN",
                f"advisor_request_failed provider={provider} "
                f"advisor_model={upstream_model} "
                f"error={type(exc).__name__}: {exc}",
            )
            self.io.write_activity(
                "advisor_error",
                provider,
                upstream_model,
                error=type(exc).__name__,
            )
            if raise_errors:
                raise
            return ""


@dataclass(frozen=True, slots=True)
class ProviderChatPolicy:
    normalize_thinking: Callable[..., dict[str, Any]]
    normalize_tool_choice: Callable[..., dict[str, Any]]
    provider_kind: Callable[..., str]
    upstream_model: Callable[[str, dict[str, Any], str], str]
    ollama_request: Callable[..., dict[str, Any]]
    openai_request: Callable[..., dict[str, Any]]
    ollama_response: Callable[..., dict[str, Any]]
    openai_response: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ProviderChatIO:
    apply_rate_limit: Callable[..., None]
    post_json: Callable[..., Any]
    join_url: Callable[[str, str], str]
    request_base: Callable[[str, dict[str, Any]], str]
    headers: Callable[..., dict[str, str]]
    provider_timeout: Callable[[dict[str, Any]], float]
    ollama_timeout: Callable[[dict[str, Any]], float]


class ProviderChatExecutor:
    def __init__(self, policy: ProviderChatPolicy, io: ProviderChatIO) -> None:
        self.policy = policy
        self.io = io

    def execute(
        self,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        body = self.policy.normalize_thinking(provider, config, body)
        body = self.policy.normalize_tool_choice(provider, config, body)
        transport = self.policy.provider_kind(provider, config)
        if transport == "ollama":
            request = self.policy.ollama_request(
                model, body, config, stream=False, provider=provider
            )
            self.io.apply_rate_limit(provider, config, model)
            data = self.io.post_json(
                self.io.join_url(str(config.get("base_url") or "").rstrip("/"), "/api/chat"),
                request,
                self.io.headers(provider, config),
                self.io.ollama_timeout(config),
                provider,
                config,
                model,
                None,
            )
            return self.policy.ollama_response(data, model, source_body=body)
        if transport == "openai-compatible":
            upstream_model = self.policy.upstream_model(provider, config, model)
            request = self.policy.openai_request(
                provider, upstream_model, body, config, stream=False
            )
            self.io.apply_rate_limit(provider, config, upstream_model)
            data = self.io.post_json(
                self.io.join_url(
                    self.io.request_base(provider, config), "/v1/chat/completions"
                ),
                request,
                self.io.headers(provider, config),
                self.io.provider_timeout(config),
                provider,
                config,
                upstream_model,
                None,
            )
            return self.policy.openai_response(data, upstream_model, source_body=body)
        raise RuntimeError(
            f"Advisor refinement is not supported for provider {provider}"
        )
