import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.managed_mcp_discovery import (
    ManagedMcpDiscoveryPaths,
    ManagedMcpDiscoveryPorts,
    ManagedMcpDiscoveryService,
    NativeMcpConfigWriter,
    NativeMcpConfigWriterPorts,
)


class ManagedMcpDiscoveryServiceTests(unittest.TestCase):
    def test_native_writer_prefers_user_server_and_persists_normalized_shape(self):
        saved = []
        logs = []
        path = Path("native-mcp.json")
        writer = NativeMcpConfigWriter(
            path,
            NativeMcpConfigWriterPorts(
                discover_user=lambda _args, _cwd, _home: {
                    "shared": {"command": "user"}
                },
                discover_managed=lambda _cwd: {
                    "shared": {"command": "managed"},
                    "generated": {"command": "generated"},
                },
                save_json=lambda target, data, operation: saved.append(
                    (target, data, operation)
                ),
                log=lambda level, message: logs.append((level, message)),
            ),
        )

        written = writer.write([], Path("work"), Path("home"))

        self.assertEqual(path, written)
        self.assertEqual("user", saved[0][1]["mcpServers"]["shared"]["command"])
        self.assertIn("generated", saved[0][1]["mcpServers"])
        self.assertTrue(any("duplicate_skipped" in message for _level, message in logs))

    def test_native_writer_skips_empty_artifact(self):
        writer = NativeMcpConfigWriter(
            Path("native-mcp.json"),
            NativeMcpConfigWriterPorts(
                discover_user=lambda _args, _cwd, _home: {},
                discover_managed=lambda _cwd: {},
                save_json=lambda *_args: self.fail("empty discovery must not persist"),
                log=lambda _level, _message: None,
            ),
        )

        self.assertIsNone(writer.write())

    def test_restores_direct_and_wrapped_servers_without_native_bridge(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            proxy = root / "proxy.json"
            wrapped = root / "wrapped.json"
            proxy.write_text("{}", encoding="utf-8")
            loaded = {
                proxy: {
                    "mcpServers": {
                        "direct": {"command": "direct"},
                        "ciel-runtime-router": {"command": "skip"},
                        "proxy-entry": {
                            "command": "ciel-runtime",
                            "args": [
                                "mcp-proxy",
                                "--server-name",
                                "restored",
                                "--server-config",
                                str(wrapped),
                            ],
                        },
                    }
                },
                wrapped: {
                    "command": "wrapped",
                    "ciel_runtime_disable_notification_stream": True,
                },
            }
            service = ManagedMcpDiscoveryService(
                paths=ManagedMcpDiscoveryPaths(
                    web_tools=root / "web.json",
                    proxy=proxy,
                ),
                ports=ManagedMcpDiscoveryPorts(
                    read_generated=lambda _path, _cwd: {
                        "web": {"command": "web"}
                    },
                    load_json=lambda path: loaded[path],
                    log=lambda _level, _message: None,
                ),
                native_channel_names=frozenset({"ciel-runtime-router"}),
            )

            servers = service.discover(root)

        self.assertEqual({"web", "direct", "restored"}, set(servers))
        self.assertEqual("wrapped", servers["restored"]["command"])
        self.assertNotIn(
            "ciel_runtime_disable_notification_stream",
            servers["restored"],
        )

    def test_broken_proxy_artifact_logs_warning_and_keeps_generated_servers(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            proxy = root / "proxy.json"
            proxy.write_text("{", encoding="utf-8")
            logs = []
            service = ManagedMcpDiscoveryService(
                paths=ManagedMcpDiscoveryPaths(
                    web_tools=root / "web.json",
                    proxy=proxy,
                ),
                ports=ManagedMcpDiscoveryPorts(
                    read_generated=lambda _path, _cwd: {
                        "web": {"command": "web"}
                    },
                    load_json=lambda _path: (_ for _ in ()).throw(
                        json.JSONDecodeError("bad", "{", 0)
                    ),
                    log=lambda level, message: logs.append(
                        (level, message)
                    ),
                ),
                native_channel_names=frozenset(),
            )

            servers = service.discover(root)

        self.assertEqual({"web": {"command": "web"}}, servers)
        self.assertEqual("WARN", logs[-1][0])
        self.assertIn("managed_mcp_proxy_config_read_failed", logs[-1][1])


if __name__ == "__main__":
    unittest.main()
