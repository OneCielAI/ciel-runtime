"""HTTP adapter for the runtime router application services."""

from __future__ import annotations

import io
import json
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from ciel_runtime_support.header_forwarding import (
    HOP_BY_HOP_REQUEST_HEADERS,
    project_end_to_end_request_headers,
)
from ciel_runtime_support.codex_reasoning_rejects import (
    drop_reasoning_matching_verdict,
    drop_rejected_reasoning,
    parse_missing_item_id,
    parse_unverifiable_encrypted_content,
    repair_unstored_items,
)
from ciel_runtime_support.responses_input_compatibility import (
    drop_rejected_tool_pair,
    parse_missing_tool_output_call_id,
    repair_replayed_response_items,
)
from ciel_runtime_support.channel_llm_context import ChannelLlmInjectionDeferred
from ciel_runtime_support.request_body_policy import (
    RequestBodyCapacityExceeded,
    RequestBodyTooLarge,
    RouterRequestBodyPolicy,
)
from ciel_runtime_support.remote_bridge import (
    REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
    REMOTE_LLM_PATHS,
    RemoteBridgeRouteError,
)
from ciel_runtime_support.tool_schema import request_tool_schema_scope
from ciel_runtime_support.upstream_dump import dump_upstream_request

# Upper bound on verdict-driven repairs of one replayed turn. The sealed
# reasoning rule needs one pass per rejected ciphertext and was measured
# converging in 39; the unknown-item rule needs exactly one.
MAX_REPLAY_REPAIR_ATTEMPTS = 64

# Each pass hands the compactor a smaller share of the window it was refused
# at, so a turn converges in a few rounds instead of the client's one-item-per
# -round-trip retry, which does not converge at all on a large transcript.
CONTEXT_COMPACTION_BUDGETS = (0.75, 0.5, 0.25)


class UpstreamContextExceeded(Exception):
    """The upstream refused the turn because its input does not fit."""

    def __init__(self, code: str, payload: bytes) -> None:
        super().__init__(code)
        self.code = code
        self.payload = payload


@dataclass(frozen=True, slots=True)
class CodexRoutedHeaderPolicy:
    decorate: Callable[[dict[str, str]], dict[str, str]]
    hop_by_hop: frozenset[str] = HOP_BY_HOP_REQUEST_HEADERS

    def project(self, inbound_headers: Any | None) -> dict[str, str]:
        headers = self.decorate(
            project_end_to_end_request_headers(
                inbound_headers,
                replace_credentials=False,
                transport_headers=self.hop_by_hop,
            )
        )
        if not any(
            str(name).casefold() == "authorization"
            for name in headers
        ):
            raise RuntimeError(
                "Codex routed mode did not receive native Codex auth headers "
                "from the Codex CLI."
            )
        return headers


@dataclass(frozen=True, slots=True)
class RouterHttpCore:
    load_config: Callable[[], dict[str, Any]]
    reject_external: Callable[[Any, dict[str, Any]], bool]
    get_current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    parse_json_body: Callable[[bytes], dict[str, Any]]
    is_client_disconnect: Callable[[BaseException], bool]
    log: Callable[[str, str], Any]
    observe_runtime: Callable[..., Any]
    request_body_policy: RouterRequestBodyPolicy
    remote_bridge: RouterHttpRemoteBridge | None = None


@dataclass(frozen=True, slots=True)
class RouterHttpRemoteBridge:
    enabled: Callable[[dict[str, Any]], bool] = lambda _config: False
    resolve_route: Callable[..., Any] | None = None
    status: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    is_request: Callable[[Any, dict[str, Any]], bool] = lambda _handler, _config: False


@dataclass(frozen=True, slots=True)
class RouterHttpGetEndpoints:
    tui: Callable[[Any, str, dict[str, list[str]]], bool]
    events: Callable[[Any, str, dict[str, list[str]]], bool]
    llm_config: Callable[[Any, str], bool]
    channel_mcp: Callable[[Any, str], bool]
    web: Callable[[Any, str], bool]
    speech: Callable[[Any, str], bool]
    chat: Callable[[Any, str], bool]
    plan: Callable[[Any, str], bool]
    runtime: Callable[..., bool]
    external_events: Callable[[Any, str], bool] | None = None


