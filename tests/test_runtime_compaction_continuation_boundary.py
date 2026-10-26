"""Characterize missing authority for post-compact continuation; no live CLI."""

import copy
import tempfile
import threading
import unittest
from pathlib import Path

from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.runtime_compaction_control import (
    CompactionScope,
    NativeCompactionCheckpoint,
    RuntimeCompactionControl,
    RuntimeCompactionPorts,
)
from ciel_runtime_support.windows_conpty import WindowsConPtySession


class RuntimeCompactionContinuationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.scope = CompactionScope("claude", "exact-session", "exact-launch")
        self.state = {}
        self.checkpoint = None
        self.request = {"id": "runtime-request", "source": "ciel-runtime-router-tool", "action": "compact"}
        self.control = RuntimeCompactionControl(RuntimeCompactionPorts(
            load=lambda: copy.deepcopy(self.state),
            save=self.save,
            lock=threading.Lock,
            scope=lambda: self.scope,
            checkpoint=lambda _scope: self.checkpoint,
            queue=lambda *_args: None,
            clear=lambda *_args: None,
            compact_kind=lambda body: "compact" if body.get("native_compact") else None,
            log=lambda *_args: None,
            now=lambda: 100.0,
        ))

    def save(self, state):
        self.state = copy.deepcopy(state)

    def submitted(self):
        self.assertTrue(self.control.command_ready(self.request))
        self.control.command_submitted(self.request)

    def boundary(self, scope=None):
        self.checkpoint = NativeCompactionCheckpoint(scope or self.scope, "new-boundary", 101.0, 290541, 22846)

    def test_runtime_tool_queue_has_no_scope_or_continuation_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = ChannelCompactRequestRepository(
                Path(tmp) / "request.json", threading.Lock(), lambda *_args: None,
                lambda text, limit: text[:limit], lambda *_args: None, lambda: 600,
            )
            request = repo.queue("ciel-runtime-router-tool", "continue unfinished work", "compact")
        self.assertNotIn("scope", request)
        self.assertNotIn("continuation", request)
        self.assertNotIn("resume", request)
        self.assertEqual("/compact", request["command"])

    def test_successful_checkpoint_alone_keeps_external_admission_paused(self):
        self.submitted()
        self.boundary()
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.state["phase"])
        self.assertEqual("new-boundary", self.state["checkpoint_id"])
        # Repeated polling is not a native turn or permission to inject one.
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.state["phase"])

    def test_no_checkpoint_and_wrong_epoch_do_not_release_admission(self):
        self.submitted()
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_checkpoint", self.state["phase"])
        self.boundary(CompactionScope("claude", "exact-session", "other-launch"))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_checkpoint", self.state["phase"])

    def test_checkpoint_phase_is_not_successful_submission_authority(self):
        self.assertTrue(self.control.command_ready(self.request))
        self.control.command_failed(self.request, "command_submission_uncertain", uncertain=True)
        self.assertEqual("blocked", self.state["phase"])
        self.boundary()
        self.assertTrue(self.control.external_admission_paused())
        # Current recovery state deliberately follows later checkpoint evidence.
        # Therefore awaiting_shrink alone cannot authorize a continuation.
        self.assertEqual("awaiting_shrink", self.state["phase"])

    def test_manual_and_runtime_commands_have_same_checkpoint_transition(self):
        self.request["source"] = "manual"
        self.submitted()
        self.boundary()
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("awaiting_shrink", self.state["phase"])
        self.assertNotIn("source", self.state)

    def test_real_new_request_releases_after_checkpoint_without_synthetic_input(self):
        self.submitted()
        self.boundary()
        self.assertTrue(self.control.external_admission_paused())
        self.control.observe_request({"messages": [{"role": "user", "content": "continue"}]}, 23000, 800000)
        self.assertEqual("verified", self.state["phase"])
        self.assertFalse(self.control.external_admission_paused())

    def test_terminal_draft_snapshot_cannot_prove_no_cancel_or_new_input(self):
        for operator_input in (bytes([3]), bytes([27, 27]), b"new instruction" + bytes([13])):
            with self.subTest(operator_input=operator_input):
                session = WindowsConPtySession.__new__(WindowsConPtySession)
                self.assertFalse(session.manual_input_active())
                session._observe_parent_input(operator_input)
                # The current exposed boolean forgets completed/cancel input.
                self.assertFalse(session.manual_input_active())


if __name__ == "__main__":
    unittest.main()
