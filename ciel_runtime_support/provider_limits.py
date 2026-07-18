from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProviderKeyServices:
    _API_KEY_ROTATION_CURSOR: Any
    _API_KEY_ROTATION_LOCK: Any
    api_key_cooldown_until: Callable[..., Any]
    provider_api_key_rotation_name: Callable[..., Any]
    provider_config_api_keys: Callable[..., Any]
    router_log: Callable[..., Any]


def choose_provider_api_key(provider: str, pcfg: dict[str, Any], *, rotate: bool = True,
    services: ProviderKeyServices,
) -> str:
    _API_KEY_ROTATION_CURSOR = services._API_KEY_ROTATION_CURSOR
    _API_KEY_ROTATION_LOCK = services._API_KEY_ROTATION_LOCK
    api_key_cooldown_until = services.api_key_cooldown_until
    provider_api_key_rotation_name = services.provider_api_key_rotation_name
    provider_config_api_keys = services.provider_config_api_keys
    router_log = services.router_log
    keys = provider_config_api_keys(provider, pcfg)
    if not keys:
        return ""
    if not rotate or len(keys) == 1:
        return keys[0]
    name = provider_api_key_rotation_name(provider, pcfg)
    # Skip keys that are resting after a 429; round-robin over the live ones so a
    # rate-limited key rests while the rest keep serving. If every key is cooling,
    # use the one that frees up soonest.
    now = time.time()
    live = [k for k in keys if api_key_cooldown_until(provider, pcfg, k) <= now]
    if not live:
        soonest = min(keys, key=lambda k: api_key_cooldown_until(provider, pcfg, k))
        router_log("DEBUG", f"api_key_round_robin provider={provider} all_cooling={len(keys)} using_soonest")
        return soonest
    with _API_KEY_ROTATION_LOCK:
        counter = _API_KEY_ROTATION_CURSOR.get(name, 0)
        _API_KEY_ROTATION_CURSOR[name] = counter + 1
    idx = counter % len(live)
    if len(live) < len(keys):
        router_log("DEBUG", f"api_key_round_robin provider={provider} key_index={idx + 1}/{len(live)} cooling={len(keys) - len(live)}")
    else:
        router_log("DEBUG", f"api_key_round_robin provider={provider} key_index={idx + 1}/{len(keys)}")
    return live[idx]



@dataclass(frozen=True, slots=True)
class RateLimitLearningServices:
    CONFIG_DIR: Any
    RATE_LIMIT_STATE_PATH: Any
    _RATE_LIMIT_LOCK: Any
    current_upstream_model_id: Callable[..., Any]
    first_header: Callable[..., Any]
    first_int_in_header: Callable[..., Any]
    provider_api_key_count: Callable[..., Any]
    rate_limit_reset_seconds: Callable[..., Any]
    router_log: Callable[..., Any]
    router_rate_limit_configured_rpm: Callable[..., Any]
    router_rate_limit_key: Callable[..., Any]
    router_rate_limit_recent: Callable[..., Any]


