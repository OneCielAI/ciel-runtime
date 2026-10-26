"""Guarded self-reset when external SSE input is not reaching the session.

A stale observable queue can justify an explicitly requested local restart;
it does not prove a transport failure. Compaction admission pauses veto resets.
Durable cooldown reservations bound attempts when restart is uncertain.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .channel_message_repository import exclusive_file_lock

_RESET_LOCK = threading.RLock()

DEFAULT_MIN_QUEUE_AGE_SECONDS = 120.0
DEFAULT_COOLDOWN_SECONDS = 900.0

# The private queue the delivery observer reads next to its status history.
_QUEUE_FILE_NAME = "runtime-inputs.jsonl"
_QUEUE_PROBE_BYTES = 2 * 1024 * 1024
_QUEUE_PROBE_LINE_BYTES = 1024 * 1024


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _read_reset_record(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("recovery record exceeds size limit")
        value = json.loads(raw)
    except FileNotFoundError:
        return {}
    if (not isinstance(value, dict) or _number(value.get("last_reset_at")) is None
            or type(value.get("count")) is not int or value["count"] < 0):
        raise ValueError("invalid recovery record")
    return value


def _write_reset_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(record), indent=2, allow_nan=False) + chr(10))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _paired_queue_has_input(status: Any) -> bool:
    """Whether the private queue file really holds at least one input row.

    The delivery observer counts a missing queue file as a complete empty
    queue, so a status history alone can look like a stale real queue. A
    restart additionally requires the paired queue to exist as a readable
    regular file with one decodable input record. This probe is read-only:
    it creates, truncates and repairs nothing.
    """
    path = getattr(status, "path", None)
    if not path:
        return False
    try:
        queue = Path(path).with_name(_QUEUE_FILE_NAME)
        if not queue.is_file():
            return False
        scanned = 0
        with queue.open("rb") as stream:
            while scanned < _QUEUE_PROBE_BYTES:
                raw = stream.readline(_QUEUE_PROBE_LINE_BYTES + 1)
                if not raw:
                    return False
                scanned += len(raw)
                if len(raw) > _QUEUE_PROBE_LINE_BYTES or not raw.endswith(b"\n"):
                    # Skip the rest of an oversized or torn physical row.
                    while raw and not raw.endswith(b"\n") and scanned < _QUEUE_PROBE_BYTES:
                        raw = stream.readline(_QUEUE_PROBE_LINE_BYTES + 1)
                        if not raw:
                            return False
                        scanned += len(raw)
                    continue
                try:
                    row = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if not isinstance(row, dict):
                    continue
                try:
                    request_id = int(row.get("id") or 0)
                except (TypeError, ValueError, OverflowError):
                    continue
                if request_id > 0:
                    return True
        return False
    except (OSError, ValueError, TypeError):
        return False


def sse_delivery_evidence(
    *,
    delivery_health: Callable[[], Mapping[str, Any]],
    admission_paused: Callable[[], bool] | None = None,
    min_queue_age_seconds: float = DEFAULT_MIN_QUEUE_AGE_SECONDS,
) -> dict[str, Any]:
    """Payload-free facts that justify (or do not justify) a reset."""

    try:
        health = dict(delivery_health())
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"problem": False, "observable": False, "restart_eligible": False,
                "reason": f"delivery_health_unavailable:{type(exc).__name__}"}
    pause_observable = True
    try:
        paused = admission_paused() if admission_paused is not None else None
        if type(paused) is not bool:
            pause_observable, paused = False, None
    except (OSError, ValueError, TypeError, AttributeError):
        pause_observable, paused = False, None
    queued_number = _number(health.get("known_queued_count"))
    queued = int(queued_number) if queued_number is not None and queued_number.is_integer() else 0
    oldest = _number(health.get("oldest_queued_age_seconds"))
    blocked = health.get("admission_blocked") is True
    reused_number = _number(health.get("reused_id_count"))
    reused = int(reused_number) if reused_number is not None and reused_number.is_integer() else 0
    min_age = _number(min_queue_age_seconds)
    stale = queued > 0 and oldest is not None and min_age is not None and oldest >= max(1.0, min_age)
    # This is observed delivery delay, not proof of a transport failure. Do
    # not promote a partial history scan (which may have missed a terminal
    # status) or an unavailable queue to restart authority.
    queue_observable = health.get("queued_coverage") == "complete"
    restart_signal = stale and queue_observable
    reasons = []
    if blocked:
        reasons.append("admission_blocked")
    if paused:
        reasons.append("compaction_admission_paused")
    if stale:
        reasons.append("queued_input_stale")
    if reused > 0:
        # New inputs carrying ids whose terminal status predates them are
        # skipped as already delivered. A restart keeps the same files, so this
        # is reported, never restart authority.
        reasons.append("input_id_reuse")
    return {
        "problem": bool(blocked or stale or paused or reused > 0),
        "observable": True,
        "restart_eligible": bool(restart_signal and pause_observable and paused is False),
        "reasons": reasons,
        "state": str(health.get("state") or "unknown"),
        "reason": str(health.get("reason") or ""),
        "admission_blocked": health.get("admission_blocked"),
        "admission_paused": paused,
        "pause_observable": pause_observable,
        "launcher_hold_observed": health.get("launcher_hold_observed") is True,
        "blocker_scope": health.get("blocker_scope"),
        "known_queued_count": queued,
        "reused_id_count": reused,
        "reused_id_newest": health.get("reused_id_newest"),
        "oldest_queued_age_seconds": oldest,
        "queued_coverage": health.get("queued_coverage"),
        "history_coverage": (health.get("history") or {}).get("coverage")
        if isinstance(health.get("history"), Mapping) else None,
        "line_number": health.get("line_number"),
    }


def _recover_sse_delivery(
    args: Mapping[str, Any],
    *,
    delivery_health: Callable[[], Mapping[str, Any]],
    admission_paused: Callable[[], bool] | None,
    restart: Callable[..., dict[str, Any]],
    state_path: Path,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """status | reset | auto.  Reset only on evidence, once per cooldown."""

    if not isinstance(args, Mapping):
        return {"ok": False, "error": "arguments must be an object"}
    action = args.get("action", "status")
    if action not in ("status", "reset", "auto"):
        return {"ok": False, "error": "action must be status, reset or auto"}
    for key in ("reason", "runtime"):
        if key in args and not isinstance(args[key], str):
            return {"ok": False, "error": f"{key} must be a string"}
    force = args.get("force", False)
    if type(force) is not bool:
        return {"ok": False, "error": "force must be a boolean"}
    if force:
        return {"ok": False, "error": "recovery cannot bypass the recovery cooldown"}
    cooldown = _number(args.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
    min_age = _number(args.get("min_queue_age_seconds", DEFAULT_MIN_QUEUE_AGE_SECONDS))
    if cooldown is None:
        return {"ok": False, "error": "cooldown_seconds must be finite and nonnegative"}
    if min_age is None or min_age < 1:
        return {"ok": False, "error": "min_queue_age_seconds must be finite and at least 1"}
    client_pid = args.get("client_pid", 0)
    if type(client_pid) is not int or client_pid < 0:
        return {"ok": False, "error": "client_pid must be a nonnegative integer"}
    cooldown = max(DEFAULT_COOLDOWN_SECONDS, cooldown)
    min_age = max(DEFAULT_MIN_QUEUE_AGE_SECONDS, min_age)
    evidence = sse_delivery_evidence(
        delivery_health=delivery_health,
        admission_paused=admission_paused,
        min_queue_age_seconds=min_age,
    )
    record = _read_reset_record(Path(state_path))
    last_reset = _number(record.get("last_reset_at")) or 0.0
    current = _number(now())
    if current is None or current <= 0:
        return {"ok": False, "reset": False, "error": "clock must be finite and positive"}
    in_cooldown = last_reset > 0 and current - last_reset < cooldown
    result: dict[str, Any] = {
        "ok": True, "action": action, "problem": evidence["problem"],
        "evidence": evidence, "checked_at": current,
        "last_reset_at": last_reset or None,
        "cooldown_seconds": cooldown,
        "cooldown_remaining_seconds": max(0.0, cooldown - (current - last_reset)) if in_cooldown else 0.0,
    }
    if action == "status":
        result["reset"] = False
        return result
    if not evidence["problem"]:
        result.update(reset=False, reason="no_sse_delivery_problem")
        return result
    if not evidence.get("restart_eligible"):
        result.update(reset=False, reason="compaction_admission_paused" if evidence.get("admission_paused")
                      else "restart_evidence_insufficient")
        return result
    if in_cooldown and not force:
        result.update(reset=False, reason="cooldown_active")
        return result
    # Reserve before outward-facing work; uncertain attempts retain cooldown.
    _write_reset_record(Path(state_path), {
        "last_reset_at": current,
        "count": int(record.get("count", 0)) + 1,
        "reasons": evidence["reasons"],
    })
    try:
        restart_result = restart(
            reason=str(args.get("reason") or "sse_delivery_recovery"),
            runtime=str(args.get("runtime") or ""),
            client_pid=client_pid,
            resume=True,
        )
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result.update(ok=False, reset=False, reason=f"restart_failed:{type(exc).__name__}")
        return result
    if not isinstance(restart_result, Mapping):
        result.update(ok=False, reset=False, reason="restart_result_unavailable")
        return result
    result["restart"] = dict(restart_result)
    if restart_result.get("ok") is not True:
        result.update(reset=False, reason="restart_refused")
        return result
    result.update(reset=True, reason="sse_delivery_recovery_queued")
    return result


def recover_sse_delivery(args: Mapping[str, Any], *, delivery_health: Callable[[], Mapping[str, Any]],
                         admission_paused: Callable[[], bool] | None,
                         restart: Callable[..., dict[str, Any]], state_path: Path,
                         now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Serialize check/reservation/restart across threads and router processes."""
    try:
        with _RESET_LOCK:
            if not isinstance(args, Mapping) or args.get("action", "status") == "status":
                return _recover_sse_delivery(args, delivery_health=delivery_health,
                                             admission_paused=admission_paused, restart=restart,
                                             state_path=state_path, now=now)
            with exclusive_file_lock(Path(state_path)):
                return _recover_sse_delivery(args, delivery_health=delivery_health,
                                             admission_paused=admission_paused, restart=restart,
                                             state_path=state_path, now=now)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        return {"ok": False, "reset": False,
                "reason": f"recovery_state_unavailable:{type(exc).__name__}"}


