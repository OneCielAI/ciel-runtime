"""Observed native-compaction checkpoint admission failures and their fix.

This file started as reproduction evidence on nightly-20261020 (see
docs/journal/2026/10/21/diagnostics/ara-checkpoint-admission/reproduction.okf)
and now holds the regression contracts for the minimal fix:

* ``native_checkpoint_from_transcript`` scans the whole exact session
  transcript with bounded per-record memory and scalar signature caching.  A ~600 KiB ``compacted`` record and a checkpoint
  followed by ~600 KiB of later records are both proof at any file size.
* a persisted ``awaiting_shrink`` phase re-evaluates its own durable evidence
  on every refresh, so a restart between observing the checkpoint and
  verifying it cannot hold admission forever.
* Existing capacity and baseline shrink thresholds are preserved; a native
  prompt count after the matching checkpoint is checked by the same thresholds.
* nothing else releases: invalid, oversized, wrong-session, quoted,
  sidechain and pre-request records never authorize admission, and no test
  changes a command slot or cursor.

Class 4 of the reproduction (a >64 KiB ``session_meta`` header) is resolved in
the transcript identity readers, which have their own focused tests.

No live Runtime state, transcript, cursor, queue or remote host is touched.
"""

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import runtime_compaction_control as control_module
from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.context_summary_policy import ContextSummaryPolicy
from ciel_runtime_support.runtime_compaction_binding import native_input_tokens
from ciel_runtime_support.runtime_compaction_control import (
    CompactionScope,
    RuntimeCompactionControl,
    RuntimeCompactionPolicy,
    RuntimeCompactionPorts,
    native_checkpoint_from_transcript,
)
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

TAIL_BYTES = 512 * 1024
NATIVE_COMPACT = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}


def claude_compact_kind(body):
    policy = ContextSummaryPolicy(
        lambda _value: 1, lambda value: int(value), str, str,
        lambda value: str((value.get("messages") or [{}])[-1].get("content", "")),
    )
    return policy.compact_request_kind(body)


def codex_compact_kind(body):
    return "native" if body.get("native") else None


def stamp(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


def claude_boundary(session_id, when, identity="checkpoint-1", post_tokens=120):
    return {
        "type": "system", "subtype": "compact_boundary", "sessionId": session_id,
        "timestamp": stamp(when), "uuid": identity, "isSidechain": False,
        "compactMetadata": {"preTokens": 1200, "postTokens": post_tokens},
    }


def claude_usage(session_id, when, input_tokens=100, cache_read=20, cache_creation=0):
    return {
        "type": "assistant", "sessionId": session_id, "timestamp": stamp(when),
        "message": {"usage": {
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        }},
    }


def codex_compacted(thread_id, when, content="handoff"):
    return {
        "type": "compacted", "timestamp": stamp(when),
        "payload": {
            "replacement_history": [{"type": "message", "role": "user", "content": content}],
            "latest_token_usage_record": {"thread_id": thread_id},
        },
    }


def codex_token_count(tokens, when):
    return {
        "type": "event_msg", "timestamp": stamp(when),
        "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": tokens},
            "total_token_usage": {"input_tokens": tokens * 20},
        }},
    }


