"""Cross-runtime transcript import repository and application service."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ciel_runtime_support.channel_transcript import (
    content_text,
    is_assistant_message,
    user_text,
)


def normalize_import_source(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    if normalized in {"codex", "openai", "codexcli"}:
        return "codex"
    if normalized in {"claude", "claudecode", "anthropic"}:
        return "claude"
    return ""


@dataclass(frozen=True, slots=True)
class ImportSessionLimits:
    max_bytes: int
    max_chars: int


class ImportSessionRepository:
    def __init__(
        self,
        home: Path,
        environment: Mapping[str, str],
        limits: ImportSessionLimits,
    ) -> None:
        self.home = home
        self.environment = environment
        self.limits = limits

    def latest(self, source: str) -> Path | None:
        if source == "claude":
            root, pattern = self.home / ".claude" / "projects", "*/*.jsonl"
        elif source == "codex":
            codex_home = Path(
                self.environment.get("CODEX_HOME") or (self.home / ".codex")
            ).expanduser()
            root, pattern = codex_home / "sessions", "**/*.jsonl"
        else:
            return None
        return self._latest_under(root, pattern)

    @staticmethod
    def _latest_under(root: Path, pattern: str) -> Path | None:
        latest: Path | None = None
        latest_mtime = -1.0
        try:
            paths = root.glob(pattern)
        except (OSError, ValueError):
            return None
        for path in paths:
            try:
                mtime = path.stat().st_mtime if path.is_file() else -1.0
            except OSError:
                continue
            if mtime > latest_mtime:
                latest = path
                latest_mtime = mtime
        return latest

    def resolve(self, source: str, path_text: str) -> tuple[Path | None, str]:
        raw = str(path_text or "").strip()
        if not raw:
            latest = self.latest(source)
            if latest is None:
                return None, (
                    f"No {source} transcript path was provided and no recent "
                    f"{source} transcript was found."
                )
            return latest, ""
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
            return None, "ImportSession only reads local transcript files, not URLs."
        path = Path(os.path.expandvars(raw)).expanduser()
        if path.is_dir():
            latest = self._latest_under(path, "**/*.jsonl")
            if latest is None:
                return None, f"No .jsonl transcript files found under {path}."
            return latest, ""
        if not path.exists():
            return None, f"Transcript file not found: {path}"
        if not path.is_file():
            return None, f"Transcript path is not a regular file: {path}"
        return path, ""

    def read(self, source: str, path: Path) -> tuple[str, dict[str, Any]]:
        size = path.stat().st_size
        truncated_bytes = size > self.limits.max_bytes
        with path.open("rb") as stream:
            if truncated_bytes:
                stream.seek(max(0, size - self.limits.max_bytes))
            raw = stream.read(self.limits.max_bytes)
        text = raw.decode("utf-8", errors="replace")
        lines: list[str] = []
        parsed_jsonl = False
        for raw_line in text.splitlines():
            try:
                record = json.loads(raw_line.strip())
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            parsed_jsonl = True
            line = import_record_line(record)
            if line:
                lines.append(line)
        imported = "\n\n".join(lines).strip() if parsed_jsonl else text.strip()
        truncated_chars = len(imported) > self.limits.max_chars
        if truncated_chars:
            imported = imported[-self.limits.max_chars :].lstrip()
        return imported, {
            "source": source,
            "path": str(path),
            "bytes": size,
            "bytes_read": len(raw),
            "byte_truncated": truncated_bytes,
            "char_truncated": truncated_chars,
            "jsonl": parsed_jsonl,
            "records": len(lines),
        }


def import_tool_text(record: dict[str, Any]) -> str:
    record_type = str(record.get("type") or "")
    payload = record.get("payload")
    payload_object = payload if isinstance(payload, dict) else {}
    payload_type = str(payload_object.get("type") or "")
    call_types = {"function_call", "custom_tool_call", "local_shell_call"}
    result_types = {
        "function_call_output",
        "custom_tool_call_output",
        "local_shell_call_output",
    }
    if record_type == "response_item" and payload_type in call_types:
        name = str(
            payload_object.get("name")
            or payload_object.get("tool_name")
            or payload_type
        ).strip()
        arguments = payload_object.get("arguments")
        if not isinstance(arguments, str):
            arguments = (
                json.dumps(arguments, ensure_ascii=False, default=str)
                if arguments is not None
                else ""
            )
        return f"tool_call {name}: {arguments}".strip()
    if record_type == "response_item" and payload_type in result_types:
        text = content_text(
            payload_object.get("output")
            or payload_object.get("content")
            or payload_object.get("message")
        )
        return f"tool_result: {text}".strip() if text else "tool_result"
    message = record.get("message")
    message_object = message if isinstance(message, dict) else {}
    content = message_object.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            arguments = json.dumps(
                block.get("input") or {}, ensure_ascii=False, default=str
            )
            parts.append(f"tool_call {block.get('name') or 'tool'}: {arguments}")
        elif block.get("type") == "tool_result":
            parts.append(f"tool_result: {content_text(block.get('content'))}".strip())
    return "\n".join(part for part in parts if part)


def import_record_line(record: dict[str, Any]) -> str:
    text = user_text(record).strip()
    if text:
        return "User: " + text
    if is_assistant_message(record):
        message = record.get("message")
        message_object = message if isinstance(message, dict) else {}
        payload = record.get("payload")
        payload_object = payload if isinstance(payload, dict) else {}
        text = content_text(message_object.get("content")).strip()
        if not text:
            text = content_text(
                payload_object.get("content") or payload_object.get("message")
            ).strip()
        if text:
            return "Assistant: " + text
    tool_text = import_tool_text(record).strip()
    return "Tool: " + tool_text if tool_text else ""


class ImportSessionService:
    def __init__(
        self,
        repository: ImportSessionRepository,
        parse_args: Callable[[dict[str, Any]], tuple[str, str]],
    ) -> None:
        self.repository = repository
        self.parse_args = parse_args

    def response_text(self, client_runtime: str, body: dict[str, Any]) -> str:
        raw_source, raw_path = self.parse_args(body)
        source = normalize_import_source(raw_source)
        if not source:
            return (
                "Usage: `/ImportSession Codex [transcript-path]` or "
                "`/ImportSession Claude [transcript-path]`."
            )
        if client_runtime == "claude" and source == "claude":
            return (
                "Claude transcript import into a Claude session is blocked. "
                "Use Claude Code's native resume/continue flow for Claude-to-Claude "
                "sessions. Use ImportSession for cross-runtime import, for example "
                "`/ImportSession Codex <path>`."
            )
        path, error = self.repository.resolve(source, raw_path)
        if error or path is None:
            return f"ImportSession failed: {error}"
        try:
            imported, metadata = self.repository.read(source, path)
        except (OSError, UnicodeError, ValueError) as exc:
            return (
                f"ImportSession failed while reading {path}: "
                f"{type(exc).__name__}: {exc}"
            )
        if not imported:
            return f"ImportSession found no importable transcript content in {path}."
        header = self._header(client_runtime, source, path, metadata)
        return "\n".join(
            [
                *header,
                "",
                "Imported transcript context:",
                f'<ciel-runtime-imported-session source="{source}" path="{path}">',
                imported,
                "</ciel-runtime-imported-session>",
            ]
        )

    def _header(
        self,
        client_runtime: str,
        source: str,
        path: Path,
        metadata: dict[str, Any],
    ) -> list[str]:
        header = [
            "Ciel Runtime ImportSession completed.",
            f"- current client: {client_runtime}",
            f"- source transcript format: {source}",
            f"- transcript: {path}",
            f"- parsed jsonl: {str(bool(metadata.get('jsonl'))).lower()}",
            f"- imported records: {metadata.get('records')}",
        ]
        if metadata.get("byte_truncated"):
            header.append(
                f"- warning: read tail only: {metadata.get('bytes_read'):,}/"
                f"{metadata.get('bytes'):,} bytes"
            )
        if metadata.get("char_truncated"):
            header.append(
                f"- warning: trimmed to last {self.repository.limits.max_chars:,} chars"
            )
        return header
