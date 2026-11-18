"""Inputs arriving while a restart is pending are held, not consumed, and picked up after it.

User requirement 2026-11-17 (decision: the hold starts when the request is received).
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support import restart_hold
from ciel_runtime_support.channel_pending_poll import (ChannelPendingInjectionOptions, ChannelPendingPollPolicy,
                                                       ChannelPendingPollServices, ChannelPendingPollState,
                                                       poll_pending_channel_messages)
from ciel_runtime_support.channel_terminal_proxy import _restart_or_compaction_hold


class RestartHoldMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_marker_keeps_the_first_claim_time_and_ends_once(self):
        first = restart_hold.begin(self.root, 4242, "a" * 32, now=100.0)
        self.assertEqual(100.0, restart_hold.begin(self.root, 4242, "a" * 32, now=500.0)["started_at"])
        self.assertEqual(first, restart_hold.current(self.root, 4242, now=200.0))
        self.assertIsNone(restart_hold.current(self.root, 9999, now=200.0))
        self.assertEqual([{"pid": 4242, "request_id": "a" * 12, "started_at": 100.0}],
                         restart_hold.listing(self.root, now=200.0))
        self.assertEqual(100.0, restart_hold.end(self.root, 4242, now=200.0)["started_at"])
        self.assertIsNone(restart_hold.end(self.root, 4242, now=200.0))

    def test_a_stale_or_broken_marker_never_shapes_a_launch(self):
        restart_hold.begin(self.root, 1, "b" * 32, now=0.0)
        self.assertIsNone(restart_hold.current(self.root, 1, now=restart_hold.MAX_AGE_SECONDS + 1))
        self.assertFalse((self.root / "restart-hold-1.json").exists())
        (self.root / "restart-hold-2.json").write_text("{broken", encoding="utf-8")
        self.assertIsNone(restart_hold.current(self.root, 2))

    def test_a_claimed_or_waiting_restart_pauses_pending_delivery(self):
        self.assertFalse(restart_hold.holds(None))
        state = SimpleNamespace(request=None, deferred=None)
        compaction = SimpleNamespace(compaction=SimpleNamespace(external_admission_paused=lambda: False))
        paused = _restart_or_compaction_hold(compaction, state)
        self.assertFalse(paused())
        state.deferred = object()      # waiting for the running turn
        self.assertTrue(paused())
        state.deferred, state.request = None, object()   # released, about to stop the CLI
        self.assertTrue(paused())
        injected = []
        services = ChannelPendingPollServices(
            file_marker=lambda: (1.0, 1), should_check=lambda *_args: True, active=lambda: False,
            ensure_cursor=lambda: 0, inject_pending=lambda *args, **kwargs: injected.append(args) or 0,
            log=lambda *_args: None, session_socket_ready=lambda: True, external_admission_paused=paused)
        options = ChannelPendingInjectionOptions(True, False, False, False, 1, False, False, None)
        poll_state = ChannelPendingPollState(last_id=0)
        poll_pending_channel_messages(100.0, None, b"\r", poll_state, options,
                                      ChannelPendingPollPolicy("unit", "active_turn"), services)
        self.assertEqual([], injected)
        self.assertTrue(poll_state.pending_recheck)
        state.request = None
        poll_pending_channel_messages(200.0, None, b"\r", poll_state, options,
                                      ChannelPendingPollPolicy("unit", "active_turn"), services)
        self.assertEqual(1, len(injected))


class RestartHoldLaunchTests(unittest.TestCase):
    def run_launch(self, hold_started_at):
        with tempfile.TemporaryDirectory(prefix="ciel-hold-launch-") as td:
            root = Path(td)
            chat_path = root / "chat-messages.jsonl"
            now = time.time()
            def stamp(seconds):
                return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - seconds))

            chat_path.write_text(
                json.dumps({"id": 10, "time": stamp(3600), "channel": "room", "message": "before the hold"}) + "\n"
                + json.dumps({"id": 11, "time": stamp(1500), "channel": "room", "message": "during the hold"}) + "\n",
                encoding="utf-8")
            (root / "channel-llm-cursor.json").write_text('{"last_id":9}\n', encoding="utf-8")
            if hold_started_at is not None:
                restart_hold.begin(root, os.getpid(), "c" * 32, now=now - hold_started_at)
            original = ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID
            try:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = None
                with (
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_path),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", root / "channel-llm-cursor.json"),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_CLEAR_FLOOR_PATH", root / "floor.json"),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_LAUNCH_GUARD_PATH", root / "guard.json"),
                    mock.patch.object(ciel_runtime, "ROUTER_INSTANCE_DIR", root),
                    mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_LAUNCH_RECENT_SECONDS": "600"}, clear=False),
                ):
                    last = ciel_runtime.prepare_channel_llm_delivery_for_launch()
            finally:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = original
            return last, (root / f"restart-hold-{os.getpid()}.json").exists()

    def test_inputs_queued_after_the_hold_began_survive_the_launch_fast_forward(self):
        # Without a hold, a row older than the 600 s replay window is skipped.
        self.assertEqual((11, False), self.run_launch(None))
        # The restart waited 2000 s for the turn: the row that came in during
        # the hold is still delivered after the relaunch, and the hold ends.
        self.assertEqual((10, False), self.run_launch(2000))

    def test_restart_hold_release_uses_its_bound_state_dir_and_ends_once(self):
        from ciel_runtime_support.channel_delivery_context import restart_hold_release

        restart_hold.begin(self.root, os.getpid(), "d" * 32, now=123.0)
        release = restart_hold_release(lambda: self.root)
        self.assertEqual(123.0, release())
        self.assertIsNone(release())


if __name__ == "__main__":
    unittest.main()
