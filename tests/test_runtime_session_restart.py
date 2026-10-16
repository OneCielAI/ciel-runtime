"""CLI session restart requests: repository, resume commands, transports, MCP tool."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support import channel_mcp_tools, runtime_launch
from ciel_runtime_support.channel_terminal_proxy import _runtime_session_restart_requested
from ciel_runtime_support.channel_terminal_dispatch import (
    ChannelDirectProcessPorts,
    ChannelTerminalDispatchService,
    ChannelTerminalDispatchSettings,
    ChannelTerminalProxyPorts,
)
from ciel_runtime_support.runtime_session_restart import (
    RESTART_REQUEST_FILE_NAME,
    RuntimeSessionRestartControl,
    RuntimeSessionRestartRepository,
    RuntimeSessionRestartRequest,
    RuntimeSessionRestartService,
    RuntimeSessionRestartServicePorts,
    restart_notice_body,
    runtime_resume_command,
    runtime_session_control_present,
)


def _claude_services(launched, control, capture, notice=None, transport=None, router_log=None):
    """Stubbed run_claude services; ``transport`` replaces the stubbed console proxy call."""

    def no_op(*_args, **_kwargs):
        return None

    def has_option(args, *options):
        return any(
            str(value) == option or str(value).startswith(option + "=")
            for value in args
            for option in options
        )

    def materialize(_runtime, executable, env, _provider, _config, **kwargs):
        return [executable, *kwargs["options"]["extra_args"]], env

    def call_with_wake(command, _env, **kwargs):
        launched.append(list(command))
        capture.append({"restart_poll": kwargs.get("restart_poll") is not None})
        state = kwargs.get("restart_state")
        poll = kwargs.get("restart_poll")
        if poll is not None and state is not None:
            claimed = poll()
            if claimed is not None:
                state.mark(claimed)
        return 7

    return runtime_launch.ClaudeLaunchServices(
        constants=runtime_launch.build_default_claude_launch_constants(),
        process=runtime_launch.ClaudeLaunchProcess(
            no_op,
            lambda *_args, **_kwargs: 0,
            lambda *_args, **_kwargs: False,
            lambda _config: {"CIEL_RUNTIME_MODEL_ALIAS": "test-model"},
            lambda _path: 0,
            lambda env: env.get("PATH", ""),
            no_op,
            transport or call_with_wake,
            lambda *_args, **_kwargs: 0,
        ),
        installation=runtime_launch.ClaudeLaunchInstallation(
            lambda _name: "claude",
            no_op,
            no_op,
            lambda: "claude",
            no_op,
            no_op,
            lambda _config: [],
            no_op,
        ),
        dispatch=runtime_launch.ClaudeLaunchDispatch(
            lambda *_args, **_kwargs: 0,
            lambda *_args, **_kwargs: 0,
            lambda *_args, **_kwargs: 0,
            materialize,
            no_op,
            lambda executable, enabled=True: executable,
            lambda *_args, **_kwargs: 0,
            lambda _provider: True,
        ),
        config=runtime_launch.ClaudeLaunchConfig(
            lambda: {"provider": "test"},
            no_op,
            lambda _config: ("test", {"current_model": "test-model"}),
            lambda _provider, _config: (True, []),
            no_op,
            lambda _config, _runtime: [],
            lambda provider, _config: provider,
            lambda *_args: "routed",
            lambda: "cwd-key",
        ),
        routing=runtime_launch.ClaudeLaunchRouting(
            lambda *_args: False,
            lambda *_args: False,
            no_op,
            lambda: True,
            no_op,
            lambda: "healthy",
            router_log or no_op,
            lambda **_kwargs: False,
            lambda callback, _managed: callback(),
            no_op,
        ),
        policy=runtime_launch.ClaudeLaunchPolicy(
            no_op,
            lambda _executable: False,
            lambda _args: False,
            has_option,
            lambda *_args: False,
            lambda *_args: False,
            lambda *_args: False,
            lambda *_args: (False, ""),
            lambda *_args: False,
        ),
        channel_delivery=runtime_launch.ClaudeLaunchChannelDelivery(
            lambda *_args: True,
            lambda *_args: True,
            lambda: 0.0,
            lambda: 1,
            no_op,
            lambda _config, _args: "",
            no_op,
        ),
        mcp_config=runtime_launch.ClaudeLaunchMcpConfig(no_op, no_op),
        restart=runtime_launch.SessionRestartPorts(lambda: control, notice or (lambda _body: None)),
    )


class ClaudeSessionRestartLoopTests(unittest.TestCase):
    """Drive the real run_claude loop with a restart requested mid-session."""

    def claude_services(self, launched, control, capture, notice=None, transport=None, router_log=None):
        return _claude_services(
            launched, control, capture, notice=notice, transport=transport, router_log=router_log
        )

    def test_restart_request_relaunches_the_cli_with_continue(self):
        launched: list[list[str]] = []
        capture: list[dict] = []
        request = RuntimeSessionRestartRequest(
            id="restart-1",
            source="ciel-runtime-router-tool",
            reason="deploy",
            runtime="claude",
            target_pid=os.getpid(),
            resume=True,
            requested_at=time.time(),
            expires_at=time.time() + 60,
        )
        control = RuntimeSessionRestartControl(poll=lambda: request)
        services = self.claude_services(launched, control, capture)

        result = runtime_launch.run_claude(
            [],
            skip_menu=True,
            update_check=False,
            self_update_check=False,
            services=services,
        )

        # The last child's exit code is returned once the restart loop settles.
        self.assertEqual(7, result)
        self.assertEqual(2, len(launched))
        self.assertTrue(capture[0]["restart_poll"])
        self.assertNotIn("--continue", launched[0])
        self.assertEqual("--continue", launched[1][-1])
        self.assertTrue(control.requested)

    def test_restart_queues_the_completion_notice_for_the_resumed_session(self):
        launched: list[list[str]] = []
        notices: list[dict] = []
        request = RuntimeSessionRestartRequest(
            id="restart-notice",
            source="ciel-runtime-router-tool",
            reason="deploy",
            runtime="claude",
            target_pid=os.getpid(),
            resume=True,
            requested_at=time.time(),
            expires_at=time.time() + 60,
        )
        control = RuntimeSessionRestartControl(poll=lambda: request)
        services = self.claude_services(
            launched, control, [], notice=lambda body: notices.append(body) or {"id": 41}
        )

        runtime_launch.run_claude(
            [],
            skip_menu=True,
            update_check=False,
            self_update_check=False,
            services=services,
        )

        # One notice per honoured restart, queued before the relaunch so the
        # resumed session's own proxy injects it after the startup grace.
        self.assertEqual(1, len(notices))
        notice = notices[0]
        self.assertEqual("restart", notice["channel"])
        self.assertEqual("계속-세션재시작됨", notice["message"])
        self.assertTrue(notice["meta"]["raw_injection"])
        self.assertEqual("claude", notice["meta"]["runtime"])
        self.assertEqual("ciel-runtime-router-tool", notice["meta"]["restart_source"])
        self.assertEqual("restart_notice", notice["meta"]["source_kind"])
        self.assertTrue(notice["meta"]["resumed"])

    def test_restart_renews_the_workspace_mcp_launch(self):
        import dataclasses

        from ciel_runtime_support.workspace_mcp import WorkspaceMcpLaunchPorts, WorkspaceMcpLaunchService

        launched: list[list[str]] = []
        request = RuntimeSessionRestartRequest(
            id="restart-mcp", source="ciel-runtime-router-tool", reason="mcp", runtime="claude",
            target_pid=os.getpid(), resume=True, requested_at=time.time(), expires_at=time.time() + 60,
        )
        control = RuntimeSessionRestartControl(poll=lambda: request)
        with tempfile.TemporaryDirectory() as directory:
            workspace_mcp = WorkspaceMcpLaunchService(Path(directory), "ws", WorkspaceMcpLaunchPorts(
                pid_running=lambda _pid: True, command_line=lambda _pid: "",
                terminate_tree=lambda *_a, **_k: True, log=lambda *_a: None,
            ))
            services = self.claude_services(launched, control, [])
            services = dataclasses.replace(
                services,
                config=dataclasses.replace(services.config, load_config=lambda: {"workspace_mcp": {"servers": {
                    "tools": {"enabled": True, "transport": "stdio", "command": "t", "runtimes": ["claude"]}}}}),
                mcp_config=dataclasses.replace(services.mcp_config, workspace_mcp=workspace_mcp),
            )
            runtime_launch.run_claude([], skip_menu=True, update_check=False, self_update_check=False, services=services)
            left = sorted(path.name for path in Path(directory).iterdir())

        paths = [command[command.index("--mcp-config") + 1] for command in launched]
        self.assertEqual(2, len(paths))
        self.assertNotEqual(Path(paths[0]).parent.name, Path(paths[1]).parent.name)
        self.assertEqual([], left)  # both launches finished: the first at the restart, the second at exit

    def test_launch_without_a_request_runs_once(self):
        launched: list[list[str]] = []
        control = RuntimeSessionRestartControl(poll=lambda: None)
        services = self.claude_services(launched, control, [])

        result = runtime_launch.run_claude(
            [],
            skip_menu=True,
            update_check=False,
            self_update_check=False,
            services=services,
        )

        self.assertEqual(7, result)
        self.assertEqual(1, len(launched))
        self.assertFalse(control.requested)


class RestartNoticeBodyTests(unittest.TestCase):
    def test_notice_is_the_exact_continue_message_with_the_restart_in_meta(self):
        body = restart_notice_body(
            "codex",
            source="ciel-runtime-router-tool",
            reason="deploy",
            resumed=False,
        )

        self.assertEqual("restart", body["channel"])
        self.assertEqual("restart_notice", body["kind"])
        self.assertEqual("계속-세션재시작됨", body["message"])
        self.assertTrue(body["meta"]["raw_injection"])
        self.assertEqual("codex", body["meta"]["runtime"])
        self.assertEqual("deploy", body["meta"]["restart_reason"])
        self.assertEqual("ciel-runtime-restart", body["meta"]["source"])
        self.assertFalse(body["meta"]["resumed"])


def _codex_services(launched, control, transport=None):
    """Stubbed run_codex services; ``transport`` replaces the stubbed console proxy call."""

    services = mock.MagicMock()
    services.constants.CODEX_RUNTIME_API_KEY_ENV = "CIEL_RUNTIME_CODEX_API_KEY"
    services.constants.PRELAUNCH_CANCEL = -1
    services.constants.PRELAUNCH_LAUNCH_CLAUDE = 1
    services.constants.PRELAUNCH_LAUNCH_CODEX = 2
    services.constants.PRELAUNCH_LAUNCH_AGY = 3
    services.constants.PRELAUNCH_LAUNCH_CODEX_APP_SERVER = 4
    services.process._channel_wake_enter_env_is_fixed.return_value = True
    services.process._codex_channel_wake_submit_delay_seconds.return_value = 0.0
    services.process._codex_channel_wake_submit_retries.return_value = 1
    services.process.codex_process_record_path.return_value = None
    services.process.path_with_ciel_runtime_user_dirs.side_effect = lambda env: env.get("PATH", "")
    services.process.env_bool.side_effect = lambda value, default=False: default

    def call_with_wake(command, _env, **kwargs):
        launched.append(list(command))
        state = kwargs.get("restart_state")
        poll = kwargs.get("restart_poll")
        if poll is not None and state is not None:
            claimed = poll()
            if claimed is not None:
                state.mark(claimed)
        return 5

    services.process.subprocess_call_with_channel_wake_proxy.side_effect = transport or call_with_wake
    services.config.load_config.return_value = {}
    services.config.get_current_provider.return_value = ("codex", {"current_model": "gpt-5"})
    services.config.current_alias.return_value = ""
    services.config.current_launch_cwd_key.return_value = "cwd"
    services.config.apply_launch_endpoint_policy.return_value = []
    services.config.codex_runtime_model_catalog_args.return_value = []
    services.cli_policy.codex_passthrough_args_for_launch.return_value = ([], [])
    services.cli_policy.codex_yolo_launch_args.return_value = []
    services.cli_policy.codex_current_model_cli_args.return_value = []
    services.cli_policy.codex_runtime_config_args.return_value = []
    services.cli_policy.codex_native_routed_config_args.return_value = []
    services.cli_policy.codex_alternate_screen_compat_args.return_value = []
    services.cli_policy.codex_passthrough_has_command.return_value = False
    services.cli_policy.codex_resume_picker_requested.return_value = False
    services.cli_policy.codex_resume_with_session_id.return_value = ""
    services.cli_policy.codex_help_requested.return_value = False

    def materialize(_runtime, executable, env, _provider, _config, **kwargs):
        return [executable, *kwargs["passthrough"]], env

    services.dispatch.materialize_runtime_command.side_effect = materialize
    services.dispatch.run_prelaunch_menu.return_value = 0
    services.dispatch.run_codex_update_check.side_effect = lambda executable, enabled=True: executable
    services.installation.find_executable.return_value = "codex"
    services.installation.install_codex_if_missing.return_value = "codex"
    services.routing.native_codex_enabled.return_value = False
    services.routing.direct_native_codex_enabled.return_value = False
    services.routing.codex_routed_enabled.return_value = True
    services.routing.launch_readiness_errors.return_value = []
    services.routing.run_with_router_lifetime.side_effect = lambda callback, _managed: callback()
    services.routing.start_router_if_needed.return_value = False
    services.channel.channel_delivery_mode.return_value = "off"
    services.channel.codex_mcp_native_http_compat_args.return_value = []
    services.channel.select_codex_resume_session.return_value = ""
    services.restart.new_control.return_value = control
    return services


class CodexSessionRestartLoopTests(unittest.TestCase):
    """Drive the real run_codex loop with a restart requested mid-session."""

    def codex_services(self, launched, control, transport=None):
        return _codex_services(launched, control, transport)

    def test_restart_request_relaunches_codex_with_resume_last(self):
        launched: list[list[str]] = []
        request = RuntimeSessionRestartRequest(
            id="restart-codex",
            source="cli",
            reason="deploy",
            runtime="codex",
            target_pid=os.getpid(),
            resume=True,
            requested_at=time.time(),
            expires_at=time.time() + 60,
        )
        control = RuntimeSessionRestartControl(poll=lambda: request)

        result = runtime_launch.run_codex(
            [],
            skip_menu=True,
            update_check=False,
            self_update_check=False,
            services=self.codex_services(launched, control),
        )

        self.assertEqual(5, result)
        self.assertEqual(2, len(launched))
        self.assertNotIn("resume", launched[0])
        self.assertEqual(["resume", "--last"], launched[1][-2:])
        self.assertTrue(control.requested)

    def test_codex_launch_without_a_request_runs_once(self):
        launched: list[list[str]] = []
        control = RuntimeSessionRestartControl(poll=lambda: None)

        result = runtime_launch.run_codex(
            [],
            skip_menu=True,
            update_check=False,
            self_update_check=False,
            services=self.codex_services(launched, control),
        )

        self.assertEqual(5, result)
        self.assertEqual(1, len(launched))
        self.assertFalse(control.requested)


class RestartRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "runtime-session-restart.json"
        self.logs: list[tuple[str, str]] = []
        self.repository = RuntimeSessionRestartRepository(
            self.path,
            lambda level, message: self.logs.append((level, message)),
            ttl_seconds=60.0,
        )

    def test_queue_writes_one_slot_and_claim_consumes_it(self):
        request = self.repository.queue(source="mcp", reason="deploy", runtime="claude", target_pid=4242)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(request.id, payload["id"])
        self.assertEqual("ciel-runtime.runtime-session-restart/v1", payload["schema"])
        self.assertEqual(4242, payload["target_pid"])

        claimed = self.repository.claim(4242)
        self.assertIsNotNone(claimed)
        self.assertEqual(request.id, claimed.id)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.repository.read())

    def test_claim_ignores_another_client(self):
        self.repository.queue(source="mcp", target_pid=111)

        self.assertIsNone(self.repository.claim(222))
        self.assertTrue(self.path.exists())
        self.assertIsNotNone(self.repository.claim(111))

    def test_broadcast_request_applies_to_any_client(self):
        self.repository.queue(source="cli", target_pid=0)

        self.assertIsNotNone(self.repository.claim(os.getpid() + 12345))

    def test_expired_request_is_dropped(self):
        self.repository.queue(source="mcp", target_pid=7)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["expires_at"] = time.time() - 1
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        self.assertIsNone(self.repository.read())
        self.assertFalse(self.path.exists())
        self.assertTrue(
            any("runtime_session_restart_expired" in message for _, message in self.logs)
        )

    def test_clear_with_other_id_keeps_request(self):
        self.repository.queue(source="mcp")

        self.assertFalse(self.repository.clear("not-this-id"))
        self.assertTrue(self.path.exists())

    def test_invalid_payload_is_discarded(self):
        self.path.write_text("{not json", encoding="utf-8")

        self.assertIsNone(self.repository.read())
        self.assertFalse(self.path.exists())


class ResumeCommandTests(unittest.TestCase):
    def test_claude_appends_continue_when_absent(self):
        argv = ["claude.exe", "--dangerously-skip-permissions", "--model", "x"]

        self.assertEqual(
            [*argv, "--continue"],
            runtime_resume_command(argv, "claude"),
        )

    def test_claude_inserts_continue_before_passthrough_boundary(self):
        argv = ["claude.exe", "--model", "x", "--", "hello"]

        self.assertEqual(
            ["claude.exe", "--model", "x", "--continue", "--", "hello"],
            runtime_resume_command(argv, "claude"),
        )

    def test_claude_keeps_existing_session_control(self):
        for arguments in (
            ["claude.exe", "--continue"],
            ["claude.exe", "--resume", "abc"],
            ["claude.exe", "-c"],
            ["claude.exe", "--session-id=1"],
            ["claude.exe", "--fork-session"],
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(arguments, runtime_resume_command(arguments, "claude"))
                self.assertTrue(runtime_session_control_present(arguments, "claude"))

    def test_codex_appends_resume_last_when_absent(self):
        argv = ["codex.exe", "--yolo", "-c", "model_provider=ciel-runtime", "-m", "x"]

        self.assertEqual(
            [*argv, "resume", "--last"],
            runtime_resume_command(argv, "codex"),
        )

    def test_codex_keeps_resume_subcommand(self):
        argv = ["codex.exe", "--yolo", "resume", "01a08e99-9b1a-7642-a2b6-2f2323c95a0f"]

        self.assertEqual(argv, runtime_resume_command(argv, "codex"))
        self.assertTrue(runtime_session_control_present(argv, "codex"))

    def test_muse_appends_resume_last_when_absent(self):
        argv = ["muse", "--yolo", "--model", "muse-spark-1.3"]

        self.assertEqual(
            [*argv, "resume", "--last"],
            runtime_resume_command(argv, "muse"),
        )

    def test_muse_keeps_an_existing_resume_subcommand(self):
        argv = ["muse", "--yolo", "resume", "--last"]

        self.assertEqual(argv, runtime_resume_command(argv, "muse"))
        self.assertTrue(runtime_session_control_present(argv, "muse"))

    def test_codex_config_override_is_not_continue(self):
        argv = ["codex.exe", "-c", "model=x"]

        self.assertFalse(runtime_session_control_present(argv, "codex"))
        self.assertEqual([*argv, "resume", "--last"], runtime_resume_command(argv, "codex"))

    def test_unknown_runtime_is_passed_through(self):
        argv = ["other.exe", "--flag"]

        self.assertEqual(argv, runtime_resume_command(argv, "other"))


class RestartControlTests(unittest.TestCase):
    def test_incoming_polls_once_until_marked(self):
        request = RuntimeSessionRestartRequest(
            id="abc",
            source="mcp",
            reason="",
            runtime="claude",
            target_pid=os.getpid(),
            resume=True,
            requested_at=time.time(),
            expires_at=time.time() + 60,
        )
        polls: list[int] = []

        def poll():
            polls.append(1)
            return request

        control = RuntimeSessionRestartControl(poll=poll)
        self.assertIs(request, control.incoming())
        self.assertTrue(control.requested)
        self.assertIsNone(control.incoming())
        self.assertEqual(1, len(polls))

        control.reset()
        self.assertFalse(control.requested)
        self.assertIs(request, control.incoming())
        self.assertEqual(2, len(polls))


class _FakeChild:
    def __init__(self):
        self.pid = 4321
        self.exited = False

    def poll(self):
        return 0 if self.exited else None

    def wait(self):
        self.exited = True
        return 0


class DirectTransportRestartTests(unittest.TestCase):
    def dispatch_service(self, captured: dict):
        def popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["child"] = _FakeChild()
            return captured["child"]

        def terminate(proc, label):
            captured.setdefault("terminations", []).append(label)
            proc.exited = True

        return ChannelTerminalDispatchService(
            settings=ChannelTerminalDispatchSettings(
                platform_name="nt",
                stdin_isatty=lambda: False,
                stdout_isatty=lambda: False,
            ),
            proxy=ChannelTerminalProxyPorts(
                windows_supported=lambda: False,
                run_windows=lambda *_args, **_kwargs: 0,
                run_posix=lambda *_args, **_kwargs: 0,
                posix_services=lambda: None,
            ),
            direct=ChannelDirectProcessPorts(
                call=lambda cmd, env=None: 0,
                popen=popen,
                write_record=lambda path, pid, cmd: captured.setdefault("record", (path, pid)),
                terminate=terminate,
                release_record=lambda path, pid: captured.setdefault("released", pid),
            ),
            log=lambda _level, _message: None,
        )

    def test_restart_request_terminates_the_cli_child(self):
        captured: dict = {}
        service = self.dispatch_service(captured)
        request = RuntimeSessionRestartRequest(
            id="abc",
            source="mcp",
            reason="deploy",
            runtime="claude",
            target_pid=os.getpid(),
            resume=True,
            requested_at=time.time(),
            expires_at=time.time() + 60,
        )
        control = RuntimeSessionRestartControl(poll=lambda: request)

        returncode = service.call_direct(
            ["claude.exe", "--continue"],
            {},
            None,
            restart_poll=control.incoming,
            restart_state=control,
        )

        self.assertEqual(0, returncode)
        self.assertEqual(["cli session restart", "current Codex"], captured["terminations"])
        self.assertIs(request, control.request)

    def test_call_without_poll_uses_the_blocking_call_port(self):
        captured: dict = {}
        service = self.dispatch_service(captured)
        calls: list[list[str]] = []

        service = ChannelTerminalDispatchService(
            settings=service.settings,
            proxy=service.proxy,
            direct=ChannelDirectProcessPorts(
                call=lambda cmd, env=None: calls.append(cmd) or 3,
                popen=service.direct.popen,
                write_record=service.direct.write_record,
                terminate=service.direct.terminate,
                release_record=service.direct.release_record,
            ),
            log=service.log,
        )

        self.assertEqual(3, service.call_direct(["claude.exe"], {}, None))
        self.assertEqual([["claude.exe"]], calls)
        self.assertNotIn("child", captured)


class McpToolTests(unittest.TestCase):
    def test_schema_exposes_restart_session(self):
        tools = channel_mcp_tools.channel_mcp_tool_schemas()
        schema = next(tool for tool in tools if tool.get("name") == "restart_session")

        self.assertIn("reason", schema["inputSchema"]["properties"])
        self.assertIn("client_pid", schema["inputSchema"]["properties"])
        self.assertIn("resume", schema["inputSchema"]["properties"])
        self.assertEqual([], schema["inputSchema"].get("required", []))

    def dispatch(self, services, arguments):
        return channel_mcp_tools.dispatch_channel_mcp_tool(
            9,
            {"name": "restart_session", "arguments": arguments},
            services,
        )

    def services(self, restart_session):
        return channel_mcp_tools.ChannelMcpToolServices(
            queue_compact=lambda *_args: {},
            append_message=lambda payload: payload,
            read_messages=lambda *_args: [],
            store_file_path=lambda *_args: {},
            store_file_upload=lambda payload: payload,
            file_message_text=lambda text, _files: text,
            handle_llm_options=lambda *_args: ([], False),
            runtime=channel_mcp_tools.ChannelMcpRuntimeServices(restart_session=restart_session),
        )

    def test_dispatch_returns_queued_request(self):
        captured: dict = {}

        def restart_session(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "queued": True, "request": {"id": "r1"}, "target": {"pid": 5}}

        response = self.dispatch(
            self.services(restart_session),
            {"reason": "deploy", "runtime": "claude", "client_pid": 5, "resume": False},
        )

        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(
            {"reason": "deploy", "runtime": "claude", "client_pid": 5, "resume": False},
            captured,
        )

    def test_dispatch_reports_missing_service(self):
        response = self.dispatch(self.services(None), {})

        self.assertTrue(response["result"]["isError"])
        self.assertIn("unavailable", response["result"]["content"][0]["text"])

    def test_dispatch_rejects_invalid_pid(self):
        response = self.dispatch(self.services(lambda **_kwargs: {"ok": True}), {"client_pid": "x"})

        self.assertTrue(response["result"]["isError"])
        self.assertIn("client_pid", response["result"]["content"][0]["text"])

    def test_dispatch_surfaces_no_active_client(self):
        response = self.dispatch(
            self.services(lambda **_kwargs: {"ok": False, "detail": "no active ciel-runtime CLI client"}),
            {},
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("no active ciel-runtime CLI client", response["result"]["content"][0]["text"])


class RouterQueueTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.instance = self.root / "9611-bd5f642b3583"
        clients = self.instance / "router-clients"
        clients.mkdir(parents=True)
        (clients / "50788.json").write_text(
            json.dumps({"pid": 50788, "workspace": r"C:\work", "started_at": "2026-09-18T09:46:55", "router_port": 9611}),
            encoding="utf-8",
        )
        (clients / "89280.json").write_text(
            json.dumps({"pid": 89280, "workspace": r"G:\other", "started_at": "2026-09-18T10:26:33", "router_port": 9611}),
            encoding="utf-8",
        )
        (clients / "1.json").write_text(json.dumps({"pid": 1, "started_at": "2026-09-17T00:00:00"}), encoding="utf-8")
        (clients / f"{os.getpid()}.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "workspace": r"C:\work",
                    "started_at": "2026-09-18T11:30:00",
                    "router_port": 9611,
                }
            ),
            encoding="utf-8",
        )
        self.live = {50788, 89280, os.getpid()}
        self.logs: list[str] = []
        self.service = RuntimeSessionRestartService(
            RuntimeSessionRestartServicePorts(
                instance_dir=self.instance,
                instances_root=self.root,
                is_running=lambda pid: pid in self.live,
                log=lambda level, message: self.logs.append(f"{level} {message}"),
                workspace_digest=lambda path: hashlib.sha256(path.encode("utf-8")).hexdigest()[:12],
            )
        )

    def test_queue_targets_the_newest_live_client(self):
        result = self.service.queue(source="cli", reason="deploy")

        self.assertTrue(result["ok"])
        self.assertEqual(os.getpid(), result["target"]["pid"])
        payload = json.loads((self.instance / RESTART_REQUEST_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(os.getpid(), payload["target_pid"])
        self.assertEqual("cli", payload["source"])
        self.assertTrue(any("runtime_session_restart_queued" in line for line in self.logs))

    def test_queue_rejects_unknown_client_pid(self):
        result = self.service.queue(source="cli", client_pid=999)

        self.assertFalse(result["ok"])
        self.assertIn("not an active client", result["detail"])
        self.assertFalse((self.instance / RESTART_REQUEST_FILE_NAME).exists())

    def test_queue_reports_when_no_client_is_live(self):
        self.live.clear()

        result = self.service.queue(source="mcp")

        self.assertFalse(result["ok"])
        self.assertEqual([], result["clients"])

    def test_instances_list_only_instances_with_live_clients(self):
        (self.root / "empty-9999").mkdir()

        instances = self.service.instances()

        self.assertEqual([self.instance.name], [record["instance"] for record in instances])
        self.assertEqual(
            [50788, 89280, os.getpid()],
            [client["pid"] for client in instances[0]["clients"]],
        )

    def test_control_claims_the_runtime_request_for_this_process(self):
        result = self.service.queue(source="mcp", client_pid=os.getpid())
        self.assertEqual(os.getpid(), result["request"]["target_pid"])

        control = self.service.control()
        request = control.incoming()

        self.assertIsNotNone(request)
        self.assertIsNone(control.incoming())
        self.assertTrue(control.requested)

    def test_queue_tool_labels_the_mcp_source(self):
        result = self.service.queue_tool(reason="deploy")

        self.assertEqual("ciel-runtime-router-tool", result["request"]["source"])

    def test_runtime_service_is_wired_to_the_router_instance_directory(self):
        service = ciel_runtime.runtime_session_restart_service()

        self.assertEqual(
            ciel_runtime.ROUTER_INSTANCE_DIR / RESTART_REQUEST_FILE_NAME,
            service.request_path(),
        )

    def test_unisolated_test_runner_never_queues_a_restart(self):
        isolated_service = RuntimeSessionRestartService(
            RuntimeSessionRestartServicePorts(
                instance_dir=self.instance,
                instances_root=self.root,
                is_running=lambda pid: pid in self.live,
                log=lambda _level, _message: None,
                workspace_digest=lambda _path: "probe",
                unisolated_test=lambda: True,
            )
        )

        result = isolated_service.queue(source="mcp")

        self.assertFalse(result["ok"])
        self.assertIn("unisolated test", result["detail"])
        self.assertFalse((self.instance / RESTART_REQUEST_FILE_NAME).exists())


class RestartSessionCommandTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.queued: list[dict] = []
        self.service = RuntimeSessionRestartService(
            RuntimeSessionRestartServicePorts(
                instance_dir=self.root / "9611-abc",
                instances_root=self.root,
                is_running=lambda _pid: True,
                log=lambda _level, _message: None,
                workspace_digest=lambda _path: "def",
            )
        )

    def client(self, pid, instance="9611-abc"):
        return {"pid": pid, "workspace": "C:/work", "started_at": "", "router_port": 9611, "instance": instance}

    def instance(self, name, pid):
        return {"instance": name, "path": self.root / name, "clients": [self.client(pid, name)]}

    def args(self, **overrides):
        values = {"reason": "", "runtime": "", "workspace": "", "pid": 0, "no_resume": False}
        values.update(overrides)
        return mock.Mock(**values)

    def run_command(self, args, instances, queue):
        with (
            mock.patch.object(self.service, "instances", return_value=instances),
            mock.patch.object(self.service, "queue", side_effect=queue),
        ):
            self.service.command(args)

    def test_single_client_is_restarted(self):
        def queue(**kwargs):
            self.queued.append(kwargs)
            return {
                "ok": True,
                "queued": True,
                "request": {"id": "r1", "resume": True},
                "target": {"pid": 50788, "workspace": "C:/work"},
            }

        self.run_command(self.args(reason="deploy", runtime="claude"), [self.instance("9611-abc", 50788)], queue)

        self.assertEqual("cli", self.queued[0]["source"])
        self.assertEqual("deploy", self.queued[0]["reason"])
        self.assertEqual("claude", self.queued[0]["runtime"])
        self.assertEqual(self.root / "9611-abc", self.queued[0]["instance_dir"])
        self.assertTrue(self.queued[0]["resume"])

    def test_explicit_pid_selects_its_instance(self):
        def queue(**kwargs):
            self.queued.append(kwargs)
            return {"ok": True, "queued": True, "request": {"id": "r1", "resume": False}, "target": {"pid": 42}}

        instances = [self.instance("9611-abc", 50788), self.instance("9465-def", 42)]
        self.run_command(self.args(pid=42, no_resume=True), instances, queue)

        self.assertEqual(self.root / "9465-def", self.queued[0]["instance_dir"])
        self.assertEqual(42, self.queued[0]["client_pid"])
        self.assertFalse(self.queued[0]["resume"])

    def test_workspace_selects_its_instance(self):
        def queue(**kwargs):
            self.queued.append(kwargs)
            return {"ok": True, "queued": True, "request": {"id": "r1", "resume": True}, "target": {"pid": 7}}

        instances = [self.instance("9611-abc", 50788), self.instance("9465-def", 7)]
        self.run_command(self.args(workspace="G:/project"), instances, queue)

        self.assertEqual("9465-def", self.queued[0]["instance_dir"].name)

    def test_multiple_clients_require_a_selection(self):
        with mock.patch.object(self.service, "instances", return_value=[self.instance("9611-abc", 1), self.instance("9465-def", 2)]):
            with mock.patch.object(self.service, "queue") as queue:
                with self.assertRaises(SystemExit) as raised:
                    self.service.command(self.args())

        self.assertEqual(2, raised.exception.code)
        queue.assert_not_called()

    def test_missing_session_exits_with_failure(self):
        with mock.patch.object(self.service, "instances", return_value=[]):
            with self.assertRaises(SystemExit) as raised:
                self.service.command(self.args())

        self.assertEqual(2, raised.exception.code)

    def test_unregistered_pid_exits_with_failure(self):
        with mock.patch.object(self.service, "instances", return_value=[self.instance("9611-abc", 1)]):
            with self.assertRaises(SystemExit) as raised:
                self.service.command(self.args(pid=99))

        self.assertEqual(2, raised.exception.code)


class _EndedChild:
    """A CLI child that has already exited when its transport first looks at it."""

    def __init__(self, returncode: int = 0) -> None:
        self.pid = 5150
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


class _AppServer:
    def __init__(self) -> None:
        self.pid = 4242

    def poll(self):
        return None


class RestartRequestAtChildExitTests(unittest.TestCase):
    """A restart request must survive the CLI child ending before a transport claims it.

    The transports stop polling once the child has exited: the direct transport
    (channel_terminal_dispatch._await_child) checks proc.poll() before restart_poll,
    and the console proxies loop on ``proc.poll() is None``.  A request queued in
    that window, or one already claimed and waiting for the running turn, never
    reached the launcher loop, which then saw ``control.request is None`` and ended
    the session.  These tests use the production control (service.control()) and
    the request file, as the router writes it.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.instance = root / "9611-exitrace"
        clients = self.instance / "router-clients"
        clients.mkdir(parents=True)
        self.other_pid = os.getpid() + 7919
        for index, pid in enumerate((self.other_pid, os.getpid())):
            (clients / f"{pid}.json").write_text(
                json.dumps({"pid": pid, "workspace": "C:/work", "started_at": f"2026-10-16T00:00:0{index}"}),
                encoding="utf-8",
            )
        self.live = {self.other_pid, os.getpid()}
        self.logs: list[str] = []
        self.service = RuntimeSessionRestartService(
            RuntimeSessionRestartServicePorts(
                instance_dir=self.instance,
                instances_root=root,
                is_running=lambda pid: pid in self.live,
                log=lambda level, message: self.logs.append(f"{level} {message}"),
                workspace_digest=lambda _path: "exitrace",
            )
        )
        self.request_path = self.instance / RESTART_REQUEST_FILE_NAME
        self.terminations: list[str] = []
        self.dispatch = ChannelTerminalDispatchService(
            settings=ChannelTerminalDispatchSettings(
                platform_name="nt", stdin_isatty=lambda: False, stdout_isatty=lambda: False,
            ),
            proxy=ChannelTerminalProxyPorts(
                windows_supported=lambda: False,
                run_windows=lambda *_args, **_kwargs: 0,
                run_posix=lambda *_args, **_kwargs: 0,
                posix_services=lambda: None,
            ),
            direct=ChannelDirectProcessPorts(
                call=lambda cmd, env=None: 0,
                popen=lambda cmd, env=None, **_kwargs: _EndedChild(),
                write_record=lambda *_args: None,
                terminate=lambda _proc, label: self.terminations.append(label),
                release_record=lambda *_args: None,
            ),
            log=lambda _level, _message: None,
        )

    def queue(self, client_pid=None, **options):
        result = self.service.queue(
            source=options.pop("source", "ciel-runtime-router-tool"),
            reason=options.pop("reason", "deploy"),
            client_pid=os.getpid() if client_pid is None else client_pid,
            **options,
        )
        self.assertTrue(result["ok"], result)
        return result["request"]

    def ended_child_transport(self, launched):
        """The real direct transport around a child that is gone before the first restart poll."""

        def transport(command, env, **kwargs):
            launched.append(list(command))
            return self.dispatch.call_direct(
                command,
                env,
                None,
                restart_poll=kwargs.get("restart_poll"),
                restart_state=kwargs.get("restart_state"),
            )

        return transport

    def turn_then_exit_transport(self, launched, notices):
        """The console proxy's own poll while a turn runs (the request waits), then the child ends."""

        def transport(command, _env, **kwargs):
            launched.append(list(command))
            if len(launched) == 1:
                self.assertFalse(_runtime_session_restart_requested(
                    lambda *_args: None,
                    kwargs["restart_poll"],
                    kwargs["restart_state"],
                    transport="test",
                    pid=5150,
                    busy=lambda: True,
                    notify=notices.append,
                ))
            return 0

        return transport

    def claimed_ids(self):
        return [
            line.split(" id=", 1)[1].split(" ", 1)[0]
            for line in self.logs
            if "runtime_session_restart_claimed" in line
        ]

    def run_claude(self, transport, launched, notices=None, router_lines=None, services=None):
        services = services or _claude_services(
            launched,
            self.service.control(),
            [],
            notice=(notices.append if notices is not None else None),
            transport=transport,
            router_log=(lambda _level, message: router_lines.append(message)) if router_lines is not None else None,
        )
        return runtime_launch.run_claude(
            [], skip_menu=True, update_check=False, self_update_check=False, services=services,
        )

    def test_claude_request_queued_as_the_child_exits_relaunches_once(self):
        queued = self.queue()
        launched: list[list[str]] = []
        notices: list[dict] = []
        router_lines: list[str] = []

        result = self.run_claude(self.ended_child_transport(launched), launched, notices, router_lines)

        self.assertEqual(0, result)
        self.assertEqual(2, len(launched))
        self.assertNotIn("--continue", launched[0])
        self.assertEqual("--continue", launched[1][-1])
        self.assertFalse(self.request_path.exists())
        self.assertEqual([queued["id"]], self.claimed_ids())
        self.assertEqual(1, len(notices))
        self.assertEqual("ciel-runtime-router-tool", notices[0]["meta"]["restart_source"])
        restart_lines = [line for line in router_lines if line.startswith("claude_session_restart ")]
        self.assertEqual(1, len(restart_lines))
        self.assertIn(f"request_id={queued['id']}", restart_lines[0])
        self.assertIn("claim=exit", restart_lines[0])

    def test_claude_request_waiting_for_the_turn_survives_the_child_exit(self):
        queued = self.queue()
        launched: list[list[str]] = []
        notices: list = []
        router_lines: list[str] = []

        self.run_claude(self.turn_then_exit_transport(launched, notices), launched, notices, router_lines)

        # The request was claimed (file gone) and held for the turn when the child ended.
        self.assertEqual(2, len(launched))
        self.assertEqual("--continue", launched[1][-1])
        self.assertFalse(self.request_path.exists())
        self.assertEqual([queued["id"]], self.claimed_ids())
        self.assertIn("진행 중인 turn이 끝나면 재시작합니다", notices[0])
        restart_lines = [line for line in router_lines if line.startswith("claude_session_restart ")]
        self.assertEqual(1, len(restart_lines))
        self.assertIn("claim=deferred", restart_lines[0])

    def test_claude_request_without_resume_relaunches_without_continue(self):
        self.queue(resume=False)
        launched: list[list[str]] = []

        self.run_claude(self.ended_child_transport(launched), launched)

        self.assertEqual(2, len(launched))
        self.assertNotIn("--continue", launched[1])

    def test_claude_normal_exit_without_a_request_ends_the_session(self):
        launched: list[list[str]] = []

        result = self.run_claude(self.ended_child_transport(launched), launched)

        self.assertEqual(0, result)
        self.assertEqual(1, len(launched))
        self.assertEqual([], self.claimed_ids())

    def test_request_for_another_client_is_left_for_that_client(self):
        self.queue(client_pid=self.other_pid)
        launched: list[list[str]] = []

        self.run_claude(self.ended_child_transport(launched), launched)

        self.assertEqual(1, len(launched))
        payload = json.loads(self.request_path.read_text(encoding="utf-8"))
        self.assertEqual(self.other_pid, payload["target_pid"])

    def test_one_shot_print_run_is_never_restarted_after_it_exits(self):
        import dataclasses

        self.queue()
        launched: list[list[str]] = []
        services = _claude_services(launched, self.service.control(), [], transport=self.ended_child_transport(launched))
        services = dataclasses.replace(
            services,
            policy=dataclasses.replace(services.policy, has_noninteractive_claude_args=lambda _args: True),
            channel_delivery=dataclasses.replace(
                services.channel_delivery, should_use_channel_stdin_proxy=lambda *_args: False,
            ),
        )
        with mock.patch.object(runtime_launch.subprocess, "call", side_effect=lambda cmd, env=None: launched.append(list(cmd)) or 0):
            self.run_claude(None, launched, services=services)

        self.assertEqual(1, len(launched))
        self.assertTrue(self.request_path.exists())
        self.assertEqual([], self.claimed_ids())

    def test_claude_restart_after_exit_renews_the_workspace_mcp_launch(self):
        import dataclasses

        from ciel_runtime_support.workspace_mcp import WorkspaceMcpLaunchPorts, WorkspaceMcpLaunchService

        self.queue()
        launched: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            workspace_mcp = WorkspaceMcpLaunchService(Path(directory), "ws", WorkspaceMcpLaunchPorts(
                pid_running=lambda _pid: True, command_line=lambda _pid: "",
                terminate_tree=lambda *_a, **_k: True, log=lambda *_a: None,
            ))
            services = _claude_services(launched, self.service.control(), [], transport=self.ended_child_transport(launched))
            services = dataclasses.replace(
                services,
                config=dataclasses.replace(services.config, load_config=lambda: {"workspace_mcp": {"servers": {
                    "tools": {"enabled": True, "transport": "stdio", "command": "t", "runtimes": ["claude"]}}}}),
                mcp_config=dataclasses.replace(services.mcp_config, workspace_mcp=workspace_mcp),
            )
            self.run_claude(None, launched, services=services)
            left = sorted(path.name for path in Path(directory).iterdir())

        paths = [command[command.index("--mcp-config") + 1] for command in launched]
        self.assertEqual(2, len(paths))
        self.assertNotEqual(Path(paths[0]).parent.name, Path(paths[1]).parent.name)
        self.assertEqual([], left)

    def test_codex_request_queued_as_the_child_exits_relaunches_with_resume_last(self):
        queued = self.queue()
        launched: list[list[str]] = []
        services = _codex_services(launched, self.service.control(), self.ended_child_transport(launched))

        result = runtime_launch.run_codex(
            [], skip_menu=True, update_check=False, self_update_check=False, services=services,
        )

        self.assertEqual(0, result)
        self.assertEqual(2, len(launched))
        self.assertNotIn("resume", launched[0])
        self.assertEqual(["resume", "--last"], launched[1][-2:])
        self.assertFalse(self.request_path.exists())
        self.assertEqual([queued["id"]], self.claimed_ids())
        logged = [call.args[1] for call in services.routing.router_log.call_args_list]
        restart_lines = [line for line in logged if line.startswith("codex_session_restart ")]
        self.assertEqual(1, len(restart_lines))
        self.assertIn(f"request_id={queued['id']}", restart_lines[0])

    def test_codex_normal_exit_without_a_request_ends_the_session(self):
        launched: list[list[str]] = []
        services = _codex_services(launched, self.service.control(), self.ended_child_transport(launched))

        runtime_launch.run_codex([], skip_menu=True, update_check=False, self_update_check=False, services=services)

        self.assertEqual(1, len(launched))

    def remote_session(self, terminal_calls, popened, logs, run_terminal=None):
        from ciel_runtime_support.codex_app_server_session import CodexRemoteTuiPorts, CodexRemoteTuiSession
        from ciel_runtime_support.codex_desktop_runtime import CodexDesktopPorts

        def ended_tui(cmd, _env, **_options):
            terminal_calls.append(list(cmd))
            return 0

        ports = CodexDesktopPorts(
            config_dir=Path(self._temp.name),
            workspace_id="ws1",
            source_codex_home=Path(self._temp.name) / "src",
            version="test",
            log=lambda level, message: logs.append(f"{level} {message}"),
            terminate_tree=lambda _pid: None,
            channel=None,
            popen=lambda cmd, **_kw: popened.append(list(cmd)) or _AppServer(),
            wait_ready=lambda *_a, **_kw: True,
        )
        control = self.service.control()
        tui = CodexRemoteTuiPorts(
            run_terminal=run_terminal or ended_tui,
            restart_control=lambda: control,
            set_transcript_scope=lambda *_a, **_kw: None,
        )
        return CodexRemoteTuiSession(ports, tui)

    def test_codex_remote_tui_exit_keeps_a_queued_restart(self):
        from ciel_runtime_support.codex_app_server_session import RemoteTuiResume

        queued = self.queue()
        terminal_calls: list[list[str]] = []
        popened: list[list[str]] = []
        logs: list[str] = []
        relaunched: list = []
        server_cmd = ["codex", "app-server", "--listen", "ws://127.0.0.1:19961"]

        rc = self.remote_session(terminal_calls, popened, logs)(
            server_cmd, {}, Path(self._temp.name), resume=RemoteTuiResume("id", "01a0-saved"),
            relaunch=lambda request: relaunched.append(request.id) or server_cmd,
        )

        self.assertEqual(0, rc)
        self.assertEqual([queued["id"]], relaunched)
        self.assertEqual(2, len(popened))
        self.assertEqual(["resume", "01a0-saved"], terminal_calls[1][-2:])
        self.assertFalse(self.request_path.exists())
        self.assertTrue(any("codex_remote_session_restart" in line and "claim=exit" in line for line in logs))

    def test_codex_remote_request_waiting_for_the_turn_survives_the_tui_exit(self):
        queued = self.queue()
        terminal_calls: list[list[str]] = []
        popened: list[list[str]] = []
        relaunched: list = []
        server_cmd = ["codex", "app-server", "--listen", "ws://127.0.0.1:19961"]

        def turn_then_exit(cmd, _env, **options):
            terminal_calls.append(list(cmd))
            if len(terminal_calls) == 1:
                self.assertIsNone(options["restart_state"].incoming(busy=lambda: True))
            return 0

        self.remote_session(terminal_calls, popened, [], run_terminal=turn_then_exit)(
            server_cmd, {}, Path(self._temp.name),
            relaunch=lambda request: relaunched.append(request.id) or server_cmd,
        )

        self.assertEqual([queued["id"]], relaunched)
        self.assertEqual(2, len(terminal_calls))

    def test_codex_remote_tui_exit_without_a_request_ends_the_session(self):
        terminal_calls: list[list[str]] = []
        popened: list[list[str]] = []
        server_cmd = ["codex", "app-server", "--listen", "ws://127.0.0.1:19961"]

        rc = self.remote_session(terminal_calls, popened, [])(server_cmd, {}, Path(self._temp.name))

        self.assertEqual(0, rc)
        self.assertEqual(1, len(terminal_calls))
        self.assertEqual(1, len(popened))

    def test_after_exit_claim_takes_only_a_request_addressed_to_this_launcher(self):
        control = self.service.control()
        # A request for any client stays for the transports of a running child.
        RuntimeSessionRestartRepository(self.request_path, lambda *_a: None).queue(source="cli", target_pid=0)

        self.assertIsNone(control.after_child_exit())
        self.assertTrue(self.request_path.exists())

        control.reset()
        self.request_path.unlink()
        # The parent of this process (a console or wrapper) is not this launcher.
        RuntimeSessionRestartRepository(self.request_path, lambda *_a: None).queue(source="cli", target_pid=os.getppid())
        self.assertIsNone(control.after_child_exit())
        self.assertTrue(self.request_path.exists())

        control.reset()
        self.request_path.unlink()
        queued = self.queue()
        self.assertEqual(queued["id"], control.after_child_exit().id)
        self.assertEqual("exit", control.claimed_by)
        self.assertTrue(any("match=exact" in line for line in self.logs))


