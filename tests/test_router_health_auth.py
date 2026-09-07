import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import ciel_runtime
from ciel_runtime_support.router_access import RouterAccessPolicy


class RouterHealthAuthTests(unittest.TestCase):
    def test_lan_health_authenticates_and_loopback_does_not_read_token(self):
        config = {'web_backend': {'enabled': True, 'host': '10.0.0.238'}}
        for base, expected in (
            ('http://10.0.0.238:9683', {'Authorization': 'Bearer test-secret'}),
            ('http://127.0.0.1:9683', {}),
        ):
            with (
                patch.object(ciel_runtime, 'ROUTER_BASE', base),
                patch.object(ciel_runtime, 'load_config', return_value=config),
                patch.object(ciel_runtime, 'router_external_access_token', return_value='test-secret') as token,
                patch.object(ciel_runtime, 'http_json', return_value={'version': 'test'}) as request,
            ):
                self.assertEqual({'version': 'test'}, ciel_runtime.router_health())
                self.assertEqual(expected, request.call_args.kwargs['headers'])
                if not expected:
                    token.assert_not_called()

    def test_real_http_health_requires_token_and_router_up_succeeds(self):
        config = {'web_backend': {'enabled': True, 'host': '10.0.0.238'}}
        policy = RouterAccessPolicy({}, lambda value, default: bool(value), lambda value, default: default, lambda: config)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Exercise the external policy even though this isolated test
                # socket listens on loopback rather than the user's LAN.
                request = SimpleNamespace(client_address=('10.0.0.238', 1234), headers=self.headers)
                allowed = policy.request_allowed(request, config, lambda: 'test-secret', lambda: '')
                self.send_response(200 if allowed else 401)
                self.end_headers()
                self.wfile.write(json.dumps({'version': 'test'} if allowed else {'error': 'unauthorized'}).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                patch.object(ciel_runtime, 'ROUTER_BASE', f'http://127.0.0.1:{server.server_port}'),
                patch('ciel_runtime_support.router_access.is_loopback_address', return_value=False),
                patch.object(ciel_runtime, 'load_config', return_value=config),
                patch.object(ciel_runtime, 'router_external_access_token', return_value='') as token,
            ):
                self.assertFalse(ciel_runtime.router_up())
                token.return_value = 'test-secret'
                self.assertTrue(ciel_runtime.router_up())
                token.return_value = 'wrong-secret'
                self.assertFalse(ciel_runtime.router_up())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
