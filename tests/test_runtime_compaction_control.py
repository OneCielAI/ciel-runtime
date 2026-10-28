import copy
import json
import tempfile
import threading
import unittest
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.context_summary_policy import ContextSummaryPolicy
from ciel_runtime_support.runtime_compaction_control import (
    CompactionScope,
    RuntimeCompactionControl,
    RuntimeCompactionPolicy,
    RuntimeCompactionPorts,
    build_runtime_compaction_control,
    native_checkpoint_from_transcript,
)
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository


def compact_kind(body):
    policy = ContextSummaryPolicy(
        lambda _value: 1, lambda value: int(value), str, str,
        lambda value: str((value.get("messages") or [{}])[-1].get("content", "")),
    )
    return policy.compact_request_kind(body)


def boundary(session_id, when, identity="checkpoint-1"):
    return {
        "type": "system", "subtype": "compact_boundary", "sessionId": session_id,
        "timestamp": datetime.fromtimestamp(when, timezone.utc).isoformat(),
        "uuid": identity, "isSidechain": False,
        "compactMetadata": {"preTokens": 1200, "postTokens": 120},
    }


class _ControllerHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = 1800000000.0
        clock_patch = patch("ciel_runtime_support.channel_compact_request_repository.time.time", side_effect=lambda: self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.scope = CompactionScope("claude", "session-a", "launch-a")
        self.logs = []
        self.trace = self.root / "session-a.jsonl"
        self.trace.write_text("", encoding="utf-8")
        effects = SecureJsonEffects(log=lambda *args: self.logs.append(args))
        artifact = SecureJsonRepository(self.root / "compact.json", effects)
        self.repository = ChannelCompactRequestRepository(
            artifact.path, threading.Lock(), artifact.save,
            lambda value, limit: value[:limit], effects.log, lambda: 10,
        )
        self.state = SecureJsonRepository(self.root / "control.json", effects)
        self.lock = threading.Lock()
        self.control = self.controller()

    def controller(self):
        return RuntimeCompactionControl(RuntimeCompactionPorts(
            load=lambda: self.state.load("compaction"),
            save=lambda value: self.state.save(value, "compaction"),
            lock=lambda: self.lock, scope=lambda: self.scope,
            checkpoint=lambda scope: native_checkpoint_from_transcript(self.trace, scope),
            queue=self.repository.queue_automatic, clear=self.repository.clear,
            compact_kind=compact_kind, log=lambda *args: self.logs.append(args),
            now=lambda: self.clock,
        ), RuntimeCompactionPolicy(confirmation_timeout_seconds=10, retry_cooldown_seconds=3, max_attempts=2))

    def factory(self):
        return build_runtime_compaction_control(
            self.state.path, scope=lambda: self.scope,
            transcript_path=lambda: self.trace, requests=self.repository,
            compact_kind=compact_kind, log=lambda *args: self.logs.append(args),
            policy=RuntimeCompactionPolicy(
                confirmation_timeout_seconds=10, retry_cooldown_seconds=3, max_attempts=2),
        )

    def request(self, tokens=1200, body=None):
        with patch("ciel_runtime_support.channel_compact_request_repository.time.time", return_value=self.clock):
            self.control.observe_request(body or {"messages": [{"role": "user", "content": "ordinary"}]}, tokens, 1000)

    def append(self, record):
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def snapshot(self):
        return self.state.load("test")


class RuntimeCompactionControlTests(_ControllerHarness):
    def test_controller_ports_are_bounded(self):
        self.assertLessEqual(len(fields(RuntimeCompactionPorts)), 10)

    def test_admission_reason_tracks_checkpoint_shrink_and_uncertainty(self):
        self.assertEqual("", self.control.admission_reason())
        self.request()
        self.assertEqual("compaction_checkpoint_pending", self.control.admission_reason())
        self.clock += 1
        self.append(boundary("session-a", self.clock))
        self.assertEqual("compaction_shrink_pending", self.control.admission_reason())
        self.request(120)
        self.assertEqual("", self.control.admission_reason())

    def test_corruption_reason_is_not_reported_as_success(self):
        self.state.path.write_text("broken", encoding="utf-8")
        self.assertEqual("state_unavailable", self.factory().admission_reason())
        self.assertEqual("state_unavailable", self.factory().admission_reason())

    def test_exact_terminal_recovery_gate_is_shared_by_runtime_scopes(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                self.scope = CompactionScope(runtime, "session-a", "launch-" + runtime)
                self.request()
                self.assertTrue(self.control.external_admission_paused())
                request = self.repository.read()
                self.assertTrue(self.control.command_ready(request))
                self.control.command_failed(request, "rpc_unconfirmed", uncertain=True)
                self.assertEqual("failed_open", self.snapshot()["phase"])
                self.clock += 100
                self.assertFalse(self.control.external_admission_paused())
                self.assertIsNone(self.repository.read())

    def test_small_ordinary_turn_does_not_touch_state_or_usage(self):
        body = {"messages": [{"role": "user", "content": "hello"}], "usage": {"input_tokens": 11}}
        before = copy.deepcopy(body)
        self.request(11, body)
        self.assertEqual(before, body)
        self.assertEqual({}, self.snapshot())
        self.assertFalse(self.control.external_admission_paused())

    def test_pressure_dedupes_and_submission_is_not_completion(self):
        self.request()
        request = self.repository.read()
        self.request(1300)
        self.assertEqual(request["id"], self.repository.read()["id"])
        self.assertTrue(self.control.command_ready(request))
        self.control.command_submitted(request)
        self.repository.clear(request["id"])
        self.assertTrue(self.control.external_admission_paused())
        self.request(90)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_checkpoint", self.snapshot()["phase"])

    def test_persisted_checkpoint_and_next_shrink_release_after_handoff(self):
        self.request()
        request = self.repository.read()
        self.assertTrue(self.control.command_ready(request))
        self.control.command_submitted(request)
        self.repository.clear(request["id"])
        self.clock += 1
        self.append(boundary("session-a", self.clock))
        self.control = self.controller()
        # Persistence is necessary, but a smaller raw request is still needed.
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.snapshot()["phase"])
        self.request(120)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("verified", self.snapshot()["phase"])
        self.assertEqual(120, self.snapshot()["next_raw_tokens"])

    def test_checkpoint_that_does_not_shrink_is_spent_and_bounded(self):
        self.request()
        first = self.repository.read()
        self.assertTrue(self.control.command_ready(first))
        self.control.command_submitted(first)
        self.repository.clear(first["id"])
        self.clock += 1
        self.append(boundary("session-a", self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.request(1300)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("retry_wait", self.snapshot()["phase"])
        self.assertEqual("", self.snapshot()["checkpoint_id"])
        self.clock += 4
        with patch("ciel_runtime_support.channel_compact_request_repository.time.time", return_value=self.clock):
            self.assertTrue(self.control.external_admission_paused())
        second = self.repository.read()
        self.assertNotEqual(first["id"], second["id"])
        # The spent checkpoint cannot satisfy the second attempt.
        self.assertTrue(self.control.command_ready(second))
        self.control.command_submitted(second)
        self.request(120)
        self.assertTrue(self.control.external_admission_paused())

    def test_retry_before_late_checkpoint_persistence_verifies_same_cycle(self):
        # Claude Code 2.1.303 writes compact_boundary after its automatic retry.
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.request(1200)
        self.request(1200, native)
        self.request(120)
        self.assertEqual("compacting", self.snapshot()["phase"])
        self.assertTrue(self.control.external_admission_paused())
        self.clock += 1
        self.append(boundary("session-a", self.clock))
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("verified", self.snapshot()["phase"])
        self.assertEqual(120, self.snapshot()["next_raw_tokens"])

    def test_early_small_request_cannot_verify_a_full_checkpoint_or_later_cycle(self):
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.request(1200, native)
        self.request(120)
        self.clock += 1
        record = boundary("session-a", self.clock)
        record["compactMetadata"]["postTokens"] = 1500
        self.append(record)
        self.assertTrue(self.control.external_admission_paused())
        self.assertNotEqual("verified", self.snapshot()["phase"])
        self.assertNotIn("shrink_observed", self.snapshot())
        self.clock += 4
        self.request(1200, native)
        self.clock += 1
        self.append(boundary("session-a", self.clock, "second"))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.snapshot()["phase"])

    def test_client_started_compactions_count_toward_the_bound(self):
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        for attempt in (1, 2):
            self.request(1200, native)
            self.assertEqual(attempt, self.snapshot()["attempts"])
            self.clock += 1
            self.append(boundary("session-a", self.clock, f"reactive-{attempt}"))
            self.request(1300)
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())

    def test_checkpoint_reporting_full_post_tokens_is_not_release(self):
        self.request()
        request = self.repository.read()
        self.assertTrue(self.control.command_ready(request))
        self.control.command_submitted(request)
        self.clock += 1
        record = boundary("session-a", self.clock)
        record["compactMetadata"]["postTokens"] = 1500
        self.append(record)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("retry_wait", self.snapshot()["phase"])

    def test_manual_compact_releases_on_its_own_boundary_without_a_retry(self):
        # /compact has no pending turn to retry, so the boundary postTokens is
        # the only measurement that will ever exist (Regina 2026-10-23).
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.request(1200, native)
        self.assertEqual("compacting", self.snapshot()["phase"])
        self.clock += 1
        record = boundary("session-a", self.clock)
        record["compactMetadata"]["trigger"] = "manual"
        self.append(record)
        self.assertFalse(self.control.external_admission_paused())
        state = self.snapshot()
        self.assertEqual("verified", state["phase"])
        self.assertEqual(120, state["next_raw_tokens"])
        self.assertTrue(any("evidence=manual_checkpoint_post_tokens" in str(item) for item in self.logs))

    def test_auto_compact_releases_from_fresh_boundary_without_another_request(self):
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.request(1200, native)
        self.clock += 1
        record = boundary("session-a", self.clock)
        record["compactMetadata"]["trigger"] = "auto"
        self.append(record)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("verified", self.snapshot()["phase"])
        self.assertTrue(any("evidence=auto_checkpoint_post_tokens" in str(row) for row in self.logs))

    def test_auto_boundary_without_valid_post_count_keeps_gate_closed(self):
        for post_tokens in (0, 1500):
            # Independent cycles rather than spending the shared budget twice.
            self.scope = CompactionScope("claude", "session-a", "post-" + str(post_tokens))
            with self.subTest(post_tokens=post_tokens):
                native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
                self.request(1200, native)
                self.clock += 1
                record = boundary("session-a", self.clock, str(post_tokens))
                record["compactMetadata"].update(trigger="auto", postTokens=post_tokens)
                self.append(record)
                self.assertTrue(self.control.external_admission_paused())

    def test_manual_compact_whose_boundary_is_still_full_stays_bounded(self):
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.request(1200, native)
        self.clock += 1
        record = boundary("session-a", self.clock)
        record["compactMetadata"].update({"trigger": "manual", "postTokens": 1500})
        self.append(record)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("retry_wait", self.snapshot()["phase"])

    def test_historical_wrong_session_and_quoted_markers_do_not_confirm(self):
        self.append(boundary("session-a", self.clock - 5, "old"))
        self.request()
        self.clock += 1
        self.append(boundary("session-b", self.clock))
        self.append({"type": "assistant", "sessionId": "session-a", "message": {"content": json.dumps(boundary("session-a", self.clock))}})
        self.request(100)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("", self.snapshot()["checkpoint_id"])

    def test_epoch_does_not_inherit_or_confirm_previous_launch(self):
        self.request()
        old_request = self.repository.read()
        self.scope = CompactionScope("claude", "session-a", "launch-b")
        self.assertFalse(self.control.external_admission_paused())
        self.assertFalse(self.control.command_ready(old_request))
        self.assertIsNone(self.repository.read())
        self.control.observe_request({"messages": []}, 2000, 1000,
                                     scope=CompactionScope("claude", "session-a", "launch-a"))
        self.assertFalse(self.control.external_admission_paused())

    def test_manual_new_or_goal_is_never_overwritten(self):
        for action in ("new_session", "goal_clear"):
            with self.subTest(action=action):
                manual = self.repository.queue("mcp", "manual", action)
                self.request()
                self.assertEqual(manual["id"], self.repository.read()["id"])
                self.assertTrue(self.control.external_admission_paused())
                self.assertTrue(self.control.command_ready(manual))
                self.assertEqual(manual["id"], self.repository.read()["id"])
                self.repository.clear(manual["id"])
                self.assertTrue(self.control.external_admission_paused())
                self.assertTrue(self.repository.read()["automatic"])
                self.repository.clear()

    def test_native_compact_reuses_shared_policy_without_extra_command(self):
        native = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.request(100, native)
        self.assertEqual("compacting", self.snapshot()["phase"])
        self.assertIsNone(self.repository.read())
        self.assertEqual("native_compaction", self.control.context_pause_reason(native))
        self.assertTrue(self.control.external_admission_paused())

    def test_timeout_holds_without_replaying_uncertain_submission(self):
        self.request()
        request = self.repository.read()
        self.assertTrue(self.control.command_ready(request))
        self.control.command_submitted(request)
        self.repository.clear(request["id"])
        self.clock += 11
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertIsNone(self.repository.read())
        self.clock += 100
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())
        self.assertTrue(self.control.command_ready(self.repository.queue("mcp", "manual")))

    def test_definite_pre_submit_failure_is_bounded_and_cooled_down(self):
        self.request()
        first = self.repository.read()
        self.assertTrue(self.control.command_ready(first))
        self.control.command_failed(first, "command_not_submitted")
        self.assertEqual("retry_wait", self.snapshot()["phase"])
        self.assertIsNone(self.repository.read())
        self.clock += 4
        with patch("ciel_runtime_support.channel_compact_request_repository.time.time", return_value=self.clock):
            self.assertTrue(self.control.external_admission_paused())
        second = self.repository.read()
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(self.control.command_ready(second))
        self.control.command_failed(second, "command_not_submitted")
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.clock += 50
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())

    def test_unreadable_state_preserves_hold_across_restart_until_new_native_proof(self):
        self.state.path.write_text("not json", encoding="utf-8")
        control = self.factory()
        self.assertTrue(control.external_admission_paused())
        self.assertTrue(self.factory().external_admission_paused())
        quarantined = sorted(self.root.glob("control.json.corrupt-*"))
        self.assertEqual(1, len(quarantined))
        self.assertEqual("not json", quarantined[0].read_text(encoding="utf-8"))
        self.assertTrue(self.state.path.with_name("control.json.unresolved").exists())
        self.assertIsNone(self.repository.read())
        # Corruption is not proof that a native write failed. Only a new
        # explicit native compact request establishes a fresh proof baseline.
        manual = self.repository.queue("mcp", "manual")
        self.assertTrue(control.command_ready(manual))
        control.command_submitted(manual)
        self.repository.clear(manual["id"])
        control.observe_request({"messages": [{"role": "user", "content": "big"}]}, 1200, 1000)
        now = __import__("time").time()
        self.append(boundary("session-a", now + .01))
        control.observe_request({"messages": [{"role": "user", "content": "small"}]}, 120, 1000)
        self.assertFalse(control.external_admission_paused())
        self.assertFalse(self.factory().external_admission_paused())

    def test_corruption_hold_does_not_gate_a_new_verified_launch_scope(self):
        self.state.path.write_text("not json", encoding="utf-8")
        self.assertTrue(self.factory().external_admission_paused())
        self.scope = CompactionScope("claude", "new-session", "new-launch")
        self.assertFalse(self.factory().external_admission_paused())

    def test_incomplete_scope_and_nonobject_sidecar_are_unknown(self):
        for malformed in ({"scope": {}, "phase": "blocked"},
                          {"scope": {"runtime": "claude", "session_id": "session-a"}, "phase": "blocked"}):
            self.state.path.write_text(json.dumps(malformed), encoding="utf-8")
            self.assertTrue(self.factory().external_admission_paused())
            self.assertEqual("state_unavailable", self.factory().admission_reason())
        self.state.path.unlink(missing_ok=True)
        self.state.path.with_name("control.json.unresolved").write_text("[]", encoding="utf-8")
        self.assertTrue(self.factory().external_admission_paused())
        self.assertEqual("state_unavailable", self.factory().admission_reason())

    def test_nonobject_state_is_unknown_not_an_empty_success(self):
        self.state.path.write_text("[]", encoding="utf-8")
        self.assertTrue(self.factory().external_admission_paused())
        self.assertEqual("state_unavailable", self.factory().admission_reason())

    def test_small_request_after_corruption_does_not_fake_native_completion(self):
        self.state.path.write_text("broken", encoding="utf-8")
        control = self.factory()
        self.assertTrue(control.external_admission_paused())
        control.observe_request({"messages": [{"role": "user", "content": "small"}]}, 10, 1000)
        self.assertTrue(control.external_admission_paused())
        self.assertIsNone(self.repository.read())

    def test_late_unrelated_checkpoint_cannot_release_corruption_hold(self):
        self.state.path.write_text("broken", encoding="utf-8")
        control = self.factory()
        self.assertTrue(control.external_admission_paused())
        self.append(boundary("other-session", __import__("time").time() + 1))
        self.assertTrue(control.external_admission_paused())

    def test_corrupt_state_fails_closed_without_consuming_command(self):
        self.state.path.write_text("not json", encoding="utf-8")
        self.assertTrue(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())

    def test_late_submit_ack_never_regresses_verified_checkpoint_or_queues_again(self):
        self.request()
        request = self.repository.read()
        self.assertTrue(self.control.command_ready(request))
        self.clock += 1
        self.append(boundary("session-a", self.clock))
        self.request(120)
        self.assertFalse(self.control.external_admission_paused())
        self.control.command_submitted(request)
        self.assertEqual("verified", self.snapshot()["phase"])
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())

    def test_manual_compact_retry_cannot_reuse_previous_checkpoint_proof(self):
        self.request()
        self.clock += 1
        self.append(boundary("session-a", self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.snapshot()["phase"])
        manual = self.repository.queue("mcp", "retry")
        self.assertTrue(self.control.command_ready(manual))
        self.clock += 1
        self.request(1200, {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]})
        self.assertTrue(self.control.external_admission_paused())
        self.request(120)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("compacting", self.snapshot()["phase"])
        self.clock += 1
        self.append(boundary("session-a", self.clock, "checkpoint-2"))
        self.request(120)
        self.assertFalse(self.control.external_admission_paused())

    def test_observed_overflow_can_pause_before_local_estimate_crosses(self):
        self.control.observe_request({"messages": []}, 990, 1000, recovery_required=True)
        self.assertTrue(self.control.external_admission_paused())
        self.assertTrue(self.repository.read()["automatic"])

    def test_duplicate_manual_and_automatic_consumers_never_dispatch_twice(self):
        self.request()
        automatic = self.repository.read()
        self.assertTrue(self.control.command_ready(automatic))
        self.assertFalse(self.controller().command_ready(automatic))
        manual = self.repository.queue("mcp", "manual")
        self.assertTrue(self.control.command_ready(manual))
        self.assertFalse(self.controller().command_ready(manual))

    def test_corrupt_session_command_slot_is_not_overwritten_by_auto(self):
        self.repository.path.write_text("unfinished manual slot", encoding="utf-8")
        self.request()
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("unfinished manual slot", self.repository.path.read_text(encoding="utf-8"))
        self.assertEqual("needed", self.snapshot()["phase"])


