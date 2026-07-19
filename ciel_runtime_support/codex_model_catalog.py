"""Codex bundled-model catalog projection and atomic persistence."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CodexModelCatalogSpec:
    alias: str
    provider_label: str
    context_window: int
    effort: str = ""


class CodexModelCatalogService:
    def __init__(
        self,
        config_dir: Path,
        run: Callable[..., Any],
        log: Callable[[str, str], None],
    ) -> None:
        self.config_dir = config_dir
        self.run = run
        self.log = log

    def write(
        self,
        codex: str,
        spec: CodexModelCatalogSpec,
        environment: dict[str, str],
    ) -> Path | None:
        try:
            result = self.run(
                [codex, "debug", "models", "--bundled"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                env=environment,
            )
            if result.returncode != 0:
                detail = result.stderr or result.stdout or f"exit {result.returncode}"
                raise RuntimeError(detail.strip())
            catalog = json.loads(result.stdout)
            models = catalog.get("models") if isinstance(catalog, dict) else None
            if not isinstance(models, list) or not models:
                raise ValueError("bundled catalog contains no models")
            template = next(
                (
                    item
                    for item in models
                    if isinstance(item, dict) and item.get("slug") == "gpt-5.2"
                ),
                None,
            )
            if template is None:
                template = next((item for item in models if isinstance(item, dict)), None)
            if template is None:
                raise ValueError("bundled catalog contains no model metadata")
            routed = self._routed_model(template, spec)
            catalog["models"] = [
                item
                for item in models
                if not (isinstance(item, dict) and item.get("slug") == spec.alias)
            ] + [routed]
            return self._save(catalog)
        except Exception as exc:
            self.log(
                "WARN",
                f"codex_model_catalog_generation_failed "
                f"error={type(exc).__name__}: {exc}",
            )
            return None

    @staticmethod
    def _routed_model(
        template: dict[str, Any], spec: CodexModelCatalogSpec
    ) -> dict[str, Any]:
        routed = json.loads(json.dumps(template))
        routed.update(
            {
                "slug": spec.alias,
                "display_name": f"Ciel Runtime {spec.provider_label}",
                "description": f"{spec.provider_label} routed through Ciel Runtime.",
                "visibility": "none",
                "supported_in_api": True,
                "priority": 99,
                "context_window": spec.context_window,
                "max_context_window": spec.context_window,
                "auto_compact_token_limit": max(1, (spec.context_window * 9) // 10),
            }
        )
        if spec.effort:
            routed["default_reasoning_level"] = spec.effort
            supported = routed.get("supported_reasoning_levels")
            if not isinstance(supported, list):
                supported = []
            if not any(
                isinstance(item, dict) and item.get("effort") == spec.effort
                for item in supported
            ):
                supported.append(
                    {
                        "effort": spec.effort,
                        "description": f"{spec.effort.title()} reasoning effort",
                    }
                )
            routed["supported_reasoning_levels"] = supported
        return routed

    def _save(self, catalog: dict[str, Any]) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / "codex-model-catalog.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return path
