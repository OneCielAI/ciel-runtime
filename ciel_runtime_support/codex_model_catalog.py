"""Codex bundled-model catalog projection and atomic persistence."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


# Provider metadata keys consumed here and never written into the catalog.
# TEMPLATE_SLUGS_KEY names bundled entries (in order) whose instructions and
# tool settings the routed entry should inherit; REASONING_EFFORTS_KEY lists
# the efforts the upstream accepts for the model.
TEMPLATE_SLUGS_KEY = "ciel_template_slugs"
REASONING_EFFORTS_KEY = "ciel_reasoning_efforts"


@dataclass(frozen=True, slots=True)
class CodexModelCatalogSpec:
    alias: str
    provider_label: str
    context_window: int
    effort: str = ""
    auto_compact_token_limit: int | None = None
    metadata: Mapping[str, Any] | None = None


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
            bundled = {
                item.get("slug"): item for item in models if isinstance(item, dict)
            }
            requested = (spec.metadata or {}).get(TEMPLATE_SLUGS_KEY) or ()
            template = next(
                (bundled[slug] for slug in requested if slug in bundled),
                bundled.get("gpt-5.2"),
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
        auto_compact_token_limit = (
            spec.auto_compact_token_limit
            if spec.auto_compact_token_limit is not None
            else max(1, (spec.context_window * 9) // 10)
        )
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
                "auto_compact_token_limit": min(
                    spec.context_window,
                    max(1, auto_compact_token_limit),
                ),
                # A bundled template can carry a migration to another bundled
                # model (Codex 0.160.0 gpt-5.6-sol -> gpt-6-sol); the routed
                # alias must stay selected, so neither the upgrade nor its
                # announcement is inherited.
                "upgrade": None,
                "availability_nux": None,
            }
        )
        metadata = dict(spec.metadata or {})
        metadata.pop(TEMPLATE_SLUGS_KEY, None)
        efforts = metadata.pop(REASONING_EFFORTS_KEY, None)
        if isinstance(efforts, list) and efforts:
            template_levels = {
                item.get("effort"): item
                for item in template.get("supported_reasoning_levels") or []
                if isinstance(item, dict)
            }
            metadata.setdefault(
                "supported_reasoning_levels",
                [
                    template_levels.get(effort)
                    or {
                        "effort": effort,
                        "description": f"{effort.title()} reasoning effort",
                    }
                    for effort in efforts
                ],
            )
            if "default_reasoning_level" not in metadata:
                default = template.get("default_reasoning_level")
                if default not in efforts:
                    default = "medium" if "medium" in efforts else efforts[0]
                metadata["default_reasoning_level"] = default
        metadata_has_default = "default_reasoning_level" in metadata
        metadata_has_levels = "supported_reasoning_levels" in metadata
        if metadata:
            routed.update(json.loads(json.dumps(metadata)))
        if spec.effort and not metadata_has_default:
            routed["default_reasoning_level"] = spec.effort
        if spec.effort and not metadata_has_levels:
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
        payload = json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        catalog_dir = self.config_dir / "codex-model-catalogs"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        path = catalog_dir / f"{digest}.json"
        if path.exists():
            return path
        temporary = catalog_dir / (
            f".{digest}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(payload, encoding="utf-8")
            # Another simultaneous launch may publish the same content first.
            # Replacing it is harmless because the target name is the content
            # digest; different provider/model snapshots always use different
            # immutable paths.
            try:
                temporary.replace(path)
            except OSError:
                if not path.exists():
                    raise
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return path
