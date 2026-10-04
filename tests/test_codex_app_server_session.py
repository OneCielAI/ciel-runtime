from __future__ import annotations

import dataclasses
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ciel_runtime_support.codex_app_server_session import (
    REMOTE_TOKEN_ENV,
    REMOTE_TUI_LAUNCH_MODE,
    CodexAppServerSessions,
    CodexBareAppServerSession,
    CodexRemoteTuiPorts,
    CodexRemoteTuiSession,
    RemoteTuiResume,
    remote_server_command,
    remote_tui_command,
    split_remote_tui_passthrough,
)
from ciel_runtime_support.codex_desktop_injection import CodexDesktopChannelPorts
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

    def test_server_gets_full_access_and_a_token_the_tui_does_not_repeat(self):
        server = remote_server_command(SERVER_CMD, Path("C:/cfg/ws-token"))
        self.assertEqual(
            ["codex", "app-server", "-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"'],
            server[:6],
        )
        self.assertEqual(["--ws-auth", "capability-token", "--ws-token-file", str(Path("C:/cfg/ws-token"))], server[-4:])
        tui = remote_tui_command(server, "ws://127.0.0.1:19961")
        # codex 0.160.0 refuses permission overrides from a TUI resuming a remote thread.
        self.assertFalse(any("approval_policy" in arg or "sandbox_mode" in arg for arg in tui))
        self.assertEqual(["--remote-auth-token-env", REMOTE_TOKEN_ENV], tui[-2:])

    def test_an_explicit_ws_auth_is_left_alone(self):
        cmd = [*SERVER_CMD, "--ws-auth", "signed-bearer-token"]
        self.assertEqual(1, remote_server_command(cmd, Path("t")).count("--ws-auth"))


class SplitPassthroughTests(unittest.TestCase):
    def test_no_resume_is_a_new_conversation(self):
        self.assertEqual((["-c", "x=1"], RemoteTuiResume()), split_remote_tui_passthrough(["-c", "x=1", "--yolo"]))

    def test_continue_and_resume_forms(self):
        self.assertEqual(RemoteTuiResume("last"), split_remote_tui_passthrough(["--continue"])[1])
        self.assertEqual(RemoteTuiResume("last"), split_remote_tui_passthrough(["resume", "--last"])[1])
        self.assertEqual(RemoteTuiResume("id", "abc"), split_remote_tui_passthrough(["resume", "abc"])[1])
        self.assertEqual(RemoteTuiResume("id", "abc"), split_remote_tui_passthrough(["--resume", "abc"])[1])
        self.assertEqual(RemoteTuiResume("pick"), split_remote_tui_passthrough(["resume"])[1])
        self.assertEqual(RemoteTuiResume("pick", picker_args=("--all",)), split_remote_tui_passthrough(["resume", "--all"])[1])

    def test_resume_never_reaches_the_server_command(self):
        server, _ = split_remote_tui_passthrough(["-c", "x=1", "resume", "abc"])
        self.assertEqual(["-c", "x=1"], server)
        self.assertEqual(
            (["-c", "x=1"], {"resume": RemoteTuiResume("id", "abc")}),
            CodexRemoteTuiSession.split_passthrough(["-c", "x=1", "resume", "abc"]),
        )


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

        self.scopes: list[dict[str, Any]] = []
        self.terminal_envs: list[dict[str, str]] = []
        self.token_seen: list[str] = []

        def run_terminal(cmd, env, **options):
            self.terminal_calls.append((list(cmd), options))
            self.terminal_envs.append(dict(env))
            token_file = Path(self.tmp.name) / "codex-remote" / "ws1" / "ws-token"
            if token_file.exists():
                self.token_seen.append(token_file.read_text(encoding="utf-8"))
            if self.restart_after_first is not None and len(self.terminal_calls) == 1:
                self.control.request = self.restart_after_first
            return 0

        self.tui_ports = CodexRemoteTuiPorts(
            run_terminal=run_terminal,
            restart_control=lambda: self.control,
            set_transcript_scope=lambda *_a, **kw: self.scopes.append(kw),
        )

    def test_remote_tui_runs_through_the_proxy_with_only_new_session_typed(self):
        rc = CodexRemoteTuiSession(self.ports, self.tui_ports)(SERVER_CMD, {}, Path(self.tmp.name))
        self.assertEqual(0, rc)
        token_file = Path(self.tmp.name) / "codex-remote" / "ws1" / "ws-token"
        self.assertEqual([remote_server_command(SERVER_CMD, token_file)], self.popened)
        cmd, options = self.terminal_calls[0]
        self.assertEqual(
            [
                "codex", "-c", 'model="deepseek-chat"', "--config=model_provider=\"ciel\"",
                "--remote", "ws://127.0.0.1:19961", "--remote-auth-token-env", REMOTE_TOKEN_ENV,
            ],
            cmd,
        )
        self.assertFalse(options["inject_channel_messages"])
        self.assertEqual("codex", options["session_command_runtime"])
        self.assertEqual(frozenset({"new_session"}), options["session_command_actions"])
        self.assertEqual([4242], self.terminated)
        self.assertIsNone(self.scopes[0]["session_id"])

    def test_remote_tui_gets_the_launch_token_and_the_file_is_removed_afterwards(self):
        CodexRemoteTuiSession(self.ports, self.tui_ports)(SERVER_CMD, {"A": "1"}, Path(self.tmp.name))
        env = self.terminal_envs[0]
        self.assertEqual("1", env["A"])
        self.assertGreaterEqual(len(env[REMOTE_TOKEN_ENV]), 32)
        self.assertEqual(env[REMOTE_TOKEN_ENV], self.token_seen[0])
        self.assertFalse((Path(self.tmp.name) / "codex-remote" / "ws1" / "ws-token").exists())

    def test_remote_tui_resumes_the_requested_conversation(self):
        rc = CodexRemoteTuiSession(self.ports, self.tui_ports)(
            SERVER_CMD, {}, Path(self.tmp.name), resume=RemoteTuiResume("id", "01a0-saved")
        )
        self.assertEqual(0, rc)
        self.assertEqual(["resume", "01a0-saved"], self.terminal_calls[0][0][-2:])
        self.assertNotIn("--yolo", self.terminal_calls[0][0])
        # The transcript is found by id even if it began in another folder.
        self.assertEqual("01a0-saved", self.scopes[0]["session_id"])

    def test_continue_picks_the_latest_conversation_of_the_folder(self):
        calls: list[dict[str, Any]] = []

        def select(env, **kw):
            calls.append(kw)
            return "latest-1"

        tui = dataclasses.replace(self.tui_ports, select_resume=select)
        CodexRemoteTuiSession(self.ports, tui)(SERVER_CMD, {}, Path(self.tmp.name), resume=RemoteTuiResume("last"))
        self.assertTrue(calls[0]["select_latest"])
        self.assertEqual(Path(self.tmp.name), calls[0]["cwd"])
        self.assertEqual(["resume", "latest-1"], self.terminal_calls[0][0][-2:])

    def test_nothing_to_resume_ends_before_the_server_starts(self):
        tui = dataclasses.replace(self.tui_ports, select_resume=lambda env, **kw: None)
        rc = CodexRemoteTuiSession(self.ports, tui)(SERVER_CMD, {}, Path(self.tmp.name), resume=RemoteTuiResume("pick"))
        self.assertEqual(0, rc)
        self.assertEqual([], self.popened)
        self.assertEqual([], self.terminal_calls)

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

    def test_bare_server_exit_ends_the_clients_readiness_wait(self):
        waits: list[str] = []

        def wait_ready(_url, alive=lambda: True):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if not alive():
                    waits.append("server-gone")
                    return False
                time.sleep(0.01)
            waits.append("timed-out")
            return False

        ports = dataclasses.replace(
            self.ports,
            wait_ready=wait_ready,
            channel=CodexDesktopChannelPorts(
                read_messages=lambda _last, _limit: [],
                read_cursor=lambda: 0,
                commit_cursor=lambda _id: None,
                status=None,
                log=lambda level, message: self.logs.append(f"{level} {message}"),
            ),
        )
        started = time.monotonic()
        rc = CodexBareAppServerSession(ports)(SERVER_CMD, {}, Path(self.tmp.name), run_server=lambda: 0)

        self.assertEqual(0, rc)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(["server-gone"], waits)

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
