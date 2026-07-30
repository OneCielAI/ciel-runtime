import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ciel_runtime


class KimiCodeRuntimeTests(unittest.TestCase):
    def test_native_api_key_panel_exposes_official_oauth_login(self):
        with patch.object(ciel_runtime, "kimi_oauth_configured", return_value=False):
            rows, values = ciel_runtime.api_key_panel_rows(
                "kimi", {"route_through_router": False}
            )

        self.assertIn("login required", rows[0])
        self.assertIn("kimi-oauth-login", values)

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
            with patch.dict(os.environ, {"KIMI_CODE_HOME": tmp}):
                self.assertTrue(ciel_runtime.kimi_oauth_configured())


if __name__ == "__main__":
    unittest.main()
