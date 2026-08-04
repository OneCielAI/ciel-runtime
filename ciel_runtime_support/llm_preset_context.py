"""LLM preset identity, recommendation, and presentation bounded context."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping

from .architecture import ProviderContextPolicy
from .llm_presets import PresetIdentityPolicy


@dataclass(frozen=True, slots=True)
class LlmPresetCatalog:
    presets: Mapping[str, tuple[str, str]]
    preset_i18n: Mapping[str, Mapping[str, tuple[str, str]]]
    family_i18n: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class LlmPresetQueries:
    load_config: Callable[[], dict[str, Any]]
    context_policy: Callable[[str, dict[str, Any]], ProviderContextPolicy]
    context_capacity: Callable[[str, dict[str, Any]], int | None]
    context_services: Callable[[str], Any]
    ui_text: Callable[[str, str | None], str]
    pad_cells: Callable[[str, int], str]
    format_context: Callable[[int | None], str]


@dataclass(frozen=True, slots=True)
class LlmPresetAlgorithms:
    classify_family: Callable[..., str]
    recommend: Callable[[str, int | None], str]
    infer: Callable[..., str | None]
    required_context: Callable[[str, ProviderContextPolicy], int | None]


@dataclass(frozen=True, slots=True)
class LlmPresetContext:
    catalog: LlmPresetCatalog
    queries: LlmPresetQueries
    algorithms: LlmPresetAlgorithms

    def model_family(self, provider: str, config: dict[str, Any]) -> str:
        return self.algorithms.classify_family(
            config,
            self.queries.context_policy(provider, config),
            self.queries.context_capacity(provider, config),
            self.queries.context_services(provider),
        )

    def recommended(self, provider: str, config: dict[str, Any]) -> str:
        return self.algorithms.recommend(
            self.model_family(provider, config),
            self.queries.context_capacity(provider, config),
        )

    def slider_ids(self) -> list[str]:
        return list(self.catalog.presets)

    @staticmethod
    def command_name(preset_id: str) -> str:
        normalized = re.sub(
            r"[^a-z0-9]+", "-", str(preset_id or "").lower()
        ).strip("-")
        return f"llm-{normalized}"

    def slash_command(self, preset_id: str) -> str:
        label, description = self.text(preset_id, "en")
        return f"""---
description: Apply ciel-runtime live preset: {label}
argument-hint: [ignored]
---

CIEL_RUNTIME_LIVE_LLM_OPTIONS

Value: {preset_id}

Apply the ciel-runtime live LLM preset `{preset_id}` ({description}) to this routed session. The original options are captured before the first live preset change and can be restored with `/llm-restore`.
"""

    def resolve(self, value: str) -> str | None:
        return PresetIdentityPolicy(self.catalog.presets, self.command_name).resolve(value)

    def required_context(
        self, preset_id: str, provider: str | None = None
    ) -> int | None:
        selected = provider or "anthropic"
        return self.algorithms.required_context(
            preset_id, self.queries.context_policy(selected, {})
        )

    def available(
        self, provider: str, config: dict[str, Any], preset_id: str
    ) -> bool:
        required = self.required_context(preset_id, provider)
        if not required:
            return True
        capacity = self.queries.context_capacity(provider, config)
        return not capacity or required <= capacity

    def infer(self, provider: str, config: dict[str, Any]) -> str | None:
        return self.algorithms.infer(
            config,
            self.queries.context_policy(provider, config),
            self.queries.context_services(provider),
        )

    def applied(self, provider: str, config: dict[str, Any]) -> str:
        preset_id = str(config.get("llm_preset") or "").strip()
        if preset_id in self.catalog.presets:
            return preset_id
        inferred = self.infer(provider, config)
        if inferred and self.available(provider, config, inferred):
            return inferred
        recommended = self.recommended(provider, config)
        return recommended if self.available(provider, config, recommended) else "balanced"

    def text(self, preset_id: str, lang: str | None = None) -> tuple[str, str]:
        language = lang or str(self.queries.load_config().get("language") or "en")
        return self.catalog.preset_i18n.get(language, {}).get(
            preset_id, self.catalog.presets[preset_id]
        )

    def family_text(self, family: str, lang: str | None = None) -> str:
        language = lang or str(self.queries.load_config().get("language") or "en")
        return self.catalog.family_i18n.get(language, {}).get(family, family)

    def panel_rows(
        self,
        provider: str,
        config: dict[str, Any],
        lang: str | None = None,
    ) -> tuple[list[str], list[str]]:
        language = lang or str(self.queries.load_config().get("language") or "en")
        recommended = self.recommended(provider, config)
        applied = self.applied(provider, config)
        family = self.model_family(provider, config)
        recommended_label, _ = self.text(recommended, language)
        rows = [
            f"{self.queries.ui_text('model_family', language)}: "
            f"{self.family_text(family, language)}; "
            f"{self.queries.ui_text('recommended_preset_is', language)} "
            f"{recommended_label}"
        ]
        values = ["__info__"]
        for preset_id in self.catalog.presets:
            label, description = self.text(preset_id, language)
            mark = "*" if preset_id == applied else " "
            suffix = ""
            required = self.required_context(preset_id, provider)
            capacity = (
                self.queries.context_capacity(provider, config) if required else None
            )
            if required and capacity and required > capacity:
                suffix = (
                    f" (requires {self.queries.format_context(required)}; "
                    f"server {self.queries.format_context(capacity)})"
                )
            rows.append(
                f"{mark} {self.queries.pad_cells(label, 24)} {description}{suffix}"
            )
            values.append(preset_id)
        rows.append(self.queries.ui_text("back", language))
        values.append("back")
        return rows, values


@dataclass(frozen=True, slots=True)
class LlmPresetCompatibilityApi:
    context: Callable[[], LlmPresetContext]

    def model_family(self, provider: str, config: dict[str, Any]) -> str:
        return self.context().model_family(provider, config)

    def recommended(self, provider: str, config: dict[str, Any]) -> str:
        return self.context().recommended(provider, config)

    def slider_ids(self) -> list[str]:
        return self.context().slider_ids()

    def command_name(self, preset_id: str) -> str:
        return self.context().command_name(preset_id)

    def slash_command(self, preset_id: str) -> str:
        return self.context().slash_command(preset_id)

    def resolve(self, value: str) -> str | None:
        return self.context().resolve(value)

    def required_context(
        self, preset_id: str, provider: str | None = None
    ) -> int | None:
        return self.context().required_context(preset_id, provider)

    def available(
        self, provider: str, config: dict[str, Any], preset_id: str
    ) -> bool:
        return self.context().available(provider, config, preset_id)

    def applied(self, provider: str, config: dict[str, Any]) -> str:
        return self.context().applied(provider, config)

    def infer(self, provider: str, config: dict[str, Any]) -> str | None:
        return self.context().infer(provider, config)

    def text(self, preset_id: str, lang: str | None = None) -> tuple[str, str]:
        return self.context().text(preset_id, lang)

    def family_text(self, family: str, lang: str | None = None) -> str:
        return self.context().family_text(family, lang)

    def panel_rows(
        self,
        provider: str,
        config: dict[str, Any],
        lang: str | None = None,
    ) -> tuple[list[str], list[str]]:
        return self.context().panel_rows(provider, config, lang)


__all__ = [
    "LlmPresetAlgorithms",
    "LlmPresetCatalog",
    "LlmPresetCompatibilityApi",
    "LlmPresetContext",
    "LlmPresetQueries",
]
