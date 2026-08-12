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
                log,
            ),
        )
        return service, save, log

    def test_web_tools_prefers_uv_runner_and_projects_fetch_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save, _log = self.service(
                Path(tmp), executables={"npx": "npx", "uv": "uv"}
            )
            path = service.write_web_tools(
                {"web_search": {"fetch_enabled": True, "fetch_user_agent": "Ciel", "fetch_ignore_robots_txt": True}}
            )
        payload = save.call_args.args[1]
        self.assertEqual(service.paths.web_tools, path)
        self.assertEqual("uv", payload["mcpServers"]["web_fetch"]["command"])
        self.assertEqual(
            [
                "tool", "run", "--with", "mcp<2", "mcp-server-fetch",
                "--user-agent", "Ciel", "--ignore-robots-txt",
            ],
            payload["mcpServers"]["web_fetch"]["args"],
        )
        self.assertIn("--ignore-robots-txt", payload["mcpServers"]["web_fetch"]["args"])

    def test_zai_config_contains_stdio_and_authenticated_http_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save, _log = self.service(Path(tmp), executables={"npx": "npx"})
            self.assertEqual(service.paths.zai, service.write_zai("zai", {"managed_mcp": True}))
        servers = save.call_args.args[1]["mcpServers"]
        self.assertEqual("secret", servers["zai-mcp-server"]["env"]["Z_AI_API_KEY"])
        self.assertEqual("Bearer secret", servers["search"]["headers"]["Authorization"])

    def test_channel_config_uses_stateless_http_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, save, _log = self.service(Path(tmp))
            self.assertEqual(service.paths.channel, service.write_channel())
        server = save.call_args.args[1]["mcpServers"]["ciel-runtime-router"]
        self.assertEqual("http", server["type"])
        self.assertTrue(server["url"].endswith("/ca/mcp"))


if __name__ == "__main__":
    unittest.main()