@dataclass(frozen=True, slots=True)
class RouterHttpPostEndpoints:
    speech: Callable[[Any, str, bytes, str], bool]
    llm_config: Callable[[Any, str, dict[str, Any]], bool]
    channel_mcp: Callable[[Any, str, dict[str, Any]], bool]
    chat: Callable[[Any, str, dict[str, Any]], bool]
    plan: Callable[[Any, str, dict[str, Any]], bool]
    runtime: Callable[..., bool]
    external_events_raw: Callable[[Any, str, bytes], bool] | None = None
    external_events_config: Callable[[Any, str, dict[str, Any]], bool] | None = None
    usage: Callable[[Any, str, dict[str, Any]], bool] | None = None
    telemetry_raw: Callable[[Any, str, bytes, str], bool] | None = None


@dataclass(frozen=True, slots=True)
class RouterHttpPresentation:
    home_html: Callable[..., str]
    health_payload: Callable[..., dict[str, Any]]
    write_text: Callable[..., Any]
    write_json: Callable[..., Any]
    list_models: Callable[..., list[dict[str, Any]]]
    resolve_model: Callable[..., str]
    model_object: Callable[..., dict[str, Any]]
    list_remote_bridge_models: Callable[..., list[dict[str, Any]]] | None = None


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
    transport_retry_limit: Callable[[], int] = lambda: 2
    retryable_exception: Callable[[BaseException], bool] = lambda _error: False


