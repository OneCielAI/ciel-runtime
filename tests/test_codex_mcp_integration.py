import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.codex_mcp_integration import (
    CodexMcpArtifactPorts,
    CodexMcpCapabilityPorts,
    CodexMcpConfigPorts,
    CodexMcpIntegrationService,
    CodexMcpProjectionPorts,
)


class CodexMcpIntegrationServiceTests(unittest.TestCase):
    def service(
        self,
        root: Path,
        *,
        discovered: dict | None = None,
        probes: list[dict] | None = None,
    ) -> CodexMcpIntegrationService:
        config_path = root / "codex-mcp.json"

        def save_json(path, payload, _label):
            path.write_text(json.dumps(payload), encoding="utf-8")

        return CodexMcpIntegrationService(
            config=CodexMcpConfigPorts(
                discover=lambda *_args, **_kwargs: discovered or {},
                log=lambda _level, _message: None,
            ),
            artifact=CodexMcpArtifactPorts(
                config_path=lambda: config_path,
                save_json=save_json,
                unlink=lambda path: path.unlink(),
                load_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
            ),
            capability=CodexMcpCapabilityPorts(
                ensure_probe_cache=lambda *_args, **_kwargs: None,
                read_servers=lambda _path, _cwd: [{"channel": "ai-net"}],
                cached_probe_servers=lambda: probes or [],
                path_key=lambda path: str(path.resolve()),
                cwd=lambda: root,
            ),
            projection=CodexMcpProjectionPorts(
                dedupe_strings=lambda values: list(dict.fromkeys(values)),
                public_name=lambda name: name.removeprefix("ciel-"),
                is_streamable_http=lambda server: server.get("type") == "http",
                split_proxy_url=lambda name: f"http://router/{name}",
                toml_string=lambda value: json.dumps(value),
            ),
            native_channel_names=frozenset({"ciel-runtime"}),
        )

    def test_discovery_config_is_persisted_through_repository_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = self.service(
                root,
                discovered={"ai-net": {"type": "http", "url": "https://mcp"}},
            )

            path = service.write_discovery_config([])

            self.assertEqual(root / "codex-mcp.json", path)
            self.assertEqual(["ai-net"], list(json.loads(path.read_text())["mcpServers"]))

    def test_channel_capability_is_scoped_to_the_generated_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "codex-mcp.json"
            path.write_text("{}", encoding="utf-8")
            service = self.service(
                root,
                probes=[
                    {
                        "name": "ai-net",
                        "capable": True,
                        "source_path": str(path),
                    }
                ],
            )

            self.assertEqual(
                ["ai-net"], service.channel_capable_server_names({}, path)
            )

    def test_http_servers_can_be_projected_to_split_proxy_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "codex-mcp.json"
            path.write_text(
                json.dumps({"mcpServers": {"ai-net": {"type": "http"}}}),
                encoding="utf-8",
            )
            service = self.service(root)

            args = service.native_http_compat_args(path, split_http_proxy=True)

            self.assertEqual(
                ["-c", 'mcp_servers.ai-net.url="http://router/ai-net"'], args
            )


if __name__ == "__main__":
    unittest.main()
