"""OpenCode Go provider adapter."""

from dataclasses import dataclass

from .constants import PROVIDER_DEFAULT_BASE_URLS
from .opencode import OpenCodeProviderAdapter


@dataclass(frozen=True)
class OpenCodeGoProviderAdapter(OpenCodeProviderAdapter):
    name: str = "opencode-go"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode-go"]
    api_key_display_name_value: str = "OpenCode Go"
    api_key_launch_error_value: str = (
        "Launch blocked: OpenCode Go requires a OpenCode Go API key."
    )


__all__ = ["OpenCodeGoProviderAdapter"]
