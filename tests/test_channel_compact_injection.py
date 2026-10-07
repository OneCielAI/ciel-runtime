import unittest

from ciel_runtime_support.channel_compact_injection import (
    ChannelCompactInjectionService,
    ChannelCompactRequestPorts,
    ChannelCompactRuntimePorts,
)


class ChannelCompactInjectionServiceTests(unittest.TestCase):
    def _service(
        self,
        request,
        *,
        active_tool_call=False,
        active_turn=False,
        writes=None,
        clears=None,
        logs=None,
    ):
        writes = writes if writes is not None else []
        clears = clears if clears is not None else []
        logs = logs if logs is not None else []
        return ChannelCompactInjectionService(
            request=ChannelCompactRequestPorts(
                read=lambda: request,
                clear=clears.append,
            ),
            runtime=ChannelCompactRuntimePorts(
                active_tool_call=lambda: active_tool_call,
                active_turn=lambda: active_turn,
                enter_bytes=lambda value: value or b"\r",
                write_prompt=lambda *args, **kwargs: writes.append((args, kwargs)),
                enter_label=lambda value: repr(value),
            ),
            log=lambda level, message: logs.append((level, message)),
        )

    def test_new_session_types_the_runtime_command(self):
        for runtime, command in (("claude", "/clear"), ("codex", "/new")):
            writes, clears = [], []
            service = self._service(
                {"id": f"n-{runtime}", "action": "new_session", "command": "/new"},
                writes=writes,
                clears=clears,
            )
            self.assertEqual("injected", service.inject(7, runtime=runtime))
            self.assertEqual(command, writes[0][0][1])
            self.assertEqual([f"n-{runtime}"], clears)

    def test_new_session_without_a_known_command_is_left_queued(self):
        writes, clears, logs = [], [], []
        service = self._service(
            {"id": "n-1", "action": "new_session"},
            writes=writes,
            clears=clears,
            logs=logs,
        )
        self.assertEqual("deferred", service.inject(7, runtime="muse"))
        self.assertEqual([], writes)
        self.assertEqual([], clears)
        self.assertIn("unsupported_runtime", logs[-1][1])

    def test_actions_owned_by_another_consumer_are_not_typed(self):
        writes, clears = [], []
        service = self._service(
            {"id": "c-1", "action": "compact"},
            writes=writes,
            clears=clears,
        )
        self.assertEqual("none", service.inject(7, runtime="codex", actions=frozenset({"new_session"})))
        self.assertEqual([], writes)
        self.assertEqual([], clears)

    def test_goal_clear_is_typed_while_a_turn_runs(self):
        # An active goal keeps starting turns; waiting for idle would never type it.
        for runtime in ("claude", "codex"):
            writes, clears = [], []
            service = self._service(
                {"id": f"g-{runtime}", "action": "goal_clear", "command": "/goal clear"},
                active_tool_call=True,
                active_turn=True,
                writes=writes,
                clears=clears,
            )
            self.assertEqual("injected", service.inject(7, runtime=runtime))
            self.assertEqual("/goal clear", writes[0][0][1])
            self.assertEqual([f"g-{runtime}"], clears)
        writes = []
        service = self._service({"id": "g-muse", "action": "goal_clear"}, writes=writes)
        self.assertEqual("deferred", service.inject(7, runtime="muse"))
        self.assertEqual([], writes)

    def test_missing_request_is_a_noop(self):
        self.assertEqual("none", self._service(None).inject(7))

    def test_active_turn_defers_without_consuming_request(self):
        clears = []
        logs = []
        service = self._service(
            {"id": "req-1", "command": "/compact"},
            active_turn=True,
            clears=clears,
            logs=logs,
        )

        self.assertEqual("deferred", service.inject(7))
        self.assertEqual([], clears)
        self.assertIn("reason=active_turn", logs[-1][1])

    def test_injection_normalizes_command_and_clears_matching_request(self):
        writes = []
        clears = []
        service = self._service(
            {"id": "req-2", "command": "/unsafe"},
            writes=writes,
            clears=clears,
        )

        self.assertEqual(
            "injected",
            service.inject(
                7,
                b"\n",
                submit_retry_count=3,
                confirm_submit=True,
            ),
        )
        args, options = writes[0]
        self.assertEqual((7, "/compact", b"\n"), args)
        self.assertEqual(3, options["submit_retry_count"])
        self.assertTrue(options["confirm_submit"])
        self.assertEqual(["req-2"], clears)


if __name__ == "__main__":
    unittest.main()
