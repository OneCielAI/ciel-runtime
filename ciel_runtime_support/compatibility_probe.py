"""Provider-aware API-key compatibility probe request builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol
import urllib.error

from .architecture import ProviderRequestPolicy


ProviderConfig = dict[str, Any]
RequestBody = dict[str, Any]


class CompatibilityApiKeyProbeError(Exception):
    def __init__(
        self,
        message: str,
        code: int | None = None,
        diagnosis: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnosis = diagnosis


class OllamaProbeRequest(Protocol):
    def __call__(
        self,
        model: str,
        body: RequestBody,
        config: ProviderConfig,
        *,
        stream: bool,
        provider: str,
    ) -> RequestBody: ...


class OpenAiProbeRequest(Protocol):
    def __call__(
        self,
        provider: str,
        model: str,
        body: RequestBody,
        config: ProviderConfig,
        *,
        stream: bool,
    ) -> RequestBody: ...


class ProbePost(Protocol):
    def __call__(
        self,
        url: str,
        body: RequestBody,
        *,
        headers: dict[str, str],
        timeout: float,
        provider: str,
        pcfg: ProviderConfig,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CompatibilityProbeProjectionPorts:
    normalize_thinking: Callable[
        [str, ProviderConfig, RequestBody],
        RequestBody,
    ]
    normalize_tool_choice: Callable[
        [str, ProviderConfig, RequestBody],
        RequestBody,
    ]
    resolve_model: Callable[[str, ProviderConfig, str], str]
    headers: Callable[[str, ProviderConfig], dict[str, str]]
    request_policy: Callable[
        [str, ProviderConfig],
        ProviderRequestPolicy,
    ]


@dataclass(frozen=True, slots=True)
class CompatibilityProbeRoutingPorts:
    ollama_request: OllamaProbeRequest
    openai_request: OpenAiProbeRequest
    endpoint: Callable[[str, ProviderConfig, str], str]
    opencode_endpoint_kind: Callable[[str, str, ProviderConfig], str]
    openai_router_enabled: Callable[[str, ProviderConfig], bool]
    request_base: Callable[[str, ProviderConfig], str]
    join_url: Callable[[str, str], str]
    ncp_model_id: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class CompatibilityProbeAnthropicPorts:
    cap_body: Callable[[str, ProviderConfig, RequestBody], RequestBody]
    apply_options: Callable[[str, ProviderConfig, RequestBody], RequestBody]
    resolve_tool_models: Callable[
        [str, ProviderConfig, RequestBody],
        RequestBody,
    ]
    native_compat_enabled: Callable[[str, ProviderConfig], bool]
    native_base_url: Callable[[str, ProviderConfig], str]


@dataclass(frozen=True, slots=True)
class CompatibilityApiKeyProbeBuilder:
    projection: CompatibilityProbeProjectionPorts
    routing: CompatibilityProbeRoutingPorts
    anthropic: CompatibilityProbeAnthropicPorts

    def build(
        self,
        provider: str,
        config: ProviderConfig,
        model: str,
        request_body: RequestBody,
    ) -> tuple[str, RequestBody, dict[str, str]]:
        body = self.projection.normalize_thinking(
            provider,
            config,
            request_body,
        )
        body = self.projection.normalize_tool_choice(provider, config, body)
        upstream_model = self.projection.resolve_model(provider, config, model)
        headers = self.projection.headers(provider, config)
        policy = self.projection.request_policy(provider, config)

        if policy.probe_strategy == "ollama":
            probe_body = self.routing.ollama_request(
                upstream_model,
                body,
                config,
                stream=False,
                provider=provider,
            )
            return (
                self.routing.endpoint(provider, config, "ollama_chat"),
                probe_body,
                headers,
            )

        if policy.probe_strategy == "opencode":
            endpoint_kind = self.routing.opencode_endpoint_kind(
                provider,
                upstream_model,
                config,
            )
            if endpoint_kind == "openai-chat":
                probe_body = self.routing.openai_request(
                    provider,
                    upstream_model,
                    body,
                    config,
                    stream=False,
                )
                url = self.routing.join_url(
                    self.routing.request_base(provider, config),
                    "/v1/chat/completions",
                )
                return url, probe_body, headers
            if endpoint_kind != "anthropic-messages":
                raise CompatibilityApiKeyProbeError(
                    f"model {upstream_model!r} uses unsupported endpoint family "
                    f"{endpoint_kind!r} for API-key probing"
                )

        if self.routing.openai_router_enabled(provider, config):
            if policy.model_alias_strategy == "ncp":
                upstream_model = self.routing.ncp_model_id(upstream_model)
            probe_body = self.routing.openai_request(
                provider,
                upstream_model,
                body,
                config,
                stream=False,
            )
            return (
                self.routing.endpoint(provider, config, "openai_chat"),
                probe_body,
                headers,
            )

        body = self.anthropic.cap_body(provider, config, body)
        body = self.anthropic.apply_options(provider, config, body)
        body = dict(body)
        body["model"] = upstream_model
        body = self.anthropic.resolve_tool_models(provider, config, body)
        if self.anthropic.native_compat_enabled(provider, config):
            base = self.anthropic.native_base_url(provider, config)
        else:
            base = self.routing.request_base(provider, config)
        return self.routing.join_url(base, "/v1/messages"), body, headers


@dataclass(frozen=True, slots=True)
class CompatibilityApiKeyProbeRunnerPorts:
    api_keys: Callable[[str, ProviderConfig], list[str]]
    mask_secret: Callable[[str], str]
    build_request: Callable[
        [str, ProviderConfig, str, RequestBody],
        tuple[str, RequestBody, dict[str, str]],
    ]
    post: ProbePost
    http_error_message: Callable[[urllib.error.HTTPError], str]
    failure_diagnosis: Callable[[str, int | None, str], str | None]


@dataclass(frozen=True, slots=True)
class CompatibilityApiKeyProbeRunner:
    ports: CompatibilityApiKeyProbeRunnerPorts

    @staticmethod
    def single_key_config(
        config: ProviderConfig,
        key: str,
    ) -> ProviderConfig:
        keyed = dict(config)
        keyed["api_key"] = key
        keyed["api_keys"] = []
        return keyed

    def run(
        self,
        provider: str,
        config: ProviderConfig,
        model: str,
        request_body: RequestBody,
        timeout: float,
    ) -> list[str]:
        keys = self.ports.api_keys(provider, config)
        if len(keys) <= 1:
            return []
        lines = [f"API key checks: running {len(keys)} configured keys"]
        for index, key in enumerate(keys, start=1):
            label = (
                f"API key {index}/{len(keys)} "
                f"({self.ports.mask_secret(key)})"
            )
            keyed_config = self.single_key_config(config, key)
            try:
                url, body, headers = self.ports.build_request(
                    provider,
                    keyed_config,
                    model,
                    request_body,
                )
                self.ports.post(
                    url,
                    body,
                    headers=headers,
                    timeout=timeout,
                    provider=provider,
                    pcfg=keyed_config,
                )
            except CompatibilityApiKeyProbeError:
                raise
            except urllib.error.HTTPError as exc:
                message = self.ports.http_error_message(exc)
                diagnosis = (
                    self.ports.failure_diagnosis(
                        provider,
                        exc.code,
                        message,
                    )
                    or ""
                )
                raise CompatibilityApiKeyProbeError(
                    f"{label}: {message}",
                    exc.code,
                    diagnosis,
                ) from exc
            except TimeoutError as exc:
                raise CompatibilityApiKeyProbeError(
                    f"{label}: timed out before the {timeout:g}s "
                    "API-key probe timeout"
                ) from exc
            except Exception as exc:
                raise CompatibilityApiKeyProbeError(
                    f"{label}: {type(exc).__name__}: {exc}"
                ) from exc
            lines.append(f"{label}: OK")
        return lines


__all__ = [
    "CompatibilityApiKeyProbeBuilder",
    "CompatibilityApiKeyProbeError",
    "CompatibilityApiKeyProbeRunner",
    "CompatibilityApiKeyProbeRunnerPorts",
    "CompatibilityProbeAnthropicPorts",
    "CompatibilityProbeProjectionPorts",
    "CompatibilityProbeRoutingPorts",
]
