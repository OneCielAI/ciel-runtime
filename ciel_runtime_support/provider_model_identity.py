"""Provider-aware model identity and presentation policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol

from .architecture import ProviderAdapter


class ProviderAdapterRegistryPort(Protocol):
    def create(self, name: str, /) -> ProviderAdapter: ...


@dataclass(frozen=True, slots=True)
class ProviderModelIdentityService:
    """Resolve provider names and model identities through registered strategies."""

    adapters: ProviderAdapterRegistryPort
    aliases: Mapping[str, str]
    labels: Mapping[str, str]

    def normalize_provider(self, name: str) -> str:
        key = name.strip().lower().replace("_", "-").replace(" ", "-")
        try:
            return self.aliases[key]
        except KeyError as exc:
            known = ", ".join(self.labels)
            raise SystemExit(f"Unknown provider: {name}\nKnown: {known}") from exc

    @staticmethod
    def slug(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.lower())
        return re.sub(r"-+", "-", normalized).strip("-") or "model"

    @staticmethod
    def sort_key(model_id: str) -> tuple[str, str]:
        return (model_id.casefold(), model_id)

    def sorted_ids(self, model_ids: list[str]) -> list[str]:
        return sorted(model_ids, key=self.sort_key)

    def unique_ids(self, provider: str, model_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for raw in model_ids:
            model_id = self.normalize_model_id(provider, str(raw))
            key = model_id.casefold()
            if model_id and key not in seen:
                seen.add(key)
                unique.append(model_id)
        return unique

    def normalize_model_id(self, provider: str, model_id: str) -> str:
        return self.adapters.create(provider).normalize_model_id(model_id)

    @staticmethod
    def strip_claude_context_suffix(model_id: str | None) -> str:
        text = str(model_id or "").strip()
        return re.sub(r"\[(?:1m)\]\s*$", "", text, flags=re.IGNORECASE)

    def upstream_api_model_id(self, provider: str, model_id: str | None) -> str:
        return self.adapters.create(provider).upstream_api_model_id(str(model_id or ""))

    def alias_for(self, provider: str, model_id: str) -> str:
        if self.adapters.create(provider).preserves_claude_model_alias(model_id):
            return model_id
        return f"ciel-runtime-{provider}-{self.slug(model_id)}"

    def unslug_alias(
        self,
        provider: str,
        alias: str,
        model_map: Mapping[str, str],
    ) -> str | None:
        alias = self.strip_claude_context_suffix(alias)
        if alias in model_map:
            return model_map[alias]
        prefix = f"ciel-runtime-{provider}-"
        if alias.startswith(prefix):
            target_slug = alias[len(prefix) :]
            for model_id in model_map.values():
                if self.slug(model_id) == target_slug:
                    return model_id
        return None

    def display_name(self, provider: str, model_id: str) -> str:
        label = self.labels.get(provider, provider)
        return self.adapters.create(provider).display_model_name(model_id, label)


__all__ = ["ProviderAdapterRegistryPort", "ProviderModelIdentityService"]
