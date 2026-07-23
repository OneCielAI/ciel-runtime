"""Pure model-identity heuristics for context-capacity hints."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelContextHintPorts:
    strip_context_suffix: Callable[[str], str]
    catalog_context: Callable[[str], tuple[int | None, str | None, str | None]]
    model_preset: Callable[[str], Mapping[str, Any]]
    positive_int: Callable[[Any], int | None]


class ModelContextHintPolicy:
    def __init__(
        self,
        zai_hints: Sequence[tuple[str, int]],
        ports: ModelContextHintPorts,
    ) -> None:
        self.zai_hints = tuple(zai_hints)
        self.ports = ports

    @staticmethod
    def is_qwen36_plus(model_id: str) -> bool:
        compact = re.sub(r"[^a-z0-9]+", "", (model_id or "").lower())
        return "qwen36plus" in compact

    def is_kimi_k3(self, model_id: str) -> bool:
        normalized = (
            self.ports.strip_context_suffix(model_id)
            .strip()
            .lower()
            .replace("_", "-")
        )
        if normalized.startswith("ciel-runtime-kimi-"):
            normalized = normalized[len("ciel-runtime-kimi-") :]
        return normalized in {"k3", "kimi-k3", "kimi/k3", "kimi-code/k3"}

    def zai_hint(self, model_id: str) -> int | None:
        model = (
            self.ports.strip_context_suffix(model_id)
            .strip()
            .lower()
            .replace("_", "-")
        )
        if not model:
            return None
        for prefix, limit in self.zai_hints:
            if model == prefix or model.startswith(prefix + "-"):
                return limit
        return None

    def resolve(self, model_id: str) -> int | None:
        model = (model_id or "").lower()
        if not model:
            return None
        zai_hint = self.zai_hint(model_id)
        if zai_hint:
            return zai_hint
        if self.is_qwen36_plus(model_id):
            return 1048576
        if self.is_kimi_k3(model_id):
            return 1048576 if "[1m]" in model else 262144
        catalog_limit, _family, _source = self.ports.catalog_context(model_id)
        if catalog_limit:
            return catalog_limit
        if any(
            marker in model
            for marker in (
                "deepseek-v4-pro",
                "deepseek-v4-flash",
                "deepseek-v4",
                "v4-pro",
                "v4-flash",
                "1m",
                "million",
            )
        ):
            return 1048576
        if any(
            marker in model
            for marker in (
                "kimi-for-coding",
                "kimi-code",
                "kimi-k2.7",
                "kimi_k2.7",
                "kimi2.7",
                "k2.7",
                "kimi-k2.6",
                "kimi_k2.6",
                "kimi2.6",
                "kimi-k2",
            )
        ):
            return 262144
        if "qwen3.6" in model:
            return 262144
        if "glm-4.7" in model or "glm-5.1" in model:
            return 200000
        if "deepseek-r1" in model or "llama3.3" in model:
            return 131072
        return self.ports.positive_int(
            self.ports.model_preset(model_id).get("num_ctx_max")
        )