class _ControllerHarness(unittest.TestCase):
    runtime = "claude"
    trace_name = "session-a.jsonl"
    compact_kind = staticmethod(claude_compact_kind)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = 1800000000.0
        clock_patch = patch(
            "ciel_runtime_support.channel_compact_request_repository.time.time",
            side_effect=lambda: self.clock,
        )
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.scope = CompactionScope(self.runtime, "session-a", "launch-a")
        self.logs = []
        self.trace = self.root / self.trace_name
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
            compact_kind=self.compact_kind, log=lambda *args: self.logs.append(args),
            now=lambda: self.clock,
        ), RuntimeCompactionPolicy(
            confirmation_timeout_seconds=10, retry_cooldown_seconds=3, max_attempts=2))

    def request(self, tokens=1200, body=None):
        with patch(
            "ciel_runtime_support.channel_compact_request_repository.time.time",
            return_value=self.clock,
        ):
            self.control.observe_request(
                body or {"messages": [{"role": "user", "content": "ordinary"}]}, tokens, 1000)

    def append(self, record):
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def append_raw(self, text):
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def state_value(self):
        return self.state.load("test") or {}

    def assert_verified(self, tokens=120):
        state = self.state_value()
        self.assertEqual("verified", state["phase"])
        self.assertEqual(tokens, state["next_raw_tokens"])
        # No command slot is reserved or retained by proof.
        self.assertIsNone(self.repository.read())

    def assert_held(self, phase="awaiting_shrink"):
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual(phase, self.state_value()["phase"])


class ClaudeHarness(_ControllerHarness):
    pass


class CodexHarness(_ControllerHarness):
    runtime = "codex"
    trace_name = "rollout-session-a.jsonl"
    compact_kind = staticmethod(codex_compact_kind)


class LateCheckpointControlTests(ClaudeHarness):
    def test_small_late_checkpoint_verifies_with_ten_second_harness(self):
        """Control: the 10 s harness verifies a small checkpoint written late.

        Observed and desired: verified after the post-checkpoint smaller
        request, so the failure classes below are not harness noise.
        """

        self.request(1200)
        self.request(1200, NATIVE_COMPACT)
        self.request(120)
        self.assertEqual("compacting", self.state_value()["phase"])
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.assertFalse(self.control.external_admission_paused())
        state = self.state_value()
        self.assertEqual("verified", state["phase"])
        self.assertEqual(120, state["next_raw_tokens"])


class CheckpointScanTests(CodexHarness):
    def test_six_hundred_kib_compacted_record_is_checkpoint_proof(self):
        """A record larger than the old 512 KiB tail window is still proof."""

        record = codex_compacted("session-a", self.clock + 1, content="h" * (600 * 1024))
        line = json.dumps(record)
        self.assertGreater(len(line.encode("utf-8")), TAIL_BYTES)
        self.assertEqual("compacted", json.loads(line)["type"])
        self.assertTrue(json.loads(line)["payload"]["replacement_history"])

        self.request(1200, {"native": "codex"})
        self.clock += 1
        self.append(record)
        checkpoint = native_checkpoint_from_transcript(self.trace, self.scope)
        self.assertIsNotNone(checkpoint)
        self.assert_held()
        self.assertNotEqual("", self.state_value()["checkpoint_id"])
        # A detected checkpoint has no confirmation deadline to expire.
        self.clock += 11
        self.assert_held()
        self.request(120)
        self.assert_verified()

    def test_checkpoint_before_six_hundred_kib_of_later_records_is_proof(self):
        """A checkpoint pushed out of the old tail window is still proof."""

        self.request(1200, {"native": "codex"})
        self.clock += 1
        self.append(codex_compacted("session-a", self.clock))
        self.append({"type": "event_msg", "payload": {
            "type": "agent_message", "message": "y" * (600 * 1024)}})
        self.assertGreater(self.trace.stat().st_size, TAIL_BYTES)
        checkpoint = native_checkpoint_from_transcript(self.trace, self.scope)
        self.assertIsNotNone(checkpoint)
        self.assert_held()
        self.request(120)
        self.assert_verified()

    def test_record_over_the_record_cap_is_not_proof(self):
        """Bounded memory: a record beyond the cap stays unknown, never proof."""

        self.append(codex_compacted("session-a", self.clock + 1, content="h" * (600 * 1024)))
        self.assertIsNone(native_checkpoint_from_transcript(self.trace, self.scope, max_bytes=64 * 1024))
        # The cap is the discriminator: the same bytes parse without it.
        self.assertIsNotNone(native_checkpoint_from_transcript(self.trace, self.scope))

    def test_unchanged_signature_uses_scalar_cache_and_rewrite_invalidates(self):
        self.append(codex_compacted("session-a", self.clock + 1))
        with patch.object(control_module, "_read_jsonl", wraps=control_module._read_jsonl) as reader:
            first = native_checkpoint_from_transcript(self.trace, self.scope)
            second = native_checkpoint_from_transcript(self.trace, self.scope)
            self.assertEqual(first, second)
            self.assertEqual(1, reader.call_count)
            self.trace.write_text("invalid\n", encoding="utf-8")
            self.assertIsNone(native_checkpoint_from_transcript(self.trace, self.scope))
            self.assertEqual(2, reader.call_count)


