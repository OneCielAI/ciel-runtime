"""Real HTTP empty gateway rejection recovery without provider traffic."""
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ciel_runtime_support import transient_retry as retry


class EmptyGatewayTests(unittest.TestCase):
    def test_real_empty_502_then_success(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                hits.append(1)
                body = b'' if len(hits) == 1 else b'ok'
                self.send_response(502 if len(hits) == 1 else 200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            logs = []
            url = f'http://127.0.0.1:{server.server_port}/test'
            response = retry.open_with_transient_retry(
                lambda: urllib.request.urlopen(url, timeout=2), provider='cielairouter',
                config={'gateway_retries': 3}, log=lambda *args: logs.append(args), sleep=lambda _: None)
            with response:
                self.assertEqual(b'ok', response.read())
            self.assertEqual(2, len(hits))
            self.assertTrue(any('status=502' in line for _, line in logs))
            print('real_empty_gateway HTTP502 bytes=0 -> HTTP200 hits=2 retry=1')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_empty_terminal_statuses_and_sse_are_not_retried(self):
        import io
        import urllib.error
        for status in (400, 401, 403, 429, 500):
            budget = retry.TransientRetryBudget(3, 60)
            error = urllib.error.HTTPError('http://fixture', status, 'error', {}, io.BytesIO())
            self.assertIsNone(budget.next_wait(error, b''))
        error = urllib.error.HTTPError('http://fixture', 503, 'SSE', {}, None)
        error._ciel_sse_error = True
        self.assertIsNone(retry.TransientRetryBudget(3, 60).next_wait(error, b''))
