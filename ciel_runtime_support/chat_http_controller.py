"""HTTP controller for the Ciel Runtime chat/channel bridge API."""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Condition
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChatHttpReadServices:
    read_after: Callable[[int, str | None, str | None, int], list[dict[str, Any]]]
    read_before: Callable[[int, str | None, str | None, int], list[dict[str, Any]]]
    condition: Condition
    connection_statuses: Callable[[], dict[str, Any]]
    safe_segment: Callable[[str, str], str]
    files_dir: Path


@dataclass(frozen=True, slots=True)
class ChatHttpWriteServices:
    write_json: Callable[..., None]
    append_message: Callable[[dict[str, Any]], dict[str, Any]]
    store_upload: Callable[[dict[str, Any]], dict[str, Any]]
    start_connection: Callable[[dict[str, Any]], dict[str, Any]]
    stop_connection: Callable[[str | None], dict[str, Any]]


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
                    "sse_status": "/ca/channel/sse/status",
                    "sse_connect": "POST /ca/channel/sse/connect",
                    "sse_disconnect": "POST /ca/channel/sse/disconnect",
                    "native_note": "This is the Ciel Runtime bridge API, not Claude Code's gated native --channels path.",
                },
            )
            return True
        if path == "/ca/chat/sse/status":
            self.writes.write_json(handler, {"ok": True, "connections": self.reads.connection_statuses()})
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
        data = target.read_bytes()
        handler.send_response(200)
        handler.send_header("content-type", "application/octet-stream")
        handler.send_header("content-disposition", f"attachment; filename={json.dumps(name)}")
        handler.send_header("content-length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True

    def post(self, handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
        path, _channel_alias = self._chat_path(path)
        if path == "/ca/chat/sse/connect":
            try:
                status = self.writes.start_connection(body)
                self.writes.write_json(handler, {"ok": True, "connection": status})
            except Exception as error:
                self.writes.write_json(handler, {"ok": False, "error": str(error)}, 400)
            return True
        if path == "/ca/chat/sse/disconnect":
            name = body.get("name")
            result = self.writes.stop_connection(str(name) if name else None)
            self.writes.write_json(handler, {"ok": True, **result})
            return True
        if path == "/ca/chat/notify":
            return self._notify(handler, body)
        if path == "/ca/chat/messages":
            message = self.writes.append_message(body)
            self.writes.write_json(handler, {"ok": True, "message": message})
            return True
        if path == "/ca/chat/files":
            return self._upload(handler, body)
        return False

    def _notify(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
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
