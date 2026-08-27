import io
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.router_server_runtime import (
    RouterServerConfig,
    RouterServerEffects,
    RouterServerRuntime,
    RouterServerStatePorts,
)


class RouterServerRuntimeTests(unittest.TestCase):
    def test_run_starts_dependencies_and_always_cleans_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "runtime" / "router.pid"
            stderr = io.StringIO()
            events = []

            class Server:
                def serve_forever(self):
                    events.append("serve")
                    raise RuntimeError("stop")

            runtime = RouterServerRuntime(
                RouterServerConfig(
                    config_dir=pid_path.parent,
                    pid_path=pid_path,
                    port=8787,
                    client_base="http://127.0.0.1:8787",
                    log_level_path=root / "missing-level",
                    log_level_names={20: "INFO"},
                    handler=object(),
                ),
                RouterServerStatePorts(
                    load_config=lambda: {"provider": "test"},
                    reset_api_key_cooldowns=lambda: events.append("reset"),
                    bind_host=lambda _config: "127.0.0.1",
                    current_log_level=lambda: 20,
                    current_pid=lambda: 123,
                    env_value=lambda name: "debug" if name == "CIEL_RUNTIME_LOG_LEVEL" else None,
                    administrative_external_access_enabled=lambda _config: True,
                    remote_bridge_enabled=lambda _config: True,
                    ensure_administrative_token=lambda: events.append("admin-token") or "admin-token",
                    ensure_bridge_token=lambda: events.append("bridge-token") or "bridge-token",
                ),
                RouterServerEffects(
                    chmod=lambda path, mode: events.append(("chmod", path, mode)),
                    stderr=stderr,
                    server_factory=lambda address, handler: Server(),
                    start_watchdog=lambda server: events.append(("watchdog", server)),
                    configure_web_endpoints=lambda host: events.append(("web", host)) or ["web: http://local/"],
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "stop"):
                runtime.run()

            self.assertFalse(pid_path.exists())
            self.assertIn("source=env", stderr.getvalue())
            self.assertIn("admin-token", events)
            self.assertIn("bridge-token", events)
            self.assertIn("serve", events)

    def test_run_generates_only_tokens_for_enabled_external_modes(self):
        scenarios = (
            ({"remote_bridge": True}, ["bridge-token"]),
            ({"router_admin": True}, ["admin-token"]),
        )
        for config, expected in scenarios:
            with self.subTest(config=config), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events = []

                class Server:
                    def serve_forever(self):
                        raise RuntimeError("stop")

                runtime = RouterServerRuntime(
                    RouterServerConfig(
                        root,
                        root / "router.pid",
                        8787,
                        "http://127.0.0.1:8787",
                        root / "missing-level",
                        {},
                        object(),
                    ),
                    RouterServerStatePorts(
                        load_config=lambda: config,
                        reset_api_key_cooldowns=lambda: None,
                        bind_host=lambda _config: "127.0.0.1",
                        current_log_level=lambda: 20,
                        current_pid=lambda: 123,
                        env_value=lambda _name: None,
                        administrative_external_access_enabled=lambda source: bool(
                            source.get("router_admin")
                        ),
                        remote_bridge_enabled=lambda source: bool(
                            source.get("remote_bridge")
                        ),
                        ensure_administrative_token=lambda: events.append("admin-token") or "",
                        ensure_bridge_token=lambda: events.append("bridge-token") or "",
                    ),
                    RouterServerEffects(
                        chmod=lambda _path, _mode: None,
                        stderr=io.StringIO(),
                        server_factory=lambda _address, _handler: Server(),
                        start_watchdog=lambda _server: None,
                        configure_web_endpoints=lambda _host: [],
                    ),
                )

                with self.assertRaisesRegex(RuntimeError, "stop"):
                    runtime.run()

                self.assertEqual(expected, events)

    def test_log_level_file_takes_precedence_over_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "level"
            path.write_text("INFO")
            runtime = RouterServerRuntime(
                RouterServerConfig(Path(directory), path, 1, "base", path, {}, None),
                RouterServerStatePorts(
                    lambda: {}, lambda: None, lambda _config: "host", lambda: 20,
                    lambda: 1, lambda _name: "debug", lambda _config: False,
                    lambda _config: False, lambda: "", lambda: "",
                ),
                RouterServerEffects(
                    lambda _path, _mode: None, io.StringIO(), lambda *_args: None,
                    lambda _server: None, lambda _host: [],
                ),
            )

            self.assertEqual("file", runtime._log_level_source())


if __name__ == "__main__":
    unittest.main()