class ClaudeUsageProofTests(ClaudeHarness):
    def test_native_prompt_size_after_checkpoint_verifies_on_refresh(self):
        """The native client's own newest prompt size is the raw request size."""

        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.assert_held()
        self.control = self.controller()
        self.clock += 1
        self.append(claude_usage("session-a", self.clock))
        self.assertFalse(self.control.external_admission_paused())
        self.assert_verified()

    def test_prompt_size_for_another_session_never_releases(self):
        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.clock += 1
        self.append(claude_usage("session-b", self.clock))
        self.clock += 100000
        self.assert_held()

    def test_prompt_size_at_or_above_capacity_never_releases(self):
        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.clock += 1
        self.append(claude_usage("session-a", self.clock, input_tokens=1200, cache_read=0))
        self.clock += 100000
        self.assert_held()

    def test_usage_with_pre_checkpoint_timestamp_is_not_proof(self):
        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.append(claude_usage("session-a", self.clock - 5))
        self.assert_held()

    def test_partial_record_with_newline_later_becomes_proof(self):
        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append_raw(json.dumps(claude_boundary("session-a", self.clock)))
        self.assert_held("compacting")
        self.append_raw(chr(10))
        self.assert_held()
        self.request(120)
        self.assert_verified()

    def test_prompt_size_before_the_checkpoint_is_not_proof(self):
        self.request(1200, NATIVE_COMPACT)
        self.append(claude_usage("session-a", self.clock))
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.clock += 100000
        self.assert_held()


