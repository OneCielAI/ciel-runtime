"""Ollama catalog refresh command application controller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OllamaCatalogCliController:
    refresh: Callable[..., dict[str, Any]]
    catalog_path: Path
    output: Callable[[str], None]

    def refresh_command(
        self,
        *,
        include_contexts: bool = True,
        timeout: float = 10.0,
    ) -> None:
        catalog = self.refresh(
            include_contexts=include_contexts,
            timeout=timeout,
        )
        raw_models = catalog.get("models")
        models = raw_models if isinstance(raw_models, dict) else {}
        context_count = sum(
            1
            for entry in models.values()
            if isinstance(entry, dict)
            and isinstance(entry.get("context_windows"), dict)
            and bool(entry["context_windows"])
        )
        self.output(f"Ollama catalog saved: {self.catalog_path}")
        self.output(f"API models: {catalog.get('model_count', 0)}")
        self.output(f"Base models: {len(models)}")
        self.output(f"Context windows: {context_count}/{len(models)}")


__all__ = ["OllamaCatalogCliController"]
