from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.mcp_proxy_config import McpProxyConfigPaths, McpProxyConfigPorts, McpProxyConfigService


class McpProxyConfigServiceTests(unittest.TestCase):
    def service(self, root: Path, servers):
        config = root / "input.json"
        config.write_text("{}", encoding="utf-8")
        save = mock.Mock()
        service = McpProxyConfigService(
            McpProxyConfigPaths(root / "proxy.json", root / "servers", root / "ciel_runtime.py"),
            McpProxyConfigPorts(
                lambda _args, _cwd, _home: [config],
                lambda _path, _cwd: list(servers),
                lambda server: server.get("type") == "http",
                lambda server: bool(server.get("force")),
                lambda server: server.get("type") == "stdio",
                lambda name: name.replace("/", "_"),
                save,
                mock.Mock(),
            ),
        )
        return service, save

    def test_materializes_stdio_server_and_preserves_direct_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save = self.service(
                Path(tmp),
                [("local", {"type": "stdio", "command": "server"}), ("remote", {"type": "http", "url": "https://mcp"})],
            )
            self.assertEqual(service.paths.output, service.write([]))
        output = save.call_args_list[-1].args[1]["mcpServers"]
        self.assertIn("mcp-proxy", output["local"]["args"])
        self.assertEqual("https://mcp", output["remote"]["url"])
        self.assertEqual("mcp_proxy_server:local", save.call_args_list[0].args[2])

    def test_forced_http_proxy_can_disable_notification_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save = self.service(Path(tmp), [("remote", {"type": "http", "url": "x", "force": True})])
            service.write([], disable_proxy_notification_stream_names={"remote"})
        saved_server = save.call_args_list[0].args[1]
        self.assertTrue(saved_server["ciel_runtime_disable_notification_stream"])

    def test_empty_server_set_does_not_write_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save = self.service(Path(tmp), [])
            self.assertIsNone(service.write([]))
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