def _restart_request(request_id: str = "r1", resume: bool = True) -> RuntimeSessionRestartRequest:
    now = time.time()
    return RuntimeSessionRestartRequest(
        id=request_id, source="mcp", reason="deploy", runtime="claude",
        target_pid=os.getpid(), resume=resume, requested_at=now, expires_at=now + 60,
    )


class RestartControlAfterChildExitTests(unittest.TestCase):
    def setUp(self):
        self.exit_polls: list[int] = []
        self.exit_pending: list[RuntimeSessionRestartRequest] = []

    def control(self, poll=lambda: None):
        def exit_poll():
            self.exit_polls.append(1)
            return self.exit_pending.pop() if self.exit_pending else None

        return RuntimeSessionRestartControl(poll=poll, exit_poll=exit_poll)

    def test_a_request_the_transport_took_is_returned_without_another_claim(self):
        request = _restart_request()
        control = self.control(poll=lambda: request)

        self.assertIs(request, control.incoming())
        self.assertIs(request, control.after_child_exit())
        self.assertEqual("transport", control.claimed_by)
        self.assertEqual([], self.exit_polls)

    def test_a_request_held_for_the_turn_is_released_when_the_child_ends(self):
        pending = [_restart_request("held")]
        control = self.control(poll=lambda: pending.pop() if pending else None)

        self.assertIsNone(control.incoming(busy=lambda: True))
        self.assertIsNotNone(control.deferred)

        released = control.after_child_exit()
        self.assertEqual("held", released.id)
        self.assertIsNone(control.deferred)
        self.assertTrue(control.requested)
        self.assertEqual("deferred", control.claimed_by)
        self.assertEqual([], self.exit_polls)

    def test_the_exit_claim_runs_once_per_launch(self):
        control = self.control()

        self.assertIsNone(control.after_child_exit())
        self.assertIsNone(control.after_child_exit())
        self.assertEqual(1, len(self.exit_polls))

        control.reset()
        self.exit_pending.append(_restart_request("late"))
        self.assertEqual("late", control.after_child_exit().id)
        self.assertEqual("late", control.after_child_exit().id)
        self.assertEqual(2, len(self.exit_polls))
        self.assertEqual("exit", control.claimed_by)

        control.reset()
        self.assertEqual("", control.claimed_by)
        self.assertFalse(control.requested)

    def test_without_an_exit_claim_an_ended_child_ends_the_session(self):
        control = RuntimeSessionRestartControl(poll=lambda: _restart_request())

        self.assertIsNone(control.after_child_exit())

    def test_a_repeated_request_id_after_exit_relaunches_only_once(self):
        # An unclearable slot hands back the same request after every exit.
        request = _restart_request("sticky")
        control = RuntimeSessionRestartControl(poll=lambda: None, exit_poll=lambda: request)
        launched: list[list[str]] = []
        notices: list[dict] = []

        result = runtime_launch.run_claude(
            [], skip_menu=True, update_check=False, self_update_check=False,
            services=_claude_services(launched, control, [], notice=notices.append),
        )

        self.assertEqual(7, result)
        self.assertEqual(2, len(launched))
        self.assertEqual("--continue", launched[1][-1])
        self.assertEqual(1, len(notices))

    def test_exact_repository_claim_leaves_broadcast_and_foreign_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RuntimeSessionRestartRepository(Path(directory) / "slot.json", lambda *_a: None)
            repository.queue(source="cli", target_pid=0)
            self.assertIsNone(repository.claim(4242, exact=True))
            self.assertIsNotNone(repository.claim(4242))

            repository.queue(source="cli", target_pid=111)
            self.assertIsNone(repository.claim(4242, exact=True))
            self.assertIsNotNone(repository.claim(111, exact=True))


if __name__ == "__main__":
    unittest.main()
