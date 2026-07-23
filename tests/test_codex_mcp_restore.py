import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from ciel_runtime_support.codex_mcp_restore import (
    CodexMcpRestorePorts,
    CodexMcpRestoreService,
)


class CodexMcpRestoreServiceTests(unittest.TestCase):
    def service(
        self,
        path: Path,
        managed: dict,
        logs: list | None = None,
        extra_paths: list[Path] | None = None,
    ):
        records = logs if logs is not None else []
        return CodexMcpRestoreService(
            CodexMcpRestorePorts(
                config_paths=lambda *_args, **_kwargs: [path, *(extra_paths or [])],
                discover_managed=lambda _cwd: managed,
                log=lambda level, message: records.append((level, message)),
            )
        )

    def test_restores_only_missing_servers_and_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / ".codex" / "config.toml"
            path.parent.mkdir()
            original = (
                '# user-owned comment\n'
                '[mcp_servers."shared"]\n'
                'command = "user-command"\n'
            )
            path.write_text(original, encoding="utf-8")
            service = self.service(
                path,
                {
                    "SHARED": {"command": "managed-command"},
                    "alpaca-mcp-server": {
                        "command": "uvx",
                        "args": ["alpaca-mcp-server"],
                        "env": {"ALPACA_API_KEY": "secret"},
                    },
                },
            )

            restored = service.restore(cwd=root)
            text = path.read_text(encoding="utf-8")
            parsed = tomllib.loads(text)

            self.assertEqual(["alpaca-mcp-server"], restored)
            self.assertTrue(text.startswith(original))
            self.assertEqual("user-command", parsed["mcp_servers"]["shared"]["command"])
            self.assertEqual(
                "uvx", parsed["mcp_servers"]["alpaca-mcp-server"]["command"]
            )
            self.assertEqual(
                "secret",
                parsed["mcp_servers"]["alpaca-mcp-server"]["env"]["ALPACA_API_KEY"],
            )
            if os.name != "nt":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_http_aliases_are_projected_to_native_codex_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".codex" / "config.toml"
            service = self.service(
                path,
                {
                    "market.data": {
                        "endpoint": "https://mcp.example.test",
                        "token_env_var": "MARKET_TOKEN",
                        "headers": {"X-Tenant": "desk"},
                    }
                },
            )

            self.assertEqual(["market.data"], service.restore())
            server = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["market.data"]

            self.assertEqual("https://mcp.example.test", server["url"])
            self.assertEqual("MARKET_TOKEN", server["bearer_token_env_var"])
            self.assertEqual("desk", server["http_headers"]["X-Tenant"])

    def test_codex_metadata_and_structured_env_vars_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            service = self.service(
                path,
                {
                    "docs": {
                        "command": "docs-server",
                        "required": True,
                        "startup_timeout_ms": 15000,
                        "env_vars": [
                            "LOCAL_TOKEN",
                            {"name": "REMOTE_TOKEN", "source": "remote"},
                        ],
                        "scopes": ["read:docs"],
                    }
                },
            )

            self.assertEqual(["docs"], service.restore())
            server = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["docs"]

            self.assertTrue(server["required"])
            self.assertEqual(15000, server["startup_timeout_ms"])
            self.assertEqual("REMOTE_TOKEN", server["env_vars"][1]["name"])
            self.assertEqual(["read:docs"], server["scopes"])

    def test_restore_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".codex" / "config.toml"
            service = self.service(path, {"local": {"command": "server"}})

            self.assertEqual(["local"], service.restore())
            first = path.read_text(encoding="utf-8")
            self.assertEqual([], service.restore())

            self.assertEqual(first, path.read_text(encoding="utf-8"))

    def test_project_or_profile_server_prevents_home_level_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home_config = root / ".codex" / "config.toml"
            project_config = root / "project" / ".codex" / "config.toml"
            project_config.parent.mkdir(parents=True)
            project_config.write_text(
                '[mcp_servers."alpaca"]\ncommand = "project-owned"\n',
                encoding="utf-8",
            )
            service = self.service(
                home_config,
                {"ALPACA": {"command": "managed"}},
                extra_paths=[project_config],
            )

            self.assertEqual([], service.restore(cwd=root / "project"))

            self.assertFalse(home_config.exists())

    def test_invalid_user_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("[broken", encoding="utf-8")
            logs = []
            service = self.service(path, {"local": {"command": "server"}}, logs)

            self.assertEqual([], service.restore())

            self.assertEqual("[broken", path.read_text(encoding="utf-8"))
            self.assertIn("codex_mcp_restore_config_read_failed", logs[-1][1])

    def test_proxy_wrapper_is_not_written_back_into_codex_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            logs = []
            service = self.service(
                path,
                {
                    "loop": {
                        "command": "python3",
                        "args": [
                            "ciel_runtime.py",
                            "mcp-proxy",
                            "--server-config",
                            "wrapped.json",
                        ],
                    }
                },
                logs,
            )

            self.assertEqual([], service.restore())

            self.assertFalse(path.exists())
            self.assertIn("codex_mcp_restore_skipped_unsupported", logs[-1][1])

    def test_legacy_sse_server_is_not_restored_as_streamable_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            service = self.service(
                path,
                {"legacy": {"type": "sse", "url": "https://example.test/sse"}},
            )

            self.assertEqual([], service.restore())

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
