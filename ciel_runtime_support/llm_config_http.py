"""HTTP controller and presentation projection for LLM configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class LlmConfigIdentity:
    load_config: Callable[..., dict[str, Any]]
    current_provider: Callable[..., tuple[str, dict[str, Any]]]
    current_alias: Callable[..., str]
    applied_preset: Callable[..., str]
    context_status: Callable[..., str]
    timeout_status: Callable[..., str]
    provider_labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class LlmConfigPanels:
    option_rows: Callable[..., tuple[list[str], list[str]]]
    option_default: Callable[..., str]
    preset_rows: Callable[..., tuple[list[str], list[str]]]
    context_rows: Callable[..., tuple[list[str], list[str]]]
    timeout_rows: Callable[..., tuple[list[str], list[str]]]


@dataclass(frozen=True, slots=True)
class LlmConfigMutations:
    set_model: Callable[..., list[str]]
    set_advisor_model: Callable[..., list[str]]
    apply_preset: Callable[..., list[str]]
    apply_context: Callable[..., list[str]]
    apply_timeout: Callable[..., list[str]]
    set_option: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class LlmConfigHttpIO:
    publish_event: Callable[..., Any]
    write_json: Callable[..., Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class LlmConfigHttpController:
    identity: LlmConfigIdentity
    panels: LlmConfigPanels
    mutations: LlmConfigMutations
    io: LlmConfigHttpIO

    PATH = "/ca/config/llm"

    def payload(self, messages: list[str] | None = None) -> dict[str, Any]:
        cfg = self.identity.load_config()
        provider, provider_config = self.identity.current_provider(cfg)
        language = cfg.get("language", "en")
        rows, values = self.panels.option_rows(provider, provider_config, language)
        preset_rows, preset_values = self.panels.preset_rows(provider, provider_config, language)
        context_rows, context_values = self.panels.context_rows(provider, provider_config, language)
        timeout_rows, timeout_values = self.panels.timeout_rows(provider_config, language)
        return {
            "ok": True,
            "messages": messages or [],
            "provider": provider,
            "provider_label": self.identity.provider_labels.get(provider, provider),
            "model": str(provider_config.get("current_model") or ""),
            "alias": self.identity.current_alias(cfg),
            "advisor_model": str(provider_config.get("advisor_model") or ""),
            "preset": self.identity.applied_preset(provider, provider_config),
            "context": self.identity.context_status(provider, provider_config),
            "timeout": self.identity.timeout_status(provider_config, language),
            "options": [
                {"label": row, "key": key, "value": self.panels.option_default(provider, provider_config, key)}
                for row, key in zip(rows, values)
                if key not in ("back", "__info__", "preset", "context_setup", "timeout_profile")
            ],
            "presets": self._selection_rows(preset_rows, preset_values),
            "contexts": self._selection_rows(context_rows, context_values),
            "timeouts": self._selection_rows(timeout_rows, timeout_values),
        }

    @staticmethod
    def _selection_rows(rows: list[str], values: list[str]) -> list[dict[str, str]]:
        return [
            {"label": row, "value": value}
            for row, value in zip(rows, values)
            if value not in ("back", "__info__")
        ]

    def handle_get(self, handler: Any, path: str) -> bool:
        if path != self.PATH:
            return False
        self.io.write_json(handler, self.payload())
        return True

    def handle_post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        if path != self.PATH:
            return False
        cfg = self.identity.load_config()
        provider, _provider_config = self.identity.current_provider(cfg)
        action = str(body.get("action") or "option").strip()
        value = str(body.get("value") or "").strip()
        key = str(body.get("key") or "").strip()
        try:
            messages = self._mutate(action, provider, key, value)
            self.io.publish_event(
                level="info",
                category="config.llm",
                message=f"updated {action} {key or value}",
                provider=provider,
                data={"action": action, "key": key, "value": value},
            )
            self.io.write_json(handler, self.payload(messages))
        except SystemExit as exc:
            self.io.write_json(handler, {"ok": False, "error": str(exc), "messages": [str(exc)]}, 400)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.io.log("ERROR", f"llm config update failed: {error}")
            self.io.write_json(handler, {"ok": False, "error": error, "messages": [error]}, 500)
        return True

    def _mutate(self, action: str, provider: str, key: str, value: str) -> list[str]:
        if action == "model":
            return self.mutations.set_model(value)
        if action == "advisor_model":
            return self.mutations.set_advisor_model(value)
        if action == "preset":
            return self.mutations.apply_preset(provider, value)
        if action == "context_setup":
            return self.mutations.apply_context(provider, value)
        if action == "timeout_profile":
            return self.mutations.apply_timeout(provider, value)
        if action == "option":
            if not key:
                raise SystemExit("Missing option key")
            return self.mutations.set_option(provider, key, value)
        raise SystemExit(f"Unknown LLM config action: {action}")


__all__ = [
    "LlmConfigHttpController",
    "LlmConfigHttpIO",
    "LlmConfigIdentity",
    "LlmConfigMutations",
    "LlmConfigPanels",
]