class CodexPersistedNativeUsageTests(CodexHarness):
    def test_persisted_awaiting_shrink_and_native_usage_verify_on_refresh(self):
        """Persisted checkpoint plus a later native token_count releases.

        Baseline 1200, limit 1000, native last input 120.  The same size as a
        raw request verifies; the transcript record for the same exact session
        is that measurement and a poll applies it without a queue change.
        """

        self.request(1200, {"native": "codex"})
        self.clock += 1
        self.append(codex_compacted("session-a", self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.state_value()["phase"])
        self.clock += 1
        self.append(codex_token_count(120, self.clock))
        self.assertEqual(120, native_input_tokens(self.trace, self.scope))
        self.control = self.controller()
        self.assertFalse(self.control.external_admission_paused())
        self.assert_verified()

    def test_native_usage_before_the_matching_checkpoint_is_not_proof(self):
        self.request(1200, {"native": "codex"})
        self.clock += 1
        self.append(codex_token_count(120, self.clock))
        self.clock += 1
        self.append(codex_compacted("session-a", self.clock))
        self.clock += 100000
        self.assert_held()

    def test_native_usage_at_or_above_capacity_is_not_proof(self):
        self.request(1200, {"native": "codex"})
        self.clock += 1
        self.append(codex_compacted("session-a", self.clock))
        self.clock += 1
        self.append(codex_token_count(1200, self.clock))
        self.clock += 100000
        self.assert_held()


class PersistedAwaitingShrinkTests(ClaudeHarness):
    def test_below_capacity_but_above_baseline_still_holds(self):
        self.request(100, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock, post_tokens=120))
        self.assert_held()
        self.request(120)
        self.assert_held()

    def test_manual_cycle_persists_capacity_from_real_request(self):
        manual = self.repository.queue("mcp", "manual")
        self.assertTrue(self.control.command_ready(manual))
        self.control.command_submitted(manual)
        self.repository.clear(manual["id"])
        self.request(120)
        self.assertEqual(1000, self.state_value()["context_limit"])
        self.control = self.controller()
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock))
        self.assertFalse(self.control.external_admission_paused())
        self.assert_verified()

    def test_persisted_shrink_observed_releases_after_restart(self):
        """A restart between observing the checkpoint and verifying it recovers.

        The durable state holds both facts; the refresh path used to skip them
        because the newest checkpoint identity was already recorded.
        """

        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock, post_tokens=120))
        checkpoint = native_checkpoint_from_transcript(self.trace, self.scope)
        self.assertIsNotNone(checkpoint)
        state = self.state_value()
        state.update({
            "phase": "awaiting_shrink", "checkpoint_id": checkpoint.identity,
            "baseline_checkpoint_id": "", "shrink_observed": {"tokens": 120, "at": self.clock},
        })
        self.state.save(state, "compaction")

        self.control = self.controller()
        self.assertFalse(self.control.external_admission_paused())
        self.assert_verified()

    def test_persisted_shrink_observed_above_capacity_never_releases(self):
        self.request(1200, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock, post_tokens=120))
        checkpoint = native_checkpoint_from_transcript(self.trace, self.scope)
        state = self.state_value()
        state.update({
            "phase": "awaiting_shrink", "checkpoint_id": checkpoint.identity,
            "baseline_checkpoint_id": "", "shrink_observed": {"tokens": 1200, "at": self.clock},
        })
        self.state.save(state, "compaction")
        self.clock += 100000
        self.assert_held()

    def test_awaiting_shrink_without_shrink_evidence_never_releases(self):
        self.request(100, NATIVE_COMPACT)
        self.clock += 1
        self.append(claude_boundary("session-a", self.clock, post_tokens=120))
        self.clock += 100000
        self.assert_held()


class BlockedRecoveryTests(ClaudeHarness):
    def _dispatch_and_time_out(self):
        self.request()
        request = self.repository.read()
        self.assertTrue(self.control.command_ready(request))
        self.control.command_submitted(request)
        self.repository.clear(request["id"])
        self.clock += 11
        self.assert_held("blocked")
        self.assertEqual("checkpoint_unconfirmed", self.state_value()["failure"])

    def test_blocked_cycle_observes_a_late_checkpoint_then_verifies(self):
        """A late valid checkpoint is proof; shrink is still required."""

        self._dispatch_and_time_out()
        self.append(claude_boundary("session-a", self.clock))
        self.assert_held()
        self.assertNotEqual("", self.state_value()["checkpoint_id"])
        self.request(120)
        self.assert_verified()

    def test_checkpoint_persisted_before_the_request_never_releases(self):
        self.append(claude_boundary("session-a", self.clock - 5, "old"))
        self._dispatch_and_time_out()
        self.clock += 100
        self.assert_held("blocked")
        self.assertEqual("", self.state_value()["checkpoint_id"])
        self.request(120)
        self.assert_held("blocked")

    def test_wrong_session_checkpoint_never_releases(self):
        self._dispatch_and_time_out()
        self.append(claude_boundary("session-b", self.clock))
        self.clock += 100
        self.assert_held("blocked")
        self.assertEqual("", self.state_value()["checkpoint_id"])

    def test_invalid_and_incomplete_records_never_release(self):
        self._dispatch_and_time_out()
        self.append_raw("not json\n")
        self.append_raw(json.dumps(claude_boundary("session-a", self.clock)))
        self.clock += 100
        self.assert_held("blocked")
        self.assertEqual("", self.state_value()["checkpoint_id"])


if __name__ == "__main__":
    unittest.main()
