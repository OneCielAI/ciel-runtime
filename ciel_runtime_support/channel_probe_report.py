"""Presentation projection for channel capability probe results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelProbeReportServices:
    bucket: Callable[[dict[str, Any]], str]
    format_timestamp: Callable[[float], str]


def channel_probe_report_lines(
    result: dict[str, Any],
    timeout_seconds: float,
    services: ChannelProbeReportServices,
) -> list[str]:
    servers = result.get("servers") or []
    grouped = {
        name: [record for record in servers if services.bucket(record) == name]
        for name in ("capable", "non_capable", "inconclusive", "skipped")
    }
    probed_at = result.get("probed_at") or 0
    timestamp = services.format_timestamp(probed_at) if probed_at else "-"
    lines = [f"channel probe complete (probed at {timestamp}, timeout {timeout_seconds:.1f}s per server)"]
    lines.extend(_capable_lines(grouped["capable"]))
    lines.extend(_non_capable_lines(grouped["non_capable"]))
    inconclusive_lines, timeout_seen, exited_seen = _inconclusive_lines(grouped["inconclusive"])
    lines.extend(inconclusive_lines)
    lines.extend(_skipped_lines(grouped["skipped"]))
    if timeout_seen:
        lines.extend(
            (
                "  hint: inconclusive timeout means the probe could not finish; the server may still be capable.",
                "        increase CIEL_RUNTIME_CHANNEL_PROBE_TIMEOUT_SECONDS if cold start is the cause,",
                "        or re-run with the latest ciel-runtime if this was an SSE endpoint event race.",
            )
        )
    if exited_seen:
        lines.append("  hint: exited_without_response means the child died before responding. Check stderr above.")
    return lines


def _capable_lines(records: list[dict[str, Any]]) -> list[str]:
    lines = [f"  capable     : {len(records)}"]
    for record in records:
        transport = record.get("transport") or "?"
        source = record.get("source_path") or ""
        suffix = " built-in" if source == "<built-in>" else f" {source}"
        lines.append(f"    * {record.get('name')} ({transport}){suffix}")
    return lines


def _non_capable_lines(records: list[dict[str, Any]]) -> list[str]:
    lines = [f"  non-capable : {len(records)}"]
    for record in records:
        lines.append(
            f"      {record.get('name')} ({record.get('transport') or '?'}) reason={record.get('reason') or '-'}"
        )
    return lines


def _inconclusive_lines(records: list[dict[str, Any]]) -> tuple[list[str], bool, bool]:
    lines = [f"  inconclusive: {len(records)}"]
    timeout_seen = False
    exited_seen = False
    for record in records:
        reason = record.get("reason") or "-"
        extras = []
        for key, label in (("elapsed_ms", "elapsed"), ("response_bytes", "stdout"), ("stderr_bytes", "stderr")):
            value = record.get(key)
            if isinstance(value, int) and value:
                suffix = "ms" if key == "elapsed_ms" else "B"
                extras.append(f"{label}={value}{suffix}")
        if record.get("exit_code") is not None:
            extras.append(f"exit={record['exit_code']}")
        extra_text = " " + " ".join(extras) if extras else ""
        lines.append(
            f"      {record.get('name')} ({record.get('transport') or '?'}) reason={reason}{extra_text}"
        )
        if record.get("stderr_preview"):
            lines.append(f"        stderr: {record['stderr_preview']}")
        if record.get("stdout_preview"):
            lines.append(f"        stdout: {record['stdout_preview']}")
        timeout_seen = timeout_seen or str(reason).startswith("timeout")
        exited_seen = exited_seen or reason == "exited_without_response"
    return lines, timeout_seen, exited_seen


def _skipped_lines(records: list[dict[str, Any]]) -> list[str]:
    if not records:
        return []
    lines = [f"  skipped     : {len(records)}"]
    for record in records:
        lines.append(
            f"      {record.get('name')} ({record.get('transport') or '?'}) reason={record.get('reason') or '-'}"
        )
    return lines