@dataclass(frozen=True, slots=True)
class CodexBackendRetryPorts:
    retry_limit: Callable[[], int]
    read_preamble: Callable[[Any], Any]
    retry_wait: Callable[[int], float]
    log: Callable[[str, str], None]
    publish: Callable[..., Any]
    sleep: Callable[[float], None]
    rejected_reasoning_contains: Callable[[str], bool] = lambda _sealed: False
    rejected_reasoning_record: Callable[[str], Any] = lambda _sealed: None
    estimate_tokens: Callable[[Any], int] = lambda _body: 0
    compact_responses: Callable[..., dict[str, Any]] = lambda body, _budget, **_kw: body


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
        allowed = {
            "cache-control",
            "content-type",
            "openai-request-id",
            "request-id",
            "retry-after",
            "x-github-request-id",
            "x-request-id",
        }
        try:
            items = headers.items()
        except (AttributeError, TypeError):
            items = []
        wrote_content_type = False
        for key, value in items:
            lowered = str(key).lower()
            if (
                lowered not in allowed
                and not lowered.startswith("anthropic-ratelimit-")
                and not lowered.startswith("x-ratelimit-")
            ):
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
        upstream_body = repair_replayed_response_items(body)
        upstream_body, prefiltered = drop_rejected_reasoning(
            upstream_body, self._retry.rejected_reasoning_contains
        )
        if prefiltered:
            self._retry.log(
                "INFO",
                f"codex_rejected_reasoning_prefiltered count={prefiltered}",
            )
        delivery_body: dict[str, Any] | None = None
        if mutate_responses:
            upstream_body, delivery_body = self._request.body_with_channel_context(upstream_body)
            self._request.begin_channel_delivery(handler, delivery_body)
        parsed = urllib.parse.urlparse(handler.path)
        url = self.upstream_url(parsed.path, parsed.query)
        headers = self._request.upstream_headers(config, handler.headers)
        data = json.dumps(upstream_body).encode("utf-8")
        dump_upstream_request(url, data, self._retry.log)
        # Every pass costs a full upstream round trip while the client sees
        # nothing, so the loop is capped: the unknown-item rule repairs the
        # whole request at once and needs one pass, and the sealed-reasoning
        # rule removes one ciphertext per pass and was measured converging in
        # 39. Past the cap the upstream's own error reaches the client instead
        # of the router retrying in silence.
        compaction_rounds = 0
        for attempt in range(MAX_REPLAY_REPAIR_ATTEMPTS + 1):
            try:
                self._send_codex_request(
                    handler, provider, config, url, headers, data, upstream_body,
                    mutate_responses=mutate_responses,
                )
                return delivery_body
            except UpstreamContextExceeded as exc:
                compacted = self._compacted_for_context(
                    upstream_body, compaction_rounds, provider, exc.code
                )
                if compacted is None:
                    self._write_preamble_failure(handler, exc.payload)
                    return delivery_body
                compaction_rounds += 1
                upstream_body = compacted
                data = json.dumps(upstream_body).encode("utf-8")
                dump_upstream_request(url, data, self._retry.log)
            except urllib.error.HTTPError as exc:
                if exc.code not in (400, 404):
                    raise
                raw = exc.read()
                exhausted = attempt >= MAX_REPLAY_REPAIR_ATTEMPTS
                repaired = (
                    None
                    if exhausted
                    else self._repaired_after_rejection(
                        exc.code,
                        raw.decode("utf-8", errors="replace"),
                        upstream_body,
                        provider,
                    )
                )
                if repaired is None:
                    if exhausted:
                        self._retry.log(
                            "WARN",
                            "codex_replay_repair_exhausted "
                            f"attempts={attempt} code={exc.code} relaying upstream error",
                        )
                    raise urllib.error.HTTPError(
                        exc.url, exc.code, exc.msg, exc.hdrs, io.BytesIO(raw)
                    ) from None
                upstream_body = repaired
                data = json.dumps(upstream_body).encode("utf-8")
                dump_upstream_request(url, data, self._retry.log)
        return delivery_body

    def _compacted_for_context(
        self,
        upstream_body: dict[str, Any],
        round_number: int,
        provider: str,
        code: str,
    ) -> dict[str, Any] | None:
        """Shrink the refused turn, keeping the newest history verbatim.

        The upstream, not a catalogued window size, decides that compaction is
        needed, so a wrong context number in our own metadata cannot discard
        history that would have fit.
        """

        if round_number >= len(CONTEXT_COMPACTION_BUDGETS):
            return None
        estimated = self._retry.estimate_tokens(upstream_body)
        budget = int(estimated * CONTEXT_COMPACTION_BUDGETS[round_number])
        compacted = self._retry.compact_responses(
            upstream_body, budget, provider=provider,
            model=str(upstream_body.get("model") or ""),
        )
        if compacted is upstream_body or compacted.get("input") == upstream_body.get("input"):
            return None
        self._retry.log(
            "ERROR",
            f"codex_context_compacted provider={provider} code={code} round={round_number + 1} "
            f"items={len(upstream_body.get('input') or [])}->{len(compacted.get('input') or [])} "
            f"tokens={estimated}->{self._retry.estimate_tokens(compacted)} budget={budget}",
        )
        self._retry.publish(
            level="warn",
            category="router.context",
            message="Compacted an oversized turn the upstream refused",
            provider=provider,
            model=str(upstream_body.get("model") or ""),
            data={"code": code, "round": round_number + 1, "budget": budget},
        )
        return compacted

    def _write_preamble_failure(
        self, handler: BaseHTTPRequestHandler, payload: bytes
    ) -> None:
        """Relay the upstream's own refusal once compaction cannot help."""

        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("connection", "close")
        handler.end_headers()
        handler.wfile.write(payload)
        handler.wfile.flush()

    def _repaired_after_rejection(
        self,
        status: int,
        error_text: str,
        upstream_body: dict[str, Any],
        provider: str,
    ) -> dict[str, Any] | None:
        """Rebuild the request around the item the upstream just named."""

        if status == 400:
            repaired = self._without_rejected_tool_pair(
                error_text, upstream_body, provider
            )
            if repaired is not None:
                return repaired
            return self._without_unverifiable_reasoning(error_text, upstream_body, provider)
        return self._without_unknown_item(error_text, upstream_body, provider)

    def _without_rejected_tool_pair(
        self,
        error_text: str,
        upstream_body: dict[str, Any],
        provider: str,
    ) -> dict[str, Any] | None:
        call_id = parse_missing_tool_output_call_id(error_text)
        if call_id is None:
            return None
        repaired, count = drop_rejected_tool_pair(upstream_body, error_text)
        if not count:
            return None
        self._retry.log(
            "WARN",
            f"codex_rejected_tool_pair_dropped call_id={call_id} "
            f"items={count} retrying",
        )
        self._retry.publish(
            level="warn",
            category="router.retry",
            message="Dropped a tool pair the upstream rejected as incomplete",
            provider=provider,
            model=str(repaired.get("model") or ""),
            data={"call_id": call_id, "items": count},
        )
        return repaired

    def _without_unverifiable_reasoning(
        self,
        error_text: str,
        upstream_body: dict[str, Any],
        provider: str,
    ) -> dict[str, Any] | None:
        verdict = parse_unverifiable_encrypted_content(error_text)
        if verdict is None:
            return None
        repaired, sealed = drop_reasoning_matching_verdict(upstream_body, *verdict)
        if sealed is None:
            return None
        self._retry.rejected_reasoning_record(sealed)
        self._retry.log(
            "WARN",
            "codex_unverifiable_reasoning_dropped "
            f"head={verdict[0]} tail={verdict[1]} retrying",
        )
        self._retry.publish(
            level="warn",
            category="router.retry",
            message="Dropped reasoning the upstream could not verify",
            provider=provider,
            model=str(repaired.get("model") or ""),
            data={"head": verdict[0], "tail": verdict[1]},
        )
        return repaired

    def _without_unknown_item(
        self,
        error_text: str,
        upstream_body: dict[str, Any],
        provider: str,
    ) -> dict[str, Any] | None:
        item_id = parse_missing_item_id(error_text)
        if item_id is None:
            return None
        repaired, count = repair_unstored_items(upstream_body)
        if not count:
            return None
        self._retry.log(
            "WARN",
            f"codex_unstored_items_repaired named={item_id} items={count} retrying",
        )
        self._retry.publish(
            level="warn",
            category="router.retry",
            message="Replayed items the upstream never stored",
            provider=provider,
            model=str(repaired.get("model") or ""),
            data={"item_id": item_id, "items": count},
        )
        return repaired

    def _send_codex_request(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        config: dict[str, Any],
        url: str,
        headers: dict[str, str],
        data: bytes,
        upstream_body: dict[str, Any],
        *,
        mutate_responses: bool,
    ) -> None:
        max_retries = self._retry.retry_limit() if mutate_responses else 0
        for attempt in range(max_retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with self._open_with_transport_retry(
                request, provider, config, str(upstream_body.get("model") or ""), "responses"
            ) as response:
                preamble = self._retry.read_preamble(response) if mutate_responses else None
                if preamble is not None and getattr(preamble, "context_error_code", None):
                    # Nothing has been written to the client yet, so the turn can
                    # still be made to fit instead of failing.
                    raise UpstreamContextExceeded(
                        preamble.context_error_code, preamble.payload
                    )
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

    def _open_with_transport_retry(
        self,
        request: urllib.request.Request,
        provider: str,
        config: dict[str, Any],
        model: str,
        operation: str,
    ) -> Any:
        retries = self._request.transport_retry_limit()
        for attempt in range(retries + 1):
            try:
                return self._request.urlopen(
                    request,
                    timeout=self._request.request_timeout(config),
                    provider=provider,
                    pcfg=config,
                )
            except (TimeoutError, urllib.error.URLError, OSError) as error:
                if attempt >= retries or not self._request.retryable_exception(error):
                    raise
                retry_number = attempt + 1
                wait = self._retry.retry_wait(retry_number)
                self._retry.log(
                    "WARN",
                    "codex_transport_retry "
                    f"operation={operation} model={model} "
                    f"attempt={retry_number}/{retries} wait={wait:.2f}s "
                    f"error={type(error).__name__}: {error}",
                )
                self._retry.publish(
                    level="warn",
                    category="router.retry",
                    message="Codex transport retry",
                    provider=provider,
                    model=model,
                    data={
                        "operation": operation,
                        "attempt": retry_number,
                        "total": retries,
                        "wait_seconds": wait,
                        "error": type(error).__name__,
                    },
                )
                self._retry.sleep(wait)
        raise RuntimeError("Codex transport retry exhausted")

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
        with self._open_with_transport_retry(
            request, provider, config, "", "get"
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
        self._ciel_runtime_response_status = None
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
        self._ciel_runtime_response_status = None
        services = self._services()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        cfg = services.core.load_config()
        if path.startswith("/ca/usage/") and services.get.events(self, path, query):
            return
        if services.core.reject_external(self, cfg):
            return
        endpoints = services.get
        if endpoints.tui(self, path, query):
            return
        if endpoints.events(self, path, query):
            return
        if endpoints.external_events is not None and endpoints.external_events(self, path):
            return
        if endpoints.llm_config(self, path):
            return
        if endpoints.channel_mcp(self, path):
            return
        if endpoints.web(self, path):
            return
        if endpoints.speech(self, path):
            return
        if endpoints.chat(self, path) or endpoints.plan(self, path):
            return
        provider, pcfg = services.core.get_current_provider(cfg)
        presentation = services.presentation
        bridge = services.core.remote_bridge or RouterHttpRemoteBridge()
        bridge_enabled = bridge.enabled(cfg)
        bridge_request = bridge_enabled and bridge.is_request(self, cfg)
        if (
            path == "/ca/bridge"
            and bridge_enabled
            and bridge.status is not None
        ):
            presentation.write_json(self, bridge.status(cfg))
            return
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
            if bridge_request and presentation.list_remote_bridge_models is not None:
                data = presentation.list_remote_bridge_models(cfg, self.headers)
            else:
                data = presentation.list_models(provider, pcfg, self.headers)
            # OpenAI-compatible clients consume ``data``. Codex 0.150.1 instead
            # deserializes this endpoint as ``ModelsResponse { models }``. Keep
            # the OpenAI catalog authoritative and advertise an empty Codex
            # metadata overlay: Codex then retains its bundled/fallback model
            # metadata without mistaking OpenAI model objects for ModelInfo.
            presentation.write_json(
                self,
                {
                    "object": "list",
                    "data": data,
                    "has_more": False,
                    "models": [],
                },
            )
            return
        if path.startswith("/v1/models/"):
            model_id = urllib.parse.unquote(path[len("/v1/models/"):])
            if bridge_request and bridge.resolve_route is not None:
                try:
                    route = bridge.resolve_route(
                        cfg,
                        self.headers,
                        {"model": model_id},
                        path,
                    )
                except RemoteBridgeRouteError as exc:
                    if path.startswith("/v1/models/"):
                        presentation.write_json(
                            self,
                            {
                                "error": {
                                    "message": (
                                        f"The model '{model_id}' does not exist"
                                    ),
                                    "type": "invalid_request_error",
                                    "param": "model",
                                    "code": "model_not_found",
                                }
                            },
                            404,
                        )
                        return
                    presentation.write_json(
                        self,
                        {
                            "error": {
                                "message": str(exc),
                                "type": "invalid_request_error",
                                "code": "invalid_request_error",
                            }
                        },
                        400,
                    )
                    return
                provider = route.provider
                pcfg = route.provider_config
                model_id = str(route.body.get("model") or "")
                if presentation.list_remote_bridge_models is not None:
                    canonical_id = f"{provider}/{model_id}"
                    known_model = next(
                        (
                            item
                            for item in presentation.list_remote_bridge_models(
                                cfg, self.headers
                            )
                            if isinstance(item, dict)
                            and str(item.get("id") or "") == canonical_id
                        ),
                        None,
                    )
                    if known_model is None:
                        presentation.write_json(
                            self,
                            {
                                "error": {
                                    "message": (
                                        f"The model '{canonical_id}' does not exist"
                                    ),
                                    "type": "invalid_request_error",
                                    "param": "model",
                                    "code": "model_not_found",
                                }
                            },
                            404,
                        )
                        return
                    presentation.write_json(self, dict(known_model))
                    return
            resolved = presentation.resolve_model(provider, pcfg, model_id)
            if bridge_request:
                obj = presentation.model_object(provider, resolved, pcfg)
                obj = dict(obj)
                obj["id"] = f"{provider}/{resolved}"
            else:
                obj = presentation.model_object(provider, resolved)
            presentation.write_json(self, obj)
            return
        presentation.write_json(self, {"type": "error", "error": {"type": "not_found_error", "message": path}}, 404)

    @staticmethod
    def _header_values(headers: Any, name: str) -> list[str]:
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            values = get_all(name)
            if values:
                return [str(value).strip() for value in values]
        raw = headers.get(name)
        if raw is None:
            try:
                raw = next(
                    value
                    for key, value in headers.items()
                    if str(key).casefold() == name.casefold()
                )
            except (AttributeError, StopIteration):
                return []
        return [str(raw).strip()]

    def _write_request_error(
        self,
        path: str,
        services: RouterHttpServices,
        *,
        status: int,
        error_type: str,
        message: str,
    ) -> None:
        # The body has not necessarily been consumed.  Closing the connection
        # prevents unread bytes from being parsed as the next HTTP request.
        self.close_connection = True
        services.core.log(
            "WARN",
            f"router_request_rejected path={path} status={status} "
            f"error_type={error_type} message={message}",
        )
        if path in {
            "/v1/responses",
            "/v1/responses/compact",
            "/backend-api/codex/responses",
        }:
            services.errors.write_responses_error(
                self,
                message,
                stream=False,
                status=status,
                error_type=error_type,
            )
            return
        if path in {"/v1/messages", "/v1/messages/count_tokens"}:
            services.errors.try_write_json(
                self,
                {
                    "type": "error",
                    "error": {"type": error_type, "message": message},
                },
                status,
            )
            return
        if path == "/v1/chat/completions":
            services.errors.try_write_json(
                self,
                {
                    "error": {
                        "message": message,
                        "type": error_type,
                        "param": None,
                        "code": error_type,
                    }
                },
                status,
            )
            return
        services.presentation.write_json(
            self,
            {"ok": False, "error": error_type, "message": message},
            status,
        )

    def _validated_content_length(
        self,
        path: str,
        services: RouterHttpServices,
    ) -> int | None:
        transfer_encodings = self._header_values(self.headers, "transfer-encoding")
        if transfer_encodings and any(
            value.casefold() not in {"", "identity"} for value in transfer_encodings
        ):
            self._write_request_error(
                path,
                services,
                status=501,
                error_type="invalid_request_error",
                message="Chunked or transformed request bodies are not supported by this router",
            )
            return None
        content_encodings = self._header_values(self.headers, "content-encoding")
        if content_encodings and any(
            value.casefold() not in {"", "identity"} for value in content_encodings
        ):
            self._write_request_error(
                path,
                services,
                status=415,
                error_type="invalid_request_error",
                message="Compressed request bodies are not supported by this router",
            )
            return None
        values = self._header_values(self.headers, "content-length")
        if not values:
            # Preserve the historical empty-POST behavior.  A transfer-coded
            # body was already rejected above, so no unread payload remains.
            return 0
        value = values[0]
        if (
            len(values) != 1
            or not value
            or len(value) > 20
            or any(character < "0" or character > "9" for character in value)
        ):
            self._write_request_error(
                path,
                services,
                status=400,
                error_type="invalid_request_error",
                message="Content-Length must be one non-negative decimal integer",
            )
            return None
        try:
            return int(value)
        except ValueError:
            self._write_request_error(
                path,
                services,
                status=400,
                error_type="invalid_request_error",
                message="Content-Length is outside the supported integer range",
            )
            return None

    def _read_request_body(
        self,
        path: str,
        services: RouterHttpServices,
        length: int,
    ) -> bytes | None:
        if not length:
            return b"{}"
        raw = self.rfile.read(length)
        if len(raw) == length:
            return raw
        self._write_request_error(
            path,
            services,
            status=400,
            error_type="invalid_request_error",
            message=f"Request body ended early: expected {length} bytes, received {len(raw)} bytes",
        )
        return None

    def do_POST(self) -> None:
        self._ciel_runtime_response_status = None
        services = self._services()
        path = urllib.parse.urlparse(self.path).path
        body: dict[str, Any] = {}
        try:
            cfg = services.core.load_config()
            endpoints = services.post
            is_webhook = path.startswith("/ca/events/webhooks/")
            has_endpoint_auth = is_webhook or path == "/v1/logs"
            if not has_endpoint_auth and services.core.reject_external(self, cfg):
                self.close_connection = True
                return
            length = self._validated_content_length(path, services)
            if length is None:
                return
            try:
                admission = services.core.request_body_policy.admit(
                    path,
                    length,
                    str(self.headers.get("content-type") or "application/json"),
                )
                with admission:
                    raw = self._read_request_body(path, services, length)
                    if raw is None:
                        return
                    if is_webhook:
                        if (
                            endpoints.external_events_raw is not None
                            and endpoints.external_events_raw(self, path, raw)
                        ):
                            return
                        services.presentation.write_json(
                            self,
                            {"ok": False, "error": "receiver_not_available"},
                            404,
                        )
                        return
                    if (
                        endpoints.telemetry_raw is not None
                        and endpoints.telemetry_raw(
                            self,
                            path,
                            raw,
                            str(self.headers.get("content-type") or "application/json"),
                        )
                    ):
                        return
                    if endpoints.speech(
                        self,
                        path,
                        raw,
                        str(self.headers.get("content-type") or "application/json"),
                    ):
                        return
                    body = services.core.parse_json_body(raw)
                    services.core.request_body_policy.validate_parsed_body(
                        path,
                        length,
                        body,
                    )
                    if endpoints.usage is not None and endpoints.usage(self, path, body):
                        return
                    if endpoints.external_events_config is not None and endpoints.external_events_config(self, path, body):
                        return
                    if endpoints.llm_config(self, path, body):
                        return
                    if endpoints.channel_mcp(self, path, body):
                        return
                    if endpoints.chat(self, path, body) or endpoints.plan(self, path, body):
                        return
                    provider, pcfg = services.core.get_current_provider(cfg)
                    bridge = services.core.remote_bridge or RouterHttpRemoteBridge()
                    bridge_request = (
                        path in REMOTE_LLM_PATHS
                        and bridge.enabled(cfg)
                        and bridge.is_request(self, cfg)
                    )
                    if bridge_request and bridge.resolve_route is not None:
                        try:
                            route = bridge.resolve_route(
                                cfg,
                                self.headers,
                                body,
                                path,
                            )
                        except RemoteBridgeRouteError as exc:
                            self._write_request_error(
                                path,
                                services,
                                status=400,
                                error_type="invalid_request_error",
                                message=str(exc),
                            )
                            return
                        provider = route.provider
                        pcfg = route.provider_config
                        body = route.body
                    model = str(body.get("model") or pcfg.get("current_model") or "")
                    inbound_headers = self.headers
                    setattr(
                        self,
                        REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
                        bridge_request,
                    )
                    if bridge_request:
                        self.headers = project_end_to_end_request_headers(
                            self.headers,
                            replace_credentials=True,
                        )
                    try:
                        if bridge_request:
                            with request_tool_schema_scope(body.get("tools")):
                                with services.core.observe_runtime(
                                    self, path, provider, model, body
                                ):
                                    if endpoints.runtime(
                                        self, cfg, provider, pcfg, path, body
                                    ):
                                        return
                        else:
                            with services.core.observe_runtime(
                                self, path, provider, model, body
                            ):
                                if endpoints.runtime(
                                    self, cfg, provider, pcfg, path, body
                                ):
                                    return
                    except ValueError as exc:
                        if (
                            not bridge_request
                            or self._ciel_runtime_response_status is not None
                        ):
                            raise
                        self._write_request_error(
                            path,
                            services,
                            status=400,
                            error_type="invalid_request_error",
                            message=str(exc),
                        )
                        return
                    finally:
                        self.headers = inbound_headers
                    services.presentation.write_json(
                        self,
                        {"type": "error", "error": {"type": "not_found_error", "message": path}},
                        404,
                    )
            except RequestBodyTooLarge as exc:
                self._write_request_error(
                    path,
                    services,
                    status=413,
                    error_type="request_too_large",
                    message=str(exc),
                )
                return
            except RequestBodyCapacityExceeded as exc:
                self._write_request_error(
                    path,
                    services,
                    status=503,
                    error_type="router_busy",
                    message=str(exc),
                )
                return
            except ChannelLlmInjectionDeferred as exc:
                # A wake request whose pending body must wait for an earlier
                # terminal-owned message.  This is an expected, retryable
                # ordering condition — answer with a structured 503 (the
                # client backs off; runtime-side wake retry/fallback recovers
                # delivery), never an uncaught 500 with a traceback.
                self._write_request_error(
                    path,
                    services,
                    status=503,
                    error_type="channel_injection_deferred",
                    message=str(exc),
                )
                return
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
        if self._ciel_runtime_response_status is not None:
            self.close_connection = True
            services.core.log(
                "ERROR",
                "router_post_uncaught_after_response_started "
                f"path={path} status={self._ciel_runtime_response_status} "
                f"error={type(exc).__name__}: {exc}",
            )
            return
        message = f"Ciel Runtime router error: {type(exc).__name__}: {exc}"
        stream = bool(body.get("stream", True))
        try:
            if path in {"/v1/responses", "/v1/responses/compact"}:
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
        self._ciel_runtime_response_status = None
        services = self._services()
        path = urllib.parse.urlparse(self.path).path
        cfg = services.core.load_config()
        if services.core.reject_external(self, cfg):
            return
        services.presentation.write_json(
            self,
            {"type": "error", "error": {"type": "not_found_error", "message": path}},
            404,
        )
