"""HTTP adapter for the runtime router application services."""

from __future__ import annotations

import json
import sys
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RouterHttpCore:
    load_config: Callable[[], dict[str, Any]]
    reject_external: Callable[[Any, dict[str, Any]], bool]
    get_current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    parse_json_body: Callable[[bytes], dict[str, Any]]
    is_client_disconnect: Callable[[BaseException], bool]
    log: Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class RouterHttpGetEndpoints:
    codex_mcp_split: Callable[[Any, str], bool]
    events: Callable[[Any, str, dict[str, list[str]]], bool]
    llm_config: Callable[[Any, str], bool]
    channel_mcp: Callable[[Any, str], bool]
    web: Callable[[Any, str], bool]
    chat: Callable[[Any, str], bool]
    plan: Callable[[Any, str], bool]
    runtime: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class RouterHttpPostEndpoints:
    codex_mcp_split: Callable[[Any, str, bytes, str], bool]
    llm_config: Callable[[Any, str, dict[str, Any]], bool]
    channel_mcp: Callable[[Any, str, dict[str, Any]], bool]
    chat: Callable[[Any, str, dict[str, Any]], bool]
    plan: Callable[[Any, str, dict[str, Any]], bool]
    runtime: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class RouterHttpPresentation:
    home_html: Callable[..., str]
    health_payload: Callable[..., dict[str, Any]]
    write_text: Callable[..., Any]
    write_json: Callable[..., Any]
    list_models: Callable[..., list[dict[str, Any]]]
    resolve_model: Callable[..., str]
    model_object: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RouterHttpErrors:
    write_responses_error: Callable[..., Any]
    try_write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class RouterHttpServices:
    core: RouterHttpCore
    get: RouterHttpGetEndpoints
    post: RouterHttpPostEndpoints
    presentation: RouterHttpPresentation
    errors: RouterHttpErrors


@dataclass(frozen=True, slots=True)
class CodexBackendRequestPorts:
    body_with_channel_context: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any] | None]]
    begin_channel_delivery: Callable[[Any, dict[str, Any] | None], None]
    upstream_headers: Callable[[dict[str, Any], Any], dict[str, str]]
    urlopen: Callable[..., Any]
    request_timeout: Callable[[dict[str, Any]], float]


@dataclass(frozen=True, slots=True)
class CodexBackendRetryPorts:
    retry_limit: Callable[[], int]
    read_preamble: Callable[[Any], Any]
    retry_wait: Callable[[int], float]
    log: Callable[[str, str], None]
    publish: Callable[..., Any]
    sleep: Callable[[float], None]


