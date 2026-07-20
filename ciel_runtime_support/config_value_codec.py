"""Scalar parsing and validation for environment, CLI, and persisted config."""

from __future__ import annotations

import json
import math
from typing import Any


def positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_config_value(value: str) -> Any:
    text = value.strip()
    normalized = text.lower()
    if normalized in {"true", "yes", "on"}:
        return True
    if normalized in {"false", "no", "off"}:
        return False
    if normalized in {"none", "null"}:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return int(text)
    except Exception:
        pass
    try:
        return float(text)
    except Exception:
        return text


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "on", "1", "enable", "enabled"}:
        return True
    if normalized in {"false", "no", "off", "0", "disable", "disabled"}:
        return False
    return default


__all__ = ["finite_float", "parse_bool", "parse_config_value", "positive_int"]
