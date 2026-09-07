import tempfile
import os
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ciel_runtime_support.codex_router_auth import authenticated_codex_command, CLIENT_TOKEN_ENV
from ciel_runtime_support.router_access import RouterAccessPolicy
from ciel_runtime_support.router_http import CodexRoutedHeaderPolicy


class CodexRouterAuthTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('CIEL_TEST_CODEX_EXE'), 'requires opted-in real Codex')
    def test_real_codex_sends_router_header_for_model_and_mcp(self):
        seen = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                seen.setdefault(self.path, []).append((
                    self.headers.get('x-ciel-runtime-token') == 'test-router-secret',
                    bool(self.headers.get('Authorization')),
                ))
                # Deliberately stop here: no inference or upstream forwarding.
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"local verification complete"}}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        exe = os.environ['CIEL_TEST_CODEX_EXE']
        base = f'http://127.0.0.1:{server.server_port}'
        servers = json.loads(subprocess.check_output([exe, 'mcp', 'list', '--json']))
        args = [value for entry in servers for value in ('-c', f'mcp_servers.{entry["name"]}.enabled=false')]
        cmd = [exe, *args, '-c', 'model_provider="ciel-auth-test"',
               '-c', 'model_providers.ciel-auth-test.name="Ciel auth verification"',
               '-c', f'model_providers.ciel-auth-test.base_url="{base}/backend-api/codex"',
               '-c', 'model_providers.ciel-auth-test.wire_api="responses"',
               '-c', 'model_providers.ciel-auth-test.requires_openai_auth=true',
               '-c', 'model_providers.ciel-auth-test.request_max_retries=0',
               '-c', 'model_providers.ciel-auth-test.stream_max_retries=0',
               '-c', f'mcp_servers.ciel-runtime-router.url="{base}/ca/mcp"',
               '-c', 'mcp_servers.ciel-runtime-router.enabled=true',
               'exec', '--skip-git-repo-check', 'Local transport verification only']
        env = dict(os.environ, CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN='test-router-secret')
        try:
            with patch('ciel_runtime_support.codex_router_auth.is_loopback_address', return_value=False):
                builtin_cmd = authenticated_codex_command(
                    [exe, '-c', 'model_provider="openai"', '-c', f'openai_base_url="{base}/backend-api/codex"', 'mcp', 'list', '--json'], env, base,
                )
            builtin = subprocess.run(
                builtin_cmd,
                env=env, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(0, builtin.returncode, builtin.stderr)
            with patch('ciel_runtime_support.codex_router_auth.is_loopback_address', return_value=False):
                cmd = authenticated_codex_command(cmd, env, base)
            subprocess.run(cmd, env=env, capture_output=True, timeout=25)
            self.assertIn('/ca/mcp', seen)
            self.assertTrue(any(token for token, _ in seen['/ca/mcp']), seen)
            self.assertIn((True, True), seen.get('/backend-api/codex/responses', []), seen)
            print('Real Codex: MCP router token received; model router token and native Authorization both received')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_model_and_mcp_get_process_only_credentials(self):
        base = 'http://10.0.0.238:9683'
        cmd = ['codex', '-c', f'model_providers.ciel.base_url="{base}/backend-api/codex"',
               '-c', f'mcp_servers.ciel-runtime-router.url="{base}/ca/mcp"', 'resume']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'router-external-token'
            path.write_text('router-secret', encoding='utf-8')
            env = {'CIEL_RUNTIME_STATE_DIR': directory}
            result = authenticated_codex_command(cmd, env, base)
            self.assertIn('model_providers.ciel.env_http_headers.x-ciel-runtime-token="CIEL_RUNTIME_ROUTER_CLIENT_TOKEN"', result)
            self.assertIn('mcp_servers.ciel-runtime-router.env_http_headers.x-ciel-runtime-token="CIEL_RUNTIME_ROUTER_CLIENT_TOKEN"', result)
            self.assertNotIn('router-secret', str(result))
            self.assertEqual('router-secret', env[CLIENT_TOKEN_ENV])
            self.assertEqual('router-secret', path.read_text())

    def test_does_not_send_token_to_other_origin_or_direct_native(self):
        for base in ('http://elsewhere:9683', 'https://10.0.0.238:9683', 'http://127.0.0.1:9683'):
            env = {'CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN': 'secret'}
            cmd = ['codex', '-c', f'model_providers.ciel.base_url="{base}/v1"']
            self.assertEqual(cmd, authenticated_codex_command(cmd, env, 'http://10.0.0.238:9683'))
            self.assertNotIn(CLIENT_TOKEN_ENV, env)

    def test_later_url_override_does_not_receive_router_credential(self):
        base = 'http://10.0.0.238:9683'
        cmd = ['codex', '-c', f'model_providers.ciel.base_url="{base}/v1"',
               '--config=model_providers.ciel.base_url="https://elsewhere/v1"']
        env = {'CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN': 'secret'}
        self.assertEqual(cmd, authenticated_codex_command(cmd, env, base))
        self.assertNotIn(CLIENT_TOKEN_ENV, env)

    def test_native_authorization_preserved_and_router_token_not_forwarded(self):
        cfg = {'web_backend': {'enabled': True, 'host': '10.0.0.238'}}
        policy = RouterAccessPolicy({}, lambda v,d: bool(v), lambda v,d:d, lambda: cfg)
        for path in ('/backend-api/codex/responses', '/ca/mcp', '/v1/responses'):
            headers = {'Authorization': 'Bearer native-openai-token', 'x-ciel-runtime-token': 'router-secret'}
            request = SimpleNamespace(client_address=('10.0.0.238', 1234), headers=headers, path=path)
            self.assertTrue(policy.request_allowed(request, cfg, lambda: 'router-secret', lambda: ''))
            forwarded = CodexRoutedHeaderPolicy(decorate=lambda h:h).project(headers)
            self.assertEqual('Bearer native-openai-token', forwarded['Authorization'])
            self.assertNotIn('x-ciel-runtime-token', forwarded)
            headers['x-ciel-runtime-token'] = 'wrong'
            self.assertFalse(policy.request_allowed(request, cfg, lambda: 'router-secret', lambda: ''))