class NativeCheckpointTests(unittest.TestCase):
    def test_codex_record_requires_replacement_and_session_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rollout-session-a.jsonl"
            scope = CompactionScope("codex", "session-a", "epoch-a")
            record = {"type": "compacted", "timestamp": "2027-01-15T08:00:01Z", "payload": {
                "replacement_history": [{"type": "message", "role": "user", "content": "handoff"}],
                "latest_token_usage_record": {"thread_id": "session-a"},
            }}
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertIsNotNone(native_checkpoint_from_transcript(path, scope))
            self.assertIsNone(native_checkpoint_from_transcript(path, CompactionScope("codex", "other", "epoch-a")))
            record["payload"]["replacement_history"] = []
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertIsNone(native_checkpoint_from_transcript(path, scope))

    def test_truncated_and_sidechain_records_are_not_checkpoint_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session-a.jsonl"
            scope = CompactionScope("claude", "session-a", "epoch-a")
            record = boundary("session-a", 1800000001)
            record["isSidechain"] = True
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertIsNone(native_checkpoint_from_transcript(path, scope))
            record["isSidechain"] = False
            path.write_text(json.dumps(record), encoding="utf-8")
            self.assertIsNone(native_checkpoint_from_transcript(path, scope, max_bytes=64))


if __name__ == "__main__":
    unittest.main()
