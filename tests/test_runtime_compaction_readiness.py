"""Published native idle opens initial admission without a user request."""
import json
import time
from unittest.mock import patch

from test_runtime_compaction_identity import IdentityHarness


class PublishedReadinessTests(IdentityHarness):
    def setUp(self):
        super().setUp()
        self.binding.bind("claude", cwd=self.root, messaging_socket_path=self.socket)

    def idle(self, session="exact", **fields):
        self.published(session, status="idle", statusUpdatedAt=int(time.time() * 1000), **fields)

    def test_fresh_idle_without_transcript_or_request_opens(self):
        self.idle()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.binding.external_admission_paused())
        value = self.binding.repository.load("test")
        self.assertTrue(value["identity_ready"])
        self.assertFalse(value.get("request_observed", False))
        self.assertFalse(self.make().external_admission_paused())
        self.assertTrue(any("reason=published_idle" in text for _, text in self.logs))

    def test_continue_provisional_to_actual_idle_ignores_provisional_cache(self):
        self.published("temporary")
        self.assertTrue(self.binding.external_admission_paused())
        self.idle()
        self.assertFalse(self.binding.external_admission_paused())
        self.assertEqual("exact", self.binding.scope().session_id)

    def test_same_identity_status_update_is_seen_immediately(self):
        self.published()
        self.assertTrue(self.binding.external_admission_paused())
        self.idle()
        self.assertFalse(self.binding.external_admission_paused())

    def test_status_and_timestamp_must_prove_idle_this_launch(self):
        for fields in ({}, {"status": "busy"}, {"status": "waiting"}, {"status": "unknown"},
                       {"status": "idle"}, {"status": "idle", "statusUpdatedAt": "bad"},
                       {"status": "idle", "statusUpdatedAt": True},
                       {"status": "idle", "statusUpdatedAt": 1},
                       {"status": "idle", "statusUpdatedAt": float("nan")},
                       {"status": "idle", "statusUpdatedAt": (time.time() + 100) * 1000}):
            with self.subTest(fields=fields):
                self.published(**fields)
                self.assertTrue(self.make().external_admission_paused())

    def test_idle_does_not_bypass_exact_record_checks(self):
        for fields in ({"messagingSocketPath": "foreign"}, {"cwd": "foreign"},
                       {"procStart": ""}, {"startedAt": 1}, {"sessionId": ""}):
            with self.subTest(fields=fields):
                self.idle(**fields)
                self.assertTrue(self.make().external_admission_paused())

    def test_ambiguous_idle_does_not_open(self):
        self.idle()
        self.idle("other", pid=12346)
        self.assertTrue(self.binding.external_admission_paused())

    def test_ready_identity_stays_ready_while_busy_then_resets_on_switch(self):
        self.idle()
        self.assertFalse(self.binding.external_admission_paused())
        self.published(status="busy")
        self.binding.published_checked = ("", 0)
        self.assertFalse(self.binding.external_admission_paused())
        self.published("cleared")
        self.binding.published_checked = ("", 0)
        self.assertTrue(self.binding.external_admission_paused())
        self.assertNotIn("identity_ready", self.binding.repository.load("test"))
        self.idle("cleared")
        self.assertFalse(self.binding.external_admission_paused())
        self.binding.bind("claude", cwd=self.root, messaging_socket_path=self.socket)
        self.published()
        self.assertTrue(self.binding.external_admission_paused())

    def test_idle_does_not_release_real_compaction(self):
        self.idle()
        self.transcript(16000)
        self.assertFalse(self.binding.external_admission_paused())
        self.binding.observe_request("other", {}, {"tokens": 16000, "native": "auto"}, "claude",
                                     {"x-claude-code-session-id": "exact"})
        self.assertTrue(self.binding.external_admission_paused())
        self.assertEqual("compacting", self.state()["phase"])

    def test_readiness_cannot_overwrite_concurrent_launch(self):
        self.idle()
        original = self.binding._published_claude_record
        def switching(value):
            record = original(value)
            self.binding.bind("claude", cwd=self.root, messaging_socket_path="new-socket")
            return record
        with patch.object(self.binding, "_published_claude_record", side_effect=switching):
            self.assertTrue(self.binding.external_admission_paused())
        value = self.binding.repository.load("test")
        self.assertEqual("new-socket", value["messaging_socket_path"])
        self.assertNotIn("identity_ready", value)

    def test_peer_process_change_does_not_inherit_readiness(self):
        self.idle()
        self.assertFalse(self.binding.external_admission_paused())
        directory = self.home / ".claude" / "sessions"
        path = directory / "12345.json"
        record = json.loads(path.read_text())
        record.update(procStart="different", status="busy")
        path.write_text(json.dumps(record))
        self.binding.published_checked = ("", 0)
        self.assertTrue(self.binding.external_admission_paused())

    def test_stale_request_cannot_prove_retargeted_session(self):
        self.published()
        self.assertTrue(self.binding.external_admission_paused())
        original_scope = self.binding.scope()
        original_estimate = self.binding.estimate

        def retarget(body):
            self.published("successor", status="busy")
            self.binding.published_checked = ("", 0)
            successor = self.binding.scope()
            self.assertEqual(original_scope.epoch, successor.epoch)
            self.assertEqual("successor", successor.session_id)
            self.assertTrue(self.binding.external_admission_paused())
            return original_estimate(body)

        with patch.object(self.binding, "estimate", side_effect=retarget):
            self.binding.observe_request("other", {}, {"tokens": 100}, "claude",
                                         {"x-claude-code-session-id": "exact"})
        value = self.binding.repository.load("test")
        self.assertEqual("successor", value["session_id"])
        self.assertFalse(value.get("request_observed", False))
        self.assertTrue(self.binding.external_admission_paused())
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude",
                                     {"x-claude-code-session-id": "successor"})
        self.assertTrue(self.binding.repository.load("test")["request_observed"])
        self.assertFalse(self.binding.external_admission_paused())
