import threading
import unittest
from unittest import mock

from ciel_runtime_support.channel_backlog import (
    ChannelBacklogCursors,
    ChannelBacklogRuntime,
    ChannelBacklogService,
)


class ChannelBacklogServiceTests(unittest.TestCase):
    def service(self, *, failing_llm_write=False):
        state = {"llm": 3, "mcp": 5}
        llm_write = mock.Mock(side_effect=OSError("disk") if failing_llm_write else None)
        mcp_write = mock.Mock()
        floor_write = mock.Mock()
        log = mock.Mock()
        sessions = {"a": {"last_id": 2}, "b": {"last_id": "bad"}}
        recovery = {"cached": True}
        condition = threading.Condition()
        service = ChannelBacklogService(
            ChannelBacklogCursors(
                lambda: 10,
                threading.RLock(),
                lambda: state["llm"],
                llm_write,
                lambda value: state.__setitem__("llm", value),
                floor_write,
                threading.RLock(),
                lambda: state["mcp"],
                mcp_write,
                lambda value: state.__setitem__("mcp", value),
            ),
            ChannelBacklogRuntime(recovery, threading.RLock(), sessions, condition, log),
        )
        return service, state, sessions, recovery, llm_write, floor_write, mcp_write, log

    def test_clear_advances_all_cursors_sessions_and_recovery_cache(self):
        service, state, sessions, recovery, llm_write, floor_write, mcp_write, _log = self.service()
        stats = service.clear()

        self.assertEqual({"llm": 10, "mcp": 10}, state)
        self.assertEqual([10, 10], [sessions["a"]["last_id"], sessions["b"]["last_id"]])
        self.assertEqual({}, recovery)
        self.assertEqual(7, stats["discarded_llm"])
        self.assertEqual(5, stats["discarded_mcp"])
        llm_write.assert_called_once_with(10)
        floor_write.assert_called_once_with(10)
        mcp_write.assert_called_once_with(10)

    def test_status_is_read_only_projection(self):
        service, _state, _sessions, _recovery, *_rest = self.service()
        self.assertEqual(
            {"chat_tail": 10, "pending_llm": 7, "pending_mcp": 5, "mcp_sessions": 2},
            service.status(),
        )

    def test_cursor_write_failure_is_logged_without_aborting_clear(self):
        service, _state, _sessions, _recovery, _llm_write, _floor, _mcp, log = self.service(
            failing_llm_write=True
        )
        self.assertEqual(10, service.clear()["chat_tail"])
        self.assertTrue(any("channel_llm_cursor_write_failed" in call.args[1] for call in log.call_args_list))


if __name__ == "__main__":
    unittest.main()
