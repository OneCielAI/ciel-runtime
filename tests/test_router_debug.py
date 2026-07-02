import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ciel_runtime


class RouterDebugTests(unittest.TestCase):
    def test_default_router_port_uses_env_override(self):
        with patch.dict("os.environ", {"CIEL_RUNTIME_ROUTER_PORT": "9876"}, clear=False):
            self.assertEqual(9876, ciel_runtime.default_router_port())

    def test_default_router_port_is_stable_per_posix_uid(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(ciel_runtime.os, "getuid", return_value=42, create=True),
        ):
            self.assertEqual(8841, ciel_runtime.default_router_port())

    def test_router_health_match_requires_same_user_and_config_dir(self):
        health = {
            "version": ciel_runtime.VERSION,
            "source_fingerprint": ciel_runtime.SOURCE_FINGERPRINT,
            "user": "other-user",
            "config_dir": str(ciel_runtime.CONFIG_DIR),
        }

        self.assertFalse(ciel_runtime.router_health_matches_current(health))

    def test_router_debug_defaults_to_local_bind(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(ciel_runtime.router_debug_external_access_enabled({}))
            self.assertEqual("127.0.0.1", ciel_runtime.router_bind_host({}))

    def test_router_debug_enables_external_bind(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = {"router_debug_external_access": True, "router_debug_external_access_confirmed": True}

            self.assertTrue(ciel_runtime.router_debug_external_access_enabled(cfg))
            self.assertEqual("0.0.0.0", ciel_runtime.router_bind_host(cfg))

    def test_unconfirmed_router_debug_true_is_treated_as_off(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = {"router_debug_external_access": True}

            self.assertFalse(ciel_runtime.router_debug_external_access_enabled(cfg))
            self.assertEqual("127.0.0.1", ciel_runtime.router_bind_host(cfg))

    def test_bind_host_environment_still_overrides_debug_setting(self):
        with patch.dict("os.environ", {"CIEL_RUNTIME_ROUTER_BIND_HOST": "192.0.2.10"}, clear=True):
            self.assertEqual("192.0.2.10", ciel_runtime.router_bind_host({"router_debug_external_access": False}))

    def test_router_debug_slash_value_parsing(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "CIEL_RUNTIME_ROUTER_DEBUG_ACCESS\n\nValue: on"}],
                }
            ]
        }

        self.assertTrue(ciel_runtime.is_router_debug_request(body))
        self.assertEqual("on", ciel_runtime.router_debug_value_from_body(body))

    def test_router_debug_slash_without_argument_toggles(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "CIEL_RUNTIME_ROUTER_DEBUG_ACCESS\n\nValue: "}],
                }
            ]
        }

        self.assertEqual("toggle", ciel_runtime.router_debug_value_from_body(body))

    def test_channel_clear_slash_value_parsing(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG\n\nValue: status"}],
                }
            ]
        }

        self.assertTrue(ciel_runtime.is_channel_clear_request(body))
        self.assertEqual("status", ciel_runtime.channel_clear_value_from_body(body))

    def test_channel_clear_slash_short_circuits(self):
        handler = object()
        body = {
            "model": "ciel-runtime-test",
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG\n\nValue: all"}],
                }
            ],
        }

        with (
            patch.object(ciel_runtime, "clear_channel_backlog", return_value={"chat_tail": 9, "summary_tail": 0}),
            patch.object(ciel_runtime, "write_anthropic_text_response") as write,
        ):
            self.assertTrue(ciel_runtime.maybe_handle_channel_clear_request(handler, body))  # type: ignore[arg-type]

        write.assert_called_once()
        self.assertIn("channel backlog discarded", write.call_args.args[2])

    def test_version_slash_short_circuits(self):
        handler = object()
        body = {
            "model": "ciel-runtime-test",
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "CIEL_RUNTIME_VERSION_STATUS"}],
                }
            ],
        }

        with patch.object(ciel_runtime, "write_anthropic_text_response") as write:
            self.assertTrue(ciel_runtime.maybe_handle_version_request(handler, body))  # type: ignore[arg-type]

        write.assert_called_once()
        self.assertIn(f"ciel-runtime {ciel_runtime.VERSION}", write.call_args.args[2])
        self.assertIn("config dir:", write.call_args.args[2])

    def test_import_session_slash_value_parsing(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": 'CIEL_RUNTIME_IMPORT_SESSION\n\nTarget: $1\nPath: $2\nArguments: Codex "C:/tmp/codex session.jsonl"',
                        }
                    ],
                }
            ]
        }

        self.assertTrue(ciel_runtime.is_import_session_request(body))
        self.assertEqual(("Codex", "C:/tmp/codex session.jsonl"), ciel_runtime.import_session_args_from_body(body))

    def test_import_session_blocks_claude_into_claude(self):
        body = {
            "model": "ciel-runtime-test",
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "CIEL_RUNTIME_IMPORT_SESSION\n\nArguments: Claude"}],
                }
            ],
        }

        text = ciel_runtime.import_session_response_text("claude", body)

        self.assertIn("blocked", text)
        self.assertIn("Claude-to-Claude", text)

    def test_import_session_codex_transcript_short_circuits(self):
        handler = object()
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "codex.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        ciel_runtime.json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": "original task"}],
                                },
                            }
                        ),
                        ciel_runtime.json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "prior answer"}],
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            body = {
                "model": "ciel-runtime-test",
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": f"CIEL_RUNTIME_IMPORT_SESSION\n\nArguments: Codex {transcript}"}],
                    }
                ],
            }

            with patch.object(ciel_runtime, "write_anthropic_text_response") as write:
                self.assertTrue(ciel_runtime.maybe_handle_import_session_request(handler, body, client_runtime="claude"))  # type: ignore[arg-type]

        write.assert_called_once()
        response_text = write.call_args.args[2]
        self.assertIn("Ciel Runtime ImportSession completed.", response_text)
        self.assertIn("User: original task", response_text)
        self.assertIn("Assistant: prior answer", response_text)

    def test_codex_import_session_prompt_installs_under_codex_home(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"CODEX_HOME": td}

            ciel_runtime.install_ciel_runtime_codex_prompts(env)

            path = Path(td) / "prompts" / "ImportSession.md"
            self.assertTrue(path.exists())
            self.assertIn("CIEL_RUNTIME_IMPORT_SESSION", path.read_text(encoding="utf-8"))

    def test_router_event_message_preview_defaults_to_off(self):
        body = {"messages": [{"role": "user", "content": "secret prompt"}]}

        self.assertEqual({}, ciel_runtime.router_event_message_preview(body, {"router_debug_message_preview_chars": 0}))

    def test_router_event_message_preview_truncates_latest_user_text(self):
        body = {"messages": [{"role": "user", "content": "hello\nworld side question"}]}

        preview = ciel_runtime.router_event_message_preview(body, {"router_debug_message_preview_chars": 11})

        self.assertEqual(11, preview["message_preview_chars"])
        self.assertEqual("hello world", preview["message_preview"])
        self.assertTrue(preview["message_preview_truncated"])


if __name__ == "__main__":
    unittest.main()
