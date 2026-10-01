from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ciel_runtime_support.codex_app_server_session import (
    REMOTE_TUI_LAUNCH_MODE,
    CodexAppServerSessions,
    CodexBareAppServerSession,
    CodexRemoteTuiPorts,
    CodexRemoteTuiSession,
    remote_tui_command,
)
from ciel_runtime_support.codex_desktop_runtime import CodexDesktopPorts, CodexDesktopSession


class _Proc:
    def __init__(self) -> None:
        self.pid = 4242
        self.alive = True

    def poll(self):
        return None if self.alive else 0


class _Control:
    def __init__(self, requests: list[Any]) -> None:
        self.requests = list(requests)
        self.request = None

    def reset(self) -> None:
        self.request = None

    def incoming(self):
        return None


SERVER_CMD = [
    "codex",
    "app-server",
    "-c",
    'model="deepseek-chat"',
    "--config=model_provider=\"ciel\"",
    "--listen",
    "ws://127.0.0.1:19961",
]


class RemoteTuiCommandTests(unittest.TestCase):
    def test_tui_keeps_the_executable_and_config_overrides(self):
        self.assertEqual(
            ["codex", "-c", 'model="deepseek-chat"', "--config=model_provider=\"ciel\"", "--remote", "ws://127.0.0.1:19961"],
            remote_tui_command(SERVER_CMD, "ws://127.0.0.1:19961"),
        )

    def test_wrapped_executable_prefix_is_kept(self):
        cmd = ["node", "codex.js", "app-server", "--listen", "ws://127.0.0.1:1"]
        self.assertEqual(["node", "codex.js", "--remote", "ws://127.0.0.1:1"], remote_tui_command(cmd, "ws://127.0.0.1:1"))


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs: list[str] = []
        self.popened: list[list[str]] = []
        self.terminated: list[int] = []
        self.server = _Proc()
        self.ready = True

        def popen(cmd, **_kw):
            self.popened.append(list(cmd))
            return self.server

        self.ports = CodexDesktopPorts(
            config_dir=Path(self.tmp.name),
            workspace_id="ws1",
            source_codex_home=Path(self.tmp.name) / "src",
            version="test",
            log=lambda level, message: self.logs.append(f"{level} {message}"),
            terminate_tree=self.terminated.append,
            channel=None,
            popen=popen,
            wait_ready=lambda *_a, **_kw: self.ready,
        )
        self.terminal_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.control = _Control([])
        self.restart_after_first: Any = None

        def run_terminal(cmd, env, **options):
            self.terminal_calls.append((list(cmd), options))
            if self.restart_after_first is not None and len(self.terminal_calls) == 1:
                self.control.request = self.restart_after_first
            return 0

        self.tui_ports = CodexRemoteTuiPorts(
            run_terminal=run_terminal,
            restart_control=lambda: self.control,
            set_transcript_scope=lambda *_a, **_kw: None,
        )

    def test_remote_tui_runs_through_the_proxy_with_only_new_session_typed(self):
        rc = CodexRemoteTuiSession(self.ports, self.tui_ports)(SERVER_CMD, {}, Path(self.tmp.name))
        self.assertEqual(0, rc)
        self.assertEqual([SERVER_CMD], self.popened)
        cmd, options = self.terminal_calls[0]
        self.assertEqual(["codex", "-c", 'model="deepseek-chat"', "--config=model_provider=\"ciel\"", "--remote", "ws://127.0.0.1:19961"], cmd)
        self.assertFalse(options["inject_channel_messages"])
        self.assertEqual("codex", options["session_command_runtime"])
        self.assertEqual(frozenset({"new_session"}), options["session_command_actions"])
        self.assertEqual([4242], self.terminated)

    def test_remote_tui_restart_without_a_followed_thread_starts_fresh(self):
        self.restart_after_first = SimpleNamespace(id="r1", resume=True, source="mcp", reason="")
        CodexRemoteTuiSession(self.ports, self.tui_ports)(SERVER_CMD, {}, Path(self.tmp.name))
        self.assertEqual(2, len(self.terminal_calls))
        self.assertNotIn("resume", self.terminal_calls[1][0])

    def test_remote_tui_needs_a_websocket_listener(self):
        cmd = ["codex", "app-server", "--listen", "stdio://"]
        self.assertEqual(2, CodexRemoteTuiSession(self.ports, self.tui_ports)(cmd, {}, Path(self.tmp.name)))
        self.assertEqual([], self.popened)

    def test_remote_tui_stops_when_the_server_never_gets_ready(self):
        self.ready = False
        self.assertEqual(1, CodexRemoteTuiSession(self.ports, self.tui_ports)(SERVER_CMD, {}, Path(self.tmp.name)))
        self.assertEqual([], self.terminal_calls)
        self.assertEqual([4242], self.terminated)

    def test_bare_server_runs_the_default_process_step(self):
        calls: list[str] = []
        rc = CodexBareAppServerSession(self.ports)(SERVER_CMD, {}, Path(self.tmp.name), run_server=lambda: calls.append("run") or 7)
        self.assertEqual(7, rc)
        self.assertEqual(["run"], calls)
        self.assertEqual([], self.popened)

    def test_bare_server_on_stdio_skips_the_channel_client(self):
        cmd = ["codex", "app-server", "--listen", "stdio://"]
        rc = CodexBareAppServerSession(self.ports)(cmd, {}, Path(self.tmp.name), run_server=lambda: 3)
        self.assertEqual(3, rc)
        self.assertTrue(any("codex_app_server_channel_unavailable" in line for line in self.logs))

    def test_sessions_name_themselves_for_the_launcher(self):
        sessions = CodexAppServerSessions(self.ports, self.tui_ports)
        self.assertEqual("", sessions.server.launch_mode)
        self.assertEqual(REMOTE_TUI_LAUNCH_MODE, sessions.remote_tui.launch_mode)
        self.assertIsInstance(sessions.desktop, CodexDesktopSession)
        self.assertEqual("codex-desktop-router", sessions.desktop.launch_mode)


if __name__ == "__main__":
    unittest.main()
