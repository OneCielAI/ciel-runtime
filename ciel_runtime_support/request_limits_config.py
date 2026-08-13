"""Workspace-scoped request and decoded media limit configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .workspace_router_selection import workspace_digest, workspace_identity


MIB = 1024 * 1024
JSON_ENVELOPE_OVERHEAD_BYTES = MIB
REQUEST_BODY_MEMORY_MULTIPLIER = 5
GENERAL_CONTROL_REQUEST_MAX_BYTES = 4 * MIB
INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES = 4 * 1024 * MIB
# A provider-neutral aggregate wire ceiling.  It is the largest request whose
# uniform memory reservation can fit inside the technical in-flight maximum.
TTS_BATCH_REQUEST_MAX_BYTES = (
    INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES // REQUEST_BODY_MEMORY_MULTIPLIER
)
REQUEST_LIMITS_CONFIG_KEY = "request_limits"


@dataclass(frozen=True, slots=True)
class RequestLimitSpec:
    key: str
    label: str
    default_bytes: int
    minimum_bytes: int
    hard_max_bytes: int
    environment_name: str


REQUEST_LIMIT_SPECS: tuple[RequestLimitSpec, ...] = (
    RequestLimitSpec(
        "model_request_max_bytes",
        "Model request transport",
        512 * MIB,
        1,
        512 * MIB,
        "CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES",
    ),
    RequestLimitSpec(
        "chat_attachment_max_bytes",
        "Chat attachment (decoded)",
        500 * MIB,
        1,
        500 * MIB,
        "CIEL_RUNTIME_CHAT_FILE_MAX_BYTES",
    ),
    RequestLimitSpec(
        "speech_audio_max_bytes",
        "ASR / speech input (decoded)",
        500 * MIB,
        1,
        500 * MIB,
        "CIEL_RUNTIME_SPEECH_AUDIO_MAX_BYTES",
    ),
    RequestLimitSpec(
        "tts_reference_audio_max_bytes",
        "TTS reference audio (decoded)",
        500 * MIB,
        1,
        500 * MIB,
        "CIEL_RUNTIME_TTS_REFERENCE_AUDIO_MAX_BYTES",
    ),
    RequestLimitSpec(
        "inflight_request_max_bytes",
        "In-flight request memory",
        INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES,
        1,
        INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES,
        "CIEL_RUNTIME_ROUTER_INFLIGHT_REQUEST_BYTES",
    ),
)
REQUEST_LIMIT_SPEC_BY_KEY = {spec.key: spec for spec in REQUEST_LIMIT_SPECS}


def base64_json_wire_max_bytes(decoded_max_bytes: int) -> int:
    """Bound a base64 JSON envelope without pretending it equals decoded size."""
    encoded = 4 * ((max(0, int(decoded_max_bytes)) + 2) // 3)
    return encoded + JSON_ENVELOPE_OVERHEAD_BYTES


@dataclass(frozen=True, slots=True)
class WorkspaceRequestLimits:
    workspace: str
    model_request_max_bytes: int
    chat_attachment_max_bytes: int
    speech_audio_max_bytes: int
    tts_reference_audio_max_bytes: int
    configured_inflight_request_max_bytes: int
    inflight_request_max_bytes: int
    sources: Mapping[str, str]

    @property
    def chat_attachment_wire_max_bytes(self) -> int:
        return base64_json_wire_max_bytes(self.chat_attachment_max_bytes)

    @property
    def speech_audio_wire_max_bytes(self) -> int:
        return base64_json_wire_max_bytes(self.speech_audio_max_bytes)

    @property
    def tts_reference_wire_max_bytes(self) -> int:
        return base64_json_wire_max_bytes(self.tts_reference_audio_max_bytes)

    @property
    def tts_batch_wire_max_bytes(self) -> int:
        return TTS_BATCH_REQUEST_MAX_BYTES

    @property
    def largest_wire_request_bytes(self) -> int:
        return max(
            self.model_request_max_bytes,
            self.chat_attachment_wire_max_bytes,
            self.speech_audio_wire_max_bytes,
            self.tts_reference_wire_max_bytes,
            self.tts_batch_wire_max_bytes,
            GENERAL_CONTROL_REQUEST_MAX_BYTES,
        )


def workspace_request_limit_record(
    config: Mapping[str, Any] | None,
    workspace: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    target = workspace_identity(workspace)
    root = config.get(REQUEST_LIMITS_CONFIG_KEY) if isinstance(config, Mapping) else None
    record = root.get(workspace_digest(target)) if isinstance(root, Mapping) else None
    if not isinstance(record, Mapping):
        return {}
    saved_workspace = workspace_identity(record.get("workspace"))
    if saved_workspace and saved_workspace != target:
        return {}
    return dict(record)


def resolve_workspace_request_limits(
    config: Mapping[str, Any] | None,
    workspace: str | os.PathLike[str] | None,
    environment: Mapping[str, str] | None = None,
) -> WorkspaceRequestLimits:
    target = workspace_identity(workspace)
    record = workspace_request_limit_record(config, target)
    environ = os.environ if environment is None else environment
    values: dict[str, int] = {}
    sources: dict[str, str] = {}
    for spec in REQUEST_LIMIT_SPECS:
        persisted = _bounded_int(record.get(spec.key), spec)
        value = persisted if persisted is not None else spec.default_bytes
        source = "workspace" if persisted is not None else "default"
        overridden = _bounded_int(environ.get(spec.environment_name), spec)
        if overridden is not None:
            value = overridden
            source = f"environment:{spec.environment_name}"
        values[spec.key] = value
        sources[spec.key] = source

    provisional = WorkspaceRequestLimits(
        workspace=target,
        model_request_max_bytes=values["model_request_max_bytes"],
        chat_attachment_max_bytes=values["chat_attachment_max_bytes"],
        speech_audio_max_bytes=values["speech_audio_max_bytes"],
        tts_reference_audio_max_bytes=values["tts_reference_audio_max_bytes"],
        configured_inflight_request_max_bytes=values["inflight_request_max_bytes"],
        inflight_request_max_bytes=values["inflight_request_max_bytes"],
        sources=sources,
    )
    effective_inflight = max(
        provisional.configured_inflight_request_max_bytes,
        REQUEST_BODY_MEMORY_MULTIPLIER * provisional.largest_wire_request_bytes,
    )
    return WorkspaceRequestLimits(
        workspace=provisional.workspace,
        model_request_max_bytes=provisional.model_request_max_bytes,
        chat_attachment_max_bytes=provisional.chat_attachment_max_bytes,
        speech_audio_max_bytes=provisional.speech_audio_max_bytes,
        tts_reference_audio_max_bytes=provisional.tts_reference_audio_max_bytes,
        configured_inflight_request_max_bytes=provisional.configured_inflight_request_max_bytes,
        inflight_request_max_bytes=effective_inflight,
        sources=provisional.sources,
    )


def update_workspace_request_limit(
    config: dict[str, Any],
    workspace: str | os.PathLike[str] | None,
    key: str,
    value: Any,
) -> int | None:
    target = workspace_identity(workspace)
    root = config.setdefault(REQUEST_LIMITS_CONFIG_KEY, {})
    if not isinstance(root, dict):
        root = {}
        config[REQUEST_LIMITS_CONFIG_KEY] = root
    digest = workspace_digest(target)
    if key == "reset":
        root.pop(digest, None)
        if not root:
            config.pop(REQUEST_LIMITS_CONFIG_KEY, None)
        return None
    spec = REQUEST_LIMIT_SPEC_BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"unknown request limit setting: {key}")
    parsed = parse_menu_size(value)
    if not spec.minimum_bytes <= parsed <= spec.hard_max_bytes:
        raise ValueError(
            f"{spec.label} must be between "
            f"{format_mib(spec.minimum_bytes)} and {format_mib(spec.hard_max_bytes)}"
        )
    record = workspace_request_limit_record(config, target)
    record["workspace"] = target
    record[key] = parsed
    root[digest] = record
    return parsed


def parse_menu_size(value: Any) -> int:
    text = str(value or "").strip().replace("_", "")
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(b|byte|bytes|kib|kb|mib|mb|gib|gb)?",
        text,
        re.I,
    )
    if not match:
        raise ValueError("enter a size such as 128, 128 MiB, or 1 GiB")
    number = float(match.group(1))
    suffix = (match.group(2) or "mib").casefold()
    multiplier = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000 * 1000,
        "mib": MIB,
        "gb": 1000 * 1000 * 1000,
        "gib": 1024 * MIB,
    }[suffix]
    result = int(number * multiplier)
    if result <= 0:
        raise ValueError("request limit must be positive")
    return result


def format_mib(value: int) -> str:
    if value < MIB:
        return f"{value} byte" if value == 1 else f"{value} bytes"
    mib = value / MIB
    return f"{int(mib)} MiB" if mib.is_integer() else f"{mib:.1f} MiB"


@dataclass(frozen=True, slots=True)
class RequestLimitsMenuService:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    workspace: str
    environment: Mapping[str, str]

    def panel_rows(self, config: dict[str, Any]) -> tuple[list[str], list[str]]:
        limits = resolve_workspace_request_limits(config, self.workspace, self.environment)
        rows: list[str] = []
        for spec in REQUEST_LIMIT_SPECS:
            value = (
                limits.configured_inflight_request_max_bytes
                if spec.key == "inflight_request_max_bytes"
                else getattr(limits, spec.key)
            )
            source = str(limits.sources.get(spec.key) or "default")
            suffix = f" · {source}" if source != "default" else ""
            if (
                spec.key == "inflight_request_max_bytes"
                and limits.inflight_request_max_bytes > value
            ):
                suffix += f" · effective {format_mib(limits.inflight_request_max_bytes)}"
            rows.append(f"{spec.label}  [{format_mib(value)}{suffix}]")
        rows.extend(["Reset this workspace to defaults", "Back"])
        return rows, [spec.key for spec in REQUEST_LIMIT_SPECS] + ["reset", "back"]

    def prompt_default(self, config: dict[str, Any], key: str) -> str:
        spec = REQUEST_LIMIT_SPEC_BY_KEY[key]
        record = workspace_request_limit_record(config, self.workspace)
        value = _bounded_int(record.get(key), spec) or spec.default_bytes
        return format_mib(value)

    def update(self, key: str, value: Any) -> list[str]:
        config = self.load_config()
        updated = update_workspace_request_limit(config, self.workspace, key, value)
        self.save_config(config)
        if key == "reset":
            return ["Request/file limits reset for this workspace."]
        spec = REQUEST_LIMIT_SPEC_BY_KEY[key]
        limits = resolve_workspace_request_limits(config, self.workspace, self.environment)
        lines = [f"{spec.label}: {format_mib(int(updated or 0))} for this workspace."]
        source = str(limits.sources.get(key) or "")
        if source.startswith("environment:"):
            lines.append(f"{source.split(':', 1)[1]} currently overrides the saved value.")
        if limits.inflight_request_max_bytes > limits.configured_inflight_request_max_bytes:
            lines.append(
                "Effective in-flight memory was raised to "
                f"{format_mib(limits.inflight_request_max_bytes)} to reserve "
                f"{REQUEST_BODY_MEMORY_MULTIPLIER}x the largest wire request."
            )
        lines.append("The limit applies when this workspace router next starts.")
        return lines


def _bounded_int(value: Any, spec: RequestLimitSpec) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return min(spec.hard_max_bytes, max(spec.minimum_bytes, parsed))


__all__ = [
    "JSON_ENVELOPE_OVERHEAD_BYTES",
    "GENERAL_CONTROL_REQUEST_MAX_BYTES",
    "INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES",
    "MIB",
    "REQUEST_LIMITS_CONFIG_KEY",
    "REQUEST_LIMIT_SPECS",
    "REQUEST_BODY_MEMORY_MULTIPLIER",
    "TTS_BATCH_REQUEST_MAX_BYTES",
    "RequestLimitSpec",
    "RequestLimitsMenuService",
    "WorkspaceRequestLimits",
    "base64_json_wire_max_bytes",
    "format_mib",
    "parse_menu_size",
    "resolve_workspace_request_limits",
    "update_workspace_request_limit",
    "workspace_request_limit_record",
]
