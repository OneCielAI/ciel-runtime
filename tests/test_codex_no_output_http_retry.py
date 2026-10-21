"""Only explicit pre-response no-output HTTP failures authorize bounded replay."""
import http.client
import io
import json
import unittest
from dataclasses import replace
import urllib.error
import urllib.request
from unittest import mock

from test_codex_backend_item_repair import (
    FakeHandler, FakeResponse, MISSING_ITEM_404, adapter_for,
)
from ciel_runtime_support.codex_router import read_codex_response_preamble
from ciel_runtime_support.upstream_stream_io import UpstreamClientDisconnected


class NoOutputHttpRetryTests(unittest.TestCase):
    def payload(self, *, sse=False, extra=None):
        value = {"type": "error", "error": {"type": "server_error", "message": (
            "codex/gpt-6.1-sol-medium: provider — upstream reported a failed response "
            "without usable output (HTTP 502)")}}
        value.update(extra or {})
        raw = json.dumps(value).encode()
        return b"event: error\ndata: " + raw + b"\n\n" if sse else raw

    def failure(self, status=502, payload=None):
        return urllib.error.HTTPError("http://upstream/responses", status, "Bad Gateway", {},
                                      io.BytesIO(self.payload() if payload is None else payload))

    def test_no_output_failures_retry_before_any_response_bytes(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                handler = FakeHandler()

                def success(*_args, **_kwargs):
                    self.assertIsNone(handler.status)
                    self.assertEqual(b"", handler.wfile.written)
                    return FakeResponse(b"ok")

                calls = [self.failure(status, self.payload(sse=True)), success]

                def upstream(*args, **kwargs):
                    result = calls.pop(0)
                    if isinstance(result, Exception):
                        raise result
                    return result(*args, **kwargs)

                adapter_for(upstream, [], transport_retries=1).forward_json(
                    handler, "codex", {}, {"model": "gpt", "input": []})
                self.assertEqual([], calls)
                self.assertEqual(b"ok", handler.wfile.written)

    def test_repeated_no_output_failure_stops_at_transport_limit(self):
        payload = self.payload()
        upstream = mock.Mock(side_effect=[self.failure(payload=payload), self.failure(payload=payload)])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            adapter_for(upstream, [], transport_retries=1).forward_json(
                FakeHandler(), "codex", {}, {"model": "gpt", "input": []})
        self.assertEqual(2, upstream.call_count)
        self.assertEqual(payload, caught.exception.read())

    def test_ambiguous_statuses_and_output_bearing_errors_do_not_retry(self):
        for status, payload in [
            (400, self.payload()), (401, self.payload()), (413, self.payload()),
            (502, b'{"error":{"type":"server_error","message":"gateway failed"}}'),
            (502, self.payload(extra={"output": [{"type": "function_call"}]})),
            (502, b'event: response.output_text.delta\ndata: {"delta":"partial"}\n\n' + self.payload(sse=True)),
        ]:
            with self.subTest(status=status, payload=payload):
                upstream = mock.Mock(side_effect=self.failure(status, payload))
                adapter = adapter_for(upstream, [], transport_retries=2)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    adapter._open_with_transport_retry(urllib.request.Request("http://upstream"),
                                                       "codex", {}, "gpt", "responses")
                self.assertEqual(1, upstream.call_count)
                self.assertEqual(payload, caught.exception.read())

    def test_json_verdict_retries_and_zero_limit_preserves_original_body(self):
        upstream = mock.Mock(side_effect=[self.failure(), FakeResponse()])
        adapter_for(upstream, [], transport_retries=1).forward_json(
            FakeHandler(), "codex", {}, {"model": "gpt", "input": []})
        self.assertEqual(2, upstream.call_count)
        payload = self.payload(sse=True)
        upstream = mock.Mock(side_effect=self.failure(payload=payload))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            adapter_for(upstream, [], transport_retries=0).forward_json(
                FakeHandler(), "codex", {}, {"model": "gpt", "input": []})
        self.assertEqual(1, upstream.call_count)
        self.assertEqual(payload, caught.exception.read())

    def test_completion_checks_never_retry_the_no_output_verdict(self):
        payload = self.payload()
        upstream = mock.Mock(side_effect=self.failure(payload=payload))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            adapter_for(upstream, [], transport_retries=2)._open_with_transport_retry(
                urllib.request.Request("http://upstream/response", method="GET"),
                "codex", {}, "gpt", "completion-check")
        self.assertEqual(1, upstream.call_count)
        self.assertEqual(payload, caught.exception.read())

    def test_disconnect_prevents_retry(self):
        upstream = mock.Mock(side_effect=self.failure())
        adapter = adapter_for(upstream, [], transport_retries=2)
        with mock.patch("ciel_runtime_support.router_http.client_connection_closed", side_effect=[False, True]):
            with self.assertRaises(UpstreamClientDisconnected):
                adapter._open_with_transport_retry(urllib.request.Request("http://upstream"),
                                                   "codex", {}, "gpt", "responses", handler=FakeHandler())
        self.assertEqual(1, upstream.call_count)

    def test_read_failure_after_response_open_does_not_replay(self):
        response = FakeResponse()
        response.read = mock.Mock(side_effect=OSError("stream broke after output"))
        upstream = mock.Mock(return_value=response)
        handler = FakeHandler()
        with self.assertRaises(OSError):
            adapter_for(upstream, [], transport_retries=2).forward_json(
                handler, "codex", {}, {"model": "gpt", "input": []})
        self.assertEqual(1, upstream.call_count)
        self.assertEqual(200, handler.status)

    def test_failed_http_body_read_is_non_retryable_and_preserves_error(self):
        for status in (400, 401, 404, 429, 502, 503, 504):
            for error_type in (OSError, ValueError):
                with self.subTest(status=status, error_type=error_type):
                    error = self.failure(status=status)
                    read_error = error_type("body unavailable")
                    error.read = mock.Mock(side_effect=read_error)
                    upstream = mock.Mock(side_effect=error)
                    adapter = adapter_for(upstream, [], transport_retries=2,
                                          classify_transport=lambda _error: True)
                    oauth = mock.Mock(lease=object())
                    oauth.headers.return_value = {}
                    handler = FakeHandler()
                    with mock.patch("ciel_runtime_support.router_http.codex_request_run", return_value=oauth):
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            adapter.forward_json(handler, "codex", {}, {"model": "gpt", "input": []})
                    self.assertIs(caught.exception, error)
                    self.assertIs(error.__cause__, read_error)
                    self.assertEqual(1, error.read.call_count)
                    self.assertEqual(1, upstream.call_count)
                    oauth.retry_headers.assert_not_called()
                    self.assertIsNone(handler.status)
                    self.assertEqual(b"", handler.wfile.written)

    def test_incomplete_http_error_body_cannot_authorize_replay(self):
        error = self.failure(status=503)
        error.read = mock.Mock(side_effect=http.client.IncompleteRead(self.payload(), 8))
        upstream = mock.Mock(side_effect=error)
        adapter = adapter_for(upstream, [], transport_retries=2, classify_transport=lambda _error: True)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            adapter.forward_json(FakeHandler(), "codex", {}, {"model": "gpt", "input": []})
        self.assertIs(caught.exception, error)
        self.assertIsInstance(error.__cause__, http.client.IncompleteRead)
        self.assertEqual(1, upstream.call_count)
        self.assertEqual(1, error.read.call_count)

    def test_malformed_no_output_verdict_fields_are_rejected(self):
        phrase = "upstream reported a failed response without usable output"
        fields = [
            {"type": value, "message": phrase}
            for value in ([], {}, None, 1, True)
        ] + [
            {"type": "server_error", "message": value}
            for value in ([phrase], {"message": phrase}, None, 1, True)
        ]
        for field in fields:
            for sse in (False, True):
                with self.subTest(field=field, sse=sse):
                    payload = json.dumps({"type": "error", "error": field}).encode()
                    if sse:
                        payload = b"event: error\ndata: " + payload + b"\n\n"
                    upstream = mock.Mock(side_effect=self.failure(payload=payload))
                    adapter = adapter_for(upstream, [], transport_retries=2)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        adapter._open_with_transport_retry(
                            urllib.request.Request("http://upstream"), "codex", {}, "gpt", "responses")
                    self.assertEqual(1, upstream.call_count)
                    self.assertEqual(payload, caught.exception.read())

    def test_no_output_allowance_is_shared_across_capacity_loop(self):
        capacity = json.dumps({"type": "response.failed", "response": {"error": {"code": "server_is_overloaded"}}}).encode()
        first = self.failure(payload=self.payload())
        second = FakeResponse(b"event: response.failed\ndata: " + capacity + b"\n\n")
        third = self.failure(status=503, payload=self.payload())
        upstream = mock.Mock(side_effect=[first, second, third])
        adapter = adapter_for(upstream, [], transport_retries=1)
        adapter._retry = replace(adapter._retry, retry_limit=lambda: 1, read_preamble=read_codex_response_preamble)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            adapter.forward_json(FakeHandler(), "codex", {}, {"model": "gpt", "input": []}, mutate_responses=True)
        self.assertEqual(3, upstream.call_count)
        self.assertEqual(self.payload(), caught.exception.read())

    def test_retry_allowance_survives_outer_overload_item_repair_and_oauth_loops(self):
        for loop in ("overload", "item-repair", "oauth"):
            with self.subTest(loop=loop):
                body = {"model": "gpt", "input": []}
                if loop == "overload":
                    middle = self.failure(status=500, payload=b'{"error":{"message":"high demand"}}')
                elif loop == "item-repair":
                    body["input"] = [{"type": "message", "id": "msg_0000", "role": "assistant"}]
                    middle = self.failure(status=404, payload=MISSING_ITEM_404.replace(b"rs_e50d87c9_0", b"msg_0000"))
                else:
                    middle = self.failure(status=401, payload=b'{"error":{"message":"expired token"}}')
                upstream = mock.Mock(side_effect=[self.failure(), middle, self.failure(), FakeResponse()])
                logs = []
                adapter = adapter_for(upstream, logs, transport_retries=1)
                oauth = mock.Mock(lease=object()) if loop == "oauth" else None
                if oauth is not None:
                    oauth.headers.return_value = {"authorization": "Bearer first"}
                    oauth.retry_headers.return_value = ({"authorization": "Bearer second"}, None)
                with mock.patch("ciel_runtime_support.router_http.codex_request_run", return_value=oauth):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        adapter.forward_json(FakeHandler(), "codex", {}, body)
                self.assertEqual(3, upstream.call_count)
                self.assertEqual(self.payload(), caught.exception.read())
                self.assertEqual(1, sum("codex_no_output_http_retry " in message for _, message in logs))
                if oauth is not None:
                    oauth.retry_headers.assert_called_once()
                elif loop == "item-repair":
                    repaired = json.loads(upstream.call_args.args[0].data)
                    self.assertNotIn("id", repaired["input"][0])

    def test_transport_and_no_output_failures_share_one_allowance(self):
        for transport_first in (False, True):
            with self.subTest(transport_first=transport_first):
                transport = urllib.error.URLError("connection reset")
                http_error = self.failure()
                failures = [transport, http_error] if transport_first else [http_error, transport]
                upstream = mock.Mock(side_effect=[*failures, FakeResponse()])
                adapter = adapter_for(upstream, [], transport_retries=1,
                                      classify_transport=lambda _error: True)
                with self.assertRaises(type(failures[-1])) as caught:
                    adapter.forward_json(FakeHandler(), "codex", {}, {"model": "gpt", "input": []})
                self.assertEqual(2, upstream.call_count)
                if transport_first:
                    self.assertEqual(self.payload(), caught.exception.read())
                else:
                    self.assertIs(transport, caught.exception)

    def test_separate_forward_json_calls_get_independent_allowances(self):
        upstream = mock.Mock(side_effect=[self.failure(), FakeResponse(), self.failure(), FakeResponse()])
        adapter = adapter_for(upstream, [], transport_retries=1)
        for _ in range(2):
            handler = FakeHandler()
            adapter.forward_json(handler, "codex", {}, {"model": "gpt", "input": []})
            self.assertEqual(b"ok", handler.wfile.written)
        self.assertEqual(4, upstream.call_count)


if __name__ == "__main__":
    unittest.main()
