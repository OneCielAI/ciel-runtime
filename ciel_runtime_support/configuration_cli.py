"""Application controller for interactive configuration CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


RuntimeConfig = dict[str, Any]
ProviderConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConfigurationCliConfigPorts:
    load: Callable[[], RuntimeConfig]
    save: Callable[[RuntimeConfig], None]
    current_provider: Callable[
        [RuntimeConfig],
        tuple[str, ProviderConfig],
    ]


@dataclass(frozen=True, slots=True)
class ConfigurationCliProviderPorts:
    normalize_choice: Callable[[str], str | None]
    normalize_provider: Callable[[str], str]
    panel_rows: Callable[[RuntimeConfig], tuple[list[str], list[str]]]
    menu_label: Callable[[str, ProviderConfig], str]
    set_choice: Callable[[str], list[str]]
    set_provider: Callable[[str], list[str]]
    set_base_url: Callable[[str, str], list[str]]


@dataclass(frozen=True, slots=True)
class ConfigurationCliModelPorts:
    cached_ids: Callable[[str, ProviderConfig], list[str]]
    alias_for: Callable[[str, str], str]
    read_cache: Callable[[str, ProviderConfig], list[str] | None]
    set_model: Callable[[str], list[str]]
    upstream_ids: Callable[[str, ProviderConfig], list[str]]
    set_advisor: Callable[[str], list[str]]
    advisor_uses_builtin: Callable[[str, ProviderConfig], bool]


@dataclass(frozen=True, slots=True)
class ConfigurationCliDisplayPorts:
    log_level_names: Mapping[int, str]
    log_level_status: Callable[[], str]
    log_level_name: Callable[[], str]
    set_log_level: Callable[[str], list[str]]
    languages: Mapping[str, str]
    web_tools_config_path: Path
    language_panel_rows: Callable[
        [RuntimeConfig],
        tuple[list[str], list[str]],
    ]


@dataclass(frozen=True, slots=True)
class ConfigurationCliIO:
    output: Callable[[str], None]
    select: Callable[[str, list[str], int], int | None]


@dataclass(frozen=True, slots=True)
class ConfigurationCliController:
    config: ConfigurationCliConfigPorts
    provider: ConfigurationCliProviderPorts
    model: ConfigurationCliModelPorts
    display: ConfigurationCliDisplayPorts
    io: ConfigurationCliIO

    def provider_command(self, name: str | None) -> None:
        config = self.config.load()
        if not name:
            current = str(config["current_provider"])
            providers = config["providers"]
            current_config = (
                providers.get(current, {})
                if isinstance(providers, dict)
                else {}
            )
            rows, _values = self.provider.panel_rows(config)
            self.io.output(
                "Available providers (current: %s)"
                % self.provider.menu_label(current, current_config)
            )
            for index, row in enumerate(rows, 1):
                self.io.output(f" {index:>2}. {row}")
            self.io.output("\nUse: /provider <name>")
            self.io.output(
                "Examples: /provider codex, /provider codex-routed, "
                "/provider ollama"
            )
            self.io.output(
                "Then run /model to choose a model for the selected provider."
            )
            return
        choice = self.provider.normalize_choice(name)
        lines = (
            self.provider.set_choice(choice)
            if choice
            else self.provider.set_provider(
                self.provider.normalize_provider(name)
            )
        )
        self._output_lines(lines)
        self.io.output(
            "Gateway model cache cleared. Run /model to refresh "
            "the model picker."
        )

    def base_url_command(self, provider: str, url: str) -> None:
        normalized = self.provider.normalize_provider(provider)
        self._output_lines(self.provider.set_base_url(normalized, url))

    def model_command(self, values: list[str] | None) -> None:
        config = self.config.load()
        provider, provider_config = self.config.current_provider(config)
        if not values:
            self.io.output(
                f"Model menu for {provider} "
                f"(current: {provider_config.get('current_model')})"
            )
            models = self.model.cached_ids(provider, provider_config)
            for index, model_id in enumerate(models[:100], 1):
                mark = (
                    "*"
                    if model_id == provider_config.get("current_model")
                    else " "
                )
                self.io.output(
                    f" {mark} {index:>3}. "
                    f"{self.model.alias_for(provider, model_id)}    "
                    f"[{model_id}]"
                )
            if len(models) > 100:
                self.io.output(f" ... {len(models) - 100} more")
            if self.model.read_cache(provider, provider_config) is None:
                self.io.output(
                    "\nProvider model list is not cached yet. Use the menu "
                    "refresh row or run: ciel-runtimectl models"
                )
            self.io.output(
                "\nSet direct/custom model with: /set-model MODEL_ID"
            )
            self.io.output(
                "Or from terminal: ciel-runtimectl model MODEL_ID"
            )
            return
        value = " ".join(values).strip()
        if value.startswith("add "):
            value = value[4:].strip()
        if not value:
            raise SystemExit("Missing model id")
        self._output_lines(self.model.set_model(value))
        self.io.output(
            "Gateway model cache cleared. Run /model to refresh if needed."
        )

    def advisor_model_command(self, values: list[str] | None) -> None:
        if not values:
            config = self.config.load()
            provider, provider_config = self.config.current_provider(config)
            if self.model.advisor_uses_builtin(provider, provider_config):
                self.io.output(
                    "Anthropic modes use Claude Code's built-in /advisor; "
                    "run /advisor in the session to pick its model."
                )
                return
            current = provider_config.get("advisor_model") or "off"
            self.io.output(f"Advisor Model for {provider}: {current}")
            self.io.output(
                "Set with: ciel-runtimectl advisor-model deepseek-v4-pro"
            )
            self.io.output(
                "Disable with: ciel-runtimectl advisor-model off"
            )
            return
        value = " ".join(values).strip()
        if value.lower() in {
            "off",
            "unset",
            "disable",
            "disabled",
            "none",
            "null",
        }:
            value = ""
        self._output_lines(self.model.set_advisor(value))

    def models_command(self, provider_override: str | None) -> None:
        config = self.config.load()
        provider, provider_config = self.config.current_provider(config)
        if provider_override:
            provider = self.provider.normalize_provider(provider_override)
            provider_config = config["providers"][provider]
        models = self.model.upstream_ids(provider, provider_config)
        self.io.output(f"{provider}: {len(models)} models")
        for model_id in models:
            self.io.output(
                f"{self.model.alias_for(provider, model_id)}\t{model_id}"
            )

    def log_level_command(self, value: str | None) -> None:
        if not value:
            self.io.output(f"log_level: {self.display.log_level_status()}")
            for numeric in sorted(self.display.log_level_names):
                name = self.display.log_level_names[numeric]
                mark = (
                    "*"
                    if name == self.display.log_level_name()
                    else " "
                )
                self.io.output(f" {mark} {name:<6} {numeric}")
            self.io.output("   DEFAULT reset to environment/default")
            return
        self._output_lines(self.display.set_log_level(str(value)))

    def language_command(self, value: str | None) -> None:
        config = self.config.load()
        if not value:
            current = str(config.get("language", "en"))
            label = self.display.languages.get(current, current)
            self.io.output(f"language: {current} ({label})")
            for code, language_label in self.display.languages.items():
                mark = "*" if code == current else " "
                self.io.output(f" {mark} {code:<2} {language_label}")
            return
        normalized = self._language_alias(value)
        if normalized not in self.display.languages:
            raise SystemExit(
                f"Unknown language: {value}\n"
                f"Known: {', '.join(self.display.languages)}"
            )
        config["language"] = normalized
        self.config.save(config)
        self.io.output(
            f"Language set to {normalized} "
            f"({self.display.languages[normalized]})."
        )

    def portable_provider_menu(self) -> int:
        config = self.config.load()
        rows, values = self.provider.panel_rows(config)
        selected = self.io.select(
            "Select ciel-runtime provider",
            rows,
            values.index(
                str(config.get("current_provider", "nvidia-hosted"))
            ),
        )
        if selected is None:
            self.io.output("Cancelled.")
            return 1
        self._output_lines(self.provider.set_provider(values[selected]))
        return 0

    def portable_language_menu(self) -> int:
        config = self.config.load()
        rows, values = self.display.language_panel_rows(config)
        selected = self.io.select(
            "Select display language",
            rows,
            values.index(str(config.get("language", "en"))),
        )
        if selected is None:
            self.io.output("Cancelled.")
            return 1
        language = values[selected]
        config["language"] = language
        self.config.save(config)
        self.io.output(
            f"Language set to {language} "
            f"({self.display.languages[language]})."
        )
        return 0

    def web_search_command(self, value: str | None) -> None:
        config = self.config.load()
        web = config.setdefault("web_search", {})
        if value:
            web["auto_for_non_native"] = self._toggle(
                value,
                "Use: ciel-runtime web-search on|off|status",
            )
            self.config.save(config)
        state = "on" if web.get("auto_for_non_native", True) else "off"
        package = web.get("package", "ddg-mcp-search")
        self.io.output(f"web_search: {state}")
        self.io.output(
            f"search_provider: {web.get('provider', 'duckduckgo')}"
        )
        self.io.output(f"search_package: {package}")
        self.io.output(
            f"web_fetch: {'on' if web.get('fetch_enabled', True) else 'off'}"
        )
        self.io.output(
            f"fetch_package: "
            f"{web.get('fetch_package', 'mcp-server-fetch')}"
        )
        self.io.output(f"mcp_config: {self.display.web_tools_config_path}")

    def web_fetch_command(self, value: str | None) -> None:
        config = self.config.load()
        web = config.setdefault("web_search", {})
        if value:
            normalized = value.lower()
            if normalized == "ignore-robots-on":
                web["fetch_ignore_robots_txt"] = True
            elif normalized == "ignore-robots-off":
                web["fetch_ignore_robots_txt"] = False
            else:
                web["fetch_enabled"] = self._toggle(
                    normalized,
                    "Use: ciel-runtime web-fetch "
                    "on|off|ignore-robots-on|ignore-robots-off",
                )
            self.config.save(config)
        self.io.output(
            f"web_fetch: {'on' if web.get('fetch_enabled', True) else 'off'}"
        )
        self.io.output(
            f"fetch_package: "
            f"{web.get('fetch_package', 'mcp-server-fetch')}"
        )
        self.io.output(
            "ignore_robots_txt: "
            f"{bool(web.get('fetch_ignore_robots_txt', False))}"
        )
        self.io.output(
            f"user_agent: {web.get('fetch_user_agent') or 'default'}"
        )
        self.io.output(f"mcp_config: {self.display.web_tools_config_path}")

    def _output_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.io.output(line)

    @staticmethod
    def _toggle(value: str, usage: str) -> bool:
        normalized = value.lower()
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            return True
        if normalized in {"off", "disable", "disabled", "false", "0"}:
            return False
        raise SystemExit(usage)

    @staticmethod
    def _language_alias(value: str) -> str:
        normalized = value.strip().lower()
        aliases = {
            "english": "en",
            "korean": "ko",
            "한국어": "ko",
            "japanese": "ja",
            "日本語": "ja",
            "chinese": "zh",
            "中文": "zh",
            "zh-cn": "zh",
            "cn": "zh",
        }
        return aliases.get(normalized, normalized)


__all__ = [
    "ConfigurationCliConfigPorts",
    "ConfigurationCliController",
    "ConfigurationCliDisplayPorts",
    "ConfigurationCliIO",
    "ConfigurationCliModelPorts",
    "ConfigurationCliProviderPorts",
]
