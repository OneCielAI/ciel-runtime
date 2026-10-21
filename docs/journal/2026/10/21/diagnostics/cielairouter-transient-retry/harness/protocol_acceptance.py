"""Owned real-loopback protocol acceptance for the CielAiRouter retry owner.

Every case runs the production retry wiring against owned loopback HTTP
fixtures (ephemeral ports, scripted provider bodies):

* the shared ``upstream_retry`` opener that Claude native Messages calls
  (``ciel_runtime.open_provider_request_with_key_retry`` built by
  ``upstream_retry_context()``),
* ``ciel_runtime.provider_responses_passthrough()`` (native Responses),
* ``ciel_runtime.provider_chat_passthrough()`` (Chat Completions),
* the real ``CielAiRouterGatewayService`` + ``CielAiRouterGatewayOverlay``
  through ``provider_network.provider_urlopen`` and the production
  ``reselect_cielairouter_gateway``.

No live runtime, provider credential, remote host or workspace state is
touched: every CIEL_RUNTIME*/provider variable is removed, and HOME, config,
cwd and temp roots are private before ``ciel_runtime`` is imported.

Output records PASS/FAIL, hit counts, budget identity/counters and payload
digests - never provider payload text.

usage: python -I protocol_acceptance.py [--label NAME]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sys
import tempfile
import threading
import time
import urllib.error
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[7]


def _isolate_environment() -> dict[str, str]:
    """Private HOME/config/cwd/temp and no inherited provider variables."""

    root = Path(tempfile.mkdtemp(prefix="ciel-protocol-acceptance-"))
    home, config, cwd, tmp = (root / name for name in ("home", "config", "cwd", "tmp"))
    for path in (home, config, cwd, tmp):
        path.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        upper = key.upper()
        if (
            upper.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "OPENAI_", "CODEX"))
            or any(word in upper for word in
                   ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "PROXY"))
        ):
            os.environ.pop(key, None)
    os.environ.update(
        HOME=str(home), USERPROFILE=str(home),
        CIEL_RUNTIME_CONFIG_DIR=str(config),
        CIEL_RUNTIME_TEST_ISOLATED="1",
        CIEL_RUNTIME_LAUNCH_CWD=str(cwd),
        TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp),
        NO_PROXY="*", no_proxy="*",
    )
    os.chdir(cwd)
    return {"root": str(root), "home": str(home), "config": str(config),
            "cwd": str(cwd), "tmp": str(tmp)}


ISOLATION = _isolate_environment()
if not (REPO / "ciel_runtime_support" / "transient_retry.py").exists():
    raise SystemExit(f"repository root not found from {HERE}")
sys.path.insert(0, str(REPO))

import ciel_runtime  # noqa: E402
from ciel_runtime_support.cielairouter_gateway_overlay import (  # noqa: E402
    ROUTE_TOKEN_KEY,
    CielAiRouterGatewayOverlay,
    use_gateway_overlay,
)
from ciel_runtime_support.cielairouter_gateways import (  # noqa: E402
    CielAiRouterGatewayService,
    ProbeResult,
)
from ciel_runtime_support.remote_bridge import (  # noqa: E402
    REMOTE_BRIDGE_CONFIG_MARKER,
    REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
)
from ciel_runtime_support.transient_retry import budget_for, request_scope  # noqa: E402
from ciel_runtime_support.upstream_retry import (  # noqa: E402
    open_provider_request_with_key_retry,
)

PROVIDER = "cielairouter"
CENTRAL = "https://central.acceptance.test"
MODEL = "accept-model"
MODEL_BODY = {"model": MODEL, "messages": [{"role": "user", "content": "accept"}], "stream": True}
MESSAGE_JSON = json.dumps({
    "id": "msg_accept", "type": "message", "role": "assistant", "model": MODEL,
    "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
    "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
}, separators=(",", ":")).encode()
MESSAGE_STREAM = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_accept",'
    b'"type":"message","role":"assistant","model":"accept-model","content":[],'
    b'"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta",'
    b'"delta":{"stop_reason":"end_turn","stop_sequence":null},'
    b'"usage":{"output_tokens":1}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)
# The exact captured CielAiRouter pre-dispatch rejection from the diagnosis.
SCREENSHOT = (
    b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
    b'"message":"service_unavailable: Service temporarily unavailable: all targets '
    b'were skipped by pre-dispatch filters"}}\n\n'
)
QUOTA = b'{"type":"error","error":{"type":"insufficient_quota","message":"quota exhausted"}}'
MALFORMED = b"<html>503</html>"

RESP_CREATED = (
    b'event: response.created\ndata: {"type":"response.created",'
    b'"response":{"id":"resp_accept"}}\n\n'
)
RESP_TOOL_ITEM = (
    b'event: response.output_item.done\ndata: '
    + json.dumps({
        "type": "response.output_item.done", "output_index": 0,
        "item": {"type": "function_call", "call_id": "call_accept_1",
                 "name": "exec_command", "arguments": "{}"},
    }, separators=(",", ":")).encode()
    + b"\n\n"
)
RESP_COMPLETED = (
    b'event: response.completed\ndata: {"type":"response.completed",'
    b'"response":{"id":"resp_accept","status":"completed","output":[]}}\n\n'
)
RESP_OK = RESP_CREATED + RESP_COMPLETED
RESP_OK_TOOL = RESP_CREATED + RESP_TOOL_ITEM + RESP_COMPLETED
RESP_BODY = {"model": MODEL, "input": [], "stream": True}
RESP_TOOL_BODY = {
    "model": MODEL,
    "input": [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "accept"}]},
        {"type": "additional_tools",
         "tools": [{"type": "function", "name": "exec_command",
                    "parameters": {"type": "object", "properties": {}}}]},
    ],
    "tools": [{"type": "function", "name": "exec_command",
               "parameters": {"type": "object", "properties": {}}}],
    "tool_choice": "none",
    "stream": True,
}
CHAT_JSON = json.dumps({
    "id": "chatcmpl-accept", "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
}, separators=(",", ":")).encode()
CHAT_TOOL_JSON = json.dumps({
    "id": "chatcmpl-accept", "object": "chat.completion",
    "choices": [{"index": 0, "message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_accept_1", "type": "function",
                        "function": {"name": "exec_command", "arguments": "{}"}}],
    }, "finish_reason": "tool_calls"}],
}, separators=(",", ":")).encode()
CHAT_BODY = {"model": MODEL, "messages": [{"role": "user", "content": "accept"}]}
CHAT_TOOL_BODY = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "accept"}],
    "tools": [{"type": "function", "function": {
        "name": "exec_command", "parameters": {"type": "object", "properties": {}}}}],
    "tool_choice": "auto",
}
MESSAGE_OUTPUT_THEN_ERROR = (
    MESSAGE_STREAM.split(b'event: content_block_start')[0]
    + b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
      b'"delta":{"type":"text_delta","text":"partial"}}\n\n'
    + SCREENSHOT
)
RESP_OUTPUT_THEN_ERROR = RESP_CREATED + RESP_TOOL_ITEM + SCREENSHOT + RESP_COMPLETED
CHAT_OUTPUT_THEN_ERROR = (
    b'data: {"id":"chatcmpl-accept","choices":[{"index":0,'
    b'"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
    + SCREENSHOT
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class Fixture:
    """One scripted loopback provider; records every real HTTP request."""

    def __init__(self, script, default):
        self.script = list(script)
        self.default = default
        self.hits: list[dict] = []
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _handler(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                with fixture.lock:
                    fixture.hits.append({
                        "path": self.path,
                        "body": raw,
                        "authorization": self.headers.get("authorization"),
                    })
                    status, body, content_type = (
                        fixture.script.pop(0) if fixture.script else fixture.default
                    )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def stop(self) -> bool:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        return not self.thread.is_alive()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.stop()


class Sink:
    """Minimal downstream handler used by the production passthroughs."""

    def __init__(self, remote_bridge: bool = False):
        self.headers: dict[str, str] = {}
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.sent_headers: list[tuple[str, str]] = []
        self.ended = False
        if remote_bridge:
            setattr(self, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

    def send_response(self, status, *_args, **_kwargs):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((str(key).lower(), str(value)))

    def end_headers(self):
        self.ended = True

    def body(self) -> bytes:
        return self.wfile.getvalue()


class Checks:
    """Ordered PASS/FAIL checks; the first failure stops one case only."""

    def __init__(self):
        self.items: list[dict] = []

    def expect(self, label: str, condition, detail: str = ""):
        ok = bool(condition)
        item = {"check": label, "ok": ok}
        if not ok:
            item["detail"] = str(detail)[:240]
        self.items.append(item)
        if not ok:
            raise AssertionError(f"{label}: {detail}")

    def equal(self, label: str, actual, expected):
        self.expect(label, actual == expected, f"actual={actual!r} expected={expected!r}")


def central_config(base: str, retries: int | None = 3) -> dict:
    config = {"base_url": base, "api_key": "fixture-key",
              "current_model": MODEL}
    if retries is not None:
        config["gateway_retries"] = retries
    return config


def budget_summary(budgets: list) -> dict:
    values = [budget for budget in budgets if budget is not None]
    return {
        "opens": len(budgets),
        "shared_objects": len({id(budget) for budget in values}),
        "none_opens": sum(budget is None for budget in budgets),
        "attempts": values[-1].attempts if values else None,
        "opens_recorded": values[-1].opens if values else None,
        "exposed": values[-1].exposed if values else None,
    }


def opener_call(config: dict, body: dict, *, stream: bool = True,
                budgets: list | None = None) -> bytes:
    """Production shared opener: ``upstream_retry_context()`` services, one scope.

    ``ciel_runtime.open_provider_request_with_key_retry`` builds these services
    and calls this exact ``upstream_retry`` entrypoint; only ``provider_urlopen``
    is wrapped to record the request-scoped budget object it observes.
    """

    budgets = [] if budgets is None else budgets
    services = ciel_runtime.upstream_retry_context().services()
    production_open = services.http.provider_urlopen

    def observed_open(request, *, timeout, provider, pcfg):
        budgets.append(budget_for(pcfg, PROVIDER))
        return production_open(request, timeout=timeout, provider=provider, pcfg=pcfg)

    instrumented = replace(
        services, http=replace(services.http, provider_urlopen=observed_open)
    )
    headers = {"authorization": f"Bearer {config['api_key']}"}
    url = config["base_url"].rstrip("/") + "/v1/messages"
    scope_provider = "" if config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True else PROVIDER
    with request_scope(config, scope_provider):
        response = open_provider_request_with_key_retry(
            url, body, headers, 5.0, PROVIDER, config, MODEL,
            stream=stream, services=instrumented,
        )
        try:
            return response.read()
        finally:
            response.close()


def assert_requests(checks: Checks, hits: list[dict], path: str,
                    *, expect_same_authorization: bool = True) -> None:
    checks.expect("upstream_hits_recorded", bool(hits), "no upstream request")
    bodies = {digest(hit["body"]) for hit in hits}
    checks.expect("all_retries_use_identical_request_body", len(bodies) == 1, bodies)
    checks.expect("all_retries_use_expected_endpoint",
                  all(hit["path"] == path for hit in hits),
                  [hit["path"] for hit in hits])
    if expect_same_authorization:
        auths = {hit["authorization"] for hit in hits}
        checks.expect("all_retries_use_identical_authorization", len(auths) == 1, auths)


def payload_facts(raw: bytes) -> dict:
    return {"bytes": len(raw), "sha256": digest(raw)}


def count_occurrences(raw: bytes, needle: bytes) -> int:
    return raw.count(needle)


class GatewayHarness:
    """Real selector + overlay selecting one loopback gateway per route."""

    def __init__(self, failing: Fixture, succeeding: Fixture):
        self.failing = failing
        self.succeeding = succeeding
        now = time.time()

        def row(node: str, endpoint: str) -> dict:
            return {
                "id": node, "endpoint": endpoint, "status": "online",
                "health": "healthy", "lastSeenAt": iso(now - 1),
                "availableUntil": iso(now + 300),
            }

        self.selector = CielAiRouterGatewayService(
            clock=time.time,
            discovery=lambda *_args, **_kwargs: {
                "object": "list", "protocol_version": 1, "generated_at": iso(now),
                "data": [row("gw-a", failing.base), row("gw-b", succeeding.base)],
            },
            probe=lambda candidate, *_args, **_kwargs: ProbeResult(
                True,
                0.001 if candidate.endpoint == failing.base else 0.05,
                200, candidate.node_id, "gateway", True,
            ),
            allow_insecure_http=True,
        )
        self.overlay = CielAiRouterGatewayOverlay(lambda: self.selector)
        use_gateway_overlay(self.overlay)
        self.central_config = central_config(CENTRAL, 3)
        self.pcfg = self.overlay.apply(PROVIDER, self.central_config)

    def route(self) -> dict:
        return self.overlay.status()["routes"][-1]

    def close(self) -> None:
        use_gateway_overlay(None)


def selected_harness(checks: Checks) -> GatewayHarness:
    failing = Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                      (503, SCREENSHOT, "text/event-stream"))
    succeeding = Fixture([], (200, b"unused", "application/json"))
    try:
        harness = GatewayHarness(failing, succeeding)
        checks.expect("overlay_pinned_first_gateway",
                      harness.pcfg["base_url"] == failing.base
                      and bool(harness.pcfg.get(ROUTE_TOKEN_KEY)),
                      harness.pcfg.get("base_url"))
        checks.equal("central_config_keeps_central_base",
                     harness.central_config["base_url"], CENTRAL)
        checks.expect("central_config_has_no_route_token",
                      ROUTE_TOKEN_KEY not in harness.central_config)
        return harness
    except BaseException:
        failing.stop()
        succeeding.stop()
        use_gateway_overlay(None)
        raise


def case(name: str, run) -> dict:
    checks = Checks()
    result = {"case": name, "started_at": iso(time.time())}
    started = time.monotonic()
    try:
        facts = run(checks) or {}
        result.update(facts)
        result["passed"] = True
    except BaseException as exc:  # noqa: BLE001 - one case must not stop the matrix
        result["passed"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        result["checks"] = checks.items
    return result


# --------------------------------------------------------------------------
# Shared opener (Claude native Messages transport)
# --------------------------------------------------------------------------

def opener_screenshot_to_success(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")],
                 (200, MESSAGE_STREAM, "text/event-stream")) as fixture:
        budgets: list = []
        data = opener_call(central_config(fixture.base, 3), MODEL_BODY, budgets=budgets)
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        checks.expect("client_stream_is_exactly_one_success",
                      data == MESSAGE_STREAM, payload_facts(data))
        assert_requests(checks, fixture.hits, "/v1/messages")
        summary = budget_summary(budgets)
        checks.equal("budget_attempts", summary["attempts"], 1)
        checks.equal("budget_opens", summary["opens_recorded"], 2)
        checks.equal("one_shared_budget_object", summary["shared_objects"], 1)
        checks.expect("budget_marked_exposed_after_success_read",
                      summary["exposed"] is True, summary)
        return {"upstream_hits": len(fixture.hits),
                "request_body": payload_facts(fixture.hits[0]["body"]),
                "client_body": payload_facts(data), "budget": summary}


def opener_screenshot_exhausted(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 8,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        budgets: list = []
        error = None
        try:
            opener_call(central_config(fixture.base, 3), MODEL_BODY, budgets=budgets)
        except urllib.error.HTTPError as exc:
            error = (exc.code, exc.read())
        checks.expect("client_error_recorded", error is not None)
        checks.equal("client_status", error[0], 503)
        checks.expect("client_error_body_is_original_screenshot",
                      error[1] == SCREENSHOT, payload_facts(error[1]))
        checks.equal("upstream_hit_count", len(fixture.hits), 4)
        assert_requests(checks, fixture.hits, "/v1/messages")
        summary = budget_summary(budgets)
        checks.equal("budget_attempts", summary["attempts"], 3)
        checks.equal("budget_opens", summary["opens_recorded"], 4)
        checks.equal("one_shared_budget_object", summary["shared_objects"], 1)
        return {"upstream_hits": len(fixture.hits),
                "client_error": payload_facts(error[1]), "budget": summary}


def opener_default_limit(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 8,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        budgets: list = []
        error = None
        try:
            opener_call(central_config(fixture.base, None), MODEL_BODY,
                        budgets=budgets)
        except urllib.error.HTTPError as exc:
            error = (exc.code, exc.read())
        checks.equal("upstream_hit_count_unset_is_three_retries",
                     len(fixture.hits), 4)
        checks.equal("client_status", error[0] if error else None, 503)
        summary = budget_summary(budgets)
        checks.equal("budget_attempts", summary["attempts"], 3)
        return {"upstream_hits": len(fixture.hits), "budget": summary}


def opener_explicit_zero(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        budgets: list = []
        error = None
        try:
            opener_call(central_config(fixture.base, 0), MODEL_BODY,
                        budgets=budgets)
        except urllib.error.HTTPError as exc:
            error = (exc.code, exc.read())
        checks.equal("upstream_hit_count", len(fixture.hits), 1)
        checks.equal("client_status", error[0] if error else None, 503)
        checks.expect("client_error_body_is_original_screenshot",
                      error is not None and error[1] == SCREENSHOT)
        summary = budget_summary(budgets)
        checks.equal("no_budget_allocated", summary["none_opens"], 1)
        checks.equal("no_budget_attempts", summary["attempts"], None)
        return {"upstream_hits": len(fixture.hits), "budget": summary}


def opener_http200_initial_error(checks: Checks) -> dict:
    with Fixture([(200, SCREENSHOT, "text/event-stream")],
                 (200, MESSAGE_STREAM, "text/event-stream")) as fixture:
        budgets: list = []
        data = opener_call(central_config(fixture.base, 3), MODEL_BODY,
                           budgets=budgets)
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        checks.expect("client_stream_is_exactly_one_success",
                      data == MESSAGE_STREAM, payload_facts(data))
        assert_requests(checks, fixture.hits, "/v1/messages")
        summary = budget_summary(budgets)
        checks.equal("budget_attempts", summary["attempts"], 1)
        return {"upstream_hits": len(fixture.hits),
                "client_body": payload_facts(data), "budget": summary}


def opener_output_then_error(checks: Checks) -> dict:
    stream = MESSAGE_OUTPUT_THEN_ERROR
    with Fixture([(200, stream, "text/event-stream")],
                 (200, MESSAGE_STREAM, "text/event-stream")) as fixture:
        budgets: list = []
        data = opener_call(central_config(fixture.base, 3), MODEL_BODY,
                           budgets=budgets)
        checks.equal("upstream_hit_count_no_replay_after_output",
                     len(fixture.hits), 1)
        checks.expect("client_receives_entire_original_stream_exactly",
                      data == stream, payload_facts(data))
        summary = budget_summary(budgets)
        checks.equal("no_retry_attempt", summary["attempts"], 0)
        checks.expect("budget_marked_exposed", summary["exposed"] is True, summary)
        return {"upstream_hits": len(fixture.hits),
                "client_body": payload_facts(data), "budget": summary}


def opener_malformed(checks: Checks) -> dict:
    with Fixture([(503, MALFORMED, "text/html")] * 4,
                 (503, MALFORMED, "text/html")) as fixture:
        budgets: list = []
        error = None
        try:
            opener_call(central_config(fixture.base, 3), MODEL_BODY,
                        budgets=budgets)
        except urllib.error.HTTPError as exc:
            error = (exc.code, exc.read())
        checks.equal("upstream_hit_count_no_verdict_no_retry",
                     len(fixture.hits), 1)
        checks.expect("client_body_is_original_malformed_error",
                      error is not None and error[1] == MALFORMED)
        summary = budget_summary(budgets)
        checks.equal("no_retry_attempt", summary["attempts"], 0)
        return {"upstream_hits": len(fixture.hits), "budget": summary}


def opener_usage_limit(checks: Checks) -> dict:
    with Fixture([(503, QUOTA, "application/json")] * 4,
                 (503, QUOTA, "application/json")) as fixture:
        budgets: list = []
        error = None
        try:
            opener_call(central_config(fixture.base, 3), MODEL_BODY,
                        budgets=budgets)
        except urllib.error.HTTPError as exc:
            error = (exc.code, exc.read())
        checks.equal("upstream_hit_count_terminal_usage_limit_no_retry",
                     len(fixture.hits), 1)
        checks.expect("client_body_is_original_quota_error",
                      error is not None and error[1] == QUOTA)
        summary = budget_summary(budgets)
        checks.equal("no_retry_attempt", summary["attempts"], 0)
        return {"upstream_hits": len(fixture.hits), "budget": summary}


def opener_fresh_budget_next_request(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 10,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        summaries, errors, bodies, budget_sets = [], [], [], []
        config = central_config(fixture.base, 3)
        for _request in range(2):
            budgets: list = []
            try:
                opener_call(config, MODEL_BODY, budgets=budgets)
                errors.append(None)
            except urllib.error.HTTPError as exc:
                errors.append((exc.code, exc.read()))
            budget_sets.append(budgets)
            summaries.append(budget_summary(budgets))
        bodies = {digest(hit["body"]) for hit in fixture.hits}
        checks.equal("two_logical_requests_each_spend_four_hits",
                     len(fixture.hits), 8)
        checks.expect("both_requests_failed_identically",
                      all(error == (503, SCREENSHOT) for error in errors),
                      errors)
        checks.expect("both_payloads_logically_identical",
                      len(bodies) == 1, bodies)
        checks.expect("each_request_spent_its_own_three_attempts",
                      [summary["attempts"] for summary in summaries] == [3, 3],
                      summaries)
        checks.expect("second_request_allocated_a_fresh_budget_object",
                      summaries[0]["shared_objects"] == 1
                      and summaries[1]["shared_objects"] == 1
                      and budget_sets[0][0] is not budget_sets[1][0],
                      summaries)
        checks.expect("config_never_holds_a_mutable_budget",
                      not any("transient_retry_budget" in key for key in config),
                      list(config))
        return {"upstream_hits": len(fixture.hits),
                "request_body": payload_facts(fixture.hits[0]["body"]),
                "budgets": summaries}


def opener_remote_bridge(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        config = central_config(fixture.base, 3)
        config[REMOTE_BRIDGE_CONFIG_MARKER] = True
        budgets: list = []
        error = None
        try:
            opener_call(config, MODEL_BODY, budgets=budgets)
        except urllib.error.HTTPError as exc:
            error = (exc.code, exc.read())
        checks.equal("remote_bridge_owner_disabled_single_hit",
                     len(fixture.hits), 1)
        checks.equal("client_status", error[0] if error else None, 503)
        checks.equal("no_budget_allocated", budget_summary(budgets)["none_opens"], 1)
        return {"upstream_hits": len(fixture.hits)}


def opener_selected_gateway_reselect(checks: Checks) -> dict:
    harness = selected_harness(checks)
    try:
        harness.succeeding.script[:] = [(200, MESSAGE_STREAM, "text/event-stream")]
        budgets: list = []
        data = opener_call(harness.pcfg, MODEL_BODY, budgets=budgets)
        checks.equal("failing_gateway_hits", len(harness.failing.hits), 1)
        checks.equal("reselect_gateway_hits", len(harness.succeeding.hits), 1)
        checks.expect("client_stream_from_reselected_gateway",
                      data == MESSAGE_STREAM, payload_facts(data))
        bodies = {digest(hit["body"]) for hit in
                  harness.failing.hits + harness.succeeding.hits}
        checks.expect("same_request_body_on_both_gateways", len(bodies) == 1, bodies)
        auths = {hit["authorization"] for hit in
                 harness.failing.hits + harness.succeeding.hits}
        checks.expect("same_authorization_on_both_gateways", len(auths) == 1, auths)
        summary = budget_summary(budgets)
        checks.equal("reselect_spends_one_attempt", summary["attempts"], 1)
        checks.equal("reselect_keeps_one_budget_object",
                     summary["shared_objects"], 1)
        route = harness.route()
        checks.equal("overlay_route_moved_to_second_gateway",
                     route.get("endpoint"), harness.succeeding.base)
        return {"failing_gateway_hits": len(harness.failing.hits),
                "reselect_gateway_hits": len(harness.succeeding.hits),
                "budget": summary, "route_state": route.get("state")}
    finally:
        harness.failing.stop()
        harness.succeeding.stop()
        harness.close()


# --------------------------------------------------------------------------
# Native Responses passthrough
# --------------------------------------------------------------------------

def responses_forward(config: dict, body: dict, *,
                      remote_bridge: bool = False) -> tuple[Sink, tuple | None]:
    sink = Sink(remote_bridge=remote_bridge)
    try:
        ciel_runtime.provider_responses_passthrough().forward(
            sink, PROVIDER, config, body
        )
        return sink, None
    except urllib.error.HTTPError as exc:
        return sink, (exc.code, exc.read())


def responses_screenshot_to_success(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")],
                 (200, RESP_OK, "text/event-stream")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3), RESP_BODY)
        checks.expect("no_client_error", error is None, error and error[0])
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_stream_is_exactly_one_success",
                      sink.body() == RESP_OK, payload_facts(sink.body()))
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        assert_requests(checks, fixture.hits, "/v1/responses")
        return {"upstream_hits": len(fixture.hits),
                "request_body": payload_facts(fixture.hits[0]["body"]),
                "client_body": payload_facts(sink.body())}


def responses_screenshot_exhausted(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 8,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3), RESP_BODY)
        checks.expect("client_error_recorded", error is not None)
        checks.equal("client_status", error[0] if error else None, 503)
        checks.expect("client_error_body_is_original_screenshot",
                      error is not None and error[1] == SCREENSHOT,
                      payload_facts(error[1]) if error else None)
        checks.equal("no_partial_downstream_status", sink.status, None)
        checks.equal("no_partial_downstream_body", sink.body(), b"")
        checks.equal("upstream_hit_count", len(fixture.hits), 4)
        assert_requests(checks, fixture.hits, "/v1/responses")
        return {"upstream_hits": len(fixture.hits),
                "client_error": payload_facts(error[1])}


def responses_explicit_zero(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 0), RESP_BODY)
        checks.equal("upstream_hit_count", len(fixture.hits), 1)
        checks.equal("client_status", error[0] if error else None, 503)
        checks.equal("no_partial_downstream_status", sink.status, None)
        return {"upstream_hits": len(fixture.hits)}


def responses_http200_initial_error(checks: Checks) -> dict:
    with Fixture([(200, SCREENSHOT, "text/event-stream")],
                 (200, RESP_OK, "text/event-stream")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3), RESP_BODY)
        checks.expect("no_client_error", error is None, error and error[0])
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_stream_is_exactly_one_success",
                      sink.body() == RESP_OK, payload_facts(sink.body()))
        assert_requests(checks, fixture.hits, "/v1/responses")
        return {"upstream_hits": len(fixture.hits),
                "client_body": payload_facts(sink.body())}


def responses_output_then_error(checks: Checks) -> dict:
    stream = RESP_OUTPUT_THEN_ERROR
    with Fixture([(200, stream, "text/event-stream")],
                 (200, RESP_OK, "text/event-stream")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3), RESP_BODY)
        checks.expect("no_client_error", error is None, error and error[0])
        checks.equal("upstream_hit_count_no_replay_after_output",
                     len(fixture.hits), 1)
        checks.expect("client_receives_entire_original_stream_exactly",
                      sink.body() == stream, payload_facts(sink.body()))
        checks.equal("client_status", sink.status, 200)
        return {"upstream_hits": len(fixture.hits),
                "client_body": payload_facts(sink.body())}


def responses_malformed(checks: Checks) -> dict:
    with Fixture([(503, MALFORMED, "text/html")] * 4,
                 (503, MALFORMED, "text/html")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3), RESP_BODY)
        checks.equal("upstream_hit_count_no_verdict_no_retry",
                     len(fixture.hits), 1)
        checks.expect("client_body_is_original_malformed_error",
                      error is not None and error[1] == MALFORMED,
                      error and payload_facts(error[1]))
        checks.equal("no_partial_downstream_status", sink.status, None)
        return {"upstream_hits": len(fixture.hits)}


def responses_usage_limit(checks: Checks) -> dict:
    with Fixture([(503, QUOTA, "application/json")] * 4,
                 (503, QUOTA, "application/json")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3), RESP_BODY)
        checks.equal("upstream_hit_count_terminal_usage_limit_no_retry",
                     len(fixture.hits), 1)
        checks.expect("client_body_is_original_quota_error",
                      error is not None and error[1] == QUOTA,
                      error and payload_facts(error[1]))
        checks.equal("no_partial_downstream_status", sink.status, None)
        return {"upstream_hits": len(fixture.hits)}


def responses_tool_item_equality(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")],
                 (200, RESP_OK_TOOL, "text/event-stream")) as fixture:
        sink, error = responses_forward(central_config(fixture.base, 3),
                                        RESP_TOOL_BODY)
        checks.expect("no_client_error", error is None, error and error[0])
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        assert_requests(checks, fixture.hits, "/v1/responses")
        body = sink.body()
        checks.expect("tool_item_delivered_exactly_once",
                      count_occurrences(body, b"call_accept_1") == 1,
                      count_occurrences(body, b"call_accept_1"))
        sent = fixture.hits[0]["body"]
        checks.expect("tool_definition_sent_exactly_once",
                      count_occurrences(sent, b"exec_command") == 1,
                      count_occurrences(sent, b"exec_command"))
        checks.expect("client_output_has_no_duplicate_stream",
                      body.count(b"event: response.output_item.done") == 1,
                      body.count(b"event: response.output_item.done"))
        return {"upstream_hits": len(fixture.hits),
                "request_body": payload_facts(sent),
                "client_body": payload_facts(body)}


def responses_selected_gateway_reselect(checks: Checks) -> dict:
    harness = selected_harness(checks)
    try:
        harness.succeeding.script[:] = [(200, RESP_OK, "text/event-stream")]
        sink, error = responses_forward(harness.pcfg, RESP_BODY)
        checks.expect("no_client_error", error is None, error and error[0])
        checks.equal("failing_gateway_hits", len(harness.failing.hits), 1)
        checks.equal("reselect_gateway_hits", len(harness.succeeding.hits), 1)
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_stream_from_reselected_gateway",
                      sink.body() == RESP_OK, payload_facts(sink.body()))
        bodies = {digest(hit["body"]) for hit in
                  harness.failing.hits + harness.succeeding.hits}
        checks.expect("same_request_body_on_both_gateways", len(bodies) == 1, bodies)
        route = harness.route()
        checks.equal("overlay_route_moved_to_second_gateway",
                     route.get("endpoint"), harness.succeeding.base)
        return {"failing_gateway_hits": len(harness.failing.hits),
                "reselect_gateway_hits": len(harness.succeeding.hits),
                "route_state": route.get("state")}
    finally:
        harness.failing.stop()
        harness.succeeding.stop()
        harness.close()


def responses_remote_bridge(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        config = central_config(fixture.base, 3)
        config[REMOTE_BRIDGE_CONFIG_MARKER] = True
        sink, error = responses_forward(config, RESP_BODY, remote_bridge=True)
        checks.equal("remote_bridge_owner_disabled_single_hit",
                     len(fixture.hits), 1)
        checks.equal("client_status", error[0] if error else None, 503)
        checks.equal("no_partial_downstream_status", sink.status, None)
        return {"upstream_hits": len(fixture.hits)}


# --------------------------------------------------------------------------
# Chat Completions passthrough
# --------------------------------------------------------------------------

def chat_forward(config: dict, body: dict, *,
                 remote_bridge: bool = False) -> Sink:
    sink = Sink(remote_bridge=remote_bridge)
    ciel_runtime.provider_chat_passthrough().forward(sink, PROVIDER, config, body)
    return sink


def chat_screenshot_to_success(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")],
                 (200, CHAT_JSON, "application/json")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_BODY)
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_body_is_exactly_one_success",
                      sink.body() == CHAT_JSON, payload_facts(sink.body()))
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        assert_requests(checks, fixture.hits, "/v1/chat/completions")
        return {"upstream_hits": len(fixture.hits),
                "request_body": payload_facts(fixture.hits[0]["body"]),
                "client_body": payload_facts(sink.body())}


def chat_screenshot_exhausted(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 8,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_BODY)
        checks.equal("client_status", sink.status, 503)
        checks.expect("client_error_body_is_original_screenshot",
                      sink.body() == SCREENSHOT, payload_facts(sink.body()))
        checks.equal("upstream_hit_count", len(fixture.hits), 4)
        assert_requests(checks, fixture.hits, "/v1/chat/completions")
        return {"upstream_hits": len(fixture.hits),
                "client_error": payload_facts(sink.body())}


def chat_explicit_zero(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        sink = chat_forward(central_config(fixture.base, 0), CHAT_BODY)
        checks.equal("upstream_hit_count", len(fixture.hits), 1)
        checks.equal("client_status", sink.status, 503)
        return {"upstream_hits": len(fixture.hits)}


def chat_http200_initial_error(checks: Checks) -> dict:
    with Fixture([(200, SCREENSHOT, "text/event-stream")],
                 (200, CHAT_JSON, "application/json")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_BODY)
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_body_is_exactly_one_success",
                      sink.body() == CHAT_JSON, payload_facts(sink.body()))
        assert_requests(checks, fixture.hits, "/v1/chat/completions")
        return {"upstream_hits": len(fixture.hits),
                "client_body": payload_facts(sink.body())}


def chat_output_then_error(checks: Checks) -> dict:
    stream = CHAT_OUTPUT_THEN_ERROR
    with Fixture([(200, stream, "text/event-stream")],
                 (200, CHAT_JSON, "application/json")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_BODY)
        checks.equal("upstream_hit_count_no_replay_after_output",
                     len(fixture.hits), 1)
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_receives_entire_original_stream_exactly",
                      sink.body() == stream, payload_facts(sink.body()))
        return {"upstream_hits": len(fixture.hits),
                "client_body": payload_facts(sink.body())}


def chat_malformed(checks: Checks) -> dict:
    with Fixture([(503, MALFORMED, "text/html")] * 4,
                 (503, MALFORMED, "text/html")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_BODY)
        checks.equal("upstream_hit_count_no_verdict_no_retry",
                     len(fixture.hits), 1)
        checks.equal("client_status", sink.status, 503)
        checks.expect("client_body_is_original_malformed_error",
                      sink.body() == MALFORMED, payload_facts(sink.body()))
        return {"upstream_hits": len(fixture.hits)}


def chat_usage_limit(checks: Checks) -> dict:
    with Fixture([(503, QUOTA, "application/json")] * 4,
                 (503, QUOTA, "application/json")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_BODY)
        checks.equal("upstream_hit_count_terminal_usage_limit_no_retry",
                     len(fixture.hits), 1)
        checks.equal("client_status", sink.status, 503)
        checks.expect("client_body_is_original_quota_error",
                      sink.body() == QUOTA, payload_facts(sink.body()))
        return {"upstream_hits": len(fixture.hits)}


def chat_tool_equality(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")],
                 (200, CHAT_TOOL_JSON, "application/json")) as fixture:
        sink = chat_forward(central_config(fixture.base, 3), CHAT_TOOL_BODY)
        checks.equal("client_status", sink.status, 200)
        checks.equal("upstream_hit_count", len(fixture.hits), 2)
        assert_requests(checks, fixture.hits, "/v1/chat/completions")
        body = sink.body()
        checks.expect("tool_call_delivered_exactly_once",
                      count_occurrences(body, b"call_accept_1") == 1,
                      count_occurrences(body, b"call_accept_1"))
        sent = fixture.hits[0]["body"]
        checks.expect("tool_definition_sent_exactly_once",
                      count_occurrences(sent, b"exec_command") == 1,
                      count_occurrences(sent, b"exec_command"))
        checks.expect("client_output_is_exactly_one_success",
                      body == CHAT_TOOL_JSON, payload_facts(body))
        return {"upstream_hits": len(fixture.hits),
                "request_body": payload_facts(sent),
                "client_body": payload_facts(body)}


def chat_selected_gateway_reselect(checks: Checks) -> dict:
    harness = selected_harness(checks)
    try:
        harness.succeeding.script[:] = [(200, CHAT_JSON, "application/json")]
        sink = chat_forward(harness.pcfg, CHAT_BODY)
        checks.equal("failing_gateway_hits", len(harness.failing.hits), 1)
        checks.equal("reselect_gateway_hits", len(harness.succeeding.hits), 1)
        checks.equal("client_status", sink.status, 200)
        checks.expect("client_body_from_reselected_gateway",
                      sink.body() == CHAT_JSON, payload_facts(sink.body()))
        bodies = {digest(hit["body"]) for hit in
                  harness.failing.hits + harness.succeeding.hits}
        checks.expect("same_request_body_on_both_gateways", len(bodies) == 1, bodies)
        route = harness.route()
        checks.equal("overlay_route_moved_to_second_gateway",
                     route.get("endpoint"), harness.succeeding.base)
        return {"failing_gateway_hits": len(harness.failing.hits),
                "reselect_gateway_hits": len(harness.succeeding.hits),
                "route_state": route.get("state")}
    finally:
        harness.failing.stop()
        harness.succeeding.stop()
        harness.close()


def chat_remote_bridge(checks: Checks) -> dict:
    with Fixture([(503, SCREENSHOT, "text/event-stream")] * 4,
                 (503, SCREENSHOT, "text/event-stream")) as fixture:
        config = central_config(fixture.base, 3)
        config[REMOTE_BRIDGE_CONFIG_MARKER] = True
        sink = chat_forward(config, CHAT_BODY, remote_bridge=True)
        checks.equal("remote_bridge_owner_disabled_single_hit",
                     len(fixture.hits), 1)
        checks.equal("client_status", sink.status, 503)
        return {"upstream_hits": len(fixture.hits)}


CASES = (
    ("opener.screenshot_sse_then_success", opener_screenshot_to_success),
    ("opener.screenshot_exhausted_configured_3", opener_screenshot_exhausted),
    ("opener.default_unset_is_three", opener_default_limit),
    ("opener.explicit_zero", opener_explicit_zero),
    ("opener.http200_initial_error_sse", opener_http200_initial_error),
    ("opener.output_then_error_no_retry", opener_output_then_error),
    ("opener.malformed_no_retry", opener_malformed),
    ("opener.usage_limit_no_retry", opener_usage_limit),
    ("opener.next_request_fresh_budget", opener_fresh_budget_next_request),
    ("opener.remote_bridge_one_hit", opener_remote_bridge),
    ("opener.selected_gateway_reselect", opener_selected_gateway_reselect),
    ("responses.screenshot_sse_then_success", responses_screenshot_to_success),
    ("responses.screenshot_exhausted_configured_3", responses_screenshot_exhausted),
    ("responses.explicit_zero", responses_explicit_zero),
    ("responses.http200_initial_error_sse", responses_http200_initial_error),
    ("responses.output_then_error_no_retry", responses_output_then_error),
    ("responses.malformed_no_retry", responses_malformed),
    ("responses.usage_limit_no_retry", responses_usage_limit),
    ("responses.tool_item_equality", responses_tool_item_equality),
    ("responses.remote_bridge_one_hit", responses_remote_bridge),
    ("responses.selected_gateway_reselect", responses_selected_gateway_reselect),
    ("chat.screenshot_sse_then_success", chat_screenshot_to_success),
    ("chat.screenshot_exhausted_configured_3", chat_screenshot_exhausted),
    ("chat.explicit_zero", chat_explicit_zero),
    ("chat.http200_initial_error_sse", chat_http200_initial_error),
    ("chat.output_then_error_no_retry", chat_output_then_error),
    ("chat.malformed_no_retry", chat_malformed),
    ("chat.usage_limit_no_retry", chat_usage_limit),
    ("chat.tool_equality", chat_tool_equality),
    ("chat.remote_bridge_one_hit", chat_remote_bridge),
    ("chat.selected_gateway_reselect", chat_selected_gateway_reselect),
)

SOURCES = (
    "ciel_runtime.py",
    "ciel_runtime_support/transient_retry.py",
    "ciel_runtime_support/upstream_retry.py",
    "ciel_runtime_support/upstream_retry_context.py",
    "ciel_runtime_support/upstream_error_policy.py",
    "ciel_runtime_support/provider_responses_passthrough.py",
    "ciel_runtime_support/openai_chat_passthrough.py",
    "ciel_runtime_support/cielairouter_gateway_overlay.py",
    "ciel_runtime_support/cielairouter_gateways.py",
    "ciel_runtime_support/cielairouter_gateway_transport.py",
    "ciel_runtime_support/provider_network.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    results = []
    for name, run in CASES:
        results.append(case(name, run))
    payload = {
        "label": args.label,
        "generated_at": iso(time.time()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "isolation": ISOLATION,
        "sources": {
            path: digest((REPO / path).read_bytes()) for path in SOURCES
        },
        "production_wiring": {
            "claude_messages_opener":
                "ciel_runtime.open_provider_request_with_key_retry via upstream_retry_context()",
            "responses": "ciel_runtime.provider_responses_passthrough()",
            "chat": "ciel_runtime.provider_chat_passthrough()",
            "gateway": "CielAiRouterGatewayService + CielAiRouterGatewayOverlay "
                       "+ provider_network.provider_urlopen + reselect_cielairouter_gateway",
        },
        "not_covered": [
            "router_http request dispatch wrapping (owned by dispatch-scope)",
            "live runtime, real provider and remote gateway hosts",
        ],
        "cases": results,
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "all_passed": all(item["passed"] for item in results),
    }
    text = json.dumps(payload, indent=2, default=str)
    print(text)
    out = HERE / ("protocol-acceptance" + ("-" + args.label if args.label else "") + ".json")
    out.write_text(text + "\n", encoding="utf-8")
    print(f"\nPASS {payload['passed']}/{len(results)}  FAIL {payload['failed']}"
          f"  evidence={out}")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
