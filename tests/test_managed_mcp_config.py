from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.managed_mcp_config import (
    ManagedMcpConfigPaths,
    ManagedMcpConfigPolicy,
    ManagedMcpConfigPorts,
    ManagedMcpConfigService,
)


class ManagedMcpConfigServiceTests(unittest.TestCase):
    def service(self, root: Path, *, executables=None, key="secret"):
        executables = executables or {}
        save = mock.Mock()
        initialize = mock.Mock()
        log = mock.Mock()
        service = ManagedMcpConfigService(
            ManagedMcpConfigPaths(
                root / "web.json",
                root / "duck.json",
                root / "zai.json",
                root / "channel.json",
            ),
            ManagedMcpConfigPolicy("http://127.0.0.1:8787", (("search", "https://z.ai/search"),)),
            ManagedMcpConfigPorts(
                lambda name: executables.get(name),
                save,
                lambda _provider, _config: key,
                lambda value: bool(value),
                initialize,
                log,
            ),
        )
        return service, save, initialize, log

    def test_web_tools_prefers_uv_runner_and_projects_fetch_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save, _initialize, _log = self.service(
                Path(tmp), executables={"npx": "npx", "uv": "uv"}
            )
            path = service.write_web_tools(
                {"web_search": {"fetch_enabled": True, "fetch_user_agent": "Ciel", "fetch_ignore_robots_txt": True}}
            )
        payload = save.call_args.args[1]
        self.assertEqual(service.paths.web_tools, path)
        self.assertEqual("uv", payload["mcpServers"]["web_fetch"]["command"])
        self.assertIn("--ignore-robots-txt", payload["mcpServers"]["web_fetch"]["args"])

    def test_zai_config_contains_stdio_and_authenticated_http_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save, _initialize, _log = self.service(Path(tmp), executables={"npx": "npx"})
            self.assertEqual(service.paths.zai, service.write_zai("zai", {"managed_mcp": True}))
        servers = save.call_args.args[1]["mcpServers"]
        self.assertEqual("secret", servers["zai-mcp-server"]["env"]["Z_AI_API_KEY"])
        self.assertEqual("Bearer secret", servers["search"]["headers"]["Authorization"])

    def test_channel_config_initializes_cursor_after_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save, initialize, _log = self.service(Path(tmp))
            self.assertEqual(service.paths.channel, service.write_channel())
        self.assertIn("/ca/mcp/sse", save.call_args.args[1]["mcpServers"]["ciel-runtime-router"]["url"])
        initialize.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
