import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExternalMcpOwnershipTests(unittest.TestCase):
    def test_retired_hijack_modules_are_absent(self):
        names = (
            "channel_mcp_discovery.py",
            "channel_mcp_transport.py",
            "channel_session_lifecycle.py",
            "channel_connection_worker.py",
            "mcp_http_proxy.py",
            "mcp_proxy_process.py",
            "mcp_split_proxy_http.py",
            "mcp_stdio_probe.py",
            "codex_mcp_restore.py",
            "agy_mcp_restore.py",
        )
        support = ROOT / "ciel_runtime_support"
        self.assertEqual([], [name for name in names if (support / name).exists()])

    def test_web_chat_has_no_external_mcp_connection_controls(self):
        source = (ROOT / "ciel_runtime_support" / "chat_http_controller.py").read_text(encoding="utf-8")
        self.assertNotIn("/ca/chat/sse/connect", source)
        self.assertNotIn("/ca/chat/sse/disconnect", source)
        self.assertNotIn("connection_statuses", source)

    def test_runtime_launch_does_not_discover_or_restore_external_mcp(self):
        source = (ROOT / "ciel_runtime_support" / "runtime_launch.py").read_text(encoding="utf-8")
        for token in (
            "channel_probe",
            "auto_start_sse",
            "restore_codex_mcp",
            "restore_agy_mcp",
            "managed_mcp_discovery",
        ):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
