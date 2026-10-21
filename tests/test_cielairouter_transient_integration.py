import dataclasses
import io
import os
import unittest
import urllib.error
from unittest import mock

for key in tuple(os.environ):
    if key.startswith(('CIEL_RUNTIME_', 'CLAUDE', 'OPENAI', 'ANTHROPIC', 'CODEX')) or any(w in key.upper() for w in ('API_KEY', 'API_TOKEN', 'CREDENTIAL', 'PROVIDER')):
        os.environ.pop(key)

from ciel_runtime_support import transient_retry  # noqa: E402 - isolate before runtime imports
from test_upstream_retry import ENTRYPOINTS, services, invoke, Response  # noqa: E402

ERROR = (bytes([101,118,101,110,116,58,32,101,114,114,111,114,10])
         + b'data: {"type":"error","error":{"type":"overloaded_error","message":"service_unavailable: Service temporarily unavailable: all targets were skipped by pre-dispatch filters"}}'
         + bytes([10,10]))


def failure():
    return urllib.error.HTTPError('http://a/v1/chat/completions', 503, 'rejected', {}, io.BytesIO(ERROR))


class TransientIntegrationTests(unittest.TestCase):
    def test_native_real_loopback_503_then_one_client_success(self):
        import http.server
        import threading
        import urllib.request
        from types import SimpleNamespace
        from test_provider_responses_gateway_replay_guard import passthrough, COMPLETE, body
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('content-length') or 0))
                self.server.hits += 1
                status, payload = (503, ERROR) if self.server.hits == 1 else (200, COMPLETE)
                self.send_response(status)
                self.send_header('content-type', 'text/event-stream')
                self.send_header('content-length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        server.hits = 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def dispatch(req, *, timeout, **kwargs):
            return opener.open(req, timeout=timeout)
        downstream = SimpleNamespace(headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), end_headers=mock.Mock())
        with mock.patch.object(transient_retry.time, 'sleep'):
            passthrough(dispatch, []).forward(downstream, 'cielairouter',
                {'base_url': 'http://127.0.0.1:' + str(server.server_address[1])}, body())
        self.assertEqual(2, server.hits)
        self.assertEqual(COMPLETE, downstream.wfile.getvalue())
        downstream.send_response.assert_called_once_with(200)

    def test_native_chat_rebuilds_url_and_headers_after_reselection(self):
        from types import SimpleNamespace
        from ciel_runtime_support.openai_chat_passthrough import OpenAIChatPassthrough, OpenAIChatPassthroughPorts
        calls = []
        def dispatch(req, **kwargs):
            calls.append((req.full_url, req.get_header('Authorization'), req.data))
            if len(calls) == 1:
                raise failure()
            return Response()
        route = OpenAIChatPassthrough(OpenAIChatPassthroughPorts(
            normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
            upstream_base=lambda p, c: c['base_url'], join_url=lambda b, p: b + p,
            headers=lambda p, c, h: {'authorization': c['api_key']}, urlopen=dispatch,
            timeout_seconds=lambda c: 1, copy_response_headers=lambda *a: None,
            reselect=lambda c: {**c, 'base_url': 'http://b', 'api_key': 'fresh'}))
        downstream = SimpleNamespace(headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), end_headers=mock.Mock())
        with mock.patch.object(transient_retry.time, 'sleep'):
            route.forward(downstream, 'cielairouter', {'base_url': 'http://a', 'api_key': 'old'},
                {'model': 'm', 'messages': []})
        self.assertEqual(['http://a/v1/chat/completions', 'http://b/v1/chat/completions'], [c[0] for c in calls])
        self.assertEqual('fresh', calls[1][1])
        self.assertEqual(calls[0][2], calls[1][2])
        downstream.send_response.assert_called_once_with(200)

    def test_all_openers_have_one_capped_structured_budget(self):
        for fn in ENTRYPOINTS:
            for count, expected in ((None, 4), (0, 1), (2, 3), (99, 11)):
                with self.subTest(fn=fn.__name__, count=count):
                    config = {'base_url': 'http://a'}
                    if count is not None:
                        config['gateway_retries'] = count
                    dispatch = mock.Mock(side_effect=lambda *a, **kw: (_ for _ in ()).throw(failure()))
                    with mock.patch.object(transient_retry.time, 'sleep'), self.assertRaises(Exception):
                        invoke(fn, services(dispatch), config)
                    self.assertEqual(expected, dispatch.call_count)

    def test_http_then_initial_sse_rejection_spends_same_budget(self):
        for fn in ENTRYPOINTS:
            with self.subTest(fn=fn.__name__):
                first = failure()
                second = Response()
                second.headers = {'content-type': 'text/event-stream'}
                second.seek(0)
                second.truncate()
                second.write(ERROR)
                second.seek(0)
                dispatch = mock.Mock(side_effect=[first, second, Response()])
                with mock.patch.object(transient_retry.time, 'sleep'):
                    invoke(fn, services(dispatch), {'base_url': 'http://a', 'gateway_retries': 2})
                self.assertEqual(3, dispatch.call_count)

    def test_selected_gateway_reselects_without_resetting_budget(self):
        for fn in ENTRYPOINTS:
            with self.subTest(fn=fn.__name__):
                calls, budgets = [], []
                def dispatch(req, *, pcfg, **kwargs):
                    calls.append(req.full_url)
                    budgets.append(transient_retry.budget_for(pcfg, 'cielairouter'))
                    if len(calls) == 1:
                        raise failure()
                    return Response()
                svc = services(dispatch)
                svc = dataclasses.replace(svc, http=dataclasses.replace(svc.http,
                    reselect=lambda c: {**c, 'base_url': 'http://b', 'ciel_gateway_route': 'b'}))
                with mock.patch.object(transient_retry.time, 'sleep'):
                    invoke(fn, svc, {'base_url': 'http://a', 'ciel_gateway_route': 'a'})
                self.assertEqual(['http://a/v1/chat/completions', 'http://b/v1/chat/completions'], calls)
                self.assertIs(budgets[0], budgets[1])


if __name__ == '__main__':
    unittest.main()
