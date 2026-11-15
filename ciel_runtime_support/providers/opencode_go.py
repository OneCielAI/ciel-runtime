"""OpenCode Go provider adapter."""

from dataclasses import dataclass, field
from typing import Mapping

from ..architecture import MessageProtocol

from .base import provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS
from .opencode import OpenCodeProviderAdapter
from .opencode_catalog import OPENCODE_GO_MODEL_PROTOCOLS


@dataclass(frozen=True)
class OpenCodeGoProviderAdapter(OpenCodeProviderAdapter):
    name: str = "opencode-go"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode-go"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            # Default chosen by the user on 2026-11-15, when qwen3.6-plus left Go.
            "kimi-k3",
            custom_models=("kimi-k3", *(model for model in OPENCODE_GO_MODEL_PROTOCOLS if model != "kimi-k3")),
            native_compat=True,
            # The zen Responses family answers "custom tools are not supported
            # on this endpoint" (live 2026-09-18); see the Zen adapter.
            responses_custom_tools_as_functions=True,
            context_window=1048576,
            max_output_tokens=8192,
            context_reserve_tokens=8192,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ip_family="ipv6-preferred",
            haiku_model="qwen3.5-plus",
            subagent_model="qwen3.6-plus",
        )
    )
    api_key_display_name_value: str = "OpenCode Go"

    def documented_model_protocols(self) -> Mapping[str, MessageProtocol]:
        return OPENCODE_GO_MODEL_PROTOCOLS

    api_key_launch_error_value: str = (
        "Launch blocked: OpenCode Go requires a OpenCode Go API key."
    )


__all__ = ["OpenCodeGoProviderAdapter"]
