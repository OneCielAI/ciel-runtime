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
