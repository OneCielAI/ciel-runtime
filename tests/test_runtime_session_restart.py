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


class ClaudeSessionRestartLoopTests(unittest.TestCase):
    """Drive the real run_claude loop with a restart requested mid-session."""

    def claude_services(self, launched, control, capture, notice=None):
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
                call_with_wake,
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
                no_op,
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
        self.assertIn("재부팅 완료", notice["message"])
        self.assertIn("claude", notice["message"])
        self.assertIn("source=ciel-runtime-router-tool", notice["message"])
        self.assertEqual("restart_notice", notice["meta"]["source_kind"])
        self.assertTrue(notice["meta"]["resumed"])

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
    def test_notice_names_the_runtime_and_the_resume_outcome(self):
        body = restart_notice_body(
            "codex",
            source="ciel-runtime-router-tool",
            reason="deploy",
            resumed=False,
        )

        self.assertEqual("restart", body["channel"])
        self.assertEqual("restart_notice", body["kind"])
        self.assertIn("재부팅 완료", body["message"])
        self.assertIn("codex", body["message"])
        self.assertIn("새로 시작되었습니다", body["message"])
        self.assertEqual("ciel-runtime-restart", body["meta"]["source"])
        self.assertFalse(body["meta"]["resumed"])


class CodexSessionRestartLoopTests(unittest.TestCase):
    """Drive the real run_codex loop with a restart requested mid-session."""

    def codex_services(self, launched, control):
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

        services.process.subprocess_call_with_channel_wake_proxy.side_effect = call_with_wake
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


if __name__ == "__main__":
    unittest.main()
