"""A failed compact retains failure evidence but cannot indefinitely gate SSE input."""
from dataclasses import replace

import test_compact_idle_retry as idle
import test_runtime_compaction_control as fixtures


class CompactFailureReleaseTests(fixtures._ControllerHarness):
    def setUp(self):
        super().setUp()
        self.control = self.factory()
        self.control.ports = replace(self.control.ports, now=lambda: self.clock)

    native_start = idle.CompactIdleRetryTests.native_start

    def test_exhaustion_opens_input_and_new_native_compact_gets_new_budget(self):
        self.native_start()
        self.clock += 1
        self.append(idle.failure(self.scope, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.clock += 4
        self.control.external_admission_paused()
        req = self.repository.read()
        self.assertTrue(self.control.command_ready(req))
        self.control.command_submitted(req)
        self.repository.clear(req["id"])
        self.native_start()
        self.clock += 1
        self.append(idle.failure(self.scope, self.clock, identity="retry-failed"))
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertEqual(2, self.snapshot()["attempts"])
        self.assertNotIn("verified_at", self.snapshot())
        self.control = self.factory()
        self.control.ports = replace(self.control.ports, now=lambda: self.clock)
        self.clock += 50
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())
        # SSE input may trigger a genuinely new native compact, not poll replay.
        self.native_start()
        self.assertEqual(1, self.snapshot()["attempts"])
        self.assertTrue(self.control.external_admission_paused())

    def test_confirmation_expiry_releases_without_automatic_replay(self):
        self.native_start()
        self.clock += 20
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertEqual("checkpoint_unconfirmed", self.snapshot()["failure"])
        self.assertIsNone(self.repository.read())

    def test_checkpoint_shrink_confirmation_expiry_releases_not_verifies(self):
        self.native_start()
        self.clock += 1
        self.append(fixtures.boundary(self.scope.session_id, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.clock += 20
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertEqual("shrink_unconfirmed", self.snapshot()["failure"])
        self.assertNotIn("verified_at", self.snapshot())

    def test_generic_compact_failure_retries_but_prompt_mentions_do_not(self):
        self.native_start()
        self.clock += 1
        row = idle.failure(self.scope, self.clock)
        row["content"] = "<local-command-stderr>Error during compaction: API Error: 504 gateway failed</local-command-stderr>"
        self.append({**row, "type": "user"})
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("compacting", self.snapshot()["phase"])
        self.append(row)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("retry_wait", self.snapshot()["phase"])
        self.assertEqual("native_compact_failed", self.snapshot()["failure"])

    def test_uncertain_command_exception_releases_without_resending(self):
        self.request()
        req = self.repository.read()
        self.assertTrue(self.control.command_ready(req))
        self.control.command_failed(req, "command_submission_uncertain", uncertain=True)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertIsNone(self.repository.read())
        self.assertFalse(self.control.command_ready(req))
        self.clock += 50
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())

    def test_manual_definite_submission_failure_retries(self):
        req = self.repository.queue()
        self.assertTrue(self.control.command_ready(req))
        self.control.command_failed(req, "command_not_submitted")
        self.assertEqual("retry_wait", self.snapshot()["phase"])
        self.clock += 4
        self.assertTrue(self.control.external_admission_paused())
        retried = self.repository.read()
        self.assertTrue(retried["automatic"])
        self.assertNotEqual(req["id"], retried["id"])

    def test_read_error_remains_distinct_from_known_failed_compact(self):
        self.native_start()
        self.state.path.write_text("not json", encoding="utf-8")
        self.assertTrue(self.control.external_admission_paused())

    def test_retry_queue_exception_opens_gate_without_forging_success(self):
        self.native_start()
        self.clock += 1
        self.append(idle.failure(self.scope, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.clock += 4
        def broken_queue(*_args):
            raise OSError("fixture queue unavailable")
        self.control.ports = replace(self.control.ports, queue=broken_queue)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("retry_queue_failed", self.snapshot()["failure"])
        self.assertEqual("failed_open", self.snapshot()["phase"])

    def test_automatic_api_error_has_no_local_command_fields(self):
        self.native_start()
        self.clock += 1
        row = idle.failure(self.scope, self.clock, automatic=True)
        for name in ("commandRun", "commandOutcome", "content", "subtype"):
            row.pop(name, None)
        row["message"]["content"][0]["text"] = (
            "Prompt is too long · automatic compaction failed: API Error: gateway unavailable")
        self.append(row)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("retry_wait", self.snapshot()["phase"])

    def test_failed_open_does_not_rearm_on_same_oversized_request(self):
        self.native_start()
        self.clock += 20
        self.assertFalse(self.control.external_admission_paused())
        self.request(1200)
        self.assertFalse(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())
        self.control.observe_request({}, 1200, 1000, recovery_required=True)
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual(1, self.snapshot()["attempts"])

    def test_persisted_old_blocked_state_releases_after_restart(self):
        self.native_start()
        state = self.snapshot()
        state.update(phase="blocked", failure="command_submission_uncertain")
        self.state.save(state, "compaction")
        self.control = self.factory()
        self.control.ports = replace(self.control.ports, now=lambda: self.clock)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertNotIn("verified_at", self.snapshot())

    def test_same_manual_command_id_never_dispatches_twice_after_failure(self):
        req = self.repository.queue()
        self.assertTrue(self.control.command_ready(req))
        self.control.command_failed(req, "command_submission_uncertain", uncertain=True)
        self.assertFalse(self.control.command_ready(req))
        self.assertFalse(self.control.external_admission_paused())
        fresh = self.repository.queue()
        self.assertTrue(self.control.command_ready(fresh))
        self.assertEqual(1, self.snapshot()["attempts"])

    def test_late_terminal_failure_resolves_uncertain_submission_for_retry(self):
        self.request()
        req = self.repository.read()
        self.assertTrue(self.control.command_ready(req))
        self.control.command_failed(req, "command_submission_uncertain", uncertain=True)
        self.assertFalse(self.control.external_admission_paused())
        self.clock += 1
        self.append(idle.failure(self.scope, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual("retry_wait", self.snapshot()["phase"])

    def test_retry_scheduling_runtime_exception_releases(self):
        self.native_start()
        self.clock += 1
        self.append(idle.failure(self.scope, self.clock))
        self.control.external_admission_paused()
        self.clock += 4
        def failed_queue(*_args):
            raise RuntimeError("owned scheduler exception")
        self.control.ports = replace(self.control.ports, queue=failed_queue)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("retry_queue_failed", self.snapshot()["failure"])

    def test_slot_cleanup_exception_does_not_preserve_failure_gate(self):
        self.request()
        req = self.repository.read()
        self.assertTrue(self.control.command_ready(req))
        def broken_clear(*_args):
            raise RuntimeError("owned cleanup exception")
        self.control.ports = replace(self.control.ports, clear=broken_clear)
        self.control.command_failed(req, "command_not_submitted")
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertEqual("retry_slot_clear_failed", self.snapshot()["failure"])

    def test_writer_exception_does_not_exit_polling_and_gate_opens(self):
        from ciel_runtime_support.channel_compact_injection import (
            ChannelCompactInjectionService, ChannelCompactRequestPorts, ChannelCompactRuntimePorts)
        from ciel_runtime_support.channel_compact_poll import (
            poll_pending_compaction, ChannelCompactPollState,
            ChannelCompactInjectionOptions, ChannelCompactPollServices)
        req = self.repository.queue()
        writes = []
        def broken_write(*_args, **_kwargs):
            writes.append(1)
            raise OSError("owned partial submission")
        service = ChannelCompactInjectionService(
            ChannelCompactRequestPorts(self.repository.read, self.repository.clear),
            ChannelCompactRuntimePorts(lambda: False, lambda: False,
                lambda value: value, broken_write, repr), lambda *_args: None)
        services = ChannelCompactPollServices(
            lambda *args, **kwargs: service.inject(*args, control=self.control, **kwargs))
        state = poll_pending_compaction(self.clock, None, bytes([13]), None,
            ChannelCompactPollState(), ChannelCompactInjectionOptions(1, False, False, None), services)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertIsNone(self.repository.read())
        self.assertFalse(self.control.command_ready(req))
        poll_pending_compaction(self.clock + 1, None, bytes([13]), None, state,
            ChannelCompactInjectionOptions(1, False, False, None), services)
        self.assertEqual([1], writes)

    def test_queued_retry_reservation_exception_releases(self):
        self.request()
        self.assertEqual("queued", self.snapshot()["phase"])
        def broken_queue(*_args):
            raise RuntimeError("owned reservation refresh exception")
        self.control.ports = replace(self.control.ports, queue=broken_queue)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual("failed_open", self.snapshot()["phase"])
        self.assertIsNone(self.repository.read())
