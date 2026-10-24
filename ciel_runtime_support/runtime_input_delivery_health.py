"""Payload-free read-only delivery observations, never delivery authority."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import nullcontext
from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time
from typing import Any

from .runtime_input_status import RuntimeInputStatusRepository

_SCAN_BYTES = 8 * 1024 * 1024
_SCAN_LINE_BYTES = 1024 * 1024
_CACHE_SECONDS = 2.0
_CACHE_LIMIT = 32
_CACHE: OrderedDict = OrderedDict()
_LOCK = threading.RLock()


def unavailable_delivery_health() -> dict[str, Any]:
    return {"state": "unknown", "reason": "delivery_observation_unavailable",
            "admission_blocked": None, "launcher_hold_observed": False,
            "known_queued_count": 0, "oldest_queued_age_seconds": None,
            "queued_coverage": "unknown", "history": {"coverage": "unknown"}}


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _input_rows(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Bound the private input scan used only for receiver scope and age."""
    rows = []
    complete = True
    offset = 0
    opened = False
    try:
        with path.open("rb") as stream:
            opened = True
            before = path.stat()
            while offset < _SCAN_BYTES:
                raw = stream.readline(min(_SCAN_LINE_BYTES + 1, _SCAN_BYTES - offset))
                if not raw:
                    break
                offset += len(raw)
                if len(raw) > _SCAN_LINE_BYTES:
                    complete = False
                    # Skip this physical row, not every later valid record.
                    while not raw.endswith(bytes([10])) and offset < _SCAN_BYTES:
                        raw = stream.readline(min(_SCAN_LINE_BYTES + 1, _SCAN_BYTES - offset))
                        if not raw:
                            break
                        offset += len(raw)
                    continue
                if not raw.endswith(bytes([10])):
                    complete = False
                    break
                try:
                    row = json.loads(raw)
                    if isinstance(row, dict):
                        rows.append(row)
                    else:
                        complete = False
                except (ValueError, UnicodeError, RecursionError):
                    complete = False
            after = path.stat()
            def signature(stat):
                return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            complete = complete and offset >= before.st_size and signature(before) == signature(after)
    except FileNotFoundError:
        complete = not opened
    except OSError:
        complete = False
    return rows, complete


