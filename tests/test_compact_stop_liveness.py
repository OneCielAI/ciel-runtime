"""Regression for compact completion, dispatch receipt races and protected wakes."""
import json
from datetime import datetime, timezone
import unittest

from ciel_runtime_support.channel_transcript import active_turn_from_text, active_tool_call_from_text
from ciel_runtime_support.channel_compact_poll import (ChannelCompactInjectionOptions, ChannelCompactPollServices,
    ChannelCompactPollState, poll_pending_compaction)
import test_claude_compact_continuation as compact_fixtures
from test_claude_compact_continuation import boundary
from test_claude_stream_recovery_runtime import SESSION, SCOPE, user


class CompactStopLivenessTests(unittest.TestCase):
    setUp = compact_fixtures.CompactContinuationTests.setUp
    mode = compact_fixtures.CompactContinuationTests.mode
    task = compact_fixtures.CompactContinuationTests.task

    def test_auto_checkpoint_releases_only_generation_busy_and_native_work_reopens(self):
        rows = [user("owned", "Finish approved task"), {"type": "assistant", "sessionId": SESSION,
            "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}},
            {"type": "user", "sessionId": SESSION, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "owned"}]}}, boundary()]
        text = "\n".join(json.dumps(row) for row in rows)
        self.assertFalse(active_turn_from_text(text))
        self.assertFalse(active_tool_call_from_text(text))
        for row in rows:
            self.h.monitor.observe(row)
        self.h.terminal.update(busy=active_turn_from_text(text), ready=True)
        self.h.clock.value += 5
        self.assertEqual("compact_resume", self.h.monitor.tick()["kind"])
        self.assertEqual(1, len(self.h.submitted))
        rows.append(user("new", "New real work"))
        self.assertTrue(active_turn_from_text("\n".join(json.dumps(row) for row in rows)))

    def test_native_continued_after_checkpoint_cancels_synthetic_resume(self):
        self.h.monitor.observe(boundary())
        self.h.monitor.observe({"type": "assistant", "sessionId": SESSION, "uuid": "native-resumed",
            "message": {"id": "native-message", "role": "assistant", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "new-tool", "name": "Read", "input": {}}]}})
        self.h.clock.value += 5
        self.assertIsNone(self.h.monitor.tick())
        self.assertEqual([], self.h.submitted)
        self.assertTrue(any("native_continued" in line for line in self.h.logs))

    def test_checkpoint_never_clears_pending_tool_ownership_or_invalid_evidence(self):
        start = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}}
        for bad in [boundary(compactMetadata={"trigger": "manual", "preTokens": 100, "postTokens": 20}),
                    boundary(compactMetadata={"trigger": "auto", "preTokens": 20, "postTokens": 100}),
                    boundary(isSidechain=True), boundary(compactMetadata={"trigger": "auto", "preTokens": True, "postTokens": 0})]:
            self.assertTrue(active_turn_from_text(json.dumps(start) + "\n" + json.dumps(bad)))
        text = json.dumps(start) + "\n" + json.dumps(boundary())
        self.assertTrue(active_tool_call_from_text(text))

    def test_echo_before_receipt_waits_then_confirmed_dispatch_resumes_once(self):
        dispatch = {"scope": SCOPE.payload(), "automatic": True, "source": "model-compact-safeguard",
                    "dispatched_at": self.h.clock() - 1, "request_id": "owned"}
        self.h.monitor.compact_dispatch = lambda: dispatch
        stamp = datetime.fromtimestamp(self.h.clock(), timezone.utc).isoformat()
        echo = dict(user("echo", "/compact"), timestamp=stamp)
        self.h.monitor.observe(echo)
        self.h.monitor.observe(boundary(timestamp=stamp, compactMetadata={"trigger": "manual", "preTokens": 20000, "postTokens": 4000}))
        self.h.clock.value += 5
        self.assertIsNone(self.h.monitor.tick())
        self.assertEqual([], self.h.submitted)
        dispatch["submission"] = "submitted"
        self.assertIsNone(self.h.monitor.tick())
        self.h.clock.value += 5
        self.assertEqual("compact_resume", self.h.monitor.tick()["kind"])
        self.assertIsNone(self.h.monitor.tick())
        self.assertEqual(1, len(self.h.submitted))

    def test_unconfirmed_echo_failure_never_grants_authority(self):
        for result in ("failed", "uncertain"):
            self.h.monitor.reset()
            self.mode()
            self.task()
            dispatch = {"scope": SCOPE.payload(), "automatic": True, "source": "model-compact-safeguard",
                        "dispatched_at": self.h.clock() - 1, "request_id": "owned"}
            self.h.monitor.compact_dispatch = lambda: dispatch
            stamp = datetime.fromtimestamp(self.h.clock(), timezone.utc).isoformat()
            self.h.monitor.observe(dict(user("echo", "/compact"), timestamp=stamp))
            dispatch["submission"] = result
            self.h.monitor.observe(boundary(timestamp=stamp, compactMetadata={"trigger": "manual", "preTokens": 20000, "postTokens": 4000}))
            self.h.clock.value += 5
            self.assertIsNone(self.h.monitor.tick())
            self.assertFalse(self.h.monitor._compact.task_open)
        self.assertEqual([], self.h.submitted)

    def test_unconfirmed_receipt_expires_without_resumption_authority(self):
        dispatch = {"scope": SCOPE.payload(), "automatic": True, "source": "model-compact-safeguard",
                    "dispatched_at": self.h.clock() - 1, "request_id": "owned"}
        self.h.monitor.compact_dispatch = lambda: dispatch
        stamp = datetime.fromtimestamp(self.h.clock(), timezone.utc).isoformat()
        self.h.monitor.observe(dict(user("echo", "/compact"), timestamp=stamp))
        self.h.clock.value += 1201
        self.assertIsNone(self.h.monitor.tick())
        self.assertEqual([], self.h.monitor._compact_bookkeeping)
        self.assertFalse(self.h.monitor._compact.task_open)
        self.assertEqual([], self.h.submitted)


class ProtectedCompactPollTests(unittest.TestCase):
    def test_released_gate_keeps_wake_ownership_but_does_not_starve_commands(self):
        calls = []
        services = ChannelCompactPollServices(lambda *args, **kw: calls.append(kw) or "injected", lambda: False)
        options = ChannelCompactInjectionOptions(1, False, False, None, runtime="claude")
        poll_pending_compaction(100, None, b"cr", 42, ChannelCompactPollState(), options, services,
                                recovery_protected=True)
        self.assertEqual(1, len(calls))
        calls.clear()
        poll_pending_compaction(100, None, b"cr", 42, ChannelCompactPollState(), options, services)
        self.assertEqual([], calls)
        poll_pending_compaction(100, None, b"cr", 42, ChannelCompactPollState(), options, services,
                                recovery_protected=True, input_ready=False)
        self.assertEqual([], calls)


class WorkSnapshotReadTests(unittest.TestCase):
    def test_mcp_status_uses_readonly_snapshot_without_exclusive_lock(self):
        from unittest.mock import patch
        from test_work_mcp_tools import WorkMcpToolsTests
        case = WorkMcpToolsTests()
        case.setUp()
        try:
            case.plan()
            with patch.object(case.store, "read", side_effect=AssertionError("exclusive read forbidden")):
                reply = case.call("work_status", format="json")
            self.assertFalse(reply["error"])
            self.assertEqual("goal", json.loads(reply["text"])["active_root"]["title"])
        finally:
            case.doCleanups()
