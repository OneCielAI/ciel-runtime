"""Prelaunch menu panel composition bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .architecture import ProviderUiPolicy
from .model_panel import (
    ModelPanelCatalog,
    ModelPanelPresentation,
    ModelPanelServices,
    advisor_model_panel_rows as project_advisor_model_panel_rows,
    model_panel_rows as project_model_panel_rows,
)
from .prelaunch_panel_projection import (
    ConfigurationPanelPorts,
    ConfigurationPanelProjection,
    MainMenuProjection,
    MainMenuProjectionPorts,
    ProviderPanelConstants,
    ProviderPanelPorts,
    ProviderPanelProjection,
)
from .web_endpoints import (
    update_web_backend_config,
    web_backend_panel_rows as project_web_backend_panel_rows,
)

PanelRows = tuple[list[str], list[str]]


@dataclass(frozen=True, slots=True)
class MainMenuPanelPorts:
    languages: Mapping[str, str]
    ui_text: Callable[..., str]
    compact_text: Callable[[Any, int], str]
    provider_label: Callable[[str, dict[str, Any]], str]
    stored_api_key_mask: Callable[[dict[str, Any]], str]
    llm_options_status: Callable[[str, dict[str, Any]], str]
    log_level_status: Callable[[dict[str, Any]], str]
    supports_runtime: Callable[[str, str], bool]
    provider_family: Callable[[str], str]
    provider_ui_policy: Callable[[str, dict[str, Any]], ProviderUiPolicy]


@dataclass(frozen=True, slots=True)
class WebBackendPanelPorts:
    router_port: int
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ProviderChoicePanelPorts:
    constants: ProviderPanelConstants
    anthropic_routed: Callable[..., bool]
    agy_routed: Callable[..., bool]
    codex_routed: Callable[..., bool]
    has_api_key: Callable[[str, dict[str, Any]], bool]
    compact_text: Callable[[Any, int], str]


@dataclass(frozen=True, slots=True)
class ConfigurationPanelContextPorts:
    languages: Mapping[str, str]
    log_level_names: Sequence[str]
    log_level_name: Callable[[dict[str, Any]], str]
    log_level_status: Callable[[dict[str, Any]], str]
    ui_text: Callable[..., str]
    compact_text: Callable[[Any, int], str]
    default_base_url: Callable[[str], str]
    api_key_count: Callable[[dict[str, Any]], int]
    platform_name: str


@dataclass(frozen=True, slots=True)
class ModelPanelCatalogPorts:
    alias_for: Callable[..., str]
    cached_or_configured_model_ids: Callable[..., list[str]]
    read_model_info_cache: Callable[..., dict[str, Any]]
    read_model_list_cache: Callable[..., Any]
    unique_model_ids: Callable[..., list[str]]
    upstream_model_ids: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class ModelPanelPresentationPorts:
    advisor_model_badge: Callable[..., str]
    advisor_panel_notice: Callable[..., str]
    format_context_tokens: Callable[[int | None], str]
    format_parameter_count: Callable[[Any], str]
    model_panel_badge: Callable[..., str]
    normalize_model_id: Callable[..., str]
    positive_int: Callable[[Any], int | None]


@dataclass(frozen=True, slots=True)
class AuthPanelPorts:
    kimi_oauth_configured: Callable[[], bool]
    copilot_panel_rows: Callable[[str], PanelRows | None]


@dataclass(frozen=True, slots=True)
class PrelaunchPanelContext:
    main: MainMenuPanelPorts
    web: WebBackendPanelPorts
    provider: ProviderChoicePanelPorts
    configuration: ConfigurationPanelContextPorts
    model_catalog: ModelPanelCatalogPorts
    model_presentation: ModelPanelPresentationPorts
    auth: AuthPanelPorts

    def main_menu_projection(self) -> MainMenuProjection:
        ports = self.main
        return MainMenuProjection(
            MainMenuProjectionPorts(
                languages=ports.languages,
                ui_text=ports.ui_text,
                compact_text=ports.compact_text,
                provider_label=ports.provider_label,
                stored_api_key_mask=ports.stored_api_key_mask,
                llm_options_status=ports.llm_options_status,
                log_level_status=ports.log_level_status,
                supports_runtime=ports.supports_runtime,
                provider_family=ports.provider_family,
                provider_ui_policy=ports.provider_ui_policy,
            )
        )

    def main_menu_rows(
        self,
        config: dict[str, Any],
        provider: str,
        provider_config: dict[str, Any],
        language: str,
    ) -> list[str]:
        projected = dict(config)
        projected["_effective_web_port"] = self.web.router_port
        return self.main_menu_projection().rows(
            projected, provider, provider_config, language
        )

    def web_backend_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return project_web_backend_panel_rows(config, self.web.router_port)

    def set_web_backend_config(self, key: str, value: Any) -> list[str]:
        config = self.web.load_config()
        lines = update_web_backend_config(
            config, key, value, self.web.router_port
        )
        self.web.save_config(config)
        self.web.clear_model_cache()
        return lines

    def provider_panel_projection(self) -> ProviderPanelProjection:
        return ProviderPanelProjection(
            self.provider.constants,
            ProviderPanelPorts(
                anthropic_routed=self.provider.anthropic_routed,
                agy_routed=self.provider.agy_routed,
                codex_routed=self.provider.codex_routed,
                has_api_key=self.provider.has_api_key,
                compact_text=self.provider.compact_text,
            ),
        )

    def provider_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.provider_panel_projection().rows(config)

    def configuration_panel_projection(self) -> ConfigurationPanelProjection:
        ports = self.configuration
        return ConfigurationPanelProjection(
            ConfigurationPanelPorts(
                languages=ports.languages,
                log_level_names=ports.log_level_names,
                log_level_name=ports.log_level_name,
                log_level_status=ports.log_level_status,
                ui_text=ports.ui_text,
                compact_text=ports.compact_text,
                default_base_url=ports.default_base_url,
                api_key_count=ports.api_key_count,
                platform_name=ports.platform_name,
            )
        )

    def language_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.configuration_panel_projection().language_rows(config)

    def log_level_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.configuration_panel_projection().log_level_rows(config)

    def model_panel_services(self) -> ModelPanelServices:
        catalog = self.model_catalog
        presentation = self.model_presentation
        return ModelPanelServices(
            catalog=ModelPanelCatalog(
                alias_for=catalog.alias_for,
                cached_or_configured_model_ids=catalog.cached_or_configured_model_ids,
                read_model_info_cache=catalog.read_model_info_cache,
                read_model_list_cache=catalog.read_model_list_cache,
                unique_model_ids=catalog.unique_model_ids,
                upstream_model_ids=catalog.upstream_model_ids,
            ),
            presentation=ModelPanelPresentation(
                advisor_model_badge=presentation.advisor_model_badge,
                advisor_panel_notice=presentation.advisor_panel_notice,
                format_context_tokens=presentation.format_context_tokens,
                format_parameter_count=presentation.format_parameter_count,
                model_panel_badge=presentation.model_panel_badge,
                normalize_model_id=presentation.normalize_model_id,
                positive_int=presentation.positive_int,
            ),
        )

    def model_panel_rows(
        self,
        provider: str,
        provider_config: dict[str, Any],
        fetch: bool = True,
        force_refresh: bool = False,
    ) -> PanelRows:
        return project_model_panel_rows(
            provider,
            provider_config,
            fetch,
            force_refresh,
            services=self.model_panel_services(),
        )

    def advisor_model_panel_rows(
        self,
        provider: str,
        provider_config: dict[str, Any],
        fetch: bool = True,
        force_refresh: bool = False,
    ) -> PanelRows:
        return project_advisor_model_panel_rows(
            provider,
            provider_config,
            fetch,
            force_refresh,
            services=self.model_panel_services(),
        )

    def api_key_panel_rows(
        self, provider: str, provider_config: dict[str, Any] | None = None
    ) -> PanelRows:
        if provider == "kimi":
            status = (
                "managed profile detected"
                if self.auth.kimi_oauth_configured()
                else "login required"
            )
            mode = (
                "Routed"
                if bool((provider_config or {}).get("route_through_router"))
                else "Native"
            )
            return (
                [
                    f"Kimi OAuth ({mode}): {status}",
                    "Login with Kimi Code OAuth (clears API key)",
                    "Set routed API key",
                    "Clear routed API key",
                    "Back",
                ],
                ["__info__", "kimi-oauth-login", "input", "clear", "back"],
            )
        oauth_rows = self.auth.copilot_panel_rows(provider)
        if oauth_rows is not None:
            return oauth_rows
        return self.configuration_panel_projection().api_key_rows(
            provider, provider_config
        )

    def base_url_panel_rows(
        self, provider: str, provider_config: dict[str, Any]
    ) -> PanelRows:
        return self.configuration_panel_projection().base_url_rows(
            provider, provider_config
        )


@dataclass(frozen=True, slots=True)
class PrelaunchPanelCompatibilityApi:
    context: Callable[[], PrelaunchPanelContext]

    def main_menu_projection(self) -> MainMenuProjection:
        return self.context().main_menu_projection()

    def main_menu_rows(
        self,
        config: dict[str, Any],
        provider: str,
        provider_config: dict[str, Any],
        language: str,
    ) -> list[str]:
        return self.context().main_menu_rows(
            config, provider, provider_config, language
        )

    def web_backend_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.context().web_backend_panel_rows(config)

    def set_web_backend_config(self, key: str, value: Any) -> list[str]:
        return self.context().set_web_backend_config(key, value)

    def provider_panel_projection(self) -> ProviderPanelProjection:
        return self.context().provider_panel_projection()

    def provider_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.context().provider_panel_rows(config)

    def configuration_panel_projection(self) -> ConfigurationPanelProjection:
        return self.context().configuration_panel_projection()

    def language_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.context().language_panel_rows(config)

    def log_level_panel_rows(self, config: dict[str, Any]) -> PanelRows:
        return self.context().log_level_panel_rows(config)

    def model_panel_services(self) -> ModelPanelServices:
        return self.context().model_panel_services()

    def model_panel_rows(
        self,
        provider: str,
        provider_config: dict[str, Any],
        fetch: bool = True,
        force_refresh: bool = False,
    ) -> PanelRows:
        return self.context().model_panel_rows(
            provider, provider_config, fetch, force_refresh
        )

    def advisor_model_panel_rows(
        self,
        provider: str,
        provider_config: dict[str, Any],
        fetch: bool = True,
        force_refresh: bool = False,
    ) -> PanelRows:
        return self.context().advisor_model_panel_rows(
            provider, provider_config, fetch, force_refresh
        )

    def api_key_panel_rows(
        self, provider: str, provider_config: dict[str, Any] | None = None
    ) -> PanelRows:
        return self.context().api_key_panel_rows(provider, provider_config)

    def base_url_panel_rows(
        self, provider: str, provider_config: dict[str, Any]
    ) -> PanelRows:
        return self.context().base_url_panel_rows(provider, provider_config)


__all__ = [
    "AuthPanelPorts",
    "ConfigurationPanelContextPorts",
    "MainMenuPanelPorts",
    "ModelPanelCatalogPorts",
    "ModelPanelPresentationPorts",
    "PrelaunchPanelCompatibilityApi",
    "PrelaunchPanelContext",
    "ProviderChoicePanelPorts",
    "WebBackendPanelPorts",
]
