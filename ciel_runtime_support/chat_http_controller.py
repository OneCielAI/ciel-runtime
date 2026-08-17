"""HTTP controller for the Ciel Runtime chat/channel bridge API."""

from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Condition
from typing import Any, Callable


CHAT_FILE_STREAM_CHUNK_BYTES = 1024 * 1024
CHAT_INPUT_MODES = frozenset({"structured", "tty"})
CHAT_RESPONSE_MODES = frozenset({"web_chat", "tty", "mcp"})
MCP_HINT_FIELD_LIMITS = {"server": 160, "tool": 240, "hint": 1200}


@dataclass(frozen=True, slots=True)
class ChatHttpReadServices:
    read_after: Callable[[int, str | None, str | None, int], list[dict[str, Any]]]
    read_before: Callable[[int, str | None, str | None, int], list[dict[str, Any]]]
    condition: Condition
    safe_segment: Callable[[str, str], str]
    files_dir: Path


@dataclass(frozen=True, slots=True)
class ChatHttpWriteServices:
    write_json: Callable[..., None]
    append_message: Callable[[dict[str, Any]], dict[str, Any]]
    store_upload: Callable[[dict[str, Any]], dict[str, Any]]
    submit_message: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    submit_notify: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    submit_tty: Callable[..., dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class ChatHttpController:
    router_base: str
    reads: ChatHttpReadServices
    writes: ChatHttpWriteServices

    @staticmethod
    def _params(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query, keep_blank_values=True)

    @staticmethod
    def _first(params: dict[str, list[str]], name: str, default: str = "") -> str:
        values = params.get(name)
        return values[0] if values else default

    @staticmethod
    def _chat_path(path: str) -> tuple[str, bool]:
        channel_alias = path.startswith("/ca/channel/")
        if channel_alias:
            path = "/ca/chat/" + path[len("/ca/channel/") :]
        return path, channel_alias

    @staticmethod
    def _normalized_mode(value: Any, aliases: dict[str, str]) -> str:
        mode = str(value or "").strip().lower().replace("-", "_")
        return aliases.get(mode, mode)

    def _message_modes(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
    ) -> tuple[str, str, dict[str, str] | None, dict[str, Any] | None]:
        params = self._params(handler)
        legacy_mode = body.get("injection_mode")
        if legacy_mode is None:
            legacy_mode = self._first(params, "injection_mode", "")
        requested_input = body.get("input_mode")
        if requested_input is None:
            requested_input = self._first(params, "input_mode", "") or legacy_mode or "structured"
        input_mode = self._normalized_mode(requested_input, {"web_chat": "structured", "standard": "structured"})
        if input_mode not in CHAT_INPUT_MODES:
            legacy_only = body.get("input_mode") is None and bool(legacy_mode)
            return input_mode, "", None, {
                "ok": False,
                "error": "invalid_injection_mode" if legacy_only else "invalid_input_mode",
                "allowed": ["web_chat", "tty"] if legacy_only else sorted(CHAT_INPUT_MODES),
            }

        requested_response = body.get("response_mode")
        if requested_response is None:
            requested_response = self._first(params, "response_mode", "")
        if requested_response in (None, ""):
            requested_response = "tty" if self._normalized_mode(legacy_mode, {}) == "tty" else "web_chat"
        response_mode = self._normalized_mode(
            requested_response,
            {"ai_net": "web_chat", "ainet": "web_chat", "raw": "tty", "terminal": "tty"},
        )
        if response_mode not in CHAT_RESPONSE_MODES:
            return input_mode, response_mode, None, {
                "ok": False,
                "error": "invalid_response_mode",
                "allowed": sorted(CHAT_RESPONSE_MODES),
            }

        raw_hint = body.get("response_mcp")
        if raw_hint is None:
            raw_hint = body.get("mcp_hint")
        if isinstance(raw_hint, str):
            raw_hint = {"server": raw_hint}
        raw_hint = raw_hint if isinstance(raw_hint, dict) else {}
        mcp_hint = {
            key: str(
                raw_hint.get(key)
                or body.get(f"mcp_{key}")
                or self._first(params, f"mcp_{key}", "")
                or ""
            ).strip()
            for key in MCP_HINT_FIELD_LIMITS
        }
        for key, limit in MCP_HINT_FIELD_LIMITS.items():
            if len(mcp_hint[key]) > limit:
                return input_mode, response_mode, None, {
                    "ok": False,
                    "error": "mcp_hint_too_large",
                    "field": key,
                    "maximum_characters": limit,
                }
        mcp_hint = {key: value for key, value in mcp_hint.items() if value}
        if response_mode == "mcp" and not mcp_hint.get("server"):
            return input_mode, response_mode, None, {
                "ok": False,
                "error": "mcp_response_requires_server",
                "required": "response_mcp.server",
            }
        return input_mode, response_mode, mcp_hint or None, None

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        path, channel_alias = self._chat_path(path)
        if path == "/ca/chat/health":
            self.writes.write_json(
                handler,
                {
                    "ok": True,
                    "base": self.router_base,
                    "messages": "/ca/channel/messages" if channel_alias else "/ca/chat/messages",
                    "wait": "/ca/channel/wait" if channel_alias else "/ca/chat/wait",
                    "stream": "/ca/channel/stream" if channel_alias else "/ca/chat/stream",
                    "notify": "/ca/channel/notify",
                    "ownership_note": "External MCP servers are configured and owned by the active CLI.",
                },
            )
            return True
        if path in ("/ca/chat/messages", "/ca/chat/wait"):
            return self._messages(handler, path)
        if path == "/ca/chat/stream":
            return self._stream(handler)
        if path.startswith("/ca/chat/files/"):
            return self._file(handler, path)
        return False

    def _messages(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        params = self._params(handler)
        after = int(self._first(params, "after", "0") or 0)
        before = int(self._first(params, "before", "0") or 0)
        limit = max(1, min(500, int(self._first(params, "limit", "100") or 100)))
        channel = self._first(params, "channel") or None
        recipient = self._first(params, "recipient") or self._first(params, "recipient_id") or None
        latest = self._first(params, "latest") or self._first(params, "history")
        timeout = 0.0 if path.endswith("/messages") else max(
            0.0, min(300.0, float(self._first(params, "timeout", "60") or 60))
        )
        deadline = time.time() + timeout
        history = path.endswith("/messages") and (before > 0 or latest.lower() in {"1", "true", "yes", "on"})
        messages = (
            self.reads.read_before(before, channel, recipient, limit)
            if history
            else self.reads.read_after(after, channel, recipient, limit)
        )
        while not messages and timeout > 0 and time.time() < deadline:
            with self.reads.condition:
                self.reads.condition.wait(timeout=min(5.0, max(0.0, deadline - time.time())))
            messages = self.reads.read_after(after, channel, recipient, limit)
        self.writes.write_json(
            handler,
            {
                "ok": True,
                "messages": messages,
                "last_id": messages[-1]["id"] if messages else after,
                "oldest_id": messages[0]["id"] if messages else None,
                "has_more": bool(messages and (before > 0 or len(messages) >= limit)),
            },
        )
        return True

    def _stream(self, handler: BaseHTTPRequestHandler) -> bool:
        params = self._params(handler)
        after = int(self._first(params, "after", "0") or 0)
        channel = self._first(params, "channel") or None
        recipient = self._first(params, "recipient") or self._first(params, "recipient_id") or None
        timeout = max(1.0, min(3600.0, float(self._first(params, "timeout", "300") or 300)))
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        deadline = time.time() + timeout
        last_id = after
        try:
            while time.time() < deadline:
                messages = self.reads.read_after(last_id, channel, recipient, 100)
                for message in messages:
                    last_id = int(message["id"])
                    handler.wfile.write(f"id: {last_id}\n".encode())
                    handler.wfile.write(b"event: message\n")
                    data = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
                    handler.wfile.write(f"data: {data}\n\n".encode())
                    handler.wfile.flush()
                if messages:
                    continue
                handler.wfile.write(b": wait\n\n")
                handler.wfile.flush()
                with self.reads.condition:
                    self.reads.condition.wait(timeout=min(15.0, max(0.0, deadline - time.time())))
        except (BrokenPipeError, ConnectionError):
            pass
        return True

    def _file(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        name = self.reads.safe_segment(urllib.parse.unquote(path[len("/ca/chat/files/") :]), "file")
        target = self.reads.files_dir / name
        if not target.exists() or not target.is_file():
            self.writes.write_json(handler, {"ok": False, "error": "not_found"}, 404)
            return True
        with target.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            request_target = str(getattr(handler, "path", path) or path)
            inline_params = urllib.parse.parse_qs(
                urllib.parse.urlparse(request_target).query,
                keep_blank_values=True,
            )
            inline = self._first(inline_params, "inline").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            guessed_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            safe_image_types = {
                "image/avif",
                "image/bmp",
                "image/gif",
                "image/jpeg",
                "image/png",
                "image/webp",
            }
            safe_text_types = {
                "application/json",
                "text/csv",
                "text/markdown",
                "text/plain",
            }
            inline_type = (
                guessed_type
                if guessed_type in safe_image_types
                or guessed_type in safe_text_types
                or guessed_type == "application/pdf"
                else "text/plain"
                if guessed_type in {"application/xhtml+xml", "image/svg+xml", "text/html"}
                else "application/octet-stream"
            )
            handler.send_response(200)
            handler.send_header(
                "content-type", inline_type if inline else "application/octet-stream"
            )
            handler.send_header("x-content-type-options", "nosniff")
            handler.send_header(
                "content-security-policy",
                "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            )
            disposition = "inline" if inline and inline_type != "application/octet-stream" else "attachment"
            handler.send_header("content-disposition", f"{disposition}; filename={json.dumps(name)}")
            handler.send_header("content-length", str(size))
            handler.end_headers()
            while chunk := stream.read(CHAT_FILE_STREAM_CHUNK_BYTES):
                handler.wfile.write(chunk)
        return True

    def post(self, handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
        path, _channel_alias = self._chat_path(path)
        if path == "/ca/chat/notify":
            return self._notify(handler, body)
        if path == "/ca/chat/messages":
            input_mode, response_mode, response_mcp, error = self._message_modes(handler, body)
            if error is not None:
                self.writes.write_json(
                    handler,
                    error,
                    400,
                )
                return True
            admitted_body = dict(body)
            meta = dict(body.get("meta") or {}) if isinstance(body.get("meta"), dict) else {}
            meta["injection_mode"] = input_mode
            meta["response_mode"] = response_mode
            if response_mode == "web_chat":
                admitted_body.setdefault("channel", "default")
                admitted_body.setdefault("thread_id", admitted_body["channel"])
                admitted_body.setdefault("kind", "web_chat")
                meta.setdefault("source", "ciel-runtime-web-chat")
                meta.setdefault("reply_channel", admitted_body["channel"])
                meta.setdefault("reply_recipient", "web")
            if response_mcp is not None:
                meta["response_mcp"] = response_mcp
            else:
                meta.pop("response_mcp", None)
            admitted_body["meta"] = meta
            if input_mode == "tty":
                if self.writes.submit_tty is None:
                    self.writes.write_json(
                        handler,
                        {"ok": False, "error": "tty_injection_unavailable"},
                        503,
                    )
                    return True
                public_message = (
                    self.writes.append_message(admitted_body)
                    if response_mode == "web_chat"
                    else None
                )
                message = (
                    self.writes.submit_tty(admitted_body, public_message)
                    if public_message is not None
                    else self.writes.submit_tty(admitted_body)
                )
                self.writes.write_json(
                    handler,
                    {
                        "ok": True,
                        "input_mode": "tty",
                        "response_mode": response_mode,
                        "injection_mode": "tty",
                        "message": public_message or message,
                    },
                )
                return True
            message = self.writes.append_message(admitted_body)
            if self.writes.submit_message is not None:
                self.writes.submit_message(message, admitted_body)
            self.writes.write_json(
                handler,
                {
                    "ok": True,
                    "input_mode": "structured",
                    "response_mode": response_mode,
                    "injection_mode": "web_chat",
                    "message": message,
                },
            )
            return True
        if path == "/ca/chat/files":
            return self._upload(handler, body)
        return False

    def _notify(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
        if self.writes.submit_notify is not None:
            message = self.writes.submit_notify(body)
            self.writes.write_json(handler, {"ok": True, "message": message})
            return True
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        meta = params.get("meta") if isinstance(params.get("meta"), dict) else body.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        content = str(params.get("content") or body.get("content") or body.get("message") or body.get("text") or "")
        message = self.writes.append_message(
            {
                "channel": body.get("channel") or meta.get("channel") or "default",
                "sender_id": body.get("sender_id") or body.get("sender") or body.get("server") or meta.get("source") or "channel",
                "recipients": body.get("recipients", body.get("recipient_id", meta.get("recipients", "all"))),
                "thread_id": body.get("thread_id") or meta.get("thread_id"),
                "parent_id": body.get("parent_id") or meta.get("parent_id"),
                "kind": body.get("kind") or "channel",
                "message": content,
                "meta": meta,
            }
        )
        self.writes.write_json(handler, {"ok": True, "message": message})
        return True

    def _upload(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
        try:
            upload = self.writes.store_upload(body)
        except OverflowError as error:
            self.writes.write_json(handler, {"ok": False, "error": str(error)}, 413)
            return True
        except ValueError as error:
            self.writes.write_json(handler, {"ok": False, "error": str(error)}, 400)
            return True
        if body.get("announce", True):
            self.writes.append_message(
                {
                    "channel": body.get("channel", "default"),
                    "sender_id": body.get("sender_id", "system"),
                    "recipients": body.get("recipients", "all"),
                    "thread_id": body.get("thread_id"),
                    "parent_id": body.get("parent_id"),
                    "kind": "file",
                    "message": str(body.get("message") or upload["url"]),
                    "meta": {"attachments": [upload], "name": upload["original_name"], "url": upload["url"]},
                }
            )
        self.writes.write_json(handler, {"ok": True, **upload})
        return True
