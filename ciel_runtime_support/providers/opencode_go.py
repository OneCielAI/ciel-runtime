"""OpenCode Go provider adapter."""

from dataclasses import dataclass, field

from .base import provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS
from .opencode import OpenCodeProviderAdapter


@dataclass(frozen=True)
class OpenCodeGoProviderAdapter(OpenCodeProviderAdapter):
    name: str = "opencode-go"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode-go"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "qwen3.6-plus",
            custom_models=("qwen3.6-plus",),
            native_compat=True,
            context_window=1048576,
            max_output_tokens=8192,
            context_reserve_tokens=8192,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ip_family="ipv6-preferred",
            haiku_model="qwen3.5-plus",
            subagent_model="qwen3.6-plus",
            model_endpoints={},
        )
    )
    api_key_display_name_value: str = "OpenCode Go"
    api_key_launch_error_value: str = (
        "Launch blocked: OpenCode Go requires a OpenCode Go API key."
    )


__all__ = ["OpenCodeGoProviderAdapter"]