class CodexBackendHttpAdapter:
    def __init__(
        self,
        upstream_base: str,
        request: CodexBackendRequestPorts,
        retry: CodexBackendRetryPorts,
    ) -> None:
        self._upstream_base = upstream_base
        self._request = request
        self._retry = retry

    def upstream_url(self, request_path: str, query: str = "") -> str:
        parsed_path = urllib.parse.urlparse(request_path).path
        suffix = parsed_path
        for prefix in ("/backend-api/codex", "/v1"):
            if parsed_path == prefix:
                suffix = ""
                break
            if parsed_path.startswith(prefix + "/"):
                suffix = parsed_path[len(prefix):]
                break
        url = f"{self._upstream_base.rstrip('/')}/{suffix.lstrip('/')}" if suffix else self._upstream_base.rstrip("/")
        return f"{url}?{query}" if query else url

    @staticmethod
    def copy_response_headers(handler: BaseHTTPRequestHandler, headers: Any) -> None:
        skipped = {"connection", "content-length", "transfer-encoding", "content-encoding"}
        try:
            items = headers.items()
        except (AttributeError, TypeError):
            items = []
        wrote_content_type = False
        for key, value in items:
            lowered = str(key).lower()
            if lowered in skipped:
                continue
            wrote_content_type = wrote_content_type or lowered == "content-type"
            handler.send_header(str(key), str(value))
        if not wrote_content_type:
            handler.send_header("content-type", "application/json")
        handler.send_header("connection", "close")

    def forward_json(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        *,
        mutate_responses: bool = False,
    ) -> dict[str, Any] | None:
        upstream_body = body
        delivery_body: dict[str, Any] | None = None
        if mutate_responses:
            upstream_body, delivery_body = self._request.body_with_channel_context(body)
            self._request.begin_channel_delivery(handler, delivery_body)
        parsed = urllib.parse.urlparse(handler.path)
        url = self.upstream_url(parsed.path, parsed.query)
        headers = self._request.upstream_headers(config, handler.headers)
        data = json.dumps(upstream_body).encode("utf-8")
        max_retries = self._retry.retry_limit() if mutate_responses else 0
        for attempt in range(max_retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with self._request.urlopen(
                request,
                timeout=self._request.request_timeout(config),
                provider=provider,
                pcfg=config,
            ) as response:
                preamble = self._retry.read_preamble(response) if mutate_responses else None
                if preamble is not None and preamble.capacity_error_code and attempt < max_retries:
                    retry_number = attempt + 1
                    wait = self._retry.retry_wait(retry_number)
                    model = str(upstream_body.get("model") or "")
                    self._retry.log(
                        "WARN",
                        "codex_capacity_retry model=%s attempt=%d/%d code=%s wait=%.2fs"
                        % (model, retry_number, max_retries, preamble.capacity_error_code, wait),
                    )
                    self._retry.publish(
                        level="warn",
                        category="router.retry",
                        message="Codex model capacity retry",
                        provider=provider,
                        model=model,
                        data={
                            "attempt": retry_number,
                            "total": max_retries,
                            "code": preamble.capacity_error_code,
                            "wait_seconds": wait,
                        },
                    )
                    self._retry.sleep(wait)
                    continue
                self._write_response(handler, response, preamble)
                break
        return delivery_body

    def forward_get(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        config: dict[str, Any],
    ) -> None:
        parsed = urllib.parse.urlparse(handler.path)
        request = urllib.request.Request(
            self.upstream_url(parsed.path, parsed.query),
            headers=self._request.upstream_headers(config, handler.headers),
            method="GET",
        )
        with self._request.urlopen(
            request,
            timeout=self._request.request_timeout(config),
            provider=provider,
            pcfg=config,
        ) as response:
            self._write_response(handler, response, None)

    def _write_response(self, handler: BaseHTTPRequestHandler, response: Any, preamble: Any) -> None:
        handler.send_response(getattr(response, "status", 200))
        self.copy_response_headers(handler, response.headers)
        handler.end_headers()
        if preamble is not None and preamble.payload:
            handler.wfile.write(preamble.payload)
            handler.wfile.flush()
        while chunk := response.read(65536):
            handler.wfile.write(chunk)
            handler.wfile.flush()


@dataclass(frozen=True, slots=True)
class EventHttpPorts:
    recent: Callable[..., list[dict[str, Any]]]
    wait_after: Callable[..., list[dict[str, Any]]]
    render_html: Callable[[], str]
    write_text: Callable[..., Any]
    write_json: Callable[..., Any]
    log: Callable[[str, str], None]


class EventHttpAdapter:
    def __init__(self, ports: EventHttpPorts) -> None:
        self._ports = ports

    @staticmethod
    def query_int(params: dict[str, list[str]], name: str, default: int) -> int:
        try:
            return int((params.get(name) or [default])[0])
        except (TypeError, ValueError):
            return default

    def handle_get(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        query: dict[str, list[str]],
    ) -> bool:
        if path == "/ca/events":
            self._ports.write_text(
                handler,
                self._ports.render_html(),
                content_type="text/html; charset=utf-8",
            )
            return True
        if path == "/ca/events/recent":
            self._ports.write_json(
                handler,
                {
                    "ok": True,
                    "events": self._ports.recent(
                        limit=self.query_int(query, "limit", 200),
                        min_id=self.query_int(query, "after", 0),
                        level=(query.get("level") or [None])[0],
                        category=(query.get("category") or [None])[0],
                    ),
                },
            )
            return True
        if path != "/ca/events/stream":
            return False
        last_id = self.query_int(query, "after", 0)
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        try:
            last_id = self._write_events(handler, self._ports.recent(limit=200, min_id=last_id), last_id)
            while True:
                events = self._ports.wait_after(last_id, timeout=15.0)
                if events:
                    last_id = self._write_events(handler, events, last_id)
                else:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return True
        except Exception as exc:
            self._ports.log("DEBUG", f"events stream closed: {type(exc).__name__}: {exc}")
        return True

    @staticmethod
    def _write_events(
        handler: BaseHTTPRequestHandler,
        events: list[dict[str, Any]],
        last_id: int,
    ) -> int:
        for event in events:
            last_id = max(last_id, int(event.get("id") or 0))
            handler.wfile.write(
                f"event: event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            )
        handler.wfile.flush()
        return last_id


class RouterHttpHandler(BaseHTTPRequestHandler):
    server_version = "ciel-runtime/0.1"
    services_factory: Callable[[], RouterHttpServices] | None = None

    def _services(self) -> RouterHttpServices:
        if self.services_factory is None:
            raise RuntimeError("RouterHttpHandler requires services_factory")
        return self.services_factory()

    def send_response(self, code: int, message: str | None = None) -> None:
        try:
            self._ciel_runtime_response_status = int(code)
        except (TypeError, ValueError):
            self._ciel_runtime_response_status = None
        super().send_response(code, message)

    def log_message(self, fmt: str, *args: Any) -> None:
        self._safe_log("INFO", "access", fmt, args)

    def log_error(self, fmt: str, *args: Any) -> None:
        self._safe_log("ERROR", "http", fmt, args)

    def _safe_log(self, level: str, prefix: str, fmt: str, args: tuple[Any, ...]) -> None:
        try:
            message = fmt % args
        except (TypeError, ValueError) as exc:
            message = f"{fmt} args={args!r} format_error={type(exc).__name__}: {exc}"
        try:
            self._services().core.log(level, f"{prefix} {message}")
        except Exception as exc:
            sys.stderr.write(f"ciel-runtime router log failure: {type(exc).__name__}: {exc}\n")

    def do_HEAD(self) -> None:
        services = self._services()
        cfg = services.core.load_config()
        if services.core.reject_external(self, cfg):
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("content-type", "application/json")
        self.end_headers()

    def do_GET(self) -> None:
        services = self._services()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        cfg = services.core.load_config()
        if services.core.reject_external(self, cfg):
            return
        endpoints = services.get
        if endpoints.codex_mcp_split(self, path):
            return
        if endpoints.events(self, path, query):
            return
        if endpoints.llm_config(self, path):
            return
        if endpoints.channel_mcp(self, path):
            return
        if endpoints.web(self, path):
            return
        if endpoints.chat(self, path) or endpoints.plan(self, path):
            return
        provider, pcfg = services.core.get_current_provider(cfg)
        presentation = services.presentation
        if path == "/":
            presentation.write_text(
                self,
                presentation.home_html(cfg, provider, pcfg),
                content_type="text/html; charset=utf-8",
            )
            return
        if path in ("/health", "/healthz"):
            presentation.write_json(self, presentation.health_payload(cfg, provider, pcfg))
            return
        if endpoints.runtime(self, path, provider, pcfg):
            return
        if path == "/v1/models":
            data = presentation.list_models(provider, pcfg, self.headers)
            presentation.write_json(self, {"object": "list", "data": data, "has_more": False})
            return
        if path.startswith("/v1/models/"):
            model_id = urllib.parse.unquote(path[len("/v1/models/"):])
            resolved = presentation.resolve_model(provider, pcfg, model_id)
            presentation.write_json(self, presentation.model_object(provider, resolved))
            return
        presentation.write_json(self, {"type": "error", "error": {"type": "not_found_error", "message": path}}, 404)

    def do_POST(self) -> None:
        services = self._services()
        path = urllib.parse.urlparse(self.path).path
        body: dict[str, Any] = {}
        try:
            cfg = services.core.load_config()
            if services.core.reject_external(self, cfg):
                return
            length = int(self.headers.get("content-length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            endpoints = services.post
            if endpoints.codex_mcp_split(self, path, raw, "POST"):
                return
            body = services.core.parse_json_body(raw)
            if endpoints.llm_config(self, path, body):
                return
            if endpoints.channel_mcp(self, path, body):
                return
            if endpoints.chat(self, path, body) or endpoints.plan(self, path, body):
                return
            provider, pcfg = services.core.get_current_provider(cfg)
            if endpoints.runtime(self, cfg, provider, pcfg, path, body):
                return
            services.presentation.write_json(
                self,
                {"type": "error", "error": {"type": "not_found_error", "message": path}},
                404,
            )
        except Exception as exc:
            self._write_uncaught_post_error(path, body, exc, services)

    def _write_uncaught_post_error(
        self,
        path: str,
        body: dict[str, Any],
        exc: Exception,
        services: RouterHttpServices,
    ) -> None:
        if services.core.is_client_disconnect(exc):
            services.core.log("WARN", f"router_post_client_disconnected path={path} error={type(exc).__name__}: {exc}")
            return
        trace = traceback.format_exc(limit=20).replace("\n", "\\n")
        services.core.log("ERROR", f"router_post_uncaught path={path} error={type(exc).__name__}: {exc} trace={trace}")
        message = f"Ciel Runtime router error: {type(exc).__name__}: {exc}"
        stream = bool(body.get("stream", True))
        try:
            if path == "/v1/responses":
                services.errors.write_responses_error(self, message, stream=stream, status=500)
            elif "text/event-stream" in str(self.headers.get("accept") or "").lower() or stream:
                self.send_response(500)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.end_headers()
                payload = {"type": "error", "error": {"type": "api_error", "message": message}}
                self.wfile.write(f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            else:
                services.errors.try_write_json(
                    self,
                    {"type": "error", "error": {"type": "api_error", "message": message}},
                    500,
                )
        except Exception as write_exc:
            if not services.core.is_client_disconnect(write_exc):
                services.core.log(
                    "ERROR",
                    f"router_post_uncaught_response_failed path={path} "
                    f"error={type(write_exc).__name__}: {write_exc}",
                )

    def do_DELETE(self) -> None:
        services = self._services()
        path = urllib.parse.urlparse(self.path).path
        cfg = services.core.load_config()
        if services.core.reject_external(self, cfg):
            return
        if services.post.codex_mcp_split(self, path, b"", "DELETE"):
            return
        services.presentation.write_json(
            self,
            {"type": "error", "error": {"type": "not_found_error", "message": path}},
            404,
        )
