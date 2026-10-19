import unittest
from unittest import mock
from types import SimpleNamespace

from ciel_runtime_support.channel_terminal_proxy import run_windows_channel_terminal_proxy
from pathlib import Path

from ciel_runtime_support.channel_terminal_dispatch import (
    ChannelDirectDeliveryServices,
    ChannelDirectProcessPorts,
    ChannelTerminalDispatchService,
    ChannelTerminalDispatchSettings,
    ChannelTerminalProxyPorts,
)


class _Process:
    pid = 42

    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code


class ChannelTerminalDispatchServiceTests(unittest.TestCase):
    def _service(
        self,
        *,
        platform_name: str,
        windows_supported=True,
        run_windows=lambda *_args, **_kwargs: 11,
        run_posix=lambda *_args, **_kwargs: 22,
        direct_call=lambda *_args, **_kwargs: 33,
        popen=lambda *_args, **_kwargs: _Process(),
        events=None,
        delivery=None,
    ) -> ChannelTerminalDispatchService:
        events = events if events is not None else []
        return ChannelTerminalDispatchService(
            settings=ChannelTerminalDispatchSettings(
                platform_name=platform_name,
                stdin_isatty=lambda: True,
                stdout_isatty=lambda: True,
            ),
            proxy=ChannelTerminalProxyPorts(
                windows_supported=lambda: windows_supported,
                run_windows=run_windows,
                run_posix=run_posix,
                posix_services=lambda: "services",
            ),
            direct=ChannelDirectProcessPorts(
                call=direct_call,
                popen=popen,
                write_record=lambda path, pid, cmd: events.append(
                    ("write", path, pid, cmd)
                ),
                terminate=lambda proc, label: events.append(
                    ("terminate", proc.pid, label)
                ),
                release_record=lambda path, pid: events.append(
                    ("release", path, pid)
                ),
            ),
            log=lambda level, message: events.append(
                ("log", level, message)
            ),
            delivery=delivery,
        )

    def test_terminal_context_composes_pressure_control_without_extra_dispatch_fields(self):
        import ciel_runtime
        from dataclasses import replace

        context = ciel_runtime.channel_terminal_context()
        control = mock.Mock()
        context = replace(context, policy=replace(context.policy, compaction=control))
        service = context.dispatch_service()
        self.assertIs(control, service.delivery.policy.compaction)
        self.assertIs(control, context.posix_services().policy.compaction)
        self.assertIs(control, context.windows_services().policy.compaction)

    def test_windows_console_adapter_receives_launch_options(self):
        calls = []
        service = self._service(
            platform_name="nt",
            run_windows=lambda *args, **kwargs: calls.append(
                (args, kwargs)
            )
            or 17,
        )

        self.assertEqual(
            17,
            service.dispatch(
                ["codex"],
                {"PATH": "x"},
                wake_for_llm_delivery=True,
                channel_wake_submit_retries=4,
            ),
        )
        self.assertEqual((["codex"], {"PATH": "x"}), calls[0][0])
        self.assertTrue(calls[0][1]["wake_for_llm_delivery"])
        self.assertEqual(4, calls[0][1]["channel_wake_submit_retries"])

    def test_windows_compatibility_entrypoint_forwards_shared_poll_state(self):
        from ciel_runtime_support.channel_pending_poll import ChannelPendingPollState
        from ciel_runtime_support.channel_terminal_context import ChannelTerminalCompatibilityApi
        context = SimpleNamespace(run_windows=mock.Mock(return_value=17))
        api = ChannelTerminalCompatibilityApi(lambda: context)
        events = []
        service = self._service(
            platform_name="nt", run_windows=api.run_windows,
            delivery=self._delivery(mock.Mock()), events=events,
        )

        self.assertEqual(17, service.dispatch(["claude"], {}))

        context.run_windows.assert_called_once()
        state = context.run_windows.call_args.kwargs["pending_poll_state"]
        self.assertIsInstance(state, ChannelPendingPollState)
        self.assertFalse(any("fallback" in str(event) for event in events))

    def test_windows_adapter_failure_falls_back_to_direct_process(self):
        events = []
        service = self._service(
            platform_name="nt",
            run_windows=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("console")
            ),
            direct_call=lambda *_args, **_kwargs: 29,
            events=events,
        )

        self.assertEqual(29, service.dispatch(["codex"], {}))
        self.assertIn("channel_windows_console_proxy_failed", events[0][2])
        self.assertIn("channel_stdin_proxy_unavailable", events[1][2])

    def _delivery(self, inject, *, ready=lambda: True, events=None):
        from ciel_runtime_support.channel_inflight import ChannelInflightEffects
        from ciel_runtime_support.channel_terminal_proxy import ChannelTerminalPolicy, ChannelTerminalPolling
        events = events if events is not None else []
        cursor = [10]
        def log(*entry):
            events.append(entry)

        return ChannelDirectDeliveryServices(
            policy=ChannelTerminalPolicy(
                initial_cursor=lambda: cursor[0], enter_bytes=lambda _: bytes([13]),
                enter_label=lambda _: "cr", enter_is_fixed=lambda: True,
                unseen_retry_seconds=lambda: 30, inflight_is_stale=lambda *a: False,
                log=log,
            ),
            polling=ChannelTerminalPolling(
                inject_compact=mock.Mock(side_effect=AssertionError("no direct terminal compaction")),
                file_marker=lambda: (1.0, 1), should_check=lambda *a: True,
                active_tool_call=lambda: False, active_turn=lambda: False,
                inject_pending=inject, wake_state=lambda _: "completed",
                inflight_effects=lambda: ChannelInflightEffects(
                    commit_cursor=lambda n: cursor.__setitem__(0, n),
                    complete_wake=lambda n: events.append(("completed", n)),
                    release_wake=mock.Mock(), ensure_cursor=lambda: cursor[0], log=log,
                ),
                session_socket_ready=ready,
            ),
        )

    def test_windows_failure_keeps_socket_polling_before_direct_child_exit(self):
        events = []
        writes = []
        proc = mock.Mock(pid=51)
        proc.poll.side_effect = [None, None, 0]
        proc.wait.return_value = 0
        def inject(writer, last_id, enter, **options):
            self.assertIsNone(writer)
            self.assertFalse(options["terminal_available"])
            writes.append(last_id)
            if not options["injected_message_ids"] and last_id < 11:
                options["injected_message_ids"].append(11)
            return 11
        service = self._service(
            platform_name="nt", popen=lambda *a, **k: proc,
            run_windows=mock.Mock(side_effect=OSError(36, "private-body-marker")),
            delivery=self._delivery(inject, events=events), events=events,
        )
        with mock.patch("ciel_runtime_support.channel_terminal_dispatch.time.sleep"):
            self.assertEqual(0, service.dispatch(["claude"], {}, channel_wake_confirm_submit=True))
        self.assertEqual(10, writes[0])
        self.assertIn(("completed", 11), events)
        self.assertTrue(any("channel_direct_socket_poller_started" in str(e) for e in events))
        self.assertTrue(any("channel_direct_socket_poller_stopped" in str(e) for e in events))
        self.assertNotIn("private-body-marker", str(events))

    def test_direct_poller_starts_before_socket_is_ready(self):
        proc = mock.Mock(pid=52)
        proc.poll.side_effect = [None, None, 0]
        seen = []
        ready = [False]
        def inject(writer, last_id, enter, **options):
            self.assertIsNone(writer)
            self.assertFalse(options["terminal_available"])
            if not ready[0]:
                ready[0] = True
                return last_id
            seen.append(last_id)
            return 11
        delivery = self._delivery(inject, ready=lambda: ready[0])
        service = self._service(platform_name="other", popen=lambda *a, **k: proc, delivery=delivery)
        with mock.patch("ciel_runtime_support.channel_terminal_dispatch.time.sleep"), mock.patch(
            "ciel_runtime_support.channel_terminal_dispatch.time.time", side_effect=[100.0, 101.0]
        ):
            self.assertEqual(0, service.dispatch(["claude"], {}))
        self.assertEqual([10], seen)
        self.assertFalse(delivery.polling.inject_compact.called)

    def test_direct_channel_disabled_retains_blocking_call(self):
        delivery = self._delivery(mock.Mock())
        popen = mock.Mock(side_effect=AssertionError("unexpected supervised child"))
        service = self._service(platform_name="other", popen=popen, delivery=delivery)
        self.assertEqual(33, service.dispatch(["claude"], {}, inject_channel_messages=False))
        popen.assert_not_called()

    def test_proxy_handoff_retains_inflight_confirmation_without_resending(self):
        proc = mock.Mock(pid=53)
        proc.poll.side_effect = [None, 0]
        events = []
        inject = mock.Mock(side_effect=lambda writer, last_id, enter, **kw: last_id)
        def proxy(*args, **options):
            state = options["pending_poll_state"]
            state.last_id = 11
            state.inflight_message_id = 11
            state.inflight_cursor = 11
            state.inflight_started_at = 1
            raise OSError(36, "fixture")
        service = self._service(
            platform_name="nt", popen=lambda *a, **k: proc, run_windows=proxy,
            delivery=self._delivery(inject, events=events), events=events,
        )
        with mock.patch("ciel_runtime_support.channel_terminal_dispatch.time.sleep"):
            self.assertEqual(0, service.dispatch(["claude"], {}, channel_wake_confirm_submit=True))
        self.assertIn(("completed", 11), events)
        if inject.called:
            self.assertEqual(11, inject.call_args.args[1])

    def test_pressure_hold_survives_proxy_socket_handoff_without_failure_or_cursor(self):
        from dataclasses import replace
        from ciel_runtime_support.channel_terminal_dispatch import _DirectChannelPoller
        from ciel_runtime_support.channel_pending_poll import ChannelPendingInjectionOptions, ChannelPendingPollState

        control = mock.Mock()
        control.external_admission_paused.return_value = True
        events = []
        inject = mock.Mock(side_effect=AssertionError("frozen socket must not inject"))
        delivery = self._delivery(inject, events=events)
        delivery = replace(delivery, policy=replace(delivery.policy, compaction=control))
        state = ChannelPendingPollState(last_id=10)
        state.inflight_message_id = 11
        state.inflight_cursor = 11
        state.inflight_started_at = 20
        poller = _DirectChannelPoller(
            delivery, ChannelPendingInjectionOptions(True, False, True, False, 1, True, False, None, False),
            b"cr", state,
        )
        poller.poll(25)
        poller.poll(525)
        inject.assert_not_called()
        self.assertEqual(11, state.inflight_message_id)
        self.assertEqual(10, state.last_id)
        self.assertEqual(20, state.inflight_started_at)
        self.assertEqual([], events)

    def test_direct_child_survives_transient_lock_during_confirmation(self):
        from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
        proc = mock.Mock(pid=54)
        proc.poll.side_effect = [None, None, None, 0]
        events = []
        delivered = []
        def inject(writer, last_id, enter, **options):
            if last_id < 11:
                delivered.append(11)
                options["injected_message_ids"].append(11)
            return 11
        delivery = self._delivery(inject, events=events)
        from dataclasses import replace
        delivery = replace(delivery, polling=replace(
            delivery.polling,
            wake_state=mock.Mock(side_effect=[ChannelFileLockBusy(36, "fixture"), "completed"]),
        ))
        service = self._service(platform_name="other", popen=lambda *a, **k: proc,
                                delivery=delivery, events=events)
        with mock.patch("ciel_runtime_support.channel_terminal_dispatch.time.sleep"), mock.patch(
            "ciel_runtime_support.channel_terminal_dispatch.time.time", side_effect=[100.0, 101.0, 102.0]
        ):
            self.assertEqual(0, service.dispatch(["claude"], {}, channel_wake_confirm_submit=True))
        self.assertEqual([11], delivered)
        self.assertIn(("completed", 11), events)
        self.assertTrue(any("phase=lock_acquire" in str(e) for e in events))

    def test_windows_proxy_survives_pending_and_confirmation_lock_contention(self):
        from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
        from ciel_runtime_support.channel_pending_poll import ChannelPendingPollState
        from ciel_runtime_support.channel_inflight import ChannelInflightEffects
        from ciel_runtime_support.channel_terminal_proxy import ChannelTerminalPolicy, ChannelTerminalPolling
        proc = mock.Mock(pid=56)
        proc.poll.side_effect = [None, None, 0]
        proc.wait.return_value = 0
        events = []
        initial = mock.Mock(side_effect=[ChannelFileLockBusy(36, "fixture"), 11])
        policy = ChannelTerminalPolicy(
            initial_cursor=initial, enter_bytes=lambda _: bytes([13]), enter_label=lambda _: "cr",
            enter_is_fixed=lambda: True, unseen_retry_seconds=lambda: 30,
            inflight_is_stale=lambda *a: False, log=lambda *entry: events.append(entry),
        )
        state = ChannelPendingPollState(last_id=10)
        state.inflight_message_id = 11
        state.inflight_cursor = 11
        state.inflight_started_at = 1
        wake = mock.Mock(side_effect=[ChannelFileLockBusy(36, "fixture"), "completed"])
        inject = mock.Mock(side_effect=lambda writer, last_id, enter, **options: last_id)
        polling = ChannelTerminalPolling(
            inject_compact=lambda *a, **k: None, file_marker=lambda: (0.0, 0),
            should_check=lambda *a: False, active_tool_call=lambda: False,
            active_turn=lambda: False, inject_pending=inject, wake_state=wake,
            inflight_effects=lambda: ChannelInflightEffects(
                commit_cursor=lambda n: events.append(("commit", n)),
                complete_wake=lambda n: events.append(("complete", n)), release_wake=mock.Mock(),
                ensure_cursor=lambda: 11, log=policy.log,
            ),
        )
        services = SimpleNamespace(
            process=SimpleNamespace(popen=lambda *a, **k: proc, write_child_record=mock.Mock(),
                                    terminate_child=mock.Mock(), release_child_record=mock.Mock()),
            policy=policy, polling=polling,
            console=SimpleNamespace(open_conpty=lambda *a: None, reset_input_mode=mock.Mock(),
                                    mouse_guard=lambda: mock.Mock(), input_writer=lambda: mock.Mock(),
                                    startup_grace_seconds=lambda: 0, reset_interval_seconds=lambda n: n,
                                    active_turn=lambda: False, sleep=lambda n: None),
        )
        with mock.patch("ciel_runtime_support.channel_terminal_proxy.poll_pending_compaction", side_effect=lambda *a, **k: a[5]):
            result = run_windows_channel_terminal_proxy(["claude"], {}, services, pending_poll_state=state)
        self.assertEqual(0, result)
        self.assertIn(("complete", 11), events)
        self.assertTrue(any("phase=cursor_initialize" in str(e) for e in events))
        self.assertTrue(any("phase=inflight_lock_acquire" in str(e) for e in events))
        self.assertFalse(any("proxy_exception" in str(e) for e in events))

    def test_unattended_direct_fallback_does_not_inject_into_startup_dialogs(self):
        inject = mock.Mock(side_effect=AssertionError("unsafe startup injection"))
        delivery = self._delivery(inject)
        service = self._service(platform_name="other", delivery=delivery)
        self.assertEqual(33, service.dispatch(["claude"], {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"}))
        inject.assert_not_called()

    def test_direct_busy_aware_restart_stops_poller_and_releases_child(self):
        from ciel_runtime_support.runtime_session_restart import RuntimeSessionRestartControl, RuntimeSessionRestartRequest
        proc = mock.Mock(pid=55)
        proc.poll.return_value = None
        proc.wait.return_value = 0
        request = RuntimeSessionRestartRequest(
            id="fixture", source="test", reason="fixture", runtime="claude",
            target_pid=55, resume=True, requested_at=100, expires_at=200,
        )
        control = RuntimeSessionRestartControl(poll=lambda: request, clock=lambda: 110)
        inject = mock.Mock(side_effect=AssertionError("poll after restart"))
        events = []
        service = self._service(platform_name="other", popen=lambda *a, **k: proc,
                                delivery=self._delivery(inject), events=events)
        self.assertEqual(0, service.dispatch(["claude"], {}, restart_poll=control.incoming,
                                             restart_state=control))
        self.assertIs(request, control.request)
        inject.assert_not_called()
        self.assertIn(("release", None, 55), events)

    def test_posix_tty_uses_posix_adapter_and_composed_services(self):
        calls = []
        service = self._service(
            platform_name="posix",
            run_posix=lambda *args, **kwargs: calls.append(
                (args, kwargs)
            )
            or 23,
        )

        self.assertEqual(23, service.dispatch(["claude"], {"PATH": "x"}))
        self.assertEqual("services", calls[0][0][2])

    def test_parent_unattended_flag_is_passed_to_child(self):
        calls = []
        service = self._service(platform_name="nt", run_windows=lambda *args, **kwargs: calls.append(args) or 0)
        with mock.patch.dict("os.environ", {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"}):
            service.dispatch(["codex"], {})
        self.assertEqual("1", calls[0][1]["CIEL_RUNTIME_STARTUP_AUTO_ANSWER"])

    def test_direct_unattended_launch_reports_no_screen_automation(self):
        events = []
        service = self._service(platform_name="other", events=events)
        service.call_direct(["codex"], {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"})
        self.assertTrue(any("transport=direct rendered_screen=false automatic_injection=false" in str(event) for event in events))

    def test_console_fallback_holds_all_automatic_injection(self):
        proc = mock.Mock(pid=42)
        proc.poll.side_effect = [None, 0]
        proc.wait.return_value = 0
        logs = []
        writer = mock.Mock()
        process = SimpleNamespace(popen=lambda *a, **k: proc, write_child_record=mock.Mock(),
                                  terminate_child=mock.Mock(), release_child_record=mock.Mock())
        policy = SimpleNamespace(initial_cursor=lambda: 0, enter_bytes=lambda _: b"x", enter_label=lambda _: "x",
                                 unseen_retry_seconds=lambda: 1, inflight_is_stale=lambda *a: False,
                                 log=lambda *entry: logs.append(entry))
        polling = SimpleNamespace(inject_compact=mock.Mock(), file_marker=lambda: None,
                                  should_check=lambda *a: False, active_tool_call=lambda: False,
                                  active_turn=lambda: False, input_busy=lambda: False, inject_pending=mock.Mock(),
                                  wake_state=lambda _: None, inflight_effects=lambda: None,
                                  runtime_interaction=lambda: None)
        console = SimpleNamespace(open_conpty=lambda *a: None, reset_input_mode=mock.Mock(),
                                  mouse_guard=lambda: mock.Mock(), input_writer=lambda: writer,
                                  startup_grace_seconds=lambda: 0, reset_interval_seconds=lambda x: x,
                                  active_turn=lambda: False, sleep=lambda _: None)
        services = SimpleNamespace(process=process, policy=policy, polling=polling, console=console)
        with mock.patch("ciel_runtime_support.channel_terminal_proxy.poll_pending_compaction", side_effect=lambda *a, **k: a[5]) as compact, mock.patch("ciel_runtime_support.channel_terminal_proxy.poll_pending_channel_messages", side_effect=lambda *a, **k: a[3]) as pending:
            self.assertEqual(0, run_windows_channel_terminal_proxy(["codex"], {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"}, services))
        self.assertFalse(compact.call_args.kwargs["input_ready"])
        self.assertFalse(pending.call_args.kwargs["input_ready"])
        writer.write.assert_not_called()
        self.assertTrue(any("transport=console-input" in message for _, message in logs))

    def test_direct_record_lifecycle_is_always_released(self):
        events = []
        process = _Process(return_code=7)
        service = self._service(
            platform_name="other",
            popen=lambda *_args, **_kwargs: process,
            events=events,
        )
        record = Path("child.json")

        self.assertEqual(7, service.call_direct(["codex"], {}, record))
        self.assertEqual(("write", record, 42, ["codex"]), events[0])
        self.assertEqual(("terminate", 42, "current Codex"), events[1])
        self.assertEqual(("release", record, 42), events[2])


if __name__ == "__main__":
    unittest.main()
