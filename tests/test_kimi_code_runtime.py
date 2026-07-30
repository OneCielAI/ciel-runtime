import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import ciel_runtime
from ciel_runtime_support.providers import kimi as kimi_provider_module


class KimiCodeRuntimeTests(unittest.TestCase):
    def test_runtime_router_accepts_kimi_cli_chat_completions_path(self):
        router = next(
            router
            for router in ciel_runtime.build_runtime_routers()
            if router.name == "openai-chat"
        )

        self.assertTrue(router.can_handle_post("/v1/chat/completions", "kimi", {}))
        self.assertFalse(router.can_handle_post("/v1/responses", "kimi", {}))

    def test_chat_passthrough_uses_kimi_upstream_and_preserves_openai_response(self):
        response_bytes = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self):
                self.stream = BytesIO(response_bytes)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return self.stream.read(size)

        handler = Mock()
        handler.headers = {"authorization": "Bearer ciel-runtime-router-local-key"}
        handler.wfile = BytesIO()
        captured = {}

        def urlopen(request, **kwargs):
            captured["request"] = request
            captured["kwargs"] = kwargs
            return Response()

        with (
            patch.object(ciel_runtime, "provider_upstream_request_base", return_value="https://api.kimi.com/coding"),
            patch.object(ciel_runtime, "resolve_requested_model", return_value="kimi-for-coding"),
            patch.object(ciel_runtime, "provider_upstream_model", return_value="kimi-for-coding"),
            patch.object(ciel_runtime, "apply_provider_adapter_request_policy", side_effect=lambda _p, _c, body: body),
            patch.object(ciel_runtime, "provider_chat_headers", return_value={"authorization": "Bearer actual-kimi-key"}),
            patch.object(ciel_runtime, "provider_urlopen", side_effect=urlopen),
            patch.object(ciel_runtime, "provider_request_timeout_seconds", return_value=60.0),
            patch.object(ciel_runtime, "_copy_upstream_response_headers"),
        ):
            ciel_runtime.forward_provider_chat(
                handler,
                "kimi",
                {},
                {"model": "ciel-runtime-kimi-k3", "messages": [], "stream": True},
            )

        request = captured["request"]
        self.assertEqual("https://api.kimi.com/coding/v1/chat/completions", request.full_url)
        self.assertEqual("Bearer actual-kimi-key", request.headers["Authorization"])
        self.assertEqual(response_bytes, handler.wfile.getvalue())
        self.assertEqual(60.0, captured["kwargs"]["timeout"])

    def test_native_api_key_panel_exposes_official_oauth_login(self):
        with patch.object(ciel_runtime, "kimi_oauth_configured", return_value=False):
            rows, values = ciel_runtime.api_key_panel_rows(
                "kimi", {"route_through_router": False}
            )

        self.assertIn("login required", rows[0])
        self.assertIn("kimi-oauth-login", values)

    def test_routed_api_key_panel_also_exposes_official_oauth_login(self):
        with patch.object(ciel_runtime, "kimi_oauth_configured", return_value=True):
            rows, values = ciel_runtime.api_key_panel_rows(
                "kimi", {"route_through_router": True}
            )

        self.assertIn("Kimi OAuth (Routed)", rows[0])
        self.assertIn("kimi-oauth-login", values)
        self.assertIn("input", values)
        self.assertIn("clear", values)

    def test_successful_oauth_login_clears_stored_kimi_api_key(self):
        with (
            patch.object(ciel_runtime, "run_kimi_oauth_login", return_value=0),
            patch.object(ciel_runtime, "kimi_oauth_configured", return_value=True),
            patch.object(
                ciel_runtime,
                "clear_api_key_config",
                return_value=["Kimi API key cleared."],
            ) as clear_api_key,
        ):
            messages = ciel_runtime.run_kimi_oauth_action("login")

        clear_api_key.assert_called_once_with("kimi")
        self.assertIn("Kimi API key cleared.", messages)

    def test_oauth_login_does_not_clear_key_without_detected_credential(self):
        with (
            patch.object(ciel_runtime, "run_kimi_oauth_login", return_value=0),
            patch.object(ciel_runtime, "kimi_oauth_configured", return_value=False),
            patch.object(ciel_runtime, "clear_api_key_config") as clear_api_key,
        ):
            messages = ciel_runtime.run_kimi_oauth_action("login")

        clear_api_key.assert_not_called()
        self.assertIn("was not cleared", messages[0])

    def test_every_kimi_protocol_uses_kimi_code_identity_headers(self):
        identity = {
            "User-Agent": "kimi-code-cli/0.30.0",
            "X-Msh-Platform": "kimi_code_cli",
            "X-Msh-Version": "0.30.0",
            "X-Msh-Device-Id": "device-id",
        }
        with (
            patch.object(kimi_provider_module, "identity_headers", return_value=identity),
            patch.object(kimi_provider_module, "oauth_access_token", return_value=None),
        ):
            for operation in ("anthropic_messages", "openai_chat", "openai_responses"):
                headers = ciel_runtime.provider_headers("kimi", {"api_key": "key"}, {}, operation)
                self.assertEqual(identity["User-Agent"], headers["User-Agent"])
                self.assertEqual(identity["X-Msh-Platform"], headers["X-Msh-Platform"])
            other = ciel_runtime.provider_headers("deepseek", {"api_key": "key"}, {}, "openai_chat")

        self.assertNotEqual(identity["User-Agent"], other.get("User-Agent"))

    def test_routed_launch_exports_official_kimi_environment_contract(self):
        cfg = {
            "providers": {
                "kimi": {
                    "route_through_router": True,
                    "current_model": "k3",
                    "context_window": 1048576,
                    "effort_level": "xhigh",
                }
            },
            "provider": "kimi",
        }
        captured = {}

        def call(argv, env=None):
            captured.update(env or {})
            captured["argv"] = argv
            return 0

        with (
            patch.object(ciel_runtime, "load_config", return_value=cfg),
            patch.object(ciel_runtime, "get_current_provider", return_value=("kimi", cfg["providers"]["kimi"])),
            patch.object(ciel_runtime, "install_kimi_code_if_missing", return_value="/usr/bin/kimi"),
            patch.object(ciel_runtime, "provider_has_api_key", return_value=True),
            patch.object(ciel_runtime, "start_router_if_needed", return_value=False),
            patch.object(ciel_runtime, "current_alias", return_value="ciel-runtime-kimi-k3"),
            patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=lambda fn, _managed: fn()),
            patch.object(ciel_runtime.subprocess, "call", side_effect=call),
        ):
            code = ciel_runtime.launch_kimi(["--continue"])

        self.assertEqual(0, code)
        self.assertEqual(["/usr/bin/kimi", "--continue"], captured["argv"])
        self.assertEqual("openai", captured["KIMI_MODEL_PROVIDER_TYPE"])
        self.assertEqual("ciel-runtime-kimi-k3", captured["KIMI_MODEL_NAME"])
        self.assertEqual("1048576", captured["KIMI_MODEL_MAX_CONTEXT_SIZE"])
        self.assertEqual("xhigh", captured["KIMI_MODEL_THINKING_EFFORT"])
        self.assertTrue(captured["KIMI_MODEL_BASE_URL"].endswith("/v1"))

    def test_native_first_launch_runs_login_before_cli(self):
        pcfg = {"route_through_router": False}
        calls = []
        with (
            patch.object(ciel_runtime, "load_config", return_value={}),
            patch.object(ciel_runtime, "get_current_provider", return_value=("kimi", pcfg)),
            patch.object(ciel_runtime, "install_kimi_code_if_missing", return_value="kimi"),
            patch.object(ciel_runtime, "kimi_oauth_configured", return_value=False),
            patch.object(ciel_runtime.subprocess, "call", side_effect=lambda argv, env=None: calls.append(argv) or 0),
        ):
            code = ciel_runtime.launch_kimi(["--session", "work"])

        self.assertEqual(0, code)
        self.assertEqual([["kimi", "login"], ["kimi", "--session", "work"]], calls)

    def test_oauth_detection_does_not_read_or_copy_token_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text('[providers.kimi]\ntype = "managed:kimi-code"\napi_key = ""\n', encoding="utf-8")
            credentials = Path(tmp) / "credentials"
            credentials.mkdir()
            (credentials / "kimi-code.json").write_text(
                '{"access_token":"secret-token","expires_at":4102444800}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"KIMI_CODE_HOME": tmp}):
                self.assertTrue(ciel_runtime.kimi_oauth_configured())

    def test_routed_chat_uses_official_oauth_bearer_when_api_key_is_absent(self):
        with (
            patch.object(kimi_provider_module, "oauth_access_token", return_value="oauth-token"),
            patch.object(kimi_provider_module, "identity_headers", return_value={}),
        ):
            headers = ciel_runtime.provider_chat_headers("kimi", {}, {})

        authorization = next(
            value for name, value in headers.items() if name.lower() == "authorization"
        )
        self.assertEqual("Bearer oauth-token", authorization)


if __name__ == "__main__":
    unittest.main()
