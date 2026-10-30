"""restart_session waits for the running turn, shows notices, and renews the workspace MCP launch."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.channel_terminal_proxy import _runtime_session_restart_requested
from ciel_runtime_support.runtime_session_restart import (
    RUNTIME_SESSION_RESTART_TURN_WAIT_ENV,
    RuntimeSessionRestartControl,
    RuntimeSessionRestartRequest,
)
from ciel_runtime_support.workspace_mcp import (
    WorkspaceMcpLaunch,
    WorkspaceMcpLaunchPorts,
    WorkspaceMcpLaunchService,
    renew_launch_command,
)


def _request(request_id: str = "r1") -> RuntimeSessionRestartRequest:
    now = time.time()
    return RuntimeSessionRestartRequest(
        id=request_id, source="ciel-runtime-router-tool", reason="deploy", runtime="codex",
        target_pid=0, resume=True, requested_at=now, expires_at=now + 60,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TurnWaitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pending = [_request()]
        self.clock = _Clock()
        self.notices: list[str] = []
        self.busy = True
        self.control = RuntimeSessionRestartControl(
            poll=lambda: self.pending.pop() if self.pending else None, clock=self.clock,
        )

    def poll(self):
        return self.control.incoming(busy=lambda: self.busy, notify=self.notices.append)

    def test_request_waits_for_the_running_turn_and_announces_both_steps(self):
        self.assertIsNone(self.poll())
        self.assertEqual(1, len(self.notices))
        self.assertIn("진행 중인 turn이 끝나면 재시작합니다", self.notices[0])
        self.assertEqual([], self.pending)  # claimed at once: the request file's TTL no longer matters
        self.clock.now += 30
        self.assertIsNone(self.poll())
        self.busy = False
        self.clock.now += 1
        request = self.poll()
        self.assertEqual("r1", request.id)
        self.assertTrue(self.control.requested)
        self.assertIn("세션을 재시작합니다", self.notices[-1])
        self.assertIsNone(self.poll())  # once only

    def test_idle_session_restarts_at_once(self):
        self.busy = False
        self.assertEqual("r1", self.poll().id)
        self.assertEqual(1, len(self.notices))
        self.assertIn("세션을 재시작합니다", self.notices[0])

    def test_a_turn_that_never_ends_is_waited_for_only_up_to_the_limit(self):
        with mock.patch.dict(os.environ, {RUNTIME_SESSION_RESTART_TURN_WAIT_ENV: "60"}):
            self.assertIsNone(self.poll())
            self.clock.now += 59
            self.assertIsNone(self.poll())
            self.clock.now += 2
            self.assertEqual("r1", self.poll().id)

    def test_the_sessions_own_turn_signal_wins_over_the_transports(self):
        self.control.busy = lambda: False
        self.assertEqual("r1", self.poll().id)

    def test_without_a_turn_signal_the_restart_is_immediate_as_before(self):
        self.assertEqual("r1", self.control.incoming().id)
        self.assertEqual([], self.notices)

    def test_reset_drops_a_waiting_request(self):
        self.assertIsNone(self.poll())
        self.control.reset()
        self.busy = False
        self.assertIsNone(self.poll())

    def test_console_proxy_passes_its_turn_state_and_notice(self):
        logs: list[str] = []
        args = dict(transport="conpty", pid=1, busy=lambda: self.busy, notify=self.notices.append)
        control = self.control
        self.assertFalse(_runtime_session_restart_requested(lambda *a: logs.append(a), control.incoming, control, **args))
        self.busy = False
        self.clock.now += 1
        self.assertTrue(_runtime_session_restart_requested(lambda *a: logs.append(a), control.incoming, control, **args))
        self.assertEqual(2, len(self.notices))

    def test_submitted_input_protects_idle_restart_beyond_turn_timeout(self):
        from ciel_runtime_support.channel_pending_poll import ChannelPendingPollState

        state = ChannelPendingPollState(last_id=139)
        state.inflight_message_id = 140
        self.busy = False  # Transcript has not observed the just-submitted turn.
        protected = lambda: state.restart_protected
        self.assertIsNone(self.control.incoming(busy=lambda: False, protected=protected))
        self.assertEqual([], self.pending)  # Claim before its short TTL expires.
        self.clock.now += 3600
        self.assertIsNone(self.control.incoming(busy=lambda: False, protected=protected))
        state.inflight_message_id = None  # Exact terminal evidence settled it.
        self.assertEqual("r1", self.control.incoming(busy=lambda: False, protected=protected).id)

    def test_older_submitted_backlog_also_protects_restart(self):
        from ciel_runtime_support.channel_pending_poll import ChannelPendingPollState

        state = ChannelPendingPollState(last_id=139)
        state.inflight.backlog.append(140)
        self.control.busy = lambda: False
        self.assertIsNone(self.control.incoming(protected=lambda: state.restart_protected))
        state.inflight.backlog.clear()
        self.clock.now += 1
        self.assertEqual("r1", self.control.incoming(protected=lambda: state.restart_protected).id)

    def test_unreadable_submission_protection_fails_closed(self):
        def unreadable():
            raise OSError("status unavailable")

        self.assertIsNone(self.control.incoming(busy=lambda: False, protected=unreadable))
        self.clock.now += 3600
        self.assertIsNone(self.control.incoming(busy=lambda: False, protected=unreadable))
        self.assertEqual("r1", self.control.after_child_exit().id)

    def test_console_proxy_passes_submission_protection(self):
        self.busy = False
        args = dict(transport="conpty", pid=1, busy=lambda: False,
                    protected=lambda: True, notify=self.notices.append)
        self.assertFalse(_runtime_session_restart_requested(
            lambda *_a: None, self.control.incoming, self.control, **args))
        self.clock.now += 3600
        self.assertFalse(_runtime_session_restart_requested(
            lambda *_a: None, self.control.incoming, self.control, **args))
        args["protected"] = lambda: False
        self.assertTrue(_runtime_session_restart_requested(
            lambda *_a: None, self.control.incoming, self.control, **args))

    def test_a_foreign_poll_is_called_as_is(self):
        foreign = [_request("x")]
        self.assertTrue(_runtime_session_restart_requested(
            lambda *_a: None, lambda: foreign.pop(), self.control, transport="pty", pid=1, busy=lambda: True,
        ))


class RenewLaunchCommandTests(unittest.TestCase):
    def test_claude_projection_paths_are_swapped_next_to_other_configs(self):
        old = WorkspaceMcpLaunch("a", "claude", Path("d"), claude_config_paths=(Path("old/claude-mcp.json"),))
        new = WorkspaceMcpLaunch("b", "claude", Path("d"), claude_config_paths=(Path("new/claude-mcp.json"),))
        cmd = ["claude", "--mcp-config", "zai.json", str(Path("old/claude-mcp.json")), "--continue"]
        self.assertEqual(
            ["claude", "--mcp-config", "zai.json", str(Path("new/claude-mcp.json")), "--continue"],
            renew_launch_command(cmd, old, new),
        )

    def test_claude_gains_and_loses_the_flag(self):
        new = WorkspaceMcpLaunch("b", "claude", Path("d"), claude_config_paths=(Path("n.json"),))
        self.assertEqual(["claude", "--mcp-config", "n.json", "--", "x"], renew_launch_command(["claude", "--", "x"], WorkspaceMcpLaunch(), new))
        self.assertEqual(["claude", "--continue"], renew_launch_command(["claude", "--mcp-config", "n.json", "--continue"], new, WorkspaceMcpLaunch()))

    def test_codex_overrides_are_swapped_and_added_after_app_server(self):
        old = WorkspaceMcpLaunch("a", "codex", Path("d"), codex_args=("-c", "mcp_servers.a.url=1"))
        new = WorkspaceMcpLaunch("b", "codex", Path("d"), codex_args=("-c", "mcp_servers.a.url=1", "-c", "mcp_servers.b.url=2"))
        cmd = ["codex", "app-server", "-c", "model=x", "-c", "mcp_servers.a.url=1", "--listen", "ws://h"]
        self.assertEqual(
            ["codex", "app-server", "-c", "model=x", "-c", "mcp_servers.a.url=1", "-c", "mcp_servers.b.url=2", "--listen", "ws://h"],
            renew_launch_command(cmd, old, new),
        )
        self.assertEqual(
            ["codex", "app-server", "-c", "mcp_servers.a.url=1", "--listen", "ws://h"],
            renew_launch_command(["codex", "app-server", "--listen", "ws://h"], WorkspaceMcpLaunch(), old),
        )


class RenewLaunchServiceTests(unittest.TestCase):
    def test_renew_makes_a_new_launch_and_finishes_the_old_one(self):
        logs: list[str] = []
        config = {"workspace_mcp": {"servers": {"tools": {"enabled": True, "transport": "stdio", "command": "t", "runtimes": ["codex"]}}}}
        with tempfile.TemporaryDirectory() as directory:
            service = WorkspaceMcpLaunchService(Path(directory), "ws", WorkspaceMcpLaunchPorts(
                pid_running=lambda _pid: True, command_line=lambda _pid: "",
                terminate_tree=lambda *_a, **_k: True, log=lambda _l, m: logs.append(m), current_pid=lambda: 11,
            ))
            first = service.prepare("codex", config, {})
            renewed = service.renew(first, "codex", config, {})
            self.assertNotEqual(first.launch_id, renewed.launch_id)
            self.assertFalse(first.directory.exists())
            manifest = json.loads(renewed.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(["tools"], manifest["server_names"])
            self.assertEqual(first.codex_args, renewed.codex_args)
        self.assertTrue(any(m.startswith("workspace_mcp_launch_renewed") for m in logs))

    def test_renew_keeps_the_old_launch_when_the_new_one_fails(self):
        service = mock.Mock(spec=WorkspaceMcpLaunchService)
        service.prepare.side_effect = OSError("disk full")
        service.ports = mock.Mock()
        old = WorkspaceMcpLaunch("a", "codex", Path("d"))
        self.assertIs(old, WorkspaceMcpLaunchService.renew(service, old, "codex", {}))
        service.finish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