def router_recovery(args: Mapping[str, Any], *, status_repository: Any, compaction: Any,
                    restart: Callable[..., dict[str, Any]], root: Path) -> dict[str, Any]:
    """Instance-local diagnostics; never restart a different or ambiguous client."""

    from .runtime_input_delivery_health import runtime_input_delivery_health

    def restart_local(**kwargs: Any) -> dict[str, Any]:
        service = getattr(restart, "__self__", None)
        clients_port = getattr(service, "clients", None)
        if not callable(clients_port):
            return {"ok": False, "detail": "active router client identity unavailable"}
        clients = clients_port()
        if len(clients) != 1:
            return {"ok": False, "detail": "active router client is missing or ambiguous"}
        pid = clients[0].get("pid")
        if type(pid) is not int or pid <= 0 or kwargs["client_pid"] not in (0, pid):
            return {"ok": False, "detail": "client_pid is not the active router client"}
        kwargs["client_pid"] = pid
        return restart(**kwargs)

    def delivery_health() -> dict[str, Any]:
        health = dict(runtime_input_delivery_health(status_repository))
        if not _paired_queue_has_input(status_repository):
            # Historical queued status rows without a real private queue are
            # not a delivered backlog; they must never authorize a restart.
            health["queued_coverage"] = "unknown"
            health["reason"] = "queueunavailable"
        return health

    result = recover_sse_delivery(
        args,
        delivery_health=delivery_health,
        admission_paused=lambda: compaction.external_admission_paused(),
        restart=restart_local,
        state_path=Path(root) / "sse-recovery.json",
    )
    status = getattr(compaction, "recovery_status", None)
    if callable(status):
        result["compaction"] = status()
    return result


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MIN_QUEUE_AGE_SECONDS",
    "recover_sse_delivery",
    "router_recovery",
    "sse_delivery_evidence",
]
