import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import hashlib
from pathlib import Path

import ciel_runtime


class StatuslineTests(unittest.TestCase):
    def run_statusline(self, env_extra: dict[str, str] | None = None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            env = os.environ.copy()
            env.pop("CIEL_RUNTIME_PROVIDER", None)
            env.pop("CIEL_RUNTIME_MODEL_ALIAS", None)
            env.pop("CIEL_RUNTIME_STATUSLINE_FORCE", None)
            env.update({
                "CIEL_RUNTIME_CONFIG_DIR": tmp,
                "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
            })
            if env_extra:
                env.update(env_extra)
            session = {
                "model": {"display_name": "claude-sonnet-4-6"},
                "workspace": {"current_dir": tmp},
            }
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            return proc.stdout.strip()

    def test_statusline_is_silent_for_native_claude_session(self):
        self.assertEqual("", self.run_statusline())

    def test_statusline_outputs_for_ciel_runtime_session(self):
        out = self.run_statusline({"CIEL_RUNTIME_PROVIDER": "ollama-cloud", "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-test"})

        self.assertIn("[claude-sonnet-4-6]", out)

    def test_statusline_prefers_router_context_for_ciel_runtime_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "current_provider": "vllm",
                        "providers": {
                            "vllm": {
                                "base_url": "http://localhost:8000",
                                "current_model": "qwen36-35b-a3b-nvfp4",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "context-usage.json").write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "provider": "vllm",
                        "model": "qwen36-35b-a3b-nvfp4",
                        "tokens": 100724,
                        "context_limit": 262144,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "CIEL_RUNTIME_CONFIG_DIR": tmp,
                    "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                    "CIEL_RUNTIME_PROVIDER": "vllm",
                    "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-test",
                }
            )
            session = {
                "model": {"display_name": "ciel-runtime-test"},
                "workspace": {"current_dir": tmp},
                "context_window": {
                    "current_usage": {"input_tokens": 100724},
                    "context_window_size": 200000,
                    "used_percentage": 50.362,
                },
            }
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

        self.assertIn("ctx 100,724/262,144 tok", proc.stdout)
        self.assertNotIn("ctx 100,724/200,000 tok", proc.stdout)

    def test_statusline_uses_current_configured_context_limit_over_stale_router_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "current_provider": "opencode",
                        "providers": {
                            "opencode": {
                                "base_url": "https://opencode.ai/zen",
                                "current_model": "deepseek-v4-flash-free",
                                "context_window": 131072,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "context-usage.json").write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "provider": "opencode",
                        "model": "ciel-runtime-opencode-deepseek-v4-flash-free",
                        "tokens": 93342,
                        "context_limit": 1048576,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "CIEL_RUNTIME_CONFIG_DIR": tmp,
                    "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                    "CIEL_RUNTIME_PROVIDER": "opencode",
                    "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-opencode-deepseek-v4-flash-free",
                }
            )
            session = {
                "model": {"display_name": "ciel-runtime-opencode-deepseek-v4-flash-free"},
                "workspace": {"current_dir": tmp},
                "context_window": {
                    "current_usage": {"input_tokens": 93342},
                    "context_window_size": 1048576,
                    "used_percentage": 8.9,
                },
            }
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

        self.assertIn("ctx 93,342/131,072 tok", proc.stdout)
        self.assertNotIn("ctx 93,342/1,048,576 tok", proc.stdout)

    def test_statusline_shows_pending_channel_queue_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            config_dir = Path(tmp)
            (config_dir / "channel-llm-cursor.json").write_text('{"last_id":1}\n', encoding="utf-8")
            messages = [
                {"id": 1, "channel": "room", "sender_id": "a", "message": "old"},
                {"id": 2, "channel": "room", "sender_id": "a", "message": "new 1", "meta": {"mcp_server": "generic-mcp"}},
                {"id": 3, "channel": "room", "sender_id": "b", "message": "new 2", "delivery": ["llm"]},
                {"id": 4, "channel": "sys", "sender_id": "sys", "message": "sys.sse.connected"},
            ]
            (config_dir / "chat-messages.jsonl").write_text(
                "\n".join(json.dumps(item) for item in messages) + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "CIEL_RUNTIME_CONFIG_DIR": tmp,
                    "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                    "CIEL_RUNTIME_PROVIDER": "ollama-cloud",
                    "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-test",
                }
            )
            session = {
                "model": {"display_name": "ciel-runtime-test"},
                "workspace": {"current_dir": tmp},
            }
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

        self.assertIn("channel queue 2", proc.stdout)

    def test_statusline_shows_multi_key_rate_limit_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            config_dir = Path(tmp)
            keys = ["sk-k1", "sk-k2", "sk-k3", "sk-k4"]
            config = {
                "current_provider": "opencode",
                "providers": {
                    "opencode": {
                        "base_url": "https://opencode.ai/zen",
                        "current_model": "deepseek-v4-flash-free",
                        "api_keys": keys,
                        "rate_limit_status": True,
                        "rate_limit_rpm": 0,
                    }
                },
            }
            (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
            now = time.time()
            cooled = hashlib.sha256(keys[1].encode("utf-8")).hexdigest()[:12]
            state = {
                "opencode:__global__": {
                    "timestamps": [],
                    "rpm": 0,
                    "updated_at": now,
                    "server_remaining": 2,
                    "server_reset_seconds": 38,
                    "server_max_concurrent": 10,
                    "server_active": 9,
                    "server_queue_limit": 15,
                    "server_queued": 14,
                },
                f"opencode:https://opencode.ai/zen:__key__:{cooled}": {
                    "cooldown_until": now + 720,
                    "last_429_at": now,
                },
            }
            (config_dir / "rate-limit-state.json").write_text(json.dumps(state), encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "CIEL_RUNTIME_CONFIG_DIR": tmp,
                    "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                    "CIEL_RUNTIME_PROVIDER": "opencode",
                    "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-test",
                }
            )
            session = {
                "model": {"display_name": "ciel-runtime-test"},
                "workspace": {"current_dir": tmp},
            }
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

        self.assertIn("RL 3/4 next 12m", proc.stdout)
        self.assertIn("server remaining 2, reset 38s", proc.stdout)
        self.assertIn("conc 9/10", proc.stdout)
        self.assertIn("q 14/15", proc.stdout)

    def test_statusline_shows_context_compact_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            config_dir = Path(tmp)
            config = {
                "current_provider": "vllm",
                "providers": {
                    "vllm": {
                        "base_url": "http://localhost:8000",
                        "current_model": "qwen36-35b-a3b-mtp-nvfp4",
                    }
                },
            }
            (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (config_dir / "context-compact-activity.json").write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "event": "compact",
                        "provider": "vllm",
                        "model": "qwen36-35b-a3b-mtp-nvfp4",
                        "chunks": 3,
                        "parallel_sessions": 1,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "CIEL_RUNTIME_CONFIG_DIR": tmp,
                    "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                    "CIEL_RUNTIME_PROVIDER": "vllm",
                    "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-test",
                }
            )
            session = {
                "model": {"display_name": "ciel-runtime-test"},
                "workspace": {"current_dir": tmp},
            }
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

        self.assertIn("compact 3 chunks", proc.stdout)

    def test_statusline_shows_parallel_context_compact_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            config_dir = Path(tmp)
            config = {
                "current_provider": "vllm",
                "providers": {"vllm": {"current_model": "model"}},
            }
            (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (config_dir / "context-compact-activity.json").write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "event": "compact",
                        "provider": "vllm",
                        "model": "model",
                        "chunks": 3,
                        "parallel_sessions": 3,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "CIEL_RUNTIME_CONFIG_DIR": tmp,
                    "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                    "CIEL_RUNTIME_PROVIDER": "vllm",
                    "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-test",
                }
            )
            session = {"model": {"display_name": "ciel-runtime-test"}, "workspace": {"current_dir": tmp}}
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(session),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

        self.assertIn("compact 3 chunks parallel 3/3", proc.stdout)


if __name__ == "__main__":
    unittest.main()
