"""Prelaunch channel panel projection policy."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelPanelPolicy:
    builtin_router_probe_record: Callable[..., Any]
    channel_specs: Callable[..., Any]
    delivery_mode: Callable[..., Any]
    official_plugins: dict[str, str]
    probe_record_bucket: Callable[..., Any]
    read_probe_cache: Callable[..., Any]


def channel_panel_rows(
    cfg: dict[str, Any], *, policy: ChannelPanelPolicy
) -> tuple[list[str], list[str]]:
    OFFICIAL_CHANNEL_PLUGINS = policy.official_plugins
    _builtin_router_probe_record = policy.builtin_router_probe_record
    channel_probe_record_bucket = policy.probe_record_bucket
    channel_specs = policy.channel_specs
    read_channel_probe_cache = policy.read_probe_cache
    channels = set(channel_specs(cfg))
    cache = read_channel_probe_cache()
    records = cache.get("servers") or []
    if not any(isinstance(r, dict) and r.get("name") == "ciel-runtime-router" for r in records):
        records = [_builtin_router_probe_record(), *records]
    probed_at = cache.get("probed_at") or 0
    capable_records = [r for r in records if channel_probe_record_bucket(r) == "capable"]
    non_capable_records = [r for r in records if channel_probe_record_bucket(r) == "non_capable"]
    inconclusive_records = [r for r in records if channel_probe_record_bucket(r) == "inconclusive"]
    skipped_records = [r for r in records if channel_probe_record_bucket(r) == "skipped"]

    rows: list[str] = []
    values: list[str] = []

    rows.append("[Auto-detected channel-capable]")
    values.append("__heading__")
    if capable_records:
        for r in capable_records:
            name = str(r.get("name") or "")
            spec = f"server:{name}"
            mark = "*" if spec in channels else " "
            transport = str(r.get("transport") or "?")
            source = str(r.get("source_path") or "")
            if source == "<built-in>":
                rows.append(f"{mark} {name:<14} ({transport}, built-in)")
            else:
                rows.append(f"{mark} {name:<14} ({transport})")
            values.append(spec)
    else:
        hint = "press Re-probe now" if not probed_at else "none capable"
        rows.append(f"  ({hint})")
        values.append("__noop__")

    if non_capable_records:
        rows.append("[Detected but not channel-capable]")
        values.append("__heading__")
        for r in non_capable_records:
            name = str(r.get("name") or "")
            transport = str(r.get("transport") or "?")
            reason = str(r.get("reason") or "-")
            rows.append(f"  {name:<14} ({transport}) {reason}")
            values.append("__noop__")

    if inconclusive_records:
        rows.append("[Probe inconclusive / selectable anyway]")
        values.append("__heading__")
        for r in inconclusive_records:
            name = str(r.get("name") or "")
            spec = f"server:{name}"
            mark = "*" if spec in channels else " "
            transport = str(r.get("transport") or "?")
            reason = str(r.get("reason") or "-")
            rows.append(f"{mark} {name:<14} ({transport}) {reason}  [select anyway]")
            values.append(spec)

    if skipped_records:
        rows.append("[Not probed]")
        values.append("__heading__")
        for r in skipped_records:
            name = str(r.get("name") or "")
            transport = str(r.get("transport") or "?")
            reason = str(r.get("reason") or "-")
            rows.append(f"  {name:<14} ({transport}) {reason}")
            values.append("__noop__")

    rows.append("[Official plugins]")
    values.append("__heading__")
    for plugin_name, spec in OFFICIAL_CHANNEL_PLUGINS.items():
        mark = "*" if spec in channels else " "
        rows.append(f"{mark} {plugin_name:<10} {spec}")
        values.append(spec)

    covered: set[str] = set(OFFICIAL_CHANNEL_PLUGINS.values())
    for r in records:
        covered.add(f"server:{r.get('name')}")
    custom_specs = [spec for spec in channel_specs(cfg) if spec not in covered]
    if custom_specs:
        rows.append("[Configured custom / imported]")
        values.append("__heading__")
        for spec in custom_specs:
            rows.append(f"* {spec}")
            values.append(spec)

    rows.append("[Actions]")
    values.append("__heading__")
    if probed_at:
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(probed_at))
        rows.append(f"Re-probe now (last: {ts_str})")
    else:
        rows.append("Re-probe now (no cache yet)")
    values.append("__reprobe__")
    rows.append("+ Add custom channel...")
    values.append("__add_custom__")
    if channels:
        rows.append("- Remove channel...")
        values.append("__remove__")
        rows.append("Clear all channels")
        values.append("__clear__")
    rows.append("Back")
    values.append("back")
    return rows, values


def _channel_panel_first_selectable(values: list[str]) -> int:
    for idx, value in enumerate(values):
        if value not in ("__heading__", "__noop__"):
            return idx
    return 0


def _channel_panel_step(values: list[str], start: int, delta: int) -> int:
    if not values:
        return 0
    n = len(values)
    idx = start
    for _ in range(n):
        idx = (idx + delta) % n
        if values[idx] not in ("__heading__", "__noop__"):
            return idx
    return start


def channel_delivery_panel_rows(
    cfg: dict[str, Any], *, policy: ChannelPanelPolicy
) -> tuple[list[str], list[str]]:
    channel_delivery_mode = policy.delivery_mode
    current = channel_delivery_mode(cfg)
    rows = [
        f"{'*' if current == 'llm' else ' '} llm    inject channel messages into next model request",
        f"{'*' if current == 'native' else ' '} native Claude Code claude/channel bridge; requires Channels",
        f"{'*' if current == 'stdin' else ' '} stdin  PTY wake proxy; terminal input fallback",
        "Back",
    ]
    return rows, ["llm", "native", "stdin", "back"]

