"""Chat attachment storage and message projection."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
import urllib.parse


@dataclass(frozen=True, slots=True)
class ChatFilePorts:
    timestamp: Callable[[], float] = time.time
    timestamp_ns: Callable[[], int] = time.time_ns


class ChatFileRepository:
    DEFAULT_MAX_BYTES = 25 * 1024 * 1024

    def __init__(
        self,
        root: Path,
        router_base: str,
        ports: ChatFilePorts | None = None,
    ) -> None:
        self._root = root
        self._router_base = router_base
        self._ports = ports or ChatFilePorts()

    @classmethod
    def configured_max_bytes(cls) -> int:
        try:
            value = int(str(os.environ.get("CIEL_RUNTIME_CHAT_FILE_MAX_BYTES") or "").strip())
            return value if value > 0 else cls.DEFAULT_MAX_BYTES
        except ValueError:
            return cls.DEFAULT_MAX_BYTES

    @staticmethod
    def safe_segment(value: str, fallback: str = "item") -> str:
        text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip(".-")
        return text[:120] or fallback

    def store_upload(self, body: dict[str, Any]) -> dict[str, Any]:
        self._root.mkdir(parents=True, exist_ok=True)
        raw_name = str(
            body.get("name") or f"file-{int(self._ports.timestamp())}.txt"
        ).strip() or "file"
        content = body.get("content", "")
        encoding = str(body.get("encoding") or "utf-8").strip().lower()
        if encoding == "base64":
            try:
                data = base64.b64decode(str(content).encode("ascii"), validate=True)
            except (ValueError, UnicodeEncodeError) as exc:
                raise ValueError("invalid base64 file content") from exc
        elif encoding in {"", "text", "utf-8", "utf8"}:
            data = str(content).encode("utf-8")
        else:
            raise ValueError(f"unsupported file encoding: {encoding}")
        self._validate_size(data)
        name = f"{self._ports.timestamp_ns()}-{self.safe_segment(raw_name, 'file')}"
        target = self._root / name
        target.write_bytes(data)
        path = f"/ca/chat/files/{urllib.parse.quote(name)}"
        content_type = str(
            body.get("content_type") or body.get("mime_type") or "application/octet-stream"
        ).strip()
        return {
            "name": name,
            "original_name": raw_name,
            "url": f"{self._router_base}{path}",
            "path": path,
            "bytes": len(data),
            "content_type": content_type[:200] or "application/octet-stream",
        }

    def store_path(
        self,
        path_value: Any,
        name: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        raw_path = str(path_value or "").strip()
        if not raw_path:
            raise ValueError("file path is required")
        source = Path(raw_path).expanduser()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"file not found: {raw_path}")
        data = source.read_bytes()
        self._validate_size(data)
        guessed_type = content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        return self.store_upload(
            {
                "name": name or source.name,
                "encoding": "base64",
                "content": base64.b64encode(data).decode("ascii"),
                "content_type": guessed_type,
            }
        )

    @staticmethod
    def markdown_lines(uploads: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for upload in uploads:
            label = str(upload.get("original_name") or upload.get("name") or "file")
            url = str(upload.get("url") or upload.get("path") or "")
            byte_count = upload.get("bytes")
            content_type = str(upload.get("content_type") or "application/octet-stream")
            details = []
            if isinstance(byte_count, int):
                details.append(f"{byte_count} bytes")
            if content_type:
                details.append(content_type)
            detail = f" ({', '.join(details)})" if details else ""
            lines.append(f"- [{label}]({url}){detail}")
        return lines

    @classmethod
    def message_text(cls, message: str, uploads: list[dict[str, Any]]) -> str:
        body = str(message or "").strip()
        lines = cls.markdown_lines(uploads)
        if not lines:
            return body
        attachment_text = "Attached files:\n" + "\n".join(lines)
        return f"{body}\n\n{attachment_text}" if body else attachment_text

    @classmethod
    def _validate_size(cls, data: bytes) -> None:
        max_bytes = cls.configured_max_bytes()
        if len(data) > max_bytes:
            raise OverflowError(f"file too large: {len(data)} bytes exceeds {max_bytes} bytes")


__all__ = ["ChatFilePorts", "ChatFileRepository"]
