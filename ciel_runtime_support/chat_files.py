"""Chat attachment storage and message projection."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable
import urllib.parse

from .request_limits_config import MIB, resolve_workspace_request_limits


@dataclass(frozen=True, slots=True)
class ChatFilePorts:
    timestamp: Callable[[], float] = time.time
    timestamp_ns: Callable[[], int] = time.time_ns
    max_bytes: Callable[[], int] | None = None


class ChatFileRepository:
    DEFAULT_MAX_BYTES = 500 * MIB
    COPY_CHUNK_BYTES = MIB

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
        return resolve_workspace_request_limits(
            {},
            "",
            os.environ,
        ).chat_attachment_max_bytes

    def _max_bytes(self) -> int:
        if self._ports.max_bytes is None:
            return self.configured_max_bytes()
        return max(1, int(self._ports.max_bytes()))

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
            encoded = str(content)
            maximum_encoded = 4 * ((self._max_bytes() + 2) // 3)
            if len(encoded) > maximum_encoded:
                raise OverflowError(
                    f"file too large: base64 content exceeds {maximum_encoded} characters"
                )
            try:
                data = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (ValueError, UnicodeEncodeError) as exc:
                raise ValueError("invalid base64 file content") from exc
        elif encoding in {"", "text", "utf-8", "utf8"}:
            data = str(content).encode("utf-8")
        else:
            raise ValueError(f"unsupported file encoding: {encoding}")
        self._validate_size(data)
        return self._store_bytes(
            data,
            raw_name,
            str(
                body.get("content_type")
                or body.get("mime_type")
                or "application/octet-stream"
            ),
        )

    def _store_bytes(
        self,
        data: bytes,
        raw_name: str,
        content_type: str,
    ) -> dict[str, Any]:
        self._root.mkdir(parents=True, exist_ok=True)
        name = f"{self._ports.timestamp_ns()}-{self.safe_segment(raw_name, 'file')}"
        target = self._root / name
        target.write_bytes(data)
        path = f"/ca/chat/files/{urllib.parse.quote(name)}"
        content_type = str(content_type or "application/octet-stream").strip()
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
        source_size = source.stat().st_size
        maximum = self._max_bytes()
        if source_size > maximum:
            raise OverflowError(
                f"file too large: {source_size} bytes exceeds {maximum} bytes"
            )
        guessed_type = content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        raw_name = name or source.name
        self._root.mkdir(parents=True, exist_ok=True)
        stored_name = f"{self._ports.timestamp_ns()}-{self.safe_segment(raw_name, 'file')}"
        target = self._root / stored_name
        temporary: Path | None = None
        copied = 0
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self._root,
                prefix=f".{stored_name}.",
                suffix=".tmp",
                delete=False,
            ) as destination:
                temporary = Path(destination.name)
                with source.open("rb") as source_stream:
                    while chunk := source_stream.read(self.COPY_CHUNK_BYTES):
                        copied += len(chunk)
                        if copied > maximum:
                            raise OverflowError(
                                f"file too large: {copied} bytes exceeds {maximum} bytes"
                            )
                        destination.write(chunk)
            temporary.replace(target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        path = f"/ca/chat/files/{urllib.parse.quote(stored_name)}"
        return {
            "name": stored_name,
            "original_name": raw_name,
            "url": f"{self._router_base}{path}",
            "path": path,
            "bytes": copied,
            "content_type": str(guessed_type).strip()[:200]
            or "application/octet-stream",
        }

    def runtime_attachment(self, upload: dict[str, Any]) -> dict[str, Any]:
        """Project a stored public attachment into the private Runtime input."""

        name = str(upload.get("name") or "").strip()
        if not name or self.safe_segment(name, "") != name:
            raise ValueError("invalid stored chat attachment name")
        root = self._root.resolve()
        target = (self._root / name).resolve()
        if target.parent != root or not target.is_file():
            raise FileNotFoundError("stored chat attachment is unavailable")
        content_type = str(
            upload.get("content_type")
            or mimetypes.guess_type(str(upload.get("original_name") or name))[0]
            or "application/octet-stream"
        ).strip()[:200]
        return {
            "name": str(upload.get("original_name") or name),
            "content_type": content_type or "application/octet-stream",
            "bytes": target.stat().st_size,
            "local_path": str(target),
            "url": str(upload.get("url") or upload.get("path") or ""),
        }

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

    def _validate_size(self, data: bytes) -> None:
        max_bytes = self._max_bytes()
        if len(data) > max_bytes:
            raise OverflowError(f"file too large: {len(data)} bytes exceeds {max_bytes} bytes")


__all__ = ["ChatFilePorts", "ChatFileRepository"]