def learn_rate_limit_headers(provider: str, pcfg: dict[str, Any], model: str | None, headers: Any,
    *,
    services: RateLimitLearningServices,
) -> None:
    CONFIG_DIR = services.CONFIG_DIR
    RATE_LIMIT_STATE_PATH = services.RATE_LIMIT_STATE_PATH
    _RATE_LIMIT_LOCK = services._RATE_LIMIT_LOCK
    current_upstream_model_id = services.current_upstream_model_id
    first_header = services.first_header
    first_int_in_header = services.first_int_in_header
    provider_api_key_count = services.provider_api_key_count
    rate_limit_reset_seconds = services.rate_limit_reset_seconds
    router_log = services.router_log
    router_rate_limit_configured_rpm = services.router_rate_limit_configured_rpm
    router_rate_limit_key = services.router_rate_limit_key
    router_rate_limit_recent = services.router_rate_limit_recent
    limit = first_int_in_header(first_header(headers, [
        "x-ratelimit-limit-requests",
        "x-rate-limit-limit-requests",
        "ratelimit-limit",
        "rate-limit-limit",
        "x-ratelimit-limit",
        "x-rate-limit-limit",
    ]))
    remaining = first_int_in_header(first_header(headers, [
        "x-ratelimit-remaining-requests",
        "x-rate-limit-remaining-requests",
        "ratelimit-remaining",
        "rate-limit-remaining",
        "x-ratelimit-remaining",
        "x-rate-limit-remaining",
    ]))
    reset = rate_limit_reset_seconds(first_header(headers, [
        "x-ratelimit-reset-requests",
        "x-rate-limit-reset-requests",
        "ratelimit-reset",
        "rate-limit-reset",
        "x-ratelimit-reset",
        "x-rate-limit-reset",
    ]))
    max_concurrent = first_int_in_header(first_header(headers, [
        "x-ratelimit-max-concurrent",
        "x-rate-limit-max-concurrent",
        "ratelimit-max-concurrent",
        "rate-limit-max-concurrent",
    ]))
    active = first_int_in_header(first_header(headers, [
        "x-ratelimit-active",
        "x-rate-limit-active",
        "ratelimit-active",
        "rate-limit-active",
    ]))
    queue_limit = first_int_in_header(first_header(headers, [
        "x-ratelimit-queue-limit",
        "x-rate-limit-queue-limit",
        "ratelimit-queue-limit",
        "rate-limit-queue-limit",
    ]))
    queued = first_int_in_header(first_header(headers, [
        "x-ratelimit-queued",
        "x-rate-limit-queued",
        "ratelimit-queued",
        "rate-limit-queued",
    ]))
    if (
        limit is None
        and remaining is None
        and reset is None
        and max_concurrent is None
        and active is None
        and queue_limit is None
        and queued is None
    ):
        return
    configured = router_rate_limit_configured_rpm(provider, pcfg)
    rpm = limit if limit and limit > 0 else configured
    if rpm is None:
        rpm = 0
    multi_key = provider_api_key_count(provider, pcfg) > 1
    key = router_rate_limit_key(provider, pcfg, model)
    with _RATE_LIMIT_LOCK:
        try:
            state = json.loads(RATE_LIMIT_STATE_PATH.read_text(encoding="utf-8")) if RATE_LIMIT_STATE_PATH.exists() else {}
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}
        now = time.time()
        entry = state.get(key)
        if not isinstance(entry, dict):
            legacy_key = f"{provider}:{model or current_upstream_model_id(provider, pcfg)}"
            entry = state.get(legacy_key)
        timestamps = entry.get("timestamps") if isinstance(entry, dict) else []
        recent = router_rate_limit_recent(timestamps, now, 60.0, include_future=True)
        penalty_until = 0.0 if multi_key else (float(entry.get("penalty_until") or 0.0) if isinstance(entry, dict) else 0.0)
        if remaining == 0 and reset and reset > 0 and not multi_key:
            penalty_until = max(penalty_until, now + reset)
        new_entry: dict[str, Any] = {
            "timestamps": recent[-max(int(rpm or 0), 240):],
            "rpm": int(rpm or 0),
            "updated_at": now,
            "last_wait": float(entry.get("last_wait") or 0.0) if isinstance(entry, dict) else 0.0,
            "server_remaining": remaining,
            "server_reset_seconds": reset,
        }
        if max_concurrent is not None:
            new_entry["server_max_concurrent"] = max_concurrent
        elif isinstance(entry, dict) and entry.get("server_max_concurrent") is not None:
            new_entry["server_max_concurrent"] = entry.get("server_max_concurrent")
        if active is not None:
            new_entry["server_active"] = active
        elif isinstance(entry, dict) and entry.get("server_active") is not None:
            new_entry["server_active"] = entry.get("server_active")
        if queue_limit is not None:
            new_entry["server_queue_limit"] = queue_limit
        elif isinstance(entry, dict) and entry.get("server_queue_limit") is not None:
            new_entry["server_queue_limit"] = entry.get("server_queue_limit")
        if queued is not None:
            new_entry["server_queued"] = queued
        elif isinstance(entry, dict) and entry.get("server_queued") is not None:
            new_entry["server_queued"] = entry.get("server_queued")
        if limit and limit > 0:
            new_entry["server_rpm"] = int(limit)
            new_entry["server_rpm_updated_at"] = now
        elif isinstance(entry, dict) and entry.get("server_rpm"):
            new_entry["server_rpm"] = entry.get("server_rpm")
            new_entry["server_rpm_updated_at"] = entry.get("server_rpm_updated_at")
        if penalty_until > now:
            new_entry["penalty_until"] = penalty_until
        state[key] = new_entry
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    extra = " multi_key_no_global_penalty=1" if multi_key and remaining == 0 and reset and reset > 0 else ""
    router_log(
        "INFO",
        f"rate_limit_headers provider={provider} model={model or ''} limit={limit} remaining={remaining} reset={reset}"
        f" max_concurrent={max_concurrent} active={active} queue_limit={queue_limit} queued={queued}{extra}",
    )