def _status_history(status: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One pass with the repository's own decoder and damage/recovery rules.

    Reusing the production scan keeps this observer from diverging: a line the
    status history ignores is ignored here, and a recovered row counts here.
    """
    scan = getattr(status, "_scan_history", None)
    if callable(scan):
        with getattr(status, "lock", None) or nullcontext():
            return scan()
    # Retain the existing path/history_health observation-port contract.
    # Production repositories take the single-pass branch above.
    repository = RuntimeInputStatusRepository(Path(status.path), lambda **_kw: None,
                                              lambda *_args: None, threading.RLock())
    rows, history = repository._scan_history()
    return rows, history if history.get("coverage") == "unknown" else status.history_health()


def _snapshot(status: Any, inputs_path: Path, receiver_id: str | None) -> dict[str, Any]:
    status_rows, history = _status_history(status)
    input_rows, inputs_complete = _input_rows(inputs_path)
    latest = {}
    first_queued = {}
    for row in status_rows:
        try:
            request_id = int(row.get("request_id") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if request_id <= 0:
            continue
        latest[request_id] = row
        if row.get("status") == "queued":
            stamp = _epoch(row.get("updated_at_epoch"))
            if stamp is not None:
                first_queued[request_id] = min(stamp, first_queued.get(request_id, stamp))
    inputs = {}
    for row in input_rows:
        try:
            request_id = int(row.get("id") or 0)
        except (TypeError, ValueError, OverflowError):
            inputs_complete = False
            continue
        if request_id > 0:
            inputs[request_id] = row
    queued = []
    for request_id, row in latest.items():
        if row.get("status") != "queued":
            continue
        message = inputs.get(request_id, {})
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if receiver_id is not None and not (
            meta.get("receiver_id") == receiver_id or
            message.get("channel") == "external:" + receiver_id or
            data.get("channel") == "external:" + receiver_id
        ):
            continue
        # Original queue time wins over repeated queued status writes.
        stamp = _epoch(message.get("created_at_epoch")) or first_queued.get(request_id)
        queued.append(stamp)
    outcomes = _delivery_outcomes(latest)
    complete = bool(history.get("complete"))
    first = next(iter(history.get("diagnostics") or []), {})
    stamps = [stamp for stamp in queued if stamp is not None]
    # Status history ignores damaged lines, so damage is never an admission
    # blocker here: it only makes this observation partial. A history that
    # could not be read at all is unknown, never a confirmed hold.
    available = history.get("coverage") != "unknown"
    observation_complete = complete and inputs_complete
    return {"state": "not_blocked" if available else "unknown",
            "reason": ("delivery_observation_unavailable" if not available else
                       "" if observation_complete else "history_observation_partial"),
            "scope": "private_runtime_inputs", "blocker_scope": None,
            "admission_blocked": False if available else None, "launcher_hold_observed": False,
            "line_number": first.get("line_number"),
            "known_queued_count": len(queued),
            "oldest_queued_at_epoch": min(stamps) if stamps else None,
            "queue_age_coverage": "complete" if len(stamps) == len(queued) else "partial",
            "queued_coverage": ("unknown" if not available else
                                "complete" if observation_complete else "partial"),
            **outcomes,
            "history": history}


def _delivery_outcomes(latest: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Resends waiting, resends exhausted, launch expiries and the last failure."""
    retrying = exhausted = expired = 0
    last_failure: dict[str, Any] | None = None
    for request_id, row in latest.items():
        status = row.get("status")
        reason = str(row.get("reason") or "")
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if status == "queued" and isinstance(data.get("retry"), dict):
            retrying += 1
        elif status == "skipped" and reason == "launch_replay_window_expired":
            expired += 1
        elif status == "failed":
            exhausted += reason.startswith("retry_exhausted:")
            stamp = _epoch(row.get("updated_at_epoch")) or 0.0
            if last_failure is None or stamp >= last_failure["at_epoch"]:
                last_failure = {"request_id": request_id, "reason": reason[:120], "at_epoch": stamp}
    return {"retrying_count": retrying, "retry_exhausted_count": exhausted,
            "launch_expired_count": expired, "last_failure": last_failure}


def runtime_input_delivery_health(status: Any = None, *, status_path: Path | None = None,
                                  inputs_path: Path | None = None,
                                  receiver_id: str | None = None,
                                  now: float | None = None) -> dict[str, Any]:
    """Cache bounded observations for two seconds without touching durable state.

    Status is duck-typed (path, history_health). Native repositories use their
    own read-only scan once for both rows and health. These are private runtime
    input paths, never the public chat frontier. Known counts may be partial.
    """
    if status is None and status_path is None:
        return unavailable_delivery_health()
    if status is None:
        status = RuntimeInputStatusRepository(Path(status_path), lambda **_kw: None,
                                             lambda *_args: None, threading.RLock())
    if not getattr(status, "path", None) or not callable(getattr(status, "history_health", None)):
        return unavailable_delivery_health()
    path = Path(status.path)
    inputs_path = Path(inputs_path) if inputs_path is not None else path.with_name("runtime-inputs.jsonl")
    key = (str(path.absolute()), str(inputs_path.absolute()), receiver_id)
    clock = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is None or clock - cached[0] >= _CACHE_SECONDS:
            try:
                snapshot = _snapshot(status, inputs_path, receiver_id)
            except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                snapshot = unavailable_delivery_health()
            _CACHE[key] = (clock, snapshot)
            _CACHE.move_to_end(key)
            while len(_CACHE) > _CACHE_LIMIT:
                _CACHE.popitem(last=False)
        result = deepcopy(_CACHE[key][1])
    stamp = result.pop("oldest_queued_at_epoch", None)
    result["oldest_queued_age_seconds"] = max(0.0, (time.time() if now is None else now) - stamp) if stamp else None
    return result


def delivery_health_for_callback(callback: Any, *, receiver_id: str | None = None) -> dict[str, Any]:
    """Use existing bound status/gateway ports without adding another facade."""
    owner = getattr(callback, "__self__", None)
    status = getattr(owner, "status", owner)
    return runtime_input_delivery_health(status, receiver_id=receiver_id)


def delivery_health_text(health: dict[str, Any]) -> str:
    """Not blocked never promises injection or the launcher state."""
    state = health.get("state") or "unknown"
    count = health.get("known_queued_count", 0)
    age = health.get("oldest_queued_age_seconds")
    oldest = f"{int(age)}s" if isinstance(age, (int, float)) else "unknown"
    coverage = health.get("queued_coverage", "unknown")
    detail = f"{state}; known queued {count}; oldest {oldest}; coverage {coverage}"
    if health.get("retrying_count"):
        detail += f"; resending {health['retrying_count']}"
    if health.get("retry_exhausted_count"):
        detail += f"; resend exhausted {health['retry_exhausted_count']}"
    if health.get("launch_expired_count"):
        detail += f"; expired at launch {health['launch_expired_count']}"
    failure = health.get("last_failure")
    if isinstance(failure, dict) and failure.get("reason"):
        when = time.strftime("%H:%M", time.localtime(float(failure.get("at_epoch") or 0)))
        detail += f"; last failure #{failure.get('request_id')} {failure['reason'][:60]} at {when}"
    if health.get("reason") == "history_observation_partial":
        detail += "; history observation partial"
        if health.get("line_number"):
            detail += f" line {health['line_number']}"
        detail += " (damaged lines ignored)"
    elif state == "unknown":
        detail += "; observation unavailable"
    return detail
