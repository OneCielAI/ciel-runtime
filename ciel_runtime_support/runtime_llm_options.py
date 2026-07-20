"""Runtime LLM option snapshot, restore, slider, and status controller."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeLlmSettings:
    option_keys: frozenset[str]
    original_key: str
    slider_labels: Mapping[str, str]
    ollama_providers: frozenset[str] = frozenset(("ollama", "ollama-cloud"))


@dataclass(frozen=True, slots=True)
class RuntimeLlmConfigPorts:
    load: Callable[[], dict[str, Any]]
    save: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]
    deep_copy: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class RuntimeLlmPresentationPorts:
    applied_preset: Callable[[str, dict[str, Any]], str]
    slider_presets: Callable[[], Sequence[str]]
    preset_text: Callable[[str, str], tuple[str, str]]
    provider_label: Callable[[str, dict[str, Any]], str]
    context_status: Callable[[str, dict[str, Any]], str]
    timeout_status: Callable[[dict[str, Any], str], str]
    ollama_options: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RuntimeLlmMutationPorts:
    apply_preset: Callable[..., list[str]]


class RuntimeLlmOptionsController:
    def __init__(
        self,
        settings: RuntimeLlmSettings,
        config: RuntimeLlmConfigPorts,
        presentation: RuntimeLlmPresentationPorts,
        mutation: RuntimeLlmMutationPorts,
    ) -> None:
        self.settings = settings
        self.config = config
        self.presentation = presentation
        self.mutation = mutation

    def snapshot(self, provider: str, provider_config: dict[str, Any]) -> dict[str, Any]:
        values = {
            key: self.config.deep_copy(provider_config[key])
            for key in sorted(self.settings.option_keys)
            if key in provider_config
        }
        return {
            "version": 1,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": provider,
            "model": str(provider_config.get("current_model") or ""),
            "values": values,
        }

    def ensure_snapshot(self, provider: str, provider_config: dict[str, Any]) -> bool:
        existing = provider_config.get(self.settings.original_key)
        if isinstance(existing, dict) and isinstance(existing.get("values"), dict):
            return False
        provider_config[self.settings.original_key] = self.snapshot(
            provider,
            provider_config,
        )
        return True

    def restore(self, provider: str) -> list[str]:
        config = self.config.load()
        provider_config = config["providers"][provider]
        snapshot = provider_config.get(self.settings.original_key)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("values"), dict):
            return ["No captured live LLM options to restore."]
        values = self.config.deep_copy(snapshot.get("values") or {})
        for key in self.settings.option_keys:
            provider_config.pop(key, None)
        provider_config.update(values)
        provider_config.pop(self.settings.original_key, None)
        self._persist(config)
        return [
            "Restored live LLM options to the values captured before the first runtime preset change.",
            f"Captured provider/model: {snapshot.get('provider') or provider} / "
            f"{snapshot.get('model') or 'unknown'}",
        ]

    def apply_preset(self, provider: str, preset_id: str) -> list[str]:
        config = self.config.load()
        provider_config = config["providers"][provider]
        captured = self.ensure_snapshot(provider, provider_config)
        lines = self.mutation.apply_preset(
            provider,
            provider_config,
            preset_id,
            config.get("language", "en"),
        )
        if captured:
            lines.insert(0, "Captured current live LLM options for /llm-restore.")
        self._persist(config)
        return lines

    def slider_line(self, provider: str, provider_config: dict[str, Any]) -> str:
        current = self.presentation.applied_preset(provider, provider_config)
        parts = []
        for preset_id in self.presentation.slider_presets():
            label = self.settings.slider_labels.get(preset_id, preset_id)
            parts.append(f"[{label}]" if preset_id == current else label)
        return "< " + " | ".join(parts) + " >"

    def apply_slider_delta(self, provider: str, delta: int) -> list[str]:
        config = self.config.load()
        provider_config = config["providers"][provider]
        presets = list(self.presentation.slider_presets())
        current = self.presentation.applied_preset(provider, provider_config)
        try:
            current_index = presets.index(current)
        except ValueError:
            current_index = 0
        next_index = max(0, min(len(presets) - 1, current_index + delta))
        next_preset = presets[next_index]
        language = config.get("language", "en")
        if next_index == current_index:
            label = self.presentation.preset_text(next_preset, language)[0]
            return [
                f"Live LLM preset remains at {label}.",
                f"Slider: {self.slider_line(provider, provider_config)}",
            ]
        captured = self.ensure_snapshot(provider, provider_config)
        lines = self.mutation.apply_preset(
            provider,
            provider_config,
            next_preset,
            language,
        )
        if captured:
            lines.insert(0, "Captured current live LLM options for /llm-restore.")
        self._persist(config)
        label = self.presentation.preset_text(next_preset, language)[0]
        return [f"Live LLM preset moved to {label}."] + lines + [
            f"Slider: {self.slider_line(provider, provider_config)}"
        ]

    def status_lines(
        self,
        provider: str,
        provider_config: dict[str, Any],
    ) -> list[str]:
        language = self.config.load().get("language", "en")
        applied = self.presentation.applied_preset(provider, provider_config)
        lines = [
            f"Provider: {self.presentation.provider_label(provider, provider_config)}",
            f"Model: {provider_config.get('current_model') or 'unknown'}",
            f"Preset: {applied} ({self.presentation.preset_text(applied, language)[0]})",
            f"Slider: {self.slider_line(provider, provider_config)}",
            f"Context: {self.presentation.context_status(provider, provider_config)}",
            f"Timeout: {self.presentation.timeout_status(provider_config, language)}",
        ]
        if provider in self.settings.ollama_providers:
            options = self.presentation.ollama_options(provider_config)
            lines.append(f"Output tokens: {options.get('num_predict', 'default')}")
        else:
            lines.append(
                f"Output tokens: {provider_config.get('max_output_tokens', 'default')}"
            )
        restore_available = isinstance(
            provider_config.get(self.settings.original_key),
            dict,
        )
        lines.append(f"Restore available: {'yes' if restore_available else 'no'}")
        return lines

    def preset_list_lines(
        self,
        provider: str,
        provider_config: dict[str, Any],
    ) -> list[str]:
        language = self.config.load().get("language", "en")
        applied = self.presentation.applied_preset(provider, provider_config)
        lines = self.status_lines(provider, provider_config)
        lines.extend(
            (
                "",
                "Use `/llm left` or `/llm right` to move one step, or `/llm <preset-id>` to jump directly.",
                "Preset ids:",
            )
        )
        for preset_id in self.presentation.slider_presets():
            label, description = self.presentation.preset_text(preset_id, language)
            mark = "*" if preset_id == applied else " "
            lines.append(f"{mark} {preset_id} — {label}: {description}")
        lines.extend(
            (
                "  /llm-restore  restore captured original options",
                "  /llm <left|right|preset-id|status|list|restore>",
            )
        )
        return lines

    def _persist(self, config: dict[str, Any]) -> None:
        self.config.save(config)
        self.config.clear_model_cache()
