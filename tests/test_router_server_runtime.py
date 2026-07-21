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

            class ImmediateThread:
                def __init__(self, *, target, daemon, name):
                    events.append(("thread", daemon, name))
                    self.target = target

                def start(self):
                    self.target()

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
                ),
                RouterServerEffects(
                    chmod=lambda path, mode: events.append(("chmod", path, mode)),
                    stderr=stderr,
                    server_factory=lambda address, handler: Server(),
                    start_watchdog=lambda server: events.append(("watchdog", server)),
                    start_channels=lambda config: events.append(("channels", config)),
                    stop_channels=lambda name: events.append(("stop", name)),
                    thread_factory=ImmediateThread,
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "stop"):
                runtime.run()

            self.assertFalse(pid_path.exists())
            self.assertIn("source=env", stderr.getvalue())
            self.assertIn(("stop", None), events)
            self.assertIn("serve", events)

    def test_log_level_file_takes_precedence_over_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "level"
            path.write_text("INFO")
            runtime = RouterServerRuntime(
                RouterServerConfig(Path(directory), path, 1, "base", path, {}, None),
                RouterServerStatePorts(
                    lambda: {}, lambda: None, lambda _config: "host", lambda: 20,
                    lambda: 1, lambda _name: "debug",
                ),
                RouterServerEffects(
                    lambda _path, _mode: None, io.StringIO(), lambda *_args: None,
                    lambda _server: None, lambda _config: None, lambda _name: None,
                    lambda **_kwargs: None,
                ),
            )

            self.assertEqual("file", runtime._log_level_source())


if __name__ == "__main__":
    unittest.main()
