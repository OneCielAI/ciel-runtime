"""Native Messages context recovery with the real shared HTTP opener."""
import copy
import http.server
import io
import json
import threading
import unittest
import urllib.request
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import claude_router as claude, transient_retry
from ciel_runtime_support.native_context_recovery import recover_output_budget
from ciel_runtime_support.upstream_retry import open_provider_request_with_key_retry
import test_router_raw_context_observers as observers
from test_upstream_retry import services as retry_services

OVERFLOW = json.dumps({"type": "error", "error": {
    "type": "invalid_request_error",
    "message": "maximum context length is 1100 tokens; requested 1200 tokens "
               "(800 in the messages, 400 in the completion)"}}).encode()
UNAVAILABLE = b'{"type":"error","error":{"type":"overloaded_error","message":"service unavailable"}}'
SUCCESS = {"id": "single-answer", "type": "message", "role": "assistant",
           "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}


def route_services(url, open_request):
    svc = observers.RawContextObserverTests().claude_services(None, [])
    def identity(_provider, _config, body):
        return body
    return replace(svc,
        pipeline=replace(svc.pipeline, inject_channel_context=lambda body: body),
        routing=replace(svc.routing, select_protocol=lambda *_a: "anthropic_messages",
            request_policy=lambda *_a: SimpleNamespace(model_alias_strategy="")),
        normalization=observers.ports(claude.ClaudeRouterNativeNormalization,
            normalize_provider_wire=identity, normalize_thinking=identity,
            normalize_system_roles=identity, cap_body=identity,
            apply_request_options=identity, rehydrate_thinking=identity,
            resolve_tool_models=identity,
            normalize_model_options=lambda _p, _c, body, _m: body,
            strip_internal_metadata=lambda body, **_kw: body),
        transport=observers.ports(claude.ClaudeRouterTransport,
            provider_endpoint=lambda *_a: url, upstream_query=lambda *_a: "",
            provider_headers=lambda *_a: {}, apply_rate_limit=lambda *_a: (0, 0, 0),
            open_request=open_request, request_timeout=lambda _c: 2),
        response=replace(svc.response,
            normalize_thinking=lambda _p, _c, payload, _m: payload,
            append_tasklist=lambda payload, *_a, **_kw: payload,
            rate_limit_notice=lambda *_a: ""),
        context_recovery=claude.ClaudeRouterContextRecovery(
            lambda body, raw, _c: recover_output_budget(body, raw)))


class ClaudeTransientContextRecoveryTests(unittest.TestCase):
    def exercise(self, second_status, second_body):
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                self.server.bodies.append(json.loads(raw))
                status, raw = ((400, OVERFLOW) if len(self.server.bodies) == 1
                               else (second_status, second_body))
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        server.bodies = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        observed = []
        def network(request, *, timeout, provider, pcfg):
            observed.append((dict(pcfg), transient_retry.budget_for(pcfg, provider)))
            return opener.open(request, timeout=timeout)
        transport = retry_services(network)
        def open_request(*a, **kw):
            return open_provider_request_with_key_retry(*a, **kw, services=transport)
        svc = route_services(f"http://127.0.0.1:{server.server_address[1]}/v1/messages", open_request)
        handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
        config = {"gateway_retries": 3, "stream_enabled": False}
        request = {"model": "model", "messages": [
            {"role": "user", "content": "keep this once"}], "max_tokens": 400, "stream": False}
        original = copy.deepcopy(request)
        with transient_retry.request_scope(config, "cielairouter") as budget:
            with mock.patch.object(transient_retry.time, "sleep") as sleep:
                claude.handle_claude_messages_post(svc, handler, {}, "cielairouter",
                                                  config, "/v1/messages", request)
            self.assertIs(budget, transient_retry.budget_for(config, "cielairouter"))
            self.assertTrue(budget.stopped)
            self.assertEqual(1, budget.opens)
            self.assertTrue(all(call.args[0] <= 0.005 for call in sleep.call_args_list))
        svc.core.try_write_json.assert_not_called()
        self.assertEqual(2, len(server.bodies))
        self.assertEqual(original, server.bodies[0])
        self.assertEqual({**original, "max_tokens": 300}, server.bodies[1])
        self.assertEqual(3, config["gateway_retries"])
        self.assertIs(budget, observed[0][1])
        self.assertEqual(0, observed[1][0]["gateway_retries"])
        self.assertIsNone(observed[1][1])
        handler.send_response.assert_called_once_with(second_status)
        print(f"native Messages 400 -> {second_status}: upstream_hits=2 "
              "max_tokens=400->300 recovery_retries=0 client_responses=1")
        return handler.wfile.getvalue()

    def test_output_budget_recovery_succeeds_once_after_terminal_400(self):
        raw = self.exercise(200, json.dumps(SUCCESS).encode())
        self.assertEqual(SUCCESS, json.loads(raw))
        self.assertEqual(1, raw.count(b"single-answer"))

    def test_recovery_503_is_original_error_without_third_upstream_hit(self):
        self.assertEqual(UNAVAILABLE, self.exercise(503, UNAVAILABLE))

    def test_recovery_second_400_does_not_recover_again(self):
        self.assertEqual(OVERFLOW, self.exercise(400, OVERFLOW))

    def test_other_provider_preserves_config_and_enclosing_scope(self):
        import urllib.error
        config = {"gateway_retries": 3, "stream_enabled": False}
        attempts = []
        response = io.BytesIO(json.dumps(SUCCESS).encode())
        response.status = 200
        response.headers = {"content-type": "application/json"}
        with transient_retry.request_scope(config, "cielairouter") as budget:
            def open_request(_url, body, _headers, _timeout, provider, pcfg, _model, **_kw):
                self.assertEqual("other", provider)
                self.assertIs(config, pcfg)
                self.assertIs(budget, transient_retry.budget_for(config, "cielairouter"))
                attempts.append(copy.deepcopy(body))
                if len(attempts) == 1:
                    raise urllib.error.HTTPError("http://unused", 400, "bad request", {},
                                                 io.BytesIO(OVERFLOW))
                return response
            svc = route_services("http://unused", open_request)
            handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
                send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
            claude.handle_claude_messages_post(svc, handler, {}, "other", config, "/v1/messages",
                {"model": "model", "messages": [], "max_tokens": 400, "stream": False})
        svc.core.try_write_json.assert_not_called()
        self.assertEqual([400, 300], [body["max_tokens"] for body in attempts])
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(SUCCESS, json.loads(handler.wfile.getvalue()))


if __name__ == "__main__":
    unittest.main()


COMBO_OVERFLOW = json.dumps({"type": "error", "error": {
    "type": "invalid_request_error",
    "message": "Conversation exceeds the known context limits of all eligible "
               "combo targets. Compact the conversation before retrying."}}).encode()


class CielAiRouterContextWindowRecoveryTests(unittest.TestCase):
    """The combo gateway states the overflow in prose; it still authorizes one retry."""

    def test_prose_context_rejection_compacts_and_retries_once(self):
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                self.server.bodies.append(json.loads(raw))
                status, payload = ((400, COMBO_OVERFLOW) if len(self.server.bodies) == 1
                                   else (200, json.dumps(SUCCESS).encode()))
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        server.bodies = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        transport = retry_services(
            lambda request, *, timeout, provider, pcfg: opener.open(request, timeout=timeout))

        def open_request(*a, **kw):
            return open_provider_request_with_key_retry(*a, **kw, services=transport)

        svc = route_services(f"http://127.0.0.1:{server.server_address[1]}/v1/messages", open_request)
        compacted_for = []

        def cap(provider, pcfg, body):
            if not pcfg.get("_context_compact_context_rejected"):
                return body
            compacted_for.append(pcfg.get("_context_compact_native_over_window"))
            return {**body, "messages": body["messages"][-1:], "tools": []}

        svc = replace(svc, normalization=replace(svc.normalization, cap_body=cap))
        handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
        config = {"gateway_retries": 3, "stream_enabled": False}
        request = {"model": "model", "messages": [
            {"role": "user", "content": "history one"},
            {"role": "assistant", "content": "history two"},
            {"role": "user", "content": "compact now"}], "max_tokens": 400, "stream": False}

        claude.handle_claude_messages_post(svc, handler, {}, "cielairouter",
                                          config, "/v1/messages", request)

        self.assertEqual(2, len(server.bodies))
        self.assertEqual(["history one", "history two", "compact now"],
                         [m["content"] for m in server.bodies[0]["messages"]])
        self.assertEqual(["compact now"], [m["content"] for m in server.bodies[1]["messages"]])
        self.assertEqual([True], compacted_for)
        self.assertEqual([], server.bodies[1]["tools"])
        self.assertNotIn("_iel_context_window_recovered", server.bodies[1])
        self.assertEqual(200, handler.send_response.call_args.args[0])
        self.assertIn(b"single-answer", handler.wfile.getvalue())

    def test_unrelated_400_is_returned_without_compaction(self):
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.server.bodies.append(1)
                payload = b'{"type":"error","error":{"type":"invalid_request_error","message":"bad tools"}}'
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        server.bodies = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        transport = retry_services(
            lambda request, *, timeout, provider, pcfg: opener.open(request, timeout=timeout))

        def open_request(*a, **kw):
            return open_provider_request_with_key_retry(*a, **kw, services=transport)

        svc = route_services(f"http://127.0.0.1:{server.server_address[1]}/v1/messages", open_request)
        cap = mock.Mock(side_effect=lambda _p, _c, body: body)
        svc = replace(svc, normalization=replace(svc.normalization, cap_body=cap))
        handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
        claude.handle_claude_messages_post(
            svc, handler, {}, "cielairouter",
            {"gateway_retries": 0, "stream_enabled": False}, "/v1/messages",
            {"model": "model", "messages": [{"role": "user", "content": "x"}],
             "max_tokens": 10, "stream": False})
        handler.send_response.assert_called_once_with(400)
        self.assertEqual(1, len(server.bodies))
        self.assertTrue(all(not call.args[1].get("_context_compact_context_rejected")
                            for call in cap.call_args_list))
