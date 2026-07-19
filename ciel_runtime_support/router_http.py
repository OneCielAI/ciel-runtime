"""HTTP adapter for the runtime router application services."""

from __future__ import annotations

import json
import sys
import traceback
import urllib.parse
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