@dataclass(frozen=True, slots=True)
class RateLimitBackoffServices:
    CONFIG_DIR: Any
    RATE_LIMIT_STATE_PATH: Any
    _RATE_LIMIT_LOCK: Any
    current_upstream_model_id: Callable[..., Any]
    parse_retry_after_seconds: Callable[..., Any]
    provider_api_key_count: Callable[..., Any]
    router_log: Callable[..., Any]
    router_rate_limit_capacity: Callable[..., Any]
    router_rate_limit_configured_rpm: Callable[..., Any]
    router_rate_limit_effective_rpm: Callable[..., Any]
    router_rate_limit_key: Callable[..., Any]
    router_rate_limit_recent: Callable[..., Any]


def register_rate_limit_backoff(provider: str, pcfg: dict[str, Any], model: str | None, retry_after: str | None = None,
    *,
    services: RateLimitBackoffServices,
) -> float:
    CONFIG_DIR = services.CONFIG_DIR
    RATE_LIMIT_STATE_PATH = services.RATE_LIMIT_STATE_PATH
    _RATE_LIMIT_LOCK = services._RATE_LIMIT_LOCK
    current_upstream_model_id = services.current_upstream_model_id
    parse_retry_after_seconds = services.parse_retry_after_seconds
    provider_api_key_count = services.provider_api_key_count
    router_log = services.router_log
    router_rate_limit_capacity = services.router_rate_limit_capacity
    router_rate_limit_configured_rpm = services.router_rate_limit_configured_rpm
    router_rate_limit_effective_rpm = services.router_rate_limit_effective_rpm
    router_rate_limit_key = services.router_rate_limit_key
    router_rate_limit_recent = services.router_rate_limit_recent
    rpm = router_rate_limit_effective_rpm(provider, pcfg, model)
    fallback = 60.0 / float(rpm) if rpm and rpm > 0 else 15.0
    wait = parse_retry_after_seconds(retry_after)
    if wait is None:
        wait = max(10.0, min(60.0, fallback * 4.0))
    wait = max(1.0, min(300.0, wait))
    key = router_rate_limit_key(provider, pcfg, model)
    multi_key = provider_api_key_count(provider, pcfg) > 1
    with _RATE_LIMIT_LOCK:
        try:
            state = json.loads(RATE_LIMIT_STATE_PATH.read_text(encoding="utf-8")) if RATE_LIMIT_STATE_PATH.exists() else {}
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}
        now = time.time()
        entry = state.get(key)
        if not isinstance(entry, dict):
            legacy_key = f"{provider}:{model or current_upstream_model_id(provider, pcfg)}"
            entry = state.get(legacy_key)
        timestamps = entry.get("timestamps") if isinstance(entry, dict) else []
        recent = router_rate_limit_recent(timestamps, now, 60.0, include_future=True)
        actual_recent = router_rate_limit_recent(timestamps, now, 60.0, include_future=False)
        configured_rpm = router_rate_limit_configured_rpm(provider, pcfg)
        inferred_rpm: int | None = None
        if (
            isinstance(entry, dict)
            and not entry.get("server_rpm")
            and configured_rpm
            and configured_rpm > 0
            and 0 < len(actual_recent) < configured_rpm
        ):
            inferred_rpm = max(1, len(actual_recent))
            rpm = inferred_rpm
        capacity = router_rate_limit_capacity(int(rpm or 0)) if rpm and rpm > 0 else int(rpm or 0)
        if capacity and capacity > 0 and len(actual_recent) >= capacity and actual_recent:
            wait = max(wait, max(0.0, actual_recent[0] + 60.0 - now))
        existing_penalty_until = 0.0 if multi_key else (float(entry.get("penalty_until") or 0.0) if isinstance(entry, dict) else 0.0)
        penalty_until = max(existing_penalty_until, now + wait) if not multi_key else 0.0
        state[key] = {
            "timestamps": recent[-max(int(rpm or 0), 240):],
            "rpm": int(rpm or 0),
            "updated_at": now,
            "last_wait": wait,
            "last_429_at": now,
        }
        if penalty_until > now:
            state[key]["penalty_until"] = penalty_until
        if isinstance(entry, dict):
            for preserve_key in (
                "server_rpm",
                "server_rpm_updated_at",
                "server_remaining",
                "server_reset_seconds",
                "server_max_concurrent",
                "server_active",
                "server_queue_limit",
                "server_queued",
            ):
                if preserve_key in entry:
                    state[key][preserve_key] = entry[preserve_key]
        if inferred_rpm:
            state[key]["server_rpm"] = inferred_rpm
            state[key]["server_rpm_updated_at"] = now
            state[key]["server_rpm_reason"] = "inferred_from_429"
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    extra = " multi_key_no_global_penalty=1" if multi_key else ""
    router_log("WARN", f"rate_limit_429_backoff provider={provider} model={model or ''} wait={wait:.2f}s{extra}")
    return wait



