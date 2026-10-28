import unittest
from unittest.mock import Mock

from ciel_runtime_support.channel_inflight import (
    ChannelInflightEffects,
    ChannelInflightPolicy,
    ChannelInflightSnapshot,
    advance_channel_inflight,
)


class ChannelInflightStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.committed = []
        self.completed = []
        self.released = []
        self.logs = []
        self.effects = ChannelInflightEffects(
            commit_cursor=self.committed.append,
            complete_wake=self.completed.append,
            release_wake=self.released.append,
            ensure_cursor=lambda: 77,
            log=lambda level, message: self.logs.append((level, message)),
        )

    def policy(self, *, stale=False, commit_stale=False):
        return ChannelInflightPolicy(
            unseen_retry_seconds=20.0,
            waiting_log_interval=30.0,
            is_stale=lambda state, started, now: stale,
            commit_cursor_on_stale=commit_stale,
            log_namespace="channel_test",
            stale_event="stale_inflight",
        )

    def snapshot(self, state, *, now=100.0, started=90.0, logged=90.0):
        return ChannelInflightSnapshot(
            message_id=12,
            cursor=11,
            wake_state=state,
            started_at=started,
            logged_at=logged,
            now=now,
            last_id=10,
        )

    def test_completed_commits_cursor_and_completes_wake(self):
        update = advance_channel_inflight(self.snapshot("completed"), self.policy(), self.effects)

        self.assertEqual("completed", update.action)
        self.assertIsNone(update.message_id)
        self.assertTrue(update.pending_recheck)
        self.assertEqual([11], self.committed)
        self.assertEqual([12], self.completed)

    def test_admission_pause_observes_exact_evidence_but_never_ages_any_state(self):
        from dataclasses import replace
        for state in ("missing", "unknown", "queued", "pending"):
            with self.subTest(state=state):
                update = advance_channel_inflight(
                    replace(self.snapshot(state, now=1000000), admission_paused=True),
                    self.policy(stale=True, commit_stale=True), self.effects,
                )
                self.assertEqual(12, update.message_id)
                self.assertEqual([], self.released)
                self.assertEqual([], self.committed)
        update = advance_channel_inflight(
            replace(self.snapshot("completed"), admission_paused=True),
            self.policy(stale=True), self.effects,
        )
        self.assertEqual("completed", update.action)
        self.assertEqual([12], self.completed)

    def test_exact_failed_attempt_settles_while_admission_paused_without_release(self):
        from dataclasses import replace
        for outcome in ("failed", "retry"):
            with self.subTest(outcome=outcome):
                failed = Mock(return_value=outcome)
                update = advance_channel_inflight(
                    replace(self.snapshot("turn_failed:0:99.000:api_error 400"), admission_paused=True),
                    self.policy(stale=True), replace(self.effects, turn_failed=failed),
                )
                self.assertEqual("retrying" if outcome == "retry" else "failed", update.action)
                failed.assert_called_once_with(12, "turn_failed:0:99.000:api_error 400", 90)
                self.assertIsNone(update.message_id)
                self.assertEqual([], self.released)

    def test_unseen_missing_wake_is_released_and_cursor_reloaded(self):
        update = advance_channel_inflight(
            self.snapshot("missing", now=120.0, started=90.0),
            self.policy(),
            self.effects,
        )

        self.assertEqual("failed", update.action)
        self.assertEqual(77, update.last_id)
        self.assertEqual([12], self.released)

    def test_stale_policy_controls_cursor_commit(self):
        update = advance_channel_inflight(
            self.snapshot("queued"),
            self.policy(stale=True, commit_stale=True),
            self.effects,
        )

        self.assertEqual("failed", update.action)
        self.assertEqual([11], self.committed)
        self.assertEqual([12], self.released)

    def test_recovery_blocks_both_age_only_failure_branches(self):
        from dataclasses import replace
        for wake_state in ("unknown", "missing"):
            with self.subTest(wake_state=wake_state):
                stale = Mock(return_value=True)
                snapshot = replace(
                    self.snapshot(wake_state, now=100000.0, started=1.0),
                    recovery_protected=True,
                )
                update = advance_channel_inflight(
                    snapshot, replace(self.policy(), is_stale=stale), self.effects,
                )
                self.assertEqual("waiting", update.action)
                self.assertEqual(12, update.message_id)
                self.assertFalse(update.pending_recheck)
                stale.assert_not_called()
        self.assertEqual([], self.released)
        self.assertEqual([], self.completed)
        self.assertEqual([], self.committed)

    def test_recovery_does_not_suppress_verified_resolution(self):
        from dataclasses import replace
        for wake_state in ("completed", "interrupted_uncertain"):
            with self.subTest(wake_state=wake_state):
                update = advance_channel_inflight(
                    replace(self.snapshot(wake_state), recovery_protected=True),
                    self.policy(stale=True), self.effects,
                )
                self.assertEqual(wake_state, update.action)
                self.assertIsNone(update.message_id)
        self.assertEqual([12], self.completed)
        self.assertEqual([], self.released)
        self.assertEqual([11, 11], self.committed)

    def test_waiting_transition_only_updates_log_time(self):
        update = advance_channel_inflight(
            self.snapshot("pending", now=125.0, logged=90.0),
            self.policy(),
            self.effects,
        )

        self.assertEqual("waiting", update.action)
        self.assertEqual(125.0, update.logged_at)
        self.assertFalse(update.pending_recheck)
        self.assertTrue(any("waiting_for_turn_completion" in message for _, message in self.logs))

    def test_no_transition_preserves_snapshot(self):
        update = advance_channel_inflight(
            self.snapshot("pending", now=100.0, logged=90.0),
            self.policy(),
            self.effects,
        )

        self.assertEqual("none", update.action)
        self.assertEqual(12, update.message_id)
        self.assertEqual(10, update.last_id)


if __name__ == "__main__":
    unittest.main()
