"""Embedded statusline program distributed with Ciel Runtime."""

STATUSLINE_SCRIPT = r'''#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HOME = Path.home()


def default_config_dir():
    configured = os.environ.get("CIEL_RUNTIME_CONFIG_DIR")
    if configured:
        return Path(configured)
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "ciel-runtime"
        return HOME / "AppData" / "Roaming" / "ciel-runtime"
    return HOME / ".config" / "ciel-runtime"


CONFIG_DIR = default_config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_PATH = CONFIG_DIR / "rate-limit-state.json"
ACTIVITY_PATH = CONFIG_DIR / "router-activity.json"
COMPACT_ACTIVITY_PATH = CONFIG_DIR / "context-compact-activity.json"
CONTEXT_PATH = CONFIG_DIR / "context-usage.json"
CHAT_MESSAGES_PATH = CONFIG_DIR / "chat-messages.jsonl"
CHANNEL_LLM_CURSOR_PATH = CONFIG_DIR / "channel-llm-cursor.json"
CHANNEL_LLM_CLEAR_FLOOR_PATH = CONFIG_DIR / "channel-llm-clear-floor.json"
PALETTE = (203, 209, 215, 221, 229, 187, 151, 116, 111, 147, 183, 219)


def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default


def _status_meaningful_key(value):
    text = str(value or "").strip()
    if not text:
        return False
    return text.lower() not in {"none", "null", "unset", "missing", "not set"}


def _status_parse_api_key_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[\r\n,;]+", value)
    elif isinstance(value, (list, tuple, set)):
        items = []
        for item in value:
            items.extend(_status_parse_api_key_list(item))
    else:
        items = [value]
    keys = []
    seen = set()
    for item in items:
        key = str(item or "").strip()
        if not _status_meaningful_key(key) or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def _status_provider_api_keys(pcfg):
    keys = []
    if isinstance(pcfg, dict):
        keys.extend(_status_parse_api_key_list(pcfg.get("api_keys")))
        keys.extend(_status_parse_api_key_list(pcfg.get("api_key")))
    out = []
    seen = set()
    for key in keys:
        if key in seen:
            continue
        out.append(key)
        seen.add(key)
    return out


def _status_provider_rotation_name(provider, pcfg):
    base = str((pcfg or {}).get("base_url") or "").rstrip("/")
    return f"{provider}:{base}" if base else f"{provider}:"


def _status_key_cooldown_summary(provider, pcfg, state, now):
    keys = _status_provider_api_keys(pcfg)
    if len(keys) <= 1 or not isinstance(state, dict):
        return ""
    prefix = _status_provider_rotation_name(provider, pcfg) + ":__key__:"
    live = 0
    next_until = 0.0
    cooling = 0
    for key in keys:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]
        entry = state.get(prefix + digest)
        until = 0.0
        if isinstance(entry, dict):
            try:
                until = float(entry.get("cooldown_until") or 0.0)
            except Exception:
                until = 0.0
        if until > now:
            cooling += 1
            next_until = until if not next_until else min(next_until, until)
        else:
            live += 1
    if not cooling:
        return f"RL {live}/{len(keys)}"
    wait_s = max(0.0, next_until - now) if next_until else 0.0
    if wait_s >= 3600:
        wait_text = f"{wait_s / 3600.0:.1f}h"
    elif wait_s >= 60:
        wait_text = f"{wait_s / 60.0:.0f}m"
    else:
        wait_text = f"{wait_s:.0f}s"
    text = f"RL {live}/{len(keys)} next {wait_text}"
    if live == 0 and len(keys) > 1:
        text = f"RL 0/{len(keys)} next {wait_text}"
    return text


def color(text):
    if os.environ.get("CIEL_RUNTIME_STATUSLINE_ANSI", "1").lower() in ("0", "false", "no"):
        return text
    phase = int(time.monotonic() * 8)
    out = []
    for i, ch in enumerate(text):
        if ch.isspace():
            out.append(ch)
        else:
            out.append(f"\033[1;38;5;{PALETTE[(phase + i) % len(PALETTE)]}m{ch}\033[0m")
    return "".join(out)


def gray(text):
    if os.environ.get("CIEL_RUNTIME_STATUSLINE_ANSI", "1").lower() in ("0", "false", "no"):
        return text
    return f"\033[38;5;245m{text}\033[0m"


def token_text(value):
    try:
        return f"{int(value):,} tok"
    except Exception:
        return f"{value} tok"


def token_part(value, muted=False):
    text = token_text(value)
    return gray(text) if muted else color(text)


def _status_positive_int(value):
    try:
        number = int(value)
        return number if number > 0 else 0
    except Exception:
        return 0


def status_config_context_limit(provider, pcfg):
    if not isinstance(pcfg, dict):
        return 0
    if provider in ("ollama", "ollama-cloud"):
        fixed = str(pcfg.get("num_ctx") or "").strip().lower()
        if fixed and fixed not in ("auto", "0", "false", "off", "none", "unset"):
            parsed = _status_positive_int(fixed)
            if parsed:
                return parsed
        return (
            _status_positive_int(pcfg.get("num_ctx_max"))
            or _status_positive_int(pcfg.get("model_context_max"))
            or _status_positive_int(pcfg.get("context_window"))
            or _status_positive_int(pcfg.get("max_model_len"))
        )
    return _status_positive_int(pcfg.get("context_window")) or _status_positive_int(pcfg.get("max_model_len"))


def context_status_text(context, provider, model, expected_limit=0):
    if not isinstance(context, dict):
        return ""
    if str(context.get("provider") or "") != str(provider or ""):
        return ""
    try:
        age = time.time() - float(context.get("updated_at") or 0)
    except Exception:
        age = 999999
    if age < 0 or age > 3600:
        return ""
    try:
        tokens = int(context.get("tokens") or 0)
    except Exception:
        tokens = 0
    if tokens <= 0:
        return ""
    limit = _status_positive_int(expected_limit) or _status_positive_int(context.get("context_limit"))
    if limit > 0:
        pct = (tokens / limit) * 100.0
        return f"ctx {tokens:,}/{limit:,} tok ({pct:.1f}%)"
    return f"ctx {tokens:,} tok"


def _as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def session_context_status_text(session):
    if not isinstance(session, dict):
        return ""
    context_window = session.get("context_window")
    if not isinstance(context_window, dict):
        return ""
    usage = context_window.get("current_usage")
    if not isinstance(usage, dict):
        return ""
    tokens = (
        _as_int(usage.get("input_tokens"))
        + _as_int(usage.get("cache_creation_input_tokens"))
        + _as_int(usage.get("cache_read_input_tokens"))
    )
    if tokens <= 0:
        tokens = _as_int(context_window.get("total_input_tokens"))
    if tokens <= 0:
        return ""
    limit = _as_int(context_window.get("context_window_size"))
    pct = context_window.get("used_percentage")
    if limit > 0:
        try:
            pct_value = float(pct) if pct is not None else (tokens / limit) * 100.0
        except Exception:
            pct_value = (tokens / limit) * 100.0
        return f"ctx {tokens:,}/{limit:,} tok ({pct_value:.1f}%)"
    return f"ctx {tokens:,} tok"


def _status_as_string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return _status_as_string_list(parsed)
        except Exception:
            pass
        return [text]
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_status_as_string_list(item))
        return out
    return [str(value).strip()] if str(value).strip() else []


def _status_channel_message_has_external_provenance(message):
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    for key in (
        "mcp_server",
        "mcp_method",
        "mcp_json",
        "sse_source",
        "sse_event",
        "sse_json",
        "stream_id",
        "sse_id",
        "cursor",
        "event_id",
        "message_id",
        "source_message_id",
        "rpc_id",
    ):
        value = meta.get(key)
        if value is not None and str(value).strip():
            return True
    return False


def _status_channel_message_skip_reason(message):
    visibility = str(message.get("visibility") or "user").strip().lower()
    if visibility in {"hidden", "internal", "transport", "control", "system"}:
        return f"visibility_{visibility}"
    recipients = {item.strip().lower() for item in _status_as_string_list(message.get("recipients"))}
    if "internal" in recipients:
        return "recipient_internal"
    delivery = _status_as_string_list(message.get("delivery"))
    if delivery:
        normalized_delivery = {item.strip().lower() for item in delivery}
        if not ({"all", "*", "llm"} & normalized_delivery):
            return "delivery_not_llm"
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    meta_kind = str(meta.get("kind") or meta.get("type") or meta.get("event_type") or meta.get("eventType") or meta.get("event") or meta.get("status") or "").strip().lower()
    if meta_kind in {
        "agent_presence",
        "check-in",
        "checkin",
        "checkins",
        "checked_in",
        "colleague_presence",
        "connection",
        "connected",
        "disconnect",
        "disconnected",
        "endpoint",
        "heartbeat",
        "initialized",
        "init",
        "keepalive",
        "ping",
        "pong",
        "presence",
        "ready",
        "status",
        "system",
        "user_presence",
    }:
        return meta_kind
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip().lower()
    kind = str(message.get("kind") or "").strip().lower()
    if not body:
        return "empty"
    if kind in {"connection", "connected", "heartbeat", "keepalive"}:
        return kind
    if re.fullmatch(r"[a-z0-9_.:-]{1,80}\.(ws|sse)\.connected", body):
        return "transport_connected"
    if not delivery and not _status_channel_message_has_external_provenance(message):
        return "unscoped_channel_message"
    return ""


def channel_pending_status_count():
    cursor = load_json(CHANNEL_LLM_CURSOR_PATH, {})
    last_id = _as_int(cursor.get("last_id") if isinstance(cursor, dict) else 0)
    count = 0
    try:
        if not CHAT_MESSAGES_PATH.exists():
            return 0
        with CHAT_MESSAGES_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                    item_id = int(item.get("id") or 0)
                except Exception:
                    continue
                if item_id <= last_id:
                    continue
                if _status_channel_message_skip_reason(item):
                    continue
                count += 1
                if count >= 999:
                    return count
    except Exception:
        return 0
    return count


def is_ciel_runtime_session(session):
    if os.environ.get("CIEL_RUNTIME_PROVIDER") or os.environ.get("CIEL_RUNTIME_MODEL_ALIAS"):
        return True
    if os.environ.get("CIEL_RUNTIME_STATUSLINE_FORCE", "").lower() in ("1", "true", "yes", "on"):
        return True
    model_name = ((session.get("model") or {}).get("display_name") if isinstance(session.get("model"), dict) else None) or ""
    return str(model_name).startswith("ciel-runtime-")


def display_capacity(rpm):
    if rpm <= 1:
        return rpm
    reserve = 1 if rpm <= 20 else max(1, int((rpm * 0.05) + 0.999))
    return max(1, rpm - reserve)


def main():
    try:
        session = json.load(sys.stdin)
        if not isinstance(session, dict):
            session = {}
    except Exception:
        session = {}
    if not is_ciel_runtime_session(session):
        return
    cfg = load_json(CONFIG_PATH, {})
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    provider = str(cfg.get("current_provider") or "")
    pcfg = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
    router_debug_external = bool(cfg.get("router_debug_external_access", False))
    rpm_status = bool(pcfg.get("rate_limit_status", False))
    migrations = cfg.get("migrations") if isinstance(cfg.get("migrations"), dict) else {}
    if not migrations.get("rate_limit_defaults_off_20260526"):
        if (
            provider in ("ollama", "ollama-cloud", "nvidia-hosted", "self-hosted-nim")
            and str(pcfg.get("rate_limit_rpm", "")).strip() == "40"
        ):
            rpm_status = False
        elif provider == "lm-studio" and str(pcfg.get("rate_limit_rpm", "")).strip() == "0":
            rpm_status = False
    model = str(pcfg.get("current_model") or "")
    raw_rpm = pcfg.get("rate_limit_rpm", 0)
    try:
        rpm = int(raw_rpm)
    except Exception:
        rpm = 0
    state = load_json(STATE_PATH, {})
    activity = load_json(ACTIVITY_PATH, {})
    compact_activity = load_json(COMPACT_ACTIVITY_PATH, {})
    context = load_json(CONTEXT_PATH, {})
    now = time.time()
    key = f"{provider}:__global__" if provider else ""
    entry = state.get(key) if key else None
    if not isinstance(entry, dict):
        legacy_key = f"{provider}:{model}" if provider and model else ""
        entry = state.get(legacy_key) if legacy_key else None
    if not isinstance(entry, dict):
        prefix = f"{provider}:"
        candidates = [(k, v) for k, v in state.items() if isinstance(k, str) and k.startswith(prefix) and ":__key__:" not in k and isinstance(v, dict)]
        if not candidates:
            candidates = [(k, v) for k, v in state.items() if isinstance(v, dict)]
        if candidates:
            key, entry = max(candidates, key=lambda item: float(item[1].get("updated_at") or 0))
    timestamps = entry.get("timestamps") if isinstance(entry, dict) else []
    if isinstance(entry, dict):
        try:
            rpm = int(entry.get("rpm") or rpm)
        except Exception:
            pass
    try:
        last_wait = float(entry.get("last_wait") or 0.0) if isinstance(entry, dict) else 0.0
    except Exception:
        last_wait = 0.0
    try:
        penalty_until = float(entry.get("penalty_until") or 0.0) if isinstance(entry, dict) else 0.0
    except Exception:
        penalty_until = 0.0
    try:
        updated_at = float(entry.get("updated_at") or 0.0) if isinstance(entry, dict) else 0.0
    except Exception:
        updated_at = 0.0
    server_remaining = entry.get("server_remaining") if isinstance(entry, dict) else None
    server_reset_seconds = entry.get("server_reset_seconds") if isinstance(entry, dict) else None
    server_rpm = entry.get("server_rpm") if isinstance(entry, dict) else None
    server_max_concurrent = entry.get("server_max_concurrent") if isinstance(entry, dict) else None
    server_active = entry.get("server_active") if isinstance(entry, dict) else None
    server_queue_limit = entry.get("server_queue_limit") if isinstance(entry, dict) else None
    server_queued = entry.get("server_queued") if isinstance(entry, dict) else None
    used = len([ts for ts in (timestamps or []) if isinstance(ts, (int, float)) and 0.0 <= now - float(ts) < 60.0])
    model_name = ((session.get("model") or {}).get("display_name") if isinstance(session.get("model"), dict) else None) or model or "model"
    current_dir = ((session.get("workspace") or {}).get("current_dir") if isinstance(session.get("workspace"), dict) else None) or session.get("cwd") or ""
    dir_name = Path(current_dir).name if current_dir else ""
    left = f"[{model_name}]"
    if dir_name:
        left += f" {dir_name}"
    status_parts = []
    expected_ctx_limit = status_config_context_limit(provider, pcfg)
    router_ctx_text = context_status_text(context, provider, model, expected_ctx_limit)
    session_ctx_text = session_context_status_text(session)
    ctx_text = router_ctx_text or session_ctx_text
    if ctx_text:
        status_parts.append(gray(ctx_text))
    channel_pending = channel_pending_status_count()
    if channel_pending > 0:
        status_parts.append(color(f"channel queue {channel_pending}"))
    if router_debug_external:
        status_parts.append(color("debug external"))
    if rpm_status:
        key_rl_text = _status_key_cooldown_summary(provider, pcfg, state, now)
        if key_rl_text:
            rpm_text = key_rl_text
        elif rpm > 0:
            shown_limit = display_capacity(rpm)
            shown_used = min(used, shown_limit)
            rpm_text = f"RPM used: {shown_used}/{shown_limit}"
        else:
            rpm_text = f"RPM used: {used}/min (unmanaged)"
        if server_rpm or server_remaining is not None or server_reset_seconds is not None:
            parts = []
            if server_remaining is not None:
                parts.append(f"remaining {server_remaining}")
            if server_rpm:
                parts.append(f"limit {server_rpm}")
            try:
                if server_reset_seconds is not None and float(server_reset_seconds) > 0:
                    parts.append(f"reset {float(server_reset_seconds):.0f}s")
            except Exception:
                pass
            if parts:
                rpm_text += " | server " + ", ".join(parts)
        if server_max_concurrent is not None or server_active is not None:
            try:
                active_text = "?" if server_active is None else str(int(server_active))
                max_text = "?" if server_max_concurrent is None else str(int(server_max_concurrent))
                rpm_text += f" | conc {active_text}/{max_text}"
            except Exception:
                pass
        if server_queue_limit is not None or server_queued is not None:
            try:
                queued_text = "?" if server_queued is None else str(int(server_queued))
                limit_text = "?" if server_queue_limit is None else str(int(server_queue_limit))
                rpm_text += f" | q {queued_text}/{limit_text}"
            except Exception:
                pass
        if penalty_until > now:
            rpm_text += f" | wait {max(0.0, penalty_until - now):.0f}s"
        elif last_wait >= 0.5 and 0.0 <= now - updated_at < 60.0:
            rpm_text += f" | wait {last_wait:.1f}s"
        status_parts.append(color(rpm_text))
    activity_text = ""
    if isinstance(activity, dict):
        try:
            age = now - float(activity.get("updated_at") or 0)
        except Exception:
            age = 999999
        if 0 <= age < 180:
            event = str(activity.get("event") or "")
            if event == "retry":
                activity_text = color(f"retry {activity.get('attempt')}/{activity.get('total')}")
                wait = activity.get("wait")
                try:
                    if rpm_status and wait is not None and float(wait) > 0:
                        activity_text += " " + color(f"wait {float(wait):.0f}s")
                except Exception:
                    pass
                tokens = activity.get("tokens")
                if tokens:
                    activity_text += " " + color("last input") + " " + token_part(tokens, muted=rpm_status)
            elif event == "request":
                tokens = activity.get("tokens")
                activity_text = color(f"upstream {age:.0f}s")
                if tokens:
                    activity_text += " " + token_part(tokens, muted=rpm_status)
                output_tokens = activity.get("output_tokens")
                if output_tokens:
                    activity_text += " " + color("->") + " " + token_part(output_tokens, muted=rpm_status)
                chunks = activity.get("chunks")
                if chunks:
                    try:
                        activity_text += " " + color(f"({int(chunks):,} chunks)")
                    except Exception:
                        activity_text += " " + color(f"({chunks} chunks)")
            elif event in ("success", "error"):
                activity_text = color(f"{event} {age:.0f}s")
    if activity_text:
        status_parts.append(activity_text)
    compact_text = ""
    if isinstance(compact_activity, dict):
        try:
            compact_age = now - float(compact_activity.get("updated_at") or 0)
        except Exception:
            compact_age = 999999
        if 0 <= compact_age < 180:
            try:
                compact_chunks = int(compact_activity.get("chunks") or 0)
            except Exception:
                compact_chunks = 0
            try:
                parallel_sessions = int(compact_activity.get("parallel_sessions") or 1)
            except Exception:
                parallel_sessions = 1
            if compact_chunks > 0:
                compact_text = color(f"compact {compact_chunks:,} chunks")
                if parallel_sessions > 1:
                    compact_text += " " + color(f"parallel {parallel_sessions:,}/{compact_chunks:,}")
            elif str(compact_activity.get("event") or "") == "compact":
                compact_text = color("compact")
    if compact_text:
        status_parts.append(compact_text)
    status_text = " | ".join(status_parts)
    if status_text:
        print(f"{left} | {status_text}")
    else:
        print(left)


if __name__ == "__main__":
    main()

'''

