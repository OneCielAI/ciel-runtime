import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

import ciel_runtime


class ApiKeyRotationTests(unittest.TestCase):
    def setUp(self):
        with ciel_runtime._API_KEY_ROTATION_LOCK:
            ciel_runtime._API_KEY_ROTATION_CURSOR.clear()

    def deepseek_pcfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["deepseek"])
        pcfg.update(overrides)
        return pcfg

    def provider_pcfg(self, provider, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"][provider])
        pcfg.update(overrides)
        return pcfg

    def test_parse_api_key_list_filters_placeholders_and_dedupes(self):
        keys = ciel_runtime.parse_api_key_list("sk-a, dummy\nsk-b;sk-a\nnot-used")

        self.assertEqual(["sk-a", "sk-b"], keys)

    def test_parse_api_key_list_repairs_soft_wrapped_comma_fields(self):
        keys = ciel_runtime.parse_api_key_list(
            "sk-one,sk-two,sk-or\n  -v1-wrapped,sk-four"
        )

        self.assertEqual(["sk-one", "sk-two", "sk-or-v1-wrapped", "sk-four"], keys)

    def test_parse_api_key_list_keeps_newline_separator_without_commas(self):
        keys = ciel_runtime.parse_api_key_list("sk-one\nsk-two\nsk-three")

        self.assertEqual(["sk-one", "sk-two", "sk-three"], keys)

    def test_parse_api_key_list_keeps_final_pasted_line_without_trailing_newline(self):
        keys = ciel_runtime.parse_api_key_list("sk-one\nsk-two\nsk-three\nsk-four")

        self.assertEqual(["sk-one", "sk-two", "sk-three", "sk-four"], keys)

    def test_provider_headers_round_robin_multiple_keys(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-one", "sk-two"])

        first = ciel_runtime.provider_headers("deepseek", pcfg)
        second = ciel_runtime.provider_headers("deepseek", pcfg)
        third = ciel_runtime.provider_headers("deepseek", pcfg)

        self.assertEqual("Bearer sk-one", first["authorization"])
        self.assertEqual("Bearer sk-two", second["authorization"])
        self.assertEqual("Bearer sk-one", third["authorization"])

    def test_model_list_headers_use_primary_key_without_advancing_rotation(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-one", "sk-two"])

        model_headers = ciel_runtime.provider_model_list_headers("deepseek", pcfg)
        request_headers = ciel_runtime.provider_headers("deepseek", pcfg)

        self.assertEqual("Bearer sk-one", model_headers["authorization"])
        self.assertEqual("Bearer sk-one", request_headers["authorization"])

    def test_env_vars_use_primary_key_for_router_auth_token(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-one", "sk-two"])
        cfg = {"current_provider": "deepseek", "providers": {"deepseek": pcfg}}

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual("sk-one", env["ANTHROPIC_AUTH_TOKEN"])

    def test_launch_readiness_accepts_api_keys_list(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-one", "sk-two"])
        cfg = {"current_provider": "deepseek", "providers": {"deepseek": pcfg}}

        errors = ciel_runtime.launch_readiness_errors(cfg)

        self.assertFalse(any("DeepSeek.com requires" in err for err in errors))

    def test_api_key_status_reports_round_robin(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-secret-one", "sk-secret-two"])

        status = ciel_runtime.api_key_status_line("deepseek", pcfg)

        self.assertIn("2 keys, round-robin", status)
        self.assertIn(f"primary {ciel_runtime.mask_secret('sk-secret-one')}", status)
        self.assertIn("fp", status)

    def test_stored_api_key_mask_includes_primary_fingerprint(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-secret-one", "sk-secret-two"])

        status = ciel_runtime.stored_api_key_mask("deepseek", pcfg)

        self.assertIn("2 keys", status)
        self.assertIn(f"primary {ciel_runtime.mask_secret('sk-secret-one')}", status)
        self.assertIn(ciel_runtime.secret_fingerprint("sk-secret-one"), status)

    def test_router_start_resets_only_per_key_cooldowns(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "rate-limit-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "opencode:https://opencode.ai/zen:__key__:abc123": {
                            "cooldown_until": 9999999999,
                            "last_429_at": 123.0,
                        },
                        "opencode:__global__": {
                            "timestamps": [1.0, 2.0],
                            "server_rpm": 20,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(ciel_runtime, "RATE_LIMIT_STATE_PATH", state_path),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmp)),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                removed = ciel_runtime.reset_api_key_cooldowns_for_router_start()

            self.assertEqual(1, removed)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("opencode:https://opencode.ai/zen:__key__:abc123", saved)
            self.assertIn("opencode:__global__", saved)
            self.assertEqual(20, saved["opencode:__global__"]["server_rpm"])

    def test_store_api_key_input_detects_multiple_keys(self):
        cfg = {
            "providers": {
                "deepseek": self.deepseek_pcfg(api_key="", api_keys=[]),
            }
        }
        saved = {}

        def fake_save_config(value):
            saved.update(copy.deepcopy(value))

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config", side_effect=fake_save_config),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            messages = ciel_runtime.store_api_key_input_config("deepseek", "sk-one,sk-two")

        pcfg = saved["providers"]["deepseek"]
        self.assertEqual("sk-one", pcfg["api_key"])
        self.assertEqual(["sk-one", "sk-two"], pcfg["api_keys"])
        self.assertIn("Round-robin: enabled", "\n".join(messages))

    def test_store_api_key_input_clear_removes_single_and_multi_keys(self):
        cfg = {
            "providers": {
                "deepseek": self.deepseek_pcfg(api_key="sk-old", api_keys=["sk-old", "sk-two"]),
            }
        }
        saved = {}

        def fake_save_config(value):
            saved.update(copy.deepcopy(value))

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config", side_effect=fake_save_config),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            messages = ciel_runtime.store_api_key_input_config("deepseek", "clear")

        pcfg = saved["providers"]["deepseek"]
        self.assertNotIn("api_key", pcfg)
        self.assertNotIn("api_keys", pcfg)
        self.assertIn("Cleared stored API key(s) for deepseek. Other providers unchanged.", "\n".join(messages))

    def test_store_api_keys_unset_clears_keys(self):
        cfg = {
            "providers": {
                "deepseek": self.deepseek_pcfg(api_key="sk-old", api_keys=["sk-old", "sk-two"]),
            }
        }
        saved = {}

        def fake_save_config(value):
            saved.update(copy.deepcopy(value))

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config", side_effect=fake_save_config),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            messages = ciel_runtime.store_api_keys_config("deepseek", ["unset"])

        pcfg = saved["providers"]["deepseek"]
        self.assertNotIn("api_key", pcfg)
        self.assertNotIn("api_keys", pcfg)
        self.assertIn("Cleared stored API key(s) for deepseek. Other providers unchanged.", "\n".join(messages))

    def test_live_api_key_slash_value_preserves_multiline_arguments(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "CIEL_RUNTIME_LIVE_API_KEYS\n\nValue: sk-one\nArguments:\nsk-one\nsk-two\nsk-three",
                        }
                    ],
                }
            ]
        }

        self.assertEqual("sk-one\nsk-two\nsk-three", ciel_runtime.live_api_keys_value_from_body(body))

    def test_live_api_key_slash_status_when_arguments_unexpanded(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "CIEL_RUNTIME_LIVE_API_KEYS\n\nValue: $0\nArguments:\n$ARGUMENTS",
                        }
                    ],
                }
            ]
        }

        self.assertEqual("status", ciel_runtime.live_api_keys_value_from_body(body))

    def test_live_api_key_action_updates_current_provider_only(self):
        cfg = {
            "current_provider": "deepseek",
            "providers": {
                "deepseek": self.deepseek_pcfg(api_key="sk-old", api_keys=[]),
                "opencode": self.provider_pcfg("opencode", api_key="sk-opencode", api_keys=["sk-opencode"]),
            },
        }

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            lines, changed = ciel_runtime.handle_live_api_keys_action("sk-live-one\nsk-live-two")

        self.assertTrue(changed)
        self.assertEqual("sk-live-one", cfg["providers"]["deepseek"]["api_key"])
        self.assertEqual(["sk-live-one", "sk-live-two"], cfg["providers"]["deepseek"]["api_keys"])
        self.assertEqual(["sk-opencode"], cfg["providers"]["opencode"]["api_keys"])
        output = "\n".join(lines)
        self.assertIn("Stored 2 API keys for deepseek.", output)
        self.assertIn("Round-robin: enabled", output)
        self.assertNotIn("sk-live-one", output)
        self.assertNotIn("sk-live-two", output)

    def test_slash_command_install_adds_live_api_key_commands(self):
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td)
            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.install_ciel_runtime_slash_commands(include_advisor=False)

            api_key = commands_dir / "api-key.md"
            api_keys = commands_dir / "api-keys.md"
            self.assertTrue(api_key.exists())
            self.assertTrue(api_keys.exists())
            text = api_key.read_text(encoding="utf-8")
            self.assertIn("CIEL_RUNTIME_LIVE_API_KEYS", text)
            self.assertIn("Arguments:", text)
            self.assertIn("$ARGUMENTS", text)

    def test_clear_api_key_preserves_other_provider_keys_after_reload(self):
        providers = ["anthropic", "deepseek", "opencode", "openrouter", "ollama-cloud", "fireworks", "zai"]
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        cfg["current_provider"] = "anthropic"
        for provider in providers:
            cfg["providers"][provider]["api_key"] = f"sk-{provider}-one"
            cfg["providers"][provider]["api_keys"] = [f"sk-{provider}-one", f"sk-{provider}-two"]

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

            with (
                mock.patch.object(ciel_runtime, "CONFIG_PATH", cfg_path),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmp)),
                mock.patch.object(ciel_runtime, "clear_model_cache", side_effect=ciel_runtime.invalidate_config_cache),
            ):
                ciel_runtime.clear_api_key_config("deepseek")
                reloaded = ciel_runtime.load_config()

        for provider in providers:
            keys = ciel_runtime.provider_config_api_keys(provider, reloaded["providers"][provider])
            if provider == "deepseek":
                self.assertEqual([], keys)
            else:
                self.assertEqual([f"sk-{provider}-one", f"sk-{provider}-two"], keys)

    def test_compatibility_api_key_probe_tests_each_configured_key(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-one", "sk-two"])
        calls = []

        def fake_post_json(url, body, headers=None, timeout=60.0, **kwargs):
            calls.append((url, body, headers or {}, timeout, kwargs))
            return {"content": [{"type": "text", "text": "OK"}]}

        with mock.patch.object(ciel_runtime, "post_json", side_effect=fake_post_json):
            lines = ciel_runtime.run_compatibility_api_key_probes(
                "deepseek",
                pcfg,
                "ciel-runtime-deepseek-deepseek-v4-pro[1m]",
                ciel_runtime.compatibility_text_request("ciel-runtime-deepseek-deepseek-v4-pro[1m]"),
                3.0,
            )

        self.assertEqual(2, len(calls))
        self.assertEqual("Bearer sk-one", calls[0][2]["authorization"])
        self.assertEqual("Bearer sk-two", calls[1][2]["authorization"])
        self.assertTrue(calls[0][0].endswith("/v1/messages"))
        self.assertEqual("deepseek", calls[0][4]["provider"])
        self.assertEqual("deepseek", calls[1][4]["provider"])
        self.assertIn("API key 1/2", "\n".join(lines))
        self.assertIn("API key 2/2", "\n".join(lines))

    def test_compatibility_api_key_probe_skips_single_key(self):
        pcfg = self.deepseek_pcfg(api_key="sk-one", api_keys=[])

        with mock.patch.object(ciel_runtime, "post_json") as post_json:
            lines = ciel_runtime.run_compatibility_api_key_probes(
                "deepseek",
                pcfg,
                "ciel-runtime-deepseek-deepseek-v4-pro[1m]",
                ciel_runtime.compatibility_text_request("ciel-runtime-deepseek-deepseek-v4-pro[1m]"),
                3.0,
            )

        self.assertEqual([], lines)
        post_json.assert_not_called()

    def test_compatibility_api_key_probe_failure_masks_key(self):
        pcfg = self.deepseek_pcfg(api_key="", api_keys=["sk-secret-one", "sk-secret-two"])
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/anthropic/v1/messages",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":{"message":"invalid key"}}'),
        )

        with mock.patch.object(ciel_runtime, "post_json", side_effect=error):
            with self.assertRaises(ciel_runtime.CompatibilityApiKeyProbeError) as caught:
                ciel_runtime.run_compatibility_api_key_probes(
                    "deepseek",
                    pcfg,
                    "ciel-runtime-deepseek-deepseek-v4-pro[1m]",
                    ciel_runtime.compatibility_text_request("ciel-runtime-deepseek-deepseek-v4-pro[1m]"),
                    3.0,
                )

        self.assertEqual(401, caught.exception.code)
        self.assertIn("invalid key", str(caught.exception))
        self.assertNotIn("sk-secret-one", str(caught.exception))

    def test_compatibility_http_error_message_preserves_type_and_retry_after(self):
        error = urllib.error.HTTPError(
            "https://opencode.ai/zen/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "63478"},
            io.BytesIO(
                b'{"type":"error","error":{"type":"FreeUsageLimitError",'
                b'"message":"Rate limit exceeded. Please try again later."},"metadata":{}}'
            ),
        )

        message = ciel_runtime.compatibility_http_error_message(error)

        self.assertIn("FreeUsageLimitError", message)
        self.assertIn("Rate limit exceeded. Please try again later.", message)
        self.assertIn("Retry-After:", message)
        self.assertIn("17h", message)
        self.assertIn("63478s", message)

    def test_upstream_429_long_retry_after_fails_fast_instead_of_timing_out(self):
        pcfg = self.provider_pcfg("opencode", api_key="sk-one", current_model="deepseek-v4-flash-free")
        error = urllib.error.HTTPError(
            "https://opencode.ai/zen/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "3600"},
            io.BytesIO(
                b'{"type":"error","error":{"type":"FreeUsageLimitError",'
                b'"message":"Rate limit exceeded. Please try again later."},"metadata":{}}'
            ),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error),
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                ciel_runtime.post_json_with_rate_retry(
                    "https://opencode.ai/zen/v1/chat/completions",
                    {"model": "deepseek-v4-flash-free", "messages": []},
                    {},
                    30.0,
                    "opencode",
                    pcfg,
                    "deepseek-v4-flash-free",
                )

        self.assertEqual(429, caught.exception.code)
        self.assertIn("FreeUsageLimitError", caught.exception.read().decode("utf-8"))
        sleep.assert_not_called()

    def test_ollama_session_usage_limit_preserves_429_and_never_retries(self):
        pcfg = self.provider_pcfg(
            "ollama-cloud",
            api_key="ollama-secret",
            current_model="deepseek-v4-flash:0731",
            gateway_retries=10,
        )
        raw = (
            b'{"error":{"type":"rate_limit_error","message":'
            b'"you (djloved) have reached your session usage limit, '
            b'add extra usage: https://ollama.com/settings"}}'
        )
        calls = 0

        def rejected(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError(
                "https://ollama.com/api/chat",
                429,
                "Too Many Requests",
                {},
                io.BytesIO(raw),
            )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=rejected),
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                ciel_runtime.open_openai_stream_with_rate_retry(
                    "https://ollama.com/api/chat",
                    {"model": "deepseek-v4-flash:0731", "messages": []},
                    {},
                    30.0,
                    "ollama-cloud",
                    pcfg,
                    "deepseek-v4-flash:0731",
                )

        self.assertEqual(1, calls)
        self.assertEqual(429, caught.exception.code)
        body = caught.exception.read().decode("utf-8")
        self.assertIn("djloved", body)
        self.assertIn("session usage limit", body)
        self.assertIn("https://ollama.com/settings", body)
        sleep.assert_not_called()

    def test_upstream_429_rotates_to_live_key_without_waiting(self):
        pcfg = self.provider_pcfg(
            "opencode",
            api_key="",
            api_keys=["sk-one", "sk-two"],
            current_model="deepseek-v4-flash-free",
        )
        error = urllib.error.HTTPError(
            "https://opencode.ai/zen/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "3600"},
            io.BytesIO(
                b'{"type":"error","error":{"type":"RateLimitError",'
                b'"message":"Rate limit exceeded."},"metadata":{}}'
            ),
        )
        calls = []

        class FakeResponse:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"choices":[{"message":{"role":"assistant","content":"OK"}}]}'

        def fake_urlopen(req, timeout):
            calls.append(dict(req.header_items()))
            if len(calls) == 1:
                raise error
            return FakeResponse()

        notices = []
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(ciel_runtime, "RATE_LIMIT_STATE_PATH", Path(tmpdir) / "rate-limit-state.json"),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmpdir)),
                mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=fake_urlopen),
                mock.patch.object(ciel_runtime, "write_router_activity"),
                mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
                mock.patch.object(ciel_runtime.time, "sleep") as sleep,
                mock.patch.object(ciel_runtime, "register_router_rate_limit_backoff") as backoff,
            ):
                data = ciel_runtime.post_json_with_rate_retry(
                    "https://opencode.ai/zen/v1/chat/completions",
                    {"model": "deepseek-v4-flash-free", "messages": []},
                    {
                        **ciel_runtime.provider_headers("opencode", pcfg),
                        "X-Client-Trace": "trace-1",
                    },
                    30.0,
                    "opencode",
                    pcfg,
                    "deepseek-v4-flash-free",
                    notices.append,
                )

        self.assertEqual("OK", data["choices"][0]["message"]["content"])
        self.assertEqual("Bearer sk-one", calls[0].get("Authorization"))
        self.assertEqual("Bearer sk-two", calls[1].get("Authorization"))
        self.assertEqual("trace-1", calls[0].get("X-client-trace"))
        self.assertEqual("trace-1", calls[1].get("X-client-trace"))
        self.assertEqual([], notices)
        sleep.assert_not_called()
        backoff.assert_not_called()

    def test_stream_429_rotates_to_live_key_without_waiting(self):
        pcfg = self.provider_pcfg(
            "opencode",
            api_key="",
            api_keys=["sk-one", "sk-two"],
            current_model="deepseek-v4-flash-free",
        )
        error = urllib.error.HTTPError(
            "https://opencode.ai/zen/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "3600"},
            io.BytesIO(
                b'{"type":"error","error":{"type":"RateLimitError",'
                b'"message":"Rate limit exceeded."},"metadata":{}}'
            ),
        )
        calls = []

        class FakeResponse:
            headers = {}

        def fake_urlopen(req, timeout):
            calls.append(dict(req.header_items()))
            if len(calls) == 1:
                raise error
            return FakeResponse()

        notices = []
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(ciel_runtime, "RATE_LIMIT_STATE_PATH", Path(tmpdir) / "rate-limit-state.json"),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmpdir)),
                mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=fake_urlopen),
                mock.patch.object(ciel_runtime, "set_upstream_stream_read_timeout"),
                mock.patch.object(ciel_runtime, "write_router_activity"),
                mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
                mock.patch.object(ciel_runtime.time, "sleep") as sleep,
                mock.patch.object(ciel_runtime, "register_router_rate_limit_backoff") as backoff,
            ):
                resp = ciel_runtime.open_openai_stream_with_rate_retry(
                    "https://opencode.ai/zen/v1/chat/completions",
                    {"model": "deepseek-v4-flash-free", "messages": [], "stream": True},
                    ciel_runtime.provider_headers("opencode", pcfg),
                    120.0,
                    "opencode",
                    pcfg,
                    "deepseek-v4-flash-free",
                    notices.append,
                )

        self.assertIsInstance(resp, FakeResponse)
        self.assertEqual("Bearer sk-one", calls[0].get("Authorization"))
        self.assertEqual("Bearer sk-two", calls[1].get("Authorization"))
        self.assertEqual([], notices)
        sleep.assert_not_called()
        backoff.assert_not_called()

    def test_stream_connection_reset_retries_then_surfaces_upstream_error(self):
        pcfg = self.provider_pcfg(
            "kimi",
            api_key="sk-one",
            current_model="k3[1m]",
            gateway_retries=2,
        )
        reset = ConnectionResetError(10054, "connection forcibly closed by remote host")

        with (
            mock.patch.object(
                ciel_runtime.urllib.request,
                "urlopen",
                side_effect=reset,
            ) as urlopen,
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime.time, "sleep") as sleep,
        ):
            with self.assertRaises(RuntimeError) as caught:
                ciel_runtime.open_openai_stream_with_rate_retry(
                    "https://api.kimi.com/coding/v1/chat/completions",
                    {"model": "k3[1m]", "messages": [], "stream": True},
                    ciel_runtime.provider_headers("kimi", pcfg),
                    120.0,
                    "kimi",
                    pcfg,
                    "k3[1m]",
                )

        self.assertEqual(3, urlopen.call_count)
        self.assertEqual([mock.call(2.0), mock.call(4.0)], sleep.call_args_list)
        self.assertIn("ConnectionResetError", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ConnectionResetError)

    def test_stream_413_preserves_upstream_status_and_consumed_body(self):
        pcfg = self.provider_pcfg(
            "kimi",
            api_key="sk-one",
            current_model="k3[1m]",
            gateway_retries=10,
        )
        raw = b'{"error":{"type":"request_too_large","message":"provider limit"}}'
        error = urllib.error.HTTPError(
            "https://api.kimi.com/coding/v1/chat/completions",
            413,
            "Payload Too Large",
            {"content-type": "application/json"},
            io.BytesIO(raw),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error) as urlopen,
            mock.patch.object(ciel_runtime, "write_router_activity"),
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            ciel_runtime.open_openai_stream_with_rate_retry(
                "https://api.kimi.com/coding/v1/chat/completions",
                {"model": "k3[1m]", "messages": [], "stream": True},
                ciel_runtime.provider_headers("kimi", pcfg),
                120.0,
                "kimi",
                pcfg,
                "k3[1m]",
            )

        self.assertEqual(413, caught.exception.code)
        self.assertEqual(raw, caught.exception.read())
        self.assertEqual(1, urlopen.call_count)

    def test_non_stream_413_preserves_upstream_status_and_consumed_body(self):
        pcfg = self.provider_pcfg(
            "kimi",
            api_key="sk-one",
            current_model="k3[1m]",
            gateway_retries=10,
        )
        raw = b'{"error":{"type":"request_too_large","message":"provider limit"}}'
        error = urllib.error.HTTPError(
            "https://api.kimi.com/coding/v1/chat/completions",
            413,
            "Payload Too Large",
            {"content-type": "application/json"},
            io.BytesIO(raw),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error) as urlopen,
            mock.patch.object(ciel_runtime, "write_router_activity"),
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            ciel_runtime.post_json_with_rate_retry(
                "https://api.kimi.com/coding/v1/chat/completions",
                {"model": "k3[1m]", "messages": []},
                ciel_runtime.provider_headers("kimi", pcfg),
                120.0,
                "kimi",
                pcfg,
                "k3[1m]",
            )

        self.assertEqual(413, caught.exception.code)
        self.assertEqual(raw, caught.exception.read())
        self.assertEqual(1, urlopen.call_count)

    def test_stream_401_preserves_auth_error_without_retry(self):
        pcfg = self.provider_pcfg(
            "kimi",
            api_key="sk-one",
            current_model="k3[1m]",
            gateway_retries=10,
        )
        raw = (
            b'{"error":{"type":"invalid_authentication_error",'
            b'"message":"The API Key appears to be invalid or may have expired"}}'
        )
        error = urllib.error.HTTPError(
            "https://api.kimi.com/coding/v1/chat/completions",
            401,
            "Unauthorized",
            {"content-type": "application/json"},
            io.BytesIO(raw),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error) as urlopen,
            mock.patch.object(ciel_runtime, "write_router_activity"),
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            ciel_runtime.open_openai_stream_with_rate_retry(
                "https://api.kimi.com/coding/v1/chat/completions",
                {"model": "k3[1m]", "messages": [], "stream": True},
                ciel_runtime.provider_headers("kimi", pcfg),
                120.0,
                "kimi",
                pcfg,
                "k3[1m]",
            )

        self.assertEqual(401, caught.exception.code)
        self.assertEqual(raw, caught.exception.read())
        self.assertEqual(1, urlopen.call_count)

    def test_non_stream_401_preserves_auth_error_without_retry(self):
        pcfg = self.provider_pcfg(
            "kimi",
            api_key="sk-one",
            current_model="k3[1m]",
            gateway_retries=10,
        )
        raw = (
            b'{"error":{"type":"invalid_authentication_error",'
            b'"message":"The API Key appears to be invalid or may have expired"}}'
        )
        error = urllib.error.HTTPError(
            "https://api.kimi.com/coding/v1/chat/completions",
            401,
            "Unauthorized",
            {"content-type": "application/json"},
            io.BytesIO(raw),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error) as urlopen,
            mock.patch.object(ciel_runtime, "write_router_activity"),
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            ciel_runtime.post_json_with_rate_retry(
                "https://api.kimi.com/coding/v1/chat/completions",
                {"model": "k3[1m]", "messages": []},
                ciel_runtime.provider_headers("kimi", pcfg),
                120.0,
                "kimi",
                pcfg,
                "k3[1m]",
            )

        self.assertEqual(401, caught.exception.code)
        self.assertEqual(raw, caught.exception.read())
        self.assertEqual(1, urlopen.call_count)

    def test_direct_anthropic_compatible_429_rotates_to_live_key(self):
        pcfg = self.provider_pcfg(
            "deepseek",
            api_key="",
            api_keys=["sk-one", "sk-two"],
            current_model="deepseek-v4-pro",
        )
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/anthropic/v1/messages",
            429,
            "Too Many Requests",
            {"Retry-After": "3600"},
            io.BytesIO(b'{"error":{"message":"rate limit"}}'),
        )
        calls = []

        class FakeResponse:
            headers = {}

            def read(self):
                return b'{"content":[{"type":"text","text":"OK"}]}'

        def fake_urlopen(req, timeout):
            calls.append(dict(req.header_items()))
            if len(calls) == 1:
                raise error
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(ciel_runtime, "RATE_LIMIT_STATE_PATH", Path(tmpdir) / "rate-limit-state.json"),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmpdir)),
                mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=fake_urlopen),
                mock.patch.object(ciel_runtime, "write_router_activity"),
                mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
                mock.patch.object(ciel_runtime.time, "sleep") as sleep,
            ):
                resp = ciel_runtime.open_provider_request_with_key_retry(
                    "https://api.deepseek.com/anthropic/v1/messages",
                    {"model": "deepseek-v4-pro", "messages": []},
                    ciel_runtime.provider_headers("deepseek", pcfg),
                    30.0,
                    "deepseek",
                    pcfg,
                    "deepseek-v4-pro",
                )

        self.assertEqual(b'{"content":[{"type":"text","text":"OK"}]}', resp.read())
        self.assertEqual("Bearer sk-one", calls[0].get("Authorization"))
        self.assertEqual("Bearer sk-two", calls[1].get("Authorization"))
        sleep.assert_not_called()

    def test_direct_anthropic_500_preserves_body_and_records_provider_message(self):
        pcfg = self.provider_pcfg(
            "anthropic",
            api_key="",
            api_keys=[],
            base_url="https://api.anthropic.com",
            gateway_retries=0,
        )
        raw = (
            b'{"type":"error","error":{"type":"api_error",'
            b'"message":"Temporary upstream failure ref=req_123"}}'
        )
        error = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages",
            500,
            "Internal Server Error",
            {"content-type": "application/json"},
            io.BytesIO(raw),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error),
            mock.patch.object(ciel_runtime, "write_router_activity") as activity,
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                ciel_runtime.open_provider_request_with_key_retry(
                    "https://api.anthropic.com/v1/messages",
                    {"model": "claude-fable-5", "messages": [], "stream": True},
                    {"content-type": "application/json"},
                    300.0,
                    "anthropic",
                    pcfg,
                    "claude-fable-5",
                    stream=True,
                )

        self.assertEqual(500, caught.exception.code)
        self.assertEqual(raw, caught.exception.read())
        self.assertEqual(raw, caught.exception.ciel_runtime_body)
        error_events = [
            call for call in activity.call_args_list
            if call.args and call.args[0] == "error"
        ]
        self.assertEqual(1, len(error_events))
        self.assertEqual(500, error_events[0].kwargs["code"])
        self.assertEqual(
            "api_error: Temporary upstream failure ref=req_123",
            error_events[0].kwargs["message"],
        )
        self.assertTrue(
            any(
                "upstream_direct_http_error" in str(call)
                and "Temporary upstream failure ref=req_123" in str(call)
                for call in router_log.call_args_list
            )
        )

    def test_direct_anthropic_compatible_single_oauth_429_retries_same_headers(self):
        pcfg = self.provider_pcfg(
            "anthropic",
            api_key="",
            api_keys=[],
            base_url="https://api.anthropic.com",
            gateway_retries=2,
        )
        error = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages",
            429,
            "Too Many Requests",
            {"Retry-After": "1"},
            io.BytesIO(b'{"error":{"type":"rate_limit_error","message":"Rate limited"}}'),
        )
        calls = []

        class FakeResponse:
            headers = {}

            def read(self):
                return b'{"content":[{"type":"text","text":"OK"}]}'

        def fake_urlopen(req, timeout):
            del timeout
            calls.append(dict(req.header_items()))
            if len(calls) == 1:
                raise error
            return FakeResponse()

        def auth_header(call):
            for key, value in call.items():
                if key.lower() == "authorization":
                    return value
            return None

        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "authorization": "Bearer oauth-token",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(ciel_runtime, "RATE_LIMIT_STATE_PATH", Path(tmpdir) / "rate-limit-state.json"),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmpdir)),
                mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=fake_urlopen),
                mock.patch.object(ciel_runtime, "write_router_activity"),
                mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
                mock.patch.object(ciel_runtime, "register_router_rate_limit_backoff", return_value=0.25) as backoff,
                mock.patch.object(ciel_runtime.time, "sleep") as sleep,
            ):
                resp = ciel_runtime.open_provider_request_with_key_retry(
                    "https://api.anthropic.com/v1/messages",
                    {"model": "claude-opus-4-8", "messages": [], "stream": True},
                    headers,
                    300.0,
                    "anthropic",
                    pcfg,
                    "claude-opus-4-8",
                    stream=True,
                )

        self.assertEqual(b'{"content":[{"type":"text","text":"OK"}]}', resp.read())
        self.assertEqual(2, len(calls))
        self.assertEqual("Bearer oauth-token", auth_header(calls[0]))
        self.assertEqual("Bearer oauth-token", auth_header(calls[1]))
        backoff.assert_called_once()
        sleep.assert_called_once_with(0.25)

    def test_stream_429_can_disable_rate_limit_retry_for_compatibility_tests(self):
        pcfg = self.provider_pcfg("opencode", api_key="sk-one", current_model="deepseek-v4-flash-free")
        error = urllib.error.HTTPError(
            "https://opencode.ai/zen/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "300"},
            io.BytesIO(
                b'{"type":"error","error":{"type":"FreeUsageLimitError",'
                b'"message":"Rate limit exceeded. Please try again later."},"metadata":{}}'
            ),
        )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=error) as urlopen,
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                ciel_runtime.open_openai_stream_with_rate_retry(
                    "https://opencode.ai/zen/v1/chat/completions",
                    {"model": "deepseek-v4-flash-free", "messages": [], "stream": True},
                    {},
                    120.0,
                    "opencode",
                    pcfg,
                    "deepseek-v4-flash-free",
                    retry_rate_limits=False,
                )

        self.assertEqual(1, urlopen.call_count)
        self.assertEqual(429, caught.exception.code)
        self.assertIn("FreeUsageLimitError", caught.exception.read().decode("utf-8"))
        sleep.assert_not_called()

    def test_ollama_template_validation_500_is_terminal_actionable_400(self):
        pcfg = self.provider_pcfg(
            "ollama", current_model="qwen3.8:27b", gateway_retries=10
        )
        calls = []

        def rejected(*args, **kwargs):
            calls.append((args, kwargs))
            raise urllib.error.HTTPError(
                "http://10.0.0.146:11434/api/chat",
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b'{"error":"system message must be at the beginning"}'),
            )

        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=rejected),
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                ciel_runtime.open_openai_stream_with_rate_retry(
                    "http://10.0.0.146:11434/api/chat",
                    {"model": "qwen3.8:27b", "messages": [], "stream": True},
                    {},
                    120.0,
                    "ollama",
                    pcfg,
                    "qwen3.8:27b",
                )

        self.assertEqual(1, len(calls))
        self.assertEqual(400, caught.exception.code)
        self.assertEqual(500, caught.exception.ciel_runtime_upstream_status)
        body = caught.exception.read().decode("utf-8")
        self.assertIn("Upstream Ollama rejected the request (HTTP 500)", body)
        self.assertIn("system message must be at the beginning", body)
        sleep.assert_not_called()

    def test_compatibility_api_key_probe_uses_provider_specific_routes(self):
        cases = [
            ("ollama-cloud", "glm-5.1", "/api/chat"),
            ("self-hosted-nim", "model", "/v1/messages"),
            ("opencode", "claude-sonnet-4-6", "/v1/messages"),
            ("opencode", "deepseek-v4-flash-free", "/v1/chat/completions"),
            ("opencode-go", "qwen3.6-plus", "/v1/messages"),
            ("opencode-go", "deepseek-v4-pro", "/v1/chat/completions"),
        ]

        for provider, model, expected_suffix in cases:
            with self.subTest(provider=provider, model=model):
                pcfg = self.provider_pcfg(provider, api_key="", api_keys=["sk-one", "sk-two"], current_model=model)
                calls = []

                def fake_post_json(url, body, headers=None, timeout=60.0, **kwargs):
                    calls.append((url, body, headers or {}, timeout, kwargs))
                    return {"content": [{"type": "text", "text": "OK"}]}

                with mock.patch.object(ciel_runtime, "post_json", side_effect=fake_post_json):
                    ciel_runtime.run_compatibility_api_key_probes(
                        provider,
                        pcfg,
                        model,
                        ciel_runtime.compatibility_text_request(model),
                        3.0,
                    )

                self.assertEqual(2, len(calls))
                self.assertTrue(calls[0][0].endswith(expected_suffix), calls[0][0])
                self.assertTrue(calls[1][0].endswith(expected_suffix), calls[1][0])
                self.assertEqual("Bearer sk-one", calls[0][2]["authorization"])
                self.assertEqual("Bearer sk-two", calls[1][2]["authorization"])
                self.assertEqual(provider, calls[0][4]["provider"])
                self.assertEqual(provider, calls[1][4]["provider"])


if __name__ == "__main__":
    unittest.main()