@dataclass(frozen=True, slots=True)
class RateLimitApplyServices:
    CONFIG_DIR: Any
    RATE_LIMIT_STATE_PATH: Any
    _RATE_LIMIT_LOCK: Any
    current_upstream_model_id: Callable[..., Any]
    provider_api_key_count: Callable[..., Any]
    record_router_rate_usage: Callable[..., Any]
    router_log: Callable[..., Any]
    router_rate_limit_capacity: Callable[..., Any]
    router_rate_limit_effective_rpm: Callable[..., Any]
    router_rate_limit_key: Callable[..., Any]
    router_rate_limit_recent: Callable[..., Any]
    wait_for_router_rate_limit_penalty: Callable[..., Any]


def apply_rate_limit(provider: str, pcfg: dict[str, Any], model: str | None = None,
    *,
    services: RateLimitApplyServices,
) -> tuple[float, int, int | None]:
    CONFIG_DIR = services.CONFIG_DIR
    RATE_LIMIT_STATE_PATH = services.RATE_LIMIT_STATE_PATH
    _RATE_LIMIT_LOCK = services._RATE_LIMIT_LOCK
    current_upstream_model_id = services.current_upstream_model_id
    provider_api_key_count = services.provider_api_key_count
    record_router_rate_usage = services.record_router_rate_usage
    router_log = services.router_log
    router_rate_limit_capacity = services.router_rate_limit_capacity
    router_rate_limit_effective_rpm = services.router_rate_limit_effective_rpm
    router_rate_limit_key = services.router_rate_limit_key
    router_rate_limit_recent = services.router_rate_limit_recent
    wait_for_router_rate_limit_penalty = services.wait_for_router_rate_limit_penalty
    rpm = router_rate_limit_effective_rpm(provider, pcfg, model)
    if rpm is None:
        waited = wait_for_router_rate_limit_penalty(provider, pcfg, model, rpm)
        return waited, 0, None
    if rpm <= 0:
        waited = wait_for_router_rate_limit_penalty(provider, pcfg, model, rpm)
        used, limit = record_router_rate_usage(provider, pcfg, model, rpm)
        return waited, used, limit
    window = 60.0
    base_interval = window / float(rpm)
    capacity = router_rate_limit_capacity(rpm)
    key = router_rate_limit_key(provider, pcfg, model)
    multi_key = provider_api_key_count(provider, pcfg) > 1
    waited = 0.0
    while True:
        with _RATE_LIMIT_LOCK:
            try:
                state = json.loads(RATE_LIMIT_STATE_PATH.read_text(encoding="utf-8")) if RATE_LIMIT_STATE_PATH.exists() else {}
                if not isinstance(state, dict):
                    state = {}
            except Exception:
                state = {}
            now = time.time()
            entry = state.get(key)
            if not isinstance(entry, dict):
                legacy_key = f"{provider}:{model or current_upstream_model_id(provider, pcfg)}"
                entry = state.get(legacy_key)
            if isinstance(entry, dict):
                timestamps = entry.get("timestamps")
                try:
                    penalty_until = 0.0 if multi_key else float(entry.get("penalty_until") or 0.0)
                except Exception:
                    penalty_until = 0.0
            elif isinstance(entry, (int, float)):
                timestamps = [float(entry)]
                penalty_until = 0.0
            else:
                timestamps = []
                penalty_until = 0.0
            recent = router_rate_limit_recent(timestamps, now, window, include_future=True)
            used = len(recent)
            usage_ratio = min(1.0, used / float(capacity))
            wait = 0.0
            if penalty_until > now:
                wait = max(wait, penalty_until - now)
            if used >= capacity and recent:
                wait = max(0.0, recent[0] + window - now)
            elif recent:
                elapsed_since_last = max(0.0, now - recent[-1])
                wait = max(0.0, base_interval - elapsed_since_last)
                if usage_ratio >= 0.70:
                    pressure = (usage_ratio - 0.70) / 0.30
                    target_interval = base_interval * (1.0 + max(0.0, min(1.0, pressure)) * 3.0)
                    wait = max(wait, target_interval - elapsed_since_last)
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            if wait <= 0.001:
                recent.append(now)
                new_entry = {"timestamps": recent[-rpm:], "rpm": rpm, "updated_at": now, "last_wait": waited}
                if penalty_until > now:
                    new_entry["penalty_until"] = penalty_until
                state[key] = new_entry
                RATE_LIMIT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
                return waited, len(recent), rpm
            if used < capacity:
                scheduled = now + wait
                recent.append(scheduled)
                new_entry = {"timestamps": recent[-rpm:], "rpm": rpm, "updated_at": scheduled, "last_wait": wait}
                if penalty_until > now:
                    new_entry["penalty_until"] = penalty_until
                state[key] = new_entry
                RATE_LIMIT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
                router_log("INFO", f"rate_limit_soft_wait provider={provider} model={model or ''} rpm={rpm} wait={wait:.2f}s")
                time.sleep(wait)
                return waited + wait, len(recent), rpm
        sleep_for = min(wait, 10.0)
        router_log("INFO", f"rate_limit_wait provider={provider} model={model or ''} rpm={rpm} wait={wait:.2f}s waited={waited:.2f}s")
        time.sleep(sleep_for)
        waited += sleep_for



__all__ = ['choose_provider_api_key','ProviderKeyServices','learn_rate_limit_headers','RateLimitLearningServices','register_rate_limit_backoff','RateLimitBackoffServices','apply_rate_limit','RateLimitApplyServices']
