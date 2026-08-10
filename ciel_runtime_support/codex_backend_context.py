"""Codex backend and provider-native passthrough bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from .codex_router import CodexChannelContextPorts, CodexChannelContextProjector
from .openai_chat_passthrough import OpenAIChatPassthrough, OpenAIChatPassthroughPorts
from .provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)
from .router_http import (
    CodexBackendHttpAdapter,
    CodexBackendRequestPorts,
    CodexBackendRetryPorts,
    CodexRoutedHeaderPolicy,
)
from .upstream_error_policy import retryable_exception


@dataclass(frozen=True, slots=True)
class CodexBackendChannelPorts:
    responses_to_anthropic: Callable[..., dict[str, Any]]
    inject_pending: Callable[..., dict[str, Any]]
    inject_tool_results: Callable[..., dict[str, Any]]
    content_to_text: Callable[..., str]
    begin_delivery: Callable[..., Any]
    mark_delivery_success: Callable[..., Any]
    commit_delivery: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CodexBackendTransportPorts:
    upstream_base: str
    decorate_headers: Callable[..., dict[str, str]]
    urlopen: Callable[..., Any]
    timeout_seconds: Callable[..., float]
    read_preamble: Callable[..., Any]
    retry_wait_seconds: Callable[..., float]
    log: Callable[..., Any]
    publish_event: Callable[..., Any]
    sleep: Callable[[float], None]
    env_get: Callable[..., str | None]


@dataclass(frozen=True, slots=True)
class CodexBackendReplayPorts:
    """Durable upstream verdicts about replayed turns."""

    rejected_reasoning_contains: Callable[[str], bool] = lambda _sealed: False
    rejected_reasoning_record: Callable[[str], Any] = lambda _sealed: None
    estimate_tokens: Callable[[Any], int] = lambda _body: 0
    compact_responses: Callable[..., dict[str, Any]] = lambda body, _budget, **_kw: body


@dataclass(frozen=True, slots=True)
class ProviderPassthroughProjectionPorts:
    provider_headers: Callable[..., dict[str, str]]
    chat_headers: Callable[..., dict[str, str]]
    responses_headers: Callable[..., dict[str, str]]
    upstream_model: Callable[..., str]
    resolve_model: Callable[..., str]
    apply_request_policy: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ProviderPassthroughTransportPorts:
    upstream_base: Callable[..., str]
    join_url: Callable[[str, str], str]
    urlopen: Callable[..., Any]
    timeout_seconds: Callable[..., float]
    copy_response_headers: Callable[..., None]
    write_activity: Callable[..., None]


@dataclass(frozen=True, slots=True)
class CodexBackendContext:
    channel: CodexBackendChannelPorts
    transport: CodexBackendTransportPorts
    provider_projection: ProviderPassthroughProjectionPorts
    provider_transport: ProviderPassthroughTransportPorts
    replay: CodexBackendReplayPorts = CodexBackendReplayPorts()

    def routed_headers(
        self,
        pcfg: dict[str, Any],
        inbound_headers: Any | None = None,
    ) -> dict[str, str]:
        del pcfg
        return CodexRoutedHeaderPolicy(
            decorate=self.transport.decorate_headers
        ).project(inbound_headers)

    @staticmethod
    def routed_auth_error_message(message: str) -> str:
        low = str(message or "").lower()
        markers = (
            "api.responses.write",
            "insufficient permissions",
            "unauthorized",
        )
        if not any(marker in low for marker in markers):
            return message
        guidance = (
            " Codex routed is expected to forward Codex CLI native auth to the "
            "ChatGPT Codex backend. If this mentions api.responses.write, the "
            "request is still using the OpenAI Platform /v1 endpoint; upgrade "
            "ciel-runtime and relaunch Codex routed so the local base URL is "
            "/backend-api/codex."
        )
        return f"{message}{guidance}"

    def project_channel_context(
        self, body: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return CodexChannelContextProjector(
            CodexChannelContextPorts(
                self.channel.responses_to_anthropic,
                self.channel.inject_pending,
                self.channel.inject_tool_results,
                self.channel.content_to_text,
            )
        ).project(body)

    @staticmethod
    def responses_input_as_list(value: Any) -> list[Any]:
        return CodexChannelContextProjector.input_items(value)

    @staticmethod
    def copy_response_headers(
        handler: BaseHTTPRequestHandler,
        headers: Any,
    ) -> None:
        CodexBackendHttpAdapter.copy_response_headers(handler, headers)

    def capacity_retry_limit(self) -> int:
        raw = str(
            self.transport.env_get("CIEL_RUNTIME_CODEX_CAPACITY_RETRIES") or "3"
        ).strip()
        try:
            return max(0, min(10, int(raw)))
        except ValueError:
            return 3

    def transport_retry_limit(self) -> int:
        raw = str(
            self.transport.env_get("CIEL_RUNTIME_CODEX_TRANSPORT_RETRIES") or "2"
        ).strip()
        try:
            return max(0, min(5, int(raw)))
        except ValueError:
            return 2

    def backend_adapter(self) -> CodexBackendHttpAdapter:
        return CodexBackendHttpAdapter(
            self.transport.upstream_base,
            CodexBackendRequestPorts(
                self.project_channel_context,
                self.channel.begin_delivery,
                self.routed_headers,
                self.transport.urlopen,
                self.transport.timeout_seconds,
                self.transport_retry_limit,
                retryable_exception,
            ),
            CodexBackendRetryPorts(
                self.capacity_retry_limit,
                self.transport.read_preamble,
                self.transport.retry_wait_seconds,
                self.transport.log,
                self.transport.publish_event,
                self.transport.sleep,
                rejected_reasoning_contains=self.replay.rejected_reasoning_contains,
                rejected_reasoning_record=self.replay.rejected_reasoning_record,
                estimate_tokens=self.replay.estimate_tokens,
                compact_responses=self.replay.compact_responses,
            ),
        )

    def provider_responses_headers(
        self,
        provider: str,
        pcfg: dict[str, Any],
        inbound_headers: Any | None = None,
    ) -> dict[str, str]:
        headers = self.provider_projection.provider_headers(
            provider,
            pcfg,
            inbound_headers,
            "openai_responses",
        )
        if inbound_headers is None:
            headers = {
                name: value
                for name, value in headers.items()
                if str(name).casefold() != "anthropic-version"
            }
        return headers

    def provider_chat_headers(
        self,
        provider: str,
        pcfg: dict[str, Any],
        inbound_headers: Any | None = None,
    ) -> dict[str, str]:
        return self.provider_projection.provider_headers(
            provider,
            pcfg,
            inbound_headers,
            "openai_chat",
        )

    def normalize_model(
        self,
        provider: str,
        pcfg: dict[str, Any],
        model: Any,
    ) -> str:
        return self.provider_projection.upstream_model(
            provider,
            pcfg,
            self.provider_projection.resolve_model(provider, pcfg, model),
        )

    def normalize_request(
        self,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self.provider_projection.apply_request_policy(
            provider, pcfg, dict(body)
        )

    def chat_passthrough(self) -> OpenAIChatPassthrough:
        return OpenAIChatPassthrough(
            OpenAIChatPassthroughPorts(
                normalize_model=self.normalize_model,
                normalize_request=self.normalize_request,
                upstream_base=self.provider_transport.upstream_base,
                join_url=self.provider_transport.join_url,
                headers=self.provider_projection.chat_headers,
                urlopen=self.provider_transport.urlopen,
                timeout_seconds=self.provider_transport.timeout_seconds,
                copy_response_headers=self.provider_transport.copy_response_headers,
            )
        )

    def forward_provider_chat(self, *args: Any, **kwargs: Any) -> None:
        self.chat_passthrough().forward(*args, **kwargs)

    def responses_passthrough(self) -> ProviderResponsesPassthrough:
        return ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=self.project_channel_context,
                begin_channel_delivery=self.channel.begin_delivery,
                normalize_model=self.normalize_model,
                normalize_request=self.normalize_request,
                upstream_base=self.provider_transport.upstream_base,
                join_url=self.provider_transport.join_url,
                headers=self.provider_projection.responses_headers,
                urlopen=self.provider_transport.urlopen,
                timeout_seconds=self.provider_transport.timeout_seconds,
                copy_response_headers=self.provider_transport.copy_response_headers,
                record_usage=lambda provider, model, usage: self.provider_transport.write_activity(
                    "success",
                    provider,
                    model,
                    protocol="openai_responses",
                    **usage,
                ),
                log=self.transport.log,
            )
        )

    def forward_provider_responses(self, *args: Any, **kwargs: Any) -> Any:
        return self.responses_passthrough().forward(*args, **kwargs)

    def upstream_url(self, request_path: str, query: str = "") -> str:
        return self.backend_adapter().upstream_url(request_path, query)

    def forward_json(self, *args: Any, **kwargs: Any) -> Any:
        return self.backend_adapter().forward_json(*args, **kwargs)

    def forward_get(self, *args: Any, **kwargs: Any) -> None:
        self.backend_adapter().forward_get(*args, **kwargs)

    def forward_responses(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        delivery_body = self.forward_json(
            handler,
            provider,
            pcfg,
            body,
            mutate_responses=True,
        )
        if delivery_body is None:
            return
        self.channel.mark_delivery_success(handler, "codex_responses_proxy")
        self.channel.commit_delivery(delivery_body, handler)


@dataclass(frozen=True, slots=True)
class CodexBackendCompatibilityApi:
    context: Callable[[], CodexBackendContext]

    def routed_headers(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().routed_headers(*args, **kwargs)

    def routed_auth_error_message(self, message: str) -> str:
        return self.context().routed_auth_error_message(message)

    def project_channel_context(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().project_channel_context(*args, **kwargs)

    def responses_input_as_list(self, value: Any) -> list[Any]:
        return self.context().responses_input_as_list(value)

    def copy_response_headers(self, *args: Any, **kwargs: Any) -> None:
        self.context().copy_response_headers(*args, **kwargs)

    def backend_adapter(self) -> CodexBackendHttpAdapter:
        return self.context().backend_adapter()

    def provider_responses_headers(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().provider_responses_headers(*args, **kwargs)

    def provider_chat_headers(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().provider_chat_headers(*args, **kwargs)

    def chat_passthrough(self) -> OpenAIChatPassthrough:
        return self.context().chat_passthrough()

    def forward_provider_chat(self, *args: Any, **kwargs: Any) -> None:
        self.context().forward_provider_chat(*args, **kwargs)

    def responses_passthrough(self) -> ProviderResponsesPassthrough:
        return self.context().responses_passthrough()

    def forward_provider_responses(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().forward_provider_responses(*args, **kwargs)

    def upstream_url(self, *args: Any, **kwargs: Any) -> str:
        return self.context().upstream_url(*args, **kwargs)

    def forward_json(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().forward_json(*args, **kwargs)

    def capacity_retry_limit(self) -> int:
        return self.context().capacity_retry_limit()

    def transport_retry_limit(self) -> int:
        return self.context().transport_retry_limit()

    def forward_get(self, *args: Any, **kwargs: Any) -> None:
        self.context().forward_get(*args, **kwargs)

    def forward_responses(self, *args: Any, **kwargs: Any) -> None:
        self.context().forward_responses(*args, **kwargs)


__all__ = [
    "CodexBackendChannelPorts",
    "CodexBackendCompatibilityApi",
    "CodexBackendContext",
    "CodexBackendReplayPorts",
    "CodexBackendTransportPorts",
    "ProviderPassthroughProjectionPorts",
    "ProviderPassthroughTransportPorts",
]
