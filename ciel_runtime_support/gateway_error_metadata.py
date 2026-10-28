"""Validate known CielAiRouter error-only combo metadata, never generation siblings."""
from __future__ import annotations

from typing import Any


def _record(value: Any, required: set[str], optional: set[str] | None = None) -> bool:
    return (isinstance(value, dict) and required <= value.keys()
            and not value.keys() - required - (optional or set()))


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _hint(value: Any) -> bool:
    if not _record(value, {"action", "next_step"}, {"retry_after_seconds"}):
        return False
    if (value["action"] not in ("retry", "wait", "try-auto", "switch-combo")
            or not _text(value["next_step"], 200) or not value["next_step"].strip()):
        return False
    seconds = value.get("retry_after_seconds", 0)
    return type(seconds) is int and 0 <= seconds <= 3600


def _targets(value: Any, model_required: bool) -> bool:
    if not isinstance(value, list) or len(value) > 64:
        return False
    required = {"provider", "model"} if model_required else {"provider", "reason"}
    optional = set() if model_required else {"model"}
    for row in value:
        if not _record(row, required, optional):
            return False
        if not all(_text(v, 96 if k == "model" else 64) for k, v in row.items()):
            return False
    return True


def _diagnostics(value: Any) -> bool:
    if not _record(value, {"poolSize", "attempted", "excluded", "attemptOrder", "terminalReason"},
                   {"recovery", "skippedTargets"}):
        return False
    if not (_count(value["poolSize"]) and _count(value["attempted"])
            and _targets(value["excluded"], False) and _targets(value["attemptOrder"], True)
            and _text(value["terminalReason"], 200)):
        return False
    if "recovery" in value and not _hint(value["recovery"]):
        return False
    if "skippedTargets" in value:
        groups = value["skippedTargets"]
        if not isinstance(groups, list) or len(groups) > 32:
            return False
        for row in groups:
            if not _record(row, {"reason", "targets"}) or not _text(row["reason"], 64):
                return False
            targets = row["targets"]
            if (not isinstance(targets, list) or len(targets) > 32
                    or not all(_text(target, 96) for target in targets)):
                return False
    return True


def valid_error_metadata(value: dict) -> bool:
    """Only the published combo diagnostics/hint schemas may accompany an error.

    Hints are metadata, not retry authority: the caller still classifies the
    original error and applies its output boundary and shared finite budget.
    """
    return (("diagnostics" not in value or _diagnostics(value["diagnostics"]))
            and ("recovery_hint" not in value or _hint(value["recovery_hint"])))
