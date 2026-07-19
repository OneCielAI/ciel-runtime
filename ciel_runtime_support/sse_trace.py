"""Bounded SSE event tracing and tool-call JSONL observability."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SseTraceConfig:
    config_dir: Path
    last_path: Path
    trace_path: Path
    tool_call_path: Path
    event_limit: int
    payload_limit: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class SseTracePorts:
    enabled: Callable[[], bool]
    truncate: Callable[[str, int], str]
    log: Callable[[str, str], None]


def summarize_payload(
    payload: dict[str, Any], truncate: Callable[[str, int], str]
) -> dict[str, Any]:
    summary: dict[str, Any] = {"type": payload.get("type")}
    if "index" in payload:
        summary["index"] = payload.get("index")
    payload_type = payload.get("type")
    if payload_type == "message_start":
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        summary["message"] = {
            "id": message.get("id"),
            "model": message.get("model"),
            "role": message.get("role"),
            "content_len": len(message.get("content") or []),
            "usage": message.get("usage"),
        }
    elif payload_type == "message_delta":
        summary["delta"] = payload.get("delta")
        summary["usage"] = payload.get("usage")
    elif payload_type == "content_block_start":
        block = payload.get("content_block") if isinstance(payload.get("content_block"), dict) else {}
        block_summary = {"type": block.get("type")}
        if block.get("type") == "tool_use":
            block_summary.update(
                {"id": block.get("id"), "name": block.get("name"), "input": block.get("input")}
            )
        elif block.get("type") == "text":
            block_summary["text_len"] = len(str(block.get("text") or ""))
        else:
            block_summary["keys"] = sorted(str(key) for key in block)
        summary["content_block"] = block_summary
    elif payload_type == "content_block_delta":
        delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
        delta_type = delta.get("type")
        delta_summary: dict[str, Any] = {"type": delta_type}
        if delta_type == "text_delta":
            text = str(delta.get("text") or "")
            delta_summary.update({"text_len": len(text), "text": truncate(text, 500)})
        elif delta_type == "input_json_delta":
            partial = str(delta.get("partial_json") or "")
            delta_summary.update(
                {
                    "partial_json_len": len(partial),
                    "partial_json": truncate(partial, 1000),
                }
            )
        else:
            delta_summary["keys"] = sorted(str(key) for key in delta)
        summary["delta"] = delta_summary
    else:
        summary["keys"] = sorted(str(key) for key in payload)
    return summary


class SseTraceRepository:
    def __init__(self, config: SseTraceConfig, ports: SseTracePorts) -> None:
        self.config = config
        self.ports = ports

    def begin(
        self,
        provider: str,
        model: str,
        source: str,
        source_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": provider,
            "model": model,
            "source": source,
            "stream": True,
            "messages_count": len(source_body.get("messages") or [])
            if isinstance(source_body, dict)
            else None,
            "tools_count": len(source_body.get("tools") or [])
            if isinstance(source_body, dict)
            else None,
            "metadata_keys": sorted(str(key) for key in (source_body.get("metadata") or {}))
            if isinstance(source_body, dict) and isinstance(source_body.get("metadata"), dict)
            else [],
            "event_count": 0,
            "events_truncated": False,
            "events": [],
        }

    def record(
        self, trace: dict[str, Any] | None, event_name: str, payload: dict[str, Any]
    ) -> None:
        if not isinstance(trace, dict):
            return
        try:
            trace["event_count"] = int(trace.get("event_count") or 0) + 1
            events = trace.get("events")
            if not isinstance(events, list):
                events = []
                trace["events"] = events
            if len(events) >= self.config.event_limit:
                trace["events_truncated"] = True
                return
            raw = ""
            if self.ports.enabled():
                try:
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    raw = self.ports.truncate(encoded, self.config.payload_limit)
                except (TypeError, ValueError, OverflowError) as exc:
                    self.ports.log(
                        "WARN",
                        f"sse_trace_payload_encode_failed event={event_name} "
                        f"error={type(exc).__name__}: {exc}",
                    )
            event = {
                "n": trace["event_count"],
                "event": event_name,
                "payload": summarize_payload(payload, self.ports.truncate),
            }
            if raw:
                event["raw"] = raw
            events.append(event)
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"sse_trace_event_record_failed event={event_name} "
                f"error={type(exc).__name__}: {exc}",
            )

    def finish(self, trace: dict[str, Any] | None, **outcome: Any) -> None:
        if not isinstance(trace, dict):
            return
        try:
            trace.update({"finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **outcome})
            self.config.config_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.config.last_path.with_name(
                f"{self.config.last_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
            )
            encoded = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.config.last_path)
            if self.ports.enabled():
                self._rotate(self.config.trace_path, self.config.max_bytes)
                with self.config.trace_path.open("a", encoding="utf-8") as stream:
                    stream.write(encoded + "\n")
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"sse_trace_finish_failed outcome={outcome.get('outcome')} "
                f"error={type(exc).__name__}: {exc}",
            )

    def append_tool_call(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self.config.config_dir.mkdir(parents=True, exist_ok=True)
            self._rotate(self.config.tool_call_path, 2_000_000)
            record = {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "event": event,
                **payload,
            }
            with self.config.tool_call_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"tool_call_log_write_failed event={event} error={type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _rotate(path: Path, max_bytes: int) -> None:
        if path.exists() and path.stat().st_size > max_bytes:
            path.replace(path.with_suffix(".jsonl.1"))
