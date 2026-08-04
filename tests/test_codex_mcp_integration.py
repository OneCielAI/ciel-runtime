import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.codex_mcp_integration import (
    CodexMcpArtifactPorts,
    CodexMcpCapabilityPorts,
    CodexMcpConfigPorts,
    CodexMcpIntegrationService,
    CodexMcpPolicy,
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
            policy=CodexMcpPolicy(
                native_channel_names=frozenset({"ciel-runtime"}),
                builtin_channel_url=lambda: "http://router/ca/mcp",
            ),
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

    def test_channel_owned_http_aliases_share_one_notification_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "codex-mcp.json"
            path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "ai-net": {"type": "http", "url": "https://mcp.example/mcp"},
                            "ai-net-http": {
                                "type": "http",
                                "url": "https://mcp.example/mcp/",
                            },
                            "other": {"type": "http", "url": "https://other.example/mcp"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            service = self.service(root)

            args = service.native_http_compat_args(
                path, channel_owned_server_names=["ai-net"]
            )

            self.assertEqual(
                [
                    "-c",
                    'mcp_servers.ai-net.url="http://router/ai-net"',
                    "-c",
                    'mcp_servers.ai-net-http.url="http://router/ai-net-http"',
                ],
                args,
            )

    def test_channel_owned_aliases_do_not_merge_different_authentication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "codex-mcp.json"
            path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "account-a": {
                                "type": "http",
                                "url": "https://mcp.example/mcp",
                                "headers": {"Authorization": "Bearer a"},
                            },
                            "account-b": {
                                "type": "http",
                                "url": "https://mcp.example/mcp",
                                "headers": {"Authorization": "Bearer b"},
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            service = self.service(root)

            args = service.native_http_compat_args(
                path, channel_owned_server_names=["account-a"]
            )

            self.assertEqual(
                ["-c", 'mcp_servers.account-a.url="http://router/account-a"'], args
            )

    def test_builtin_channel_can_be_injected_without_discovered_servers(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = self.service(Path(temporary))

            args = service.native_http_compat_args(
                None, include_builtin_channel=True
            )

            self.assertEqual(
                [
                    "-c",
                    'mcp_servers.ciel-runtime-router.url="http://router/ca/mcp"',
                ],
                args,
            )


if __name__ == "__main__":
    unittest.main()
