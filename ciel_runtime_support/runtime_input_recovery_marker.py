"""Dependency-free offline recovery generation fence for durable readers."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class RecoveryRefused(RuntimeError):
    """Offline proof, confirmation or transaction integrity failed."""

def recovery_marker(queue: Path) -> Path:
    return queue.with_name(queue.name + ".recovery.json")

def recovery_generation(queue: Path) -> dict[str, Any] | None:
    marker = recovery_marker(queue)
    if not marker.exists():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RecoveryRefused("recovery marker unreadable") from exc
    if (not isinstance(value, dict) or value.get("phase") != "finalized"
            or value.get("version") != 1 or not isinstance(value.get("generation"), str)
            or type(value.get("id_floor")) is not int or value["id_floor"] < 1):
        raise RecoveryRefused("offline history recovery requires finalization")
    return value

def guard_status_history(status: Path, request_ids=()) -> None:
    generation = recovery_generation(status.with_name("runtime-inputs.jsonl"))
    if generation and any(int(i) < generation["id_floor"] for i in request_ids):
        raise RecoveryRefused("request belongs to quarantined history")
