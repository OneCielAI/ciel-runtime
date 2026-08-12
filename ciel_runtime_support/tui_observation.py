"""Normalized, authenticated observation of routed coding-agent TUI traffic.

The router sees the semantic traffic that produces a TUI turn, but it does not
own terminal pixels or local keybindings.  This module exposes the useful,
portable subset: the latest user input, visible assistant text, tool activity,
errors, and turn lifecycle.  Hidden thinking and tool arguments are deliberately
not copied into the observation stream.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Iterator


DEFAULT_OBSERVATION_BUFFER = 2_000
DEFAULT_TEXT_CHUNK = 4_096
OBSERVED_RUNTIME_PATHS = frozenset(
    {"/v1/messages", "/v1/responses", "/backend-api/codex/responses"}
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"", "0", "false", "off", "no"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _now() -> tuple[float, str]:
    epoch = time.time()
    display = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    return epoch, display


class TuiObservationBus:
    """Bounded in-memory event bus with active-turn state and long polling."""

    def __init__(self, *, enabled: bool | None = None, capacity: int | None = None) -> None:
        self.enabled = _env_bool("CIEL_RUNTIME_TUI_OBSERVATION", True) if enabled is None else enabled
        self.capacity = capacity or _env_int(
            "CIEL_RUNTIME_TUI_OBSERVATION_BUFFER", DEFAULT_OBSERVATION_BUFFER, 100, 20_000
        )
        self.text_chunk = _env_int(
            "CIEL_RUNTIME_TUI_OBSERVATION_CHUNK", DEFAULT_TEXT_CHUNK, 256, 32_768
        )
        self._events: deque[dict[str, Any]] = deque(maxlen=self.capacity)
        self._active: dict[str, dict[str, Any]] = {}
        self._condition = threading.Condition()
        self._next_id = 1

    def begin(
        self,
        *,
        request_id: str,
        protocol: str,
        path: str,
        provider: str,
        model: str,
    ) -> None:
        if not self.enabled:
            return
        epoch, display = _now()
        with self._condition:
            self._active[request_id] = {
                "request_id": request_id,
                "started_at": display,
                "started_ts": epoch,
                "protocol": protocol,
                "path": path,
                "provider": provider,
                "model": model,
            }
        self.publish(
            kind="turn.started",
            request_id=request_id,
            role="system",
            provider=provider,
            model=model,
            data={"protocol": protocol, "path": path},
        )

    def finish(self, request_id: str, *, status: int | None, error: str = "") -> None:
        if not self.enabled:
            return
        with self._condition:
            active = self._active.pop(request_id, None) or {}
        started = float(active.get("started_ts") or time.time())
        self.publish(
            kind="turn.error" if error else "turn.completed",
            request_id=request_id,
            role="system",
            provider=str(active.get("provider") or ""),
            model=str(active.get("model") or ""),
            text=error,
            data={"status": status, "duration_ms": max(0, round((time.time() - started) * 1000))},
        )

    def publish(
        self,
        *,
        kind: str,
        request_id: str,
        role: str,
        provider: str = "",
        model: str = "",
        text: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        epoch, display = _now()
        with self._condition:
            event = {
                "id": self._next_id,
                "time": display,
                "ts": epoch,
                "kind": str(kind or "event"),
                "request_id": request_id,
                "role": role,
                "provider": provider,
                "model": model,
                "text": str(text or ""),
                "data": dict(data or {}),
            }
            self._next_id += 1
            self._events.append(event)
            self._condition.notify_all()
            return event

    def publish_text(self, *, text: str, **fields: Any) -> None:
        value = str(text or "")
        if not value:
            return
        for start in range(0, len(value), self.text_chunk):
            self.publish(text=value[start : start + self.text_chunk], **fields)

    def recent(
        self,
        *,
        after: int = 0,
        limit: int = 200,
        kind: str | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._condition:
            events = list(self._events)
        events = [event for event in events if int(event.get("id") or 0) > after]
        if kind:
            events = [event for event in events if str(event.get("kind") or "").startswith(kind)]
        if request_id:
            events = [event for event in events if event.get("request_id") == request_id]
        return events[-max(1, min(1_000, int(limit or 200))) :]

    def wait_after(
        self,
        after: int,
        *,
        timeout: float,
        kind: str | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        deadline = time.time() + max(0.1, timeout)

        def has_match() -> bool:
            return any(
                int(event.get("id") or 0) > after
                and (not kind or str(event.get("kind") or "").startswith(kind))
                and (not request_id or event.get("request_id") == request_id)
                for event in self._events
            )

        with self._condition:
            while not has_match():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._condition.wait(min(remaining, 1.0))
        return self.recent(after=after, limit=1_000, kind=kind, request_id=request_id)

    def status(self) -> dict[str, Any]:
        with self._condition:
            active = list(self._active.values())
            latest_id = int(self._events[-1]["id"]) if self._events else 0
        return {
            "enabled": self.enabled,
            "active": active,
            "active_count": len(active),
            "latest_event_id": latest_id,
            "buffer_capacity": self.capacity,
            "capture_scope": "routed runtime traffic",
            "captures": ["user text", "visible assistant text", "tool names", "errors", "turn lifecycle"],
            "excluded": ["hidden thinking", "tool arguments", "native traffic that bypasses this router", "terminal pixels"],
        }


def _text_from_content(content: Any, *, output: bool = False) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        return []
    allowed = {"text", "input_text", "output_text"} if output else {"text", "input_text"}
    return [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") in allowed and str(item.get("text") or "")
    ]


def publish_latest_input(
    bus: TuiObservationBus,
    body: dict[str, Any],
    *,
    request_id: str,
    provider: str,
    model: str,
) -> None:
    """Publish only the latest input turn, never the replayed transcript."""

    messages = body.get("messages")
    candidates = [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []
    if not candidates:
        value = body.get("input")
        if isinstance(value, str):
            candidates = [{"role": "user", "content": value}]
        elif isinstance(value, list):
            candidates = [item for item in value if isinstance(item, dict)]
    if not candidates:
        return
    latest = candidates[-1]
    role = str(latest.get("role") or "user")
    content = latest.get("content", latest.get("input"))
    if latest.get("type") in {"tool_result", "function_call_output"}:
        content = [latest]
    for text in _text_from_content(content):
        bus.publish_text(
            kind="input.text",
            request_id=request_id,
            role=role,
            provider=provider,
            model=model,
            text=text,
        )
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in {"tool_result", "function_call_output"}:
            continue
        result_text = block.get("content", block.get("output"))
        length = sum(len(text) for text in _text_from_content(result_text))
        if isinstance(result_text, str):
            length = len(result_text)
        bus.publish(
            kind="tool.result",
            request_id=request_id,
            role="tool",
            provider=provider,
            model=model,
            data={
                "tool_use_id": str(block.get("tool_use_id") or block.get("call_id") or ""),
                "content_chars": length,
                "is_error": bool(block.get("is_error")),
            },
        )


class ObservedResponseWriter:
    """Transparent wfile proxy that normalizes Anthropic and Responses SSE."""

    def __init__(
        self,
        target: Any,
        bus: TuiObservationBus,
        *,
        request_id: str,
        provider: str,
        model: str,
    ) -> None:
        self._target = target
        self._bus = bus
        self._request_id = request_id
        self._provider = provider
        self._model = model
        self._headers_done = False
        self._header_buffer = bytearray()
        self._sse_buffer = bytearray()
        self._json_buffer = bytearray()
        self._content_type = ""

    def write(self, data: bytes) -> Any:
        result = self._target.write(data)
        try:
            self._observe(bytes(data))
        except Exception:
            pass
        return result

    def flush(self) -> Any:
        return self._target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def _observe(self, data: bytes) -> None:
        if not self._headers_done:
            self._header_buffer.extend(data)
            split = self._header_buffer.find(b"\r\n\r\n")
            width = 4
            if split < 0:
                split = self._header_buffer.find(b"\n\n")
                width = 2
            if split < 0:
                return
            headers = bytes(self._header_buffer[:split]).decode("latin-1", errors="replace")
            for line in headers.splitlines():
                if line.lower().startswith("content-type:"):
                    self._content_type = line.split(":", 1)[1].strip().lower()
            body = bytes(self._header_buffer[split + width :])
            self._header_buffer.clear()
            self._headers_done = True
            if body:
                self._observe_body(body)
            return
        self._observe_body(data)

    def _observe_body(self, data: bytes) -> None:
        if "text/event-stream" in self._content_type:
            self._sse_buffer.extend(data)
            normalized = self._sse_buffer.replace(b"\r\n", b"\n")
            blocks = normalized.split(b"\n\n")
            self._sse_buffer = bytearray(blocks.pop() if blocks else b"")
            for block in blocks:
                self._observe_sse_block(block)
            return
        if "json" in self._content_type and len(self._json_buffer) < 2_000_000:
            remaining = 2_000_000 - len(self._json_buffer)
            self._json_buffer.extend(data[:remaining])

    def _observe_sse_block(self, block: bytes) -> None:
        event_name = ""
        data_lines: list[str] = []
        for raw_line in block.decode("utf-8", errors="replace").splitlines():
            if raw_line.startswith("event:"):
                event_name = raw_line[6:].strip()
            elif raw_line.startswith("data:"):
                data_lines.append(raw_line[5:].lstrip())
        raw_data = "\n".join(data_lines).strip()
        if not raw_data or raw_data == "[DONE]":
            return
        try:
            payload = json.loads(raw_data)
        except (TypeError, ValueError):
            return
        if isinstance(payload, dict):
            self._publish_payload(event_name, payload)

    def _publish_payload(self, event_name: str, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or event_name or "")
        delta = payload.get("delta")
        if event_type == "content_block_delta" and isinstance(delta, dict):
            if delta.get("type") == "text_delta":
                self._publish_text(str(delta.get("text") or ""))
            elif delta.get("type") == "input_json_delta":
                self._publish_tool_arguments(str(delta.get("partial_json") or ""))
            return
        if event_type == "content_block_start":
            block = payload.get("content_block") if isinstance(payload.get("content_block"), dict) else {}
            if block.get("type") == "text":
                self._publish_text(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                self._publish_tool(str(block.get("name") or ""), str(block.get("id") or ""))
            return
        if event_type == "response.output_text.delta":
            self._publish_text(str(payload.get("delta") or ""))
            return
        if event_type == "response.output_item.added":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") in {"function_call", "custom_tool_call", "computer_call"}:
                self._publish_tool(str(item.get("name") or item.get("type") or ""), str(item.get("call_id") or item.get("id") or ""))
            return
        if event_type in {"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"}:
            self._publish_tool_arguments(str(payload.get("delta") or ""))
            return
        if event_type in {"error", "response.failed"}:
            error = payload.get("error")
            if not isinstance(error, dict):
                response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
                error = response.get("error") if isinstance(response.get("error"), dict) else {}
            self._bus.publish(
                kind="output.error",
                request_id=self._request_id,
                role="system",
                provider=self._provider,
                model=self._model,
                text=str(error.get("message") or error.get("code") or "runtime response failed"),
            )

    def _publish_text(self, text: str) -> None:
        self._bus.publish_text(
            kind="output.text.delta",
            request_id=self._request_id,
            role="assistant",
            provider=self._provider,
            model=self._model,
            text=text,
        )

    def _publish_tool(self, name: str, tool_id: str) -> None:
        self._bus.publish(
            kind="tool.started",
            request_id=self._request_id,
            role="assistant",
            provider=self._provider,
            model=self._model,
            data={"name": name, "tool_id": tool_id},
        )

    def _publish_tool_arguments(self, value: str) -> None:
        if not value:
            return
        # Arguments can contain credentials or file contents. Publish progress,
        # not the argument body.
        self._bus.publish(
            kind="tool.arguments.delta",
            request_id=self._request_id,
            role="assistant",
            provider=self._provider,
            model=self._model,
            data={"chars": len(value)},
        )

    def finish(self) -> None:
        if self._sse_buffer:
            self._observe_sse_block(bytes(self._sse_buffer))
            self._sse_buffer.clear()
        if not self._json_buffer:
            return
        try:
            payload = json.loads(self._json_buffer.decode("utf-8", errors="replace"))
        except (TypeError, ValueError):
            return
        self._observe_json(payload)

    def _observe_json(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        for text in _text_from_content(payload.get("content"), output=True):
            self._publish_text(text)
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        output = response.get("output") if isinstance(response, dict) else None
        if not isinstance(output, list):
            return
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for text in _text_from_content(item.get("content"), output=True):
                    self._publish_text(text)
            elif item.get("type") in {"function_call", "custom_tool_call", "computer_call"}:
                self._publish_tool(str(item.get("name") or item.get("type") or ""), str(item.get("call_id") or item.get("id") or ""))


@contextlib.contextmanager
def observe_runtime_response(
    handler: BaseHTTPRequestHandler,
    path: str,
    provider: str,
    model: str,
    body: dict[str, Any],
    bus: TuiObservationBus,
) -> Iterator[None]:
    if (
        not bus.enabled
        or path not in OBSERVED_RUNTIME_PATHS
        or getattr(handler, "wfile", None) is None
    ):
        yield
        return
    request_id = f"tui-{os.getpid()}-{time.time_ns()}"
    protocol = "anthropic_messages" if path == "/v1/messages" else "openai_responses"
    bus.begin(
        request_id=request_id,
        protocol=protocol,
        path=path,
        provider=provider,
        model=model,
    )
    publish_latest_input(
        bus, body, request_id=request_id, provider=provider, model=model
    )
    original = handler.wfile
    observed = ObservedResponseWriter(
        original,
        bus,
        request_id=request_id,
        provider=provider,
        model=model,
    )
    handler.wfile = observed
    error = ""
    try:
        yield
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        observed.finish()
        handler.wfile = original
        status = getattr(handler, "_ciel_runtime_response_status", None)
        if not error and isinstance(status, int) and status >= 400:
            error = f"HTTP {status}"
        bus.finish(request_id, status=status, error=error)


@dataclass(frozen=True, slots=True)
class TuiObservationHttpPorts:
    bus: TuiObservationBus
    write_json: Callable[..., Any]
    write_text: Callable[..., Any]
    log: Callable[[str, str], None]


class TuiObservationHttpAdapter:
    def __init__(self, ports: TuiObservationHttpPorts) -> None:
        self._ports = ports

    @staticmethod
    def _int(query: dict[str, list[str]], name: str, default: int) -> int:
        try:
            return int((query.get(name) or [default])[0])
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _first(query: dict[str, list[str]], name: str) -> str | None:
        value = str((query.get(name) or [""])[0]).strip()
        return value or None

    def handle_get(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        query: dict[str, list[str]],
    ) -> bool:
        if path == "/ca/tui":
            self._ports.write_text(
                handler,
                render_tui_observation_html(),
                content_type="text/html; charset=utf-8",
            )
            return True
        if path in {"/ca/tui/health", "/ca/tui/status"}:
            self._ports.write_json(
                handler,
                {
                    "ok": True,
                    **self._ports.bus.status(),
                    "endpoints": {
                        "recent": "GET /ca/tui/recent",
                        "stream": "GET /ca/tui/stream",
                        "monitor": "GET /ca/tui",
                    },
                },
            )
            return True
        if path == "/ca/tui/recent":
            after = max(0, self._int(query, "after", 0))
            events = self._ports.bus.recent(
                after=after,
                limit=self._int(query, "limit", 200),
                kind=self._first(query, "kind"),
                request_id=self._first(query, "request_id"),
            )
            self._ports.write_json(
                handler,
                {
                    "ok": True,
                    "events": events,
                    "last_id": int(events[-1]["id"]) if events else after,
                    "active": self._ports.bus.status()["active"],
                },
            )
            return True
        if path != "/ca/tui/stream":
            return False
        return self._stream(handler, query)

    def _stream(
        self, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]
    ) -> bool:
        if "after" in query:
            last_id = max(0, self._int(query, "after", 0))
        else:
            try:
                last_id = max(0, int(str(handler.headers.get("last-event-id") or "0")))
            except (AttributeError, TypeError, ValueError):
                last_id = 0
        timeout = max(1.0, min(3600.0, float(self._int(query, "timeout", 300))))
        kind = self._first(query, "kind")
        request_id = self._first(query, "request_id")
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                events = self._ports.bus.recent(
                    after=last_id, limit=1_000, kind=kind, request_id=request_id
                )
                if not events:
                    events = self._ports.bus.wait_after(
                        last_id,
                        timeout=min(15.0, max(0.1, deadline - time.time())),
                        kind=kind,
                        request_id=request_id,
                    )
                if not events:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                    continue
                for event in events:
                    last_id = max(last_id, int(event.get("id") or 0))
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    handler.wfile.write(
                        f"id: {last_id}\nevent: tui\ndata: {data}\n\n".encode()
                    )
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return True
        except Exception as exc:
            self._ports.log(
                "DEBUG", f"tui observation stream closed: {type(exc).__name__}: {exc}"
            )
        return True


def render_tui_observation_html() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ciel Runtime TUI Live</title><style>
:root{color-scheme:dark;font-family:ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:#080c12;color:#e7edf5;height:100vh;overflow:hidden}header{height:56px;display:flex;align-items:center;gap:14px;padding:0 18px;border-bottom:1px solid #243044;background:#101722}h1{font-size:16px;margin:0}a{color:#7dd3fc}.state{color:#94a3b8;font:12px ui-monospace,monospace}main{height:calc(100vh - 56px);overflow:auto;padding:14px}.event{padding:9px 11px;margin-bottom:7px;border:1px solid #263448;border-radius:8px;background:#0e1520}.meta{font:11px ui-monospace,monospace;color:#8190a5}.text{white-space:pre-wrap;word-break:break-word;margin-top:5px}.input{border-left:3px solid #60a5fa}.output{border-left:3px solid #34d399}.tool{border-left:3px solid #fbbf24}.turn{opacity:.8}.error{border-left:3px solid #fb7185}</style></head>
<body><header><h1>TUI Live</h1><span class="state" id="state">connecting</span><a href="/ca/web/chat">Web Chat</a><a href="/ca/events">Router Events</a></header><main id="events"></main>
<script>const root=document.getElementById('events'),state=document.getElementById('state');let seen=new Set();function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function add(e){if(seen.has(e.id))return;seen.add(e.id);const c=String(e.kind||'').split('.')[0],d=e.data||{},summary=e.text||((d.name?'tool '+d.name:'')||(e.kind==='turn.completed'?'turn completed':''));const row=document.createElement('div');row.className='event '+c+(c==='output'&&e.kind.endsWith('error')?' error':'');row.innerHTML='<div class="meta">#'+e.id+' '+esc(e.time)+' · '+esc(e.kind)+' · '+esc(e.provider)+' '+esc(e.model)+'</div><div class="text">'+esc(summary)+'</div>';root.appendChild(row);while(root.children.length>1200)root.firstChild.remove();root.scrollTop=root.scrollHeight}fetch('/ca/tui/recent?limit=300').then(r=>r.json()).then(j=>(j.events||[]).forEach(add));const es=new EventSource('/ca/tui/stream');es.onopen=()=>state.textContent='live';es.onerror=()=>state.textContent='reconnecting';es.addEventListener('tui',ev=>{try{add(JSON.parse(ev.data))}catch(_){}});</script></body></html>"""


__all__ = [
    "OBSERVED_RUNTIME_PATHS",
    "ObservedResponseWriter",
    "TuiObservationBus",
    "TuiObservationHttpAdapter",
    "TuiObservationHttpPorts",
    "observe_runtime_response",
    "publish_latest_input",
    "render_tui_observation_html",
]
