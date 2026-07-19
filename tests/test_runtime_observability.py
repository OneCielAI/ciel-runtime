import tempfile
from pathlib import Path
import unittest
from unittest import mock

import ciel_runtime


class RuntimeObservabilityTests(unittest.TestCase):
    def test_sse_event_projection_failure_is_observable(self):
        trace = {"event_count": object(), "events": []}
        with mock.patch.object(ciel_runtime, "router_log") as log:
            ciel_runtime.record_outgoing_sse_event(trace, "content_block_delta", {})

        self.assertIn("sse_trace_event_record_failed", log.call_args.args[1])

    def test_sse_trace_persistence_failure_is_observable(self):
        trace = {}
        trace["cycle"] = trace
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmp)),
                mock.patch.object(ciel_runtime, "router_log") as log,
            ):
                ciel_runtime.finish_outgoing_sse_trace(trace, outcome="complete")

        self.assertIn("sse_trace_finish_failed", log.call_args.args[1])

    def test_channel_delivery_guard_state_failure_is_observable(self):
        class ReadOnlyHandler:
            def __setattr__(self, _name, _value):
                raise RuntimeError("read only")

        body = {"metadata": {"ciel_runtime_channel_cursor_last_id": "9"}}
        with mock.patch.object(ciel_runtime, "router_log") as log:
            ciel_runtime.begin_pending_channel_delivery(ReadOnlyHandler(), body)  # type: ignore[arg-type]

        self.assertIn("channel_delivery_guard_begin_failed", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
