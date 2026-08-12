import threading
import unittest
from unittest import mock

from ciel_runtime_support.channel_backlog import (
    ChannelBacklogCursors,
    ChannelBacklogRuntime,
    ChannelBacklogService,
)


class ChannelBacklogServiceTests(unittest.TestCase):
    def service(self, *, failing_write=False):
        state = {"llm": 3}
        writer = mock.Mock(side_effect=OSError("disk") if failing_write else None)
        floor = mock.Mock()
        log = mock.Mock()
        recovery = {"cached": True}
        service = ChannelBacklogService(
            ChannelBacklogCursors(
                lambda: 10,
                threading.RLock(),
                lambda: state["llm"],
                writer,
                lambda value: state.__setitem__("llm", value),
                floor,
            ),
            ChannelBacklogRuntime(recovery, threading.Condition(), log),
        )
        return service, state, recovery, writer, floor, log

    def test_clear_advances_only_llm_cursor_and_recovery_cache(self):
        service, state, recovery, writer, floor, _log = self.service()
        self.assertEqual({"chat_tail": 10, "discarded_llm": 7}, service.clear())
        self.assertEqual({"llm": 10}, state)
        self.assertEqual({}, recovery)
        writer.assert_called_once_with(10)
        floor.assert_called_once_with(10)

    def test_status_has_no_external_mcp_cursor_or_sessions(self):
        service, *_ = self.service()
        self.assertEqual({"chat_tail": 10, "pending_llm": 7}, service.status())

    def test_cursor_write_failure_is_logged_without_aborting_clear(self):
        service, _state, _recovery, _writer, _floor, log = self.service(failing_write=True)
        self.assertEqual(10, service.clear()["chat_tail"])
        self.assertTrue(any("channel_llm_cursor_write_failed" in call.args[1] for call in log.call_args_list))


if __name__ == "__main__":
    unittest.main()
