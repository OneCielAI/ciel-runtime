"""Provider sampling option normalization and validation policy."""

from __future__ import annotations

from typing import Any

from .config_value_codec import finite_float, positive_int


class ProviderSamplingPolicy:
    """Canonicalize and validate provider-independent sampling options."""

    _ALIASES = {
        "temp": "temperature",
        "temperature": "temperature",
        "top": "top_p",
        "top_p": "top_p",
        "topp": "top_p",
        "topk": "top_k",
        "top_k": "top_k",
    }

    def option_key(self, key: str) -> str | None:
        normalized = key.strip().lower().replace("-", "_")
        return self._ALIASES.get(normalized)

    def validate(self, key: str, value: Any) -> float | int:
        if key == "temperature":
            fixed = finite_float(value)
            if fixed is None or fixed < 0 or fixed > 2:
                raise SystemExit("temperature must be a number from 0 to 2")
            return fixed
        if key == "top_p":
            fixed = finite_float(value)
            if fixed is None or fixed <= 0 or fixed > 1:
                raise SystemExit("top_p must be a number greater than 0 and up to 1")
            return fixed
        if key == "top_k":
            fixed = positive_int(value)
            if not fixed:
                raise SystemExit("top_k must be a positive integer")
            return fixed
        raise SystemExit(f"Unknown provider option: {key}")


__all__ = ["ProviderSamplingPolicy"]
