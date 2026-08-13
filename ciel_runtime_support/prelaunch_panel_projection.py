"""Prelaunch main-menu and configuration-panel projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ciel_runtime_support.web_endpoints import web_backend_summary


ProviderConfig = dict[str, Any]
RuntimeConfig = dict[str, Any]


class ProviderUiProjection(Protocol):
    model_placeholder: str
    advisor_placeholder: str


@dataclass(frozen=True, slots=True)
class MainMenuProjectionPorts:
    languages: Mapping[str, str]
    ui_text: Callable[[str, str], str]
    compact_text: Callable[[Any, int], str]
    provider_label: Callable[[str, ProviderConfig], str]
    stored_api_key_mask: Callable[[str, ProviderConfig], str]
    llm_options_status: Callable[[str, ProviderConfig], str]
    log_level_status: Callable[[], str]
    supports_runtime: Callable[[str, str], bool]
    provider_family: Callable[[str, str], str]
    provider_ui_policy: Callable[
        [str, ProviderConfig],
        ProviderUiProjection,
    ]


@dataclass(frozen=True, slots=True)
class MainMenuProjection:
    ports: MainMenuProjectionPorts

    def rows(
        self,
        config: RuntimeConfig,
        provider: str,
        provider_config: ProviderConfig,
        language: str,
    ) -> list[str]:
        policy = self.ports.provider_ui_policy(provider, provider_config)
        policy_model = policy.model_placeholder
        policy_advisor = policy.advisor_placeholder
        model_text = (
            str(policy_model)
            if policy_model and not provider_config.get("current_model")
            else self.ports.compact_text(
                provider_config.get("current_model", "unset"),
                62,
            )
        )
        advisor_text = (
            str(policy_advisor)
            if policy_advisor
            else self.ports.compact_text(
                provider_config.get("advisor_model") or "off",
                62,
            )
        )
        remote = config.get("remote_instructions")
        remote_enabled = bool(isinstance(remote, dict) and remote.get("enabled"))
        return [
            f"0. {self.ports.ui_text('language', language)}  "
            f"[{self.ports.languages.get(language, language)}]",
            f"1. {self.ports.ui_text('provider', language)}  "
            f"[{self.ports.provider_label(provider, provider_config)}]",
            f"2. {self.ports.ui_text('api_key', language)}  "
            f"[{self.ports.stored_api_key_mask(provider, provider_config)}]",
            f"3. {self.ports.ui_text('base_url', language)}  "
            f"[{self.ports.compact_text(provider_config.get('base_url', 'unset'), 62)}]",
            f"4. {self.ports.ui_text('model', language)}  [{model_text}]",
            f"5. {self.ports.ui_text('advisor_model', language)}  "
            f"[{advisor_text}]",
            f"6. {self.ports.ui_text('options', language)}  "
            f"[{self.ports.compact_text(self.ports.llm_options_status(provider, provider_config), 62)}]",
            f"7. {self.ports.ui_text('log_level', language)}  "
            f"[{self.ports.log_level_status()}]",
            f"8. {self.ports.ui_text('test', language)}",
            "9. Launch  [Claude · Codex · AGY · Kimi · Codex app-server]",
            "10. External event inputs  [CloudEvents + Webhook/SSE]",
            f"11. Remote instructions  [HTTP GET · {'on' if remote_enabled else 'off'}]",
            f"12. {self.ports.ui_text('web_backend', language)}  "
            f"[{web_backend_summary(config, 0)}]",
            self.ports.ui_text("quit", language),
        ]

    def _provider_family(
        self,
        provider: str,
        provider_config: ProviderConfig,
    ) -> str:
        return self.ports.provider_family(
            provider,
            self.ports.provider_label(provider, provider_config),
        )


@dataclass(frozen=True, slots=True)
class ProviderPanelConstants:
    labels: Mapping[str, str]
    anthropic_native_choice: str
    anthropic_routed_choice: str
    agy_native_choice: str
    agy_routed_choice: str
    codex_native_choice: str
    codex_routed_choice: str


@dataclass(frozen=True, slots=True)
class ProviderPanelPorts:
    anthropic_routed: Callable[[str, ProviderConfig], bool]
    agy_routed: Callable[[str, ProviderConfig], bool]
    codex_routed: Callable[[str, ProviderConfig], bool]
    has_api_key: Callable[[str, ProviderConfig], bool]
    compact_text: Callable[[Any, int], str]


@dataclass(frozen=True, slots=True)
class ProviderPanelProjection:
    constants: ProviderPanelConstants
    ports: ProviderPanelPorts

    def rows(self, config: RuntimeConfig) -> tuple[list[str], list[str]]:
        entries: list[tuple[str, str, str]] = []
        current = config.get("current_provider", "nvidia-hosted")
        providers = config.get("providers", {})
        provider_configs = providers if isinstance(providers, dict) else {}
        for key, label in self.constants.labels.items():
            raw_config = provider_configs.get(key, {})
            provider_config = raw_config if isinstance(raw_config, dict) else {}
            if key == "anthropic":
                routed = self.ports.anthropic_routed(key, provider_config)
                entries.extend(
                    self._anthropic_rows(
                        key,
                        provider_config,
                        current,
                        routed,
                    )
                )
                continue
            if key == "agy":
                routed = self.ports.agy_routed(key, provider_config)
                entries.extend(self._agy_rows(current, routed))
                continue
            if key == "codex":
                routed = self.ports.codex_routed(key, provider_config)
                entries.extend(self._codex_rows(current, routed))
                continue
            if key == "kimi":
                routed = bool(provider_config.get("route_through_router"))
                entries.extend(
                    (
                        ("Kimi Native", f"{'*' if current == key and not routed else ' '} {'Kimi Native':<16} {'kimi:native':<15} official OAuth/config", "kimi:native"),
                        ("Kimi Routed", f"{'*' if current == key and routed else ' '} {'Kimi Routed':<16} {'kimi:routed':<15} via ciel-runtime", "kimi:routed"),
                    )
                )
                continue
            mark = "*" if key == current else " "
            entries.append(
                (
                    label,
                    f"{mark} {label:<16} {key:<15} "
                    f"{self.ports.compact_text(provider_config.get('base_url', ''), 54)}",
                    key,
                )
            )
        entries.sort(key=lambda item: (item[0].casefold(), item[2].casefold()))
        return (
            [row for _label, row, _value in entries],
            [value for _label, _row, value in entries],
        )

    def _anthropic_rows(
        self,
        provider: str,
        config: ProviderConfig,
        current: Any,
        routed: bool,
    ) -> list[tuple[str, str, str]]:
        native_mark = "*" if current == provider and not routed else " "
        routed_mark = "*" if current == provider and routed else " "
        suffix = (
            "router features"
            if self.ports.has_api_key(provider, config)
            else "router via Claude Code auth"
        )
        return [
            (
                "Claude Native",
                f"{native_mark} {'Claude Native':<16} {'anthropic-native':<17} "
                f"{self.ports.compact_text(config.get('base_url', ''), 52)}",
                self.constants.anthropic_native_choice,
            ),
            (
                "Anthropic routed",
                f"{routed_mark} {'Anthropic routed':<16} "
                f"{'anthropic-routed':<17} {suffix}",
                self.constants.anthropic_routed_choice,
            ),
        ]

    def _agy_rows(
        self,
        current: Any,
        routed: bool,
    ) -> list[tuple[str, str, str]]:
        native_mark = "*" if current == "agy" and not routed else " "
        routed_mark = "*" if current == "agy" and routed else " "
        return [
            (
                "AGY",
                f"{native_mark} {'AGY':<16} {'agy-native':<17} "
                "native Antigravity settings",
                self.constants.agy_native_choice,
            ),
            (
                "AGY Routed",
                f"{routed_mark} {'AGY Routed':<16} {'agy-routed':<17} "
                "channel/PTY wake support",
                self.constants.agy_routed_choice,
            ),
        ]

    def _codex_rows(
        self,
        current: Any,
        routed: bool,
    ) -> list[tuple[str, str, str]]:
        native_mark = "*" if current == "codex" and not routed else " "
        routed_mark = "*" if current == "codex" and routed else " "
        return [
            (
                "Codex Native",
                f"{native_mark} {'Codex Native':<16} {'codex-native':<17} "
                "native Codex settings",
                self.constants.codex_native_choice,
            ),
            (
                "Codex routed",
                f"{routed_mark} {'Codex routed':<16} {'codex-routed':<17} "
                "router via native Codex auth",
                self.constants.codex_routed_choice,
            ),
        ]


@dataclass(frozen=True, slots=True)
class ConfigurationPanelPorts:
    languages: Mapping[str, str]
    log_level_names: Mapping[int, str]
    log_level_name: Callable[[], str]
    log_level_status: Callable[[], str]
    ui_text: Callable[[str, str], str]
    compact_text: Callable[[Any, int], str]
    default_base_url: Callable[[str], str]
    api_key_count: Callable[[str, ProviderConfig], int]
    platform_name: str


@dataclass(frozen=True, slots=True)
class ConfigurationPanelProjection:
    ports: ConfigurationPanelPorts

    def language_rows(
        self,
        config: RuntimeConfig,
    ) -> tuple[list[str], list[str]]:
        current = config.get("language", "en")
        rows = [
            f"{'*' if code == current else ' '} {code:<2} {label}"
            for code, label in self.ports.languages.items()
        ]
        return rows, list(self.ports.languages)

    def log_level_rows(
        self,
        config: RuntimeConfig,
    ) -> tuple[list[str], list[str]]:
        current = self.ports.log_level_name()
        descriptions = {
            "SILENT": "no router log writes",
            "ERROR": "errors only",
            "WARN": "warnings and errors",
            "INFO": "normal diagnostics",
            "DEBUG": "verbose diagnostics",
            "TRACE": "request/response trace detail",
        }
        rows: list[str] = []
        values: list[str] = []
        for numeric in sorted(self.ports.log_level_names):
            name = self.ports.log_level_names[numeric]
            mark = "*" if name == current else " "
            rows.append(
                f"{mark} {name:<6} {numeric}  {descriptions.get(name, '')}"
            )
            values.append(name)
        rows.append(f"Reset to default/env  [{self.ports.log_level_status()}]")
        values.append("DEFAULT")
        rows.append(
            self.ports.ui_text(
                "back",
                str(config.get("language", "en")),
            )
        )
        values.append("back")
        return rows, values

    def api_key_rows(
        self,
        provider: str,
        provider_config: ProviderConfig | None = None,
    ) -> tuple[list[str], list[str]]:
        rows = [
            "Type or paste API key as hidden input",
            "Type or paste multiple API keys (comma/newline separated)",
            "Read API key from an environment variable",
            "Read API keys from an environment variable",
            "Read API key from clipboard",
            "Read API keys from clipboard",
            "Back",
        ]
        values = [
            "input",
            "multi-input",
            "env",
            "multi-env",
            "clipboard",
            "multi-clipboard",
            "back",
        ]
        if self.ports.platform_name != "nt":
            rows[4] = "Read API key from desktop clipboard if available"
            rows[5] = "Read API keys from desktop clipboard if available"
        if (
            provider_config is not None
            and self.ports.api_key_count(provider, provider_config)
        ):
            rows.insert(-1, "Clear stored API key(s)")
            values.insert(-1, "clear")
        return rows, values

    def base_url_rows(
        self,
        provider: str,
        provider_config: ProviderConfig,
    ) -> tuple[list[str], list[str]]:
        default = self.ports.default_base_url(provider)
        current = provider_config.get("base_url") or default
        return (
            [
                f"Edit Base URL  [{self.ports.compact_text(current, 72)}]",
                f"Reset to provider default  [{default}]",
                "Back",
            ],
            ["edit", "default", "back"],
        )


__all__ = [
    "ConfigurationPanelPorts",
    "ConfigurationPanelProjection",
    "MainMenuProjection",
    "MainMenuProjectionPorts",
    "ProviderPanelConstants",
    "ProviderPanelPorts",
    "ProviderPanelProjection",
]
