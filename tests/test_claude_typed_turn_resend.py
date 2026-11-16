"""Resend of a person-typed turn that ended on a transient API error (2026-11-15)."""

import unittest

from ciel_runtime_support import claude_typed_turn_resend as resend
from ciel_runtime_support.claude_stream_recovery_runtime import ClaudeStreamRecoveryMonitor, StreamRecoveryLedger
from ciel_runtime_support.runtime_compaction_control import CompactionScope

SESSION = "b43eb527-ba34-40d8-9ee5-c2fbc0add1fa"
SCOPE = CompactionScope("claude", SESSION, "epoch-1")


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


def typed(uuid, text="Fix the analytics boundary and run the tests"):
    return {"type": "user", "uuid": uuid, "sessionId": SESSION, "message": {"role": "user", "content": text}}


def resent(uuid, attempt):
    text = f"{resend.RESEND_MARKER} {attempt}/{resend.MAX_RESENDS} · err] ...\n\noriginal"
    return {"type": "user", "uuid": uuid, "sessionId": SESSION, "isMeta": True, "origin": {"kind": "peer"},
            "message": {"role": "user", "content": text}}


def api_error(uuid, *, error="server_error", status=500, **extra):
    # The shape Claude Code wrote on F:\omini-router at 2026-11-15T09:33:47Z.
    return {"type": "assistant", "uuid": uuid, "sessionId": SESSION, "isApiErrorMessage": True, "error": error,
            "apiErrorStatus": status, "message": {"role": "assistant", "content": [{"type": "text", "text": "API Error"}]},
            **extra}


def transport_error(uuid, text, status=None):
    # error "unknown" and no status, as Claude Code 2.1.325 recorded them.
    return {"type": "assistant", "uuid": uuid, "sessionId": SESSION, "isApiErrorMessage": True, "error": "unknown",
            **({"apiErrorStatus": status} if status else {}),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def compact_failed(uuid, cause="API Error: Stream idle timeout - no chunks received"):
    # The record runtime_compaction_control._compact_terminal_failure recognizes.
    return {"type": "assistant", "uuid": uuid, "sessionId": SESSION, "isApiErrorMessage": True,
            "error": "invalid_request", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Prompt is too long · automatic compaction failed: " + cause}]}}


def answer(uuid):
    return {"type": "assistant", "uuid": uuid, "sessionId": SESSION,
            "message": {"id": "msg_" + uuid, "role": "assistant", "content": [{"type": "text", "text": "done"}]}}


def tool_result(uuid):
    return {"type": "user", "uuid": uuid, "sessionId": SESSION,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


class Harness:
    def __init__(self):
        self.clock = Clock()
        self.logs, self.submitted = [], []
        self.blocked = ""
        self.scope = SCOPE
        self.resend = resend.TypedTurnResend(
            scope=lambda: self.scope, submit=self.submit, log=lambda level, text: self.logs.append(text),
            terminal_blocked=lambda: self.blocked, now=self.clock)

    def submit(self, body):
        self.submitted.append(body)
        return {"id": 500 + len(self.submitted)}

    def feed(self, *records):
        for record in records:
            self.resend.observe(record)

    def advance(self, seconds):
        self.clock.value += seconds
        return self.resend.tick()


class TypedTurnResendTests(unittest.TestCase):
    def test_schedule_is_ten_one_minute_then_ten_ten_minute_resends(self):
        self.assertEqual([60.0] * 10 + [600.0] * 10, [resend.delay_before(n) for n in range(1, 21)])
        self.assertIsNone(resend.delay_before(21))

    def test_typed_turn_resent_after_a_minute_with_the_original_text(self):
        h = Harness()
        h.feed(typed("u1"), tool_result("r1"), api_error("e1"))
        self.assertIsNone(h.advance(59))
        self.assertEqual([], h.submitted)
        self.assertEqual(1, h.advance(1)["attempt"])
        body = h.submitted[0]
        self.assertTrue(body["message"].startswith(f"{resend.RESEND_MARKER} 1/20 · e1]"))
        self.assertTrue(body["message"].endswith("\n\nFix the analytics boundary and run the tests"))
        self.assertEqual(True, body["meta"]["raw_injection"])
        self.assertEqual("stream_recovery", body["meta"]["source_kind"])
        self.assertEqual(SCOPE.payload(), body["meta"]["recovery_scope"])

    def test_full_chain_uses_twenty_resends_then_stops(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e0"))
        waits = []
        for attempt in range(1, 21):
            due = h.resend.status()["due"] - h.clock.value
            waits.append(due)
            self.assertEqual(attempt, h.advance(due)["attempt"])
            h.feed(resent(f"s{attempt}", attempt), api_error(f"e{attempt}"))
        self.assertEqual([60.0] * 10 + [600.0] * 10, waits)
        self.assertIsNone(h.resend.status())
        self.assertEqual(20, len(h.submitted))
        self.assertTrue(any("claude_typed_resend_exhausted" in line for line in h.logs))

    def test_answer_to_a_resend_ends_the_chain_as_recovered(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1"))
        h.advance(60)
        h.feed(resent("s1", 1), answer("a1"))
        self.assertIsNone(h.resend.status())
        self.assertTrue(any("claude_typed_resend_recovered" in line for line in h.logs))
        h.advance(3600)
        self.assertEqual(1, len(h.submitted))

    def test_new_typed_input_interrupt_queue_and_compaction_cancel(self):
        for record in (typed("u2", "something else"),
                       {"type": "user", "uuid": "i", "message": {"content": "[Request interrupted by user]"}},
                       {"type": "queue-operation", "operation": "enqueue"},
                       {"type": "system", "subtype": "compact_boundary"}):
            h = Harness()
            h.feed(typed("u1"), api_error("e1"), record)
            self.assertIsNone(h.resend.status(), record)
            h.advance(120)
            self.assertEqual([], h.submitted, record)

    def test_non_transient_errors_and_unanswered_channel_turns_are_not_resent(self):
        cases = (
            [typed("u1"), api_error("e1", error="invalid_request", status=400)],
            [typed("u1"), api_error("e1", truncatedAfterOutput=True)],  # stream recovery decides first
            [typed("u1"), api_error("e1", error="unknown", status=None)],
            [typed("u1"), api_error("e1", apiErrorIsTransient=False)],
            # A channel message that never got an answer: runtime_input_retry resends it.
            [typed("c1", "[ciel-runtime external channel message] from walkie"), api_error("e1")],
            [typed("u1"), transport_error("e1", "API Error: 400 This model is hosted outside", status=400)],
        )
        for records in cases:
            h = Harness()
            h.feed(*records)
            h.advance(120)
            self.assertEqual([], h.submitted, records)

    def test_image_input_resumes_with_the_continuation_line_not_text_only(self):
        # The image stays in the conversation; only a continuation is sent
        # (id400 2026-11-16 06:07Z and GAM-495 17:38Z: image turns the person
        # had to continue by typing "계속").
        h = Harness()
        h.feed({"type": "user", "uuid": "u1", "message": {"content": [
            {"type": "text", "text": "what is this"}, {"type": "image", "source": {"type": "base64", "data": "x"}}]}},
            api_error("e1"))
        self.assertEqual(1, h.advance(60)["attempt"])
        body = h.submitted[0]["message"]
        self.assertTrue(body.endswith("\n\n" + resend.API_RESUME_TEXT))
        self.assertNotIn("what is this", body)

    def test_router_and_fetch_transport_failures_are_transient(self):
        # The exact main-thread records of 2026-11-14..16 (error "unknown", no
        # status) that the router logged as claude_typed_resend_skipped
        # reason=not_transient, after which a person typed "계속".
        texts = ("API Error: terminated",
                 "API Error: Upstream Anthropic stream failed before message_stop (TimeoutError).",
                 "API Error: Upstream Anthropic stream failed before message_stop (IncompleteRead).",
                 "API Error: Ciel Runtime stopped waiting for the first upstream output: the router pre-output "
                 "time limit (540s since the request arrived) was reached (0 retries).",
                 "API Error: Gateway response stream was interrupted")
        for text in texts:
            with self.subTest(text=text):
                h = Harness()
                h.feed(typed("u1", "계속"), tool_result("r1"), transport_error("e1", text))
                self.assertEqual(1, h.advance(60)["attempt"], h.logs)
                self.assertTrue(h.submitted[0]["message"].endswith("\n\n계속"))

    def test_untyped_turns_resume_with_the_continuation_line(self):
        # Task notifications, Work continuations and peer/side wakes ended on
        # API errors with nobody resuming them (census 2026-11-10..14).
        for opener in ({"type": "user", "uuid": "p", "isMeta": True, "origin": {"kind": "peer"},
                        "message": {"content": "peer input"}},
                       typed("t", "<task-notification> <task-id>b1</task-id>"),
                       typed("s", "This session is being continued from a previous conversation")):
            with self.subTest(opener=opener):
                h = Harness()
                h.feed(opener, api_error("e1"))
                self.assertEqual(1, h.advance(60)["attempt"])
                self.assertTrue(h.submitted[0]["message"].endswith("\n\n" + resend.API_RESUME_TEXT))

    def test_channel_turn_that_already_answered_is_resumed_here(self):
        h = Harness()
        h.feed(typed("c1", "[ciel-runtime external channel message] from walkie"), answer("a1"),
               tool_result("r1"), api_error("e1"))
        self.assertEqual(1, h.advance(60)["attempt"])
        self.assertTrue(h.submitted[0]["message"].endswith("\n\n" + resend.API_RESUME_TEXT))

    def test_the_resends_own_queue_entry_does_not_cancel_the_chain(self):
        # GAM-495 2026-11-16: resend admitted 17:43:10Z, Claude Code wrote
        # enqueue+dequeue at 17:43:11Z, the chain logged cancelled
        # reason=queued_input, and the 17:49:06Z failure was never resumed.
        h = Harness()
        h.feed(typed("u1", "계속"), api_error("e1", status=503))
        self.assertEqual(1, h.advance(60)["attempt"])
        h.feed({"type": "queue-operation", "operation": "enqueue"},
               {"type": "queue-operation", "operation": "dequeue"}, resent("s1", 1), api_error("e2", status=503))
        self.assertEqual(2, h.advance(60)["attempt"])

    def test_a_later_failure_after_progress_starts_a_new_chain(self):
        h = Harness()
        h.feed(typed("u1", "계속"), api_error("e1"))
        h.advance(60)
        h.feed(resent("s1", 1), answer("a1"), tool_result("r1"))
        self.assertIsNone(h.resend.status())
        h.feed(api_error("e2"))
        self.assertEqual(1, h.advance(60)["attempt"])
        self.assertTrue(h.submitted[-1]["message"].endswith("\n\n계속"))

    def test_stream_cut_is_adopted_only_when_handed_over(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1", truncatedAfterOutput=True))
        self.assertFalse(h.resend.adopt_cut("e1", "manual_input"))
        self.assertFalse(h.resend.adopt_cut("other", "tool_use_seen"))
        self.assertTrue(h.resend.adopt_cut("e1", "tool_use_seen"))
        self.assertEqual(1, h.advance(60)["attempt"])
        h2 = Harness()
        h2.feed(typed("u1"), api_error("e1", truncatedAfterOutput=True), typed("u2", "new"))
        self.assertFalse(h2.resend.adopt_cut("e1", "tool_use_seen"))

    def test_busy_terminal_waits_instead_of_dropping(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1"))
        h.blocked = "manual_input"
        self.assertIsNone(h.advance(300))
        self.assertEqual([], h.submitted)
        h.blocked = ""
        self.assertEqual(1, h.advance(1)["attempt"])

    def test_undelivered_resend_is_admitted_again_without_counting(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1"))
        h.advance(60)
        h.advance(resend.UNDELIVERED_AFTER_SECONDS + 1)
        self.assertEqual(1, h.advance(0)["attempt"])
        self.assertEqual(2, len(h.submitted))

    def test_resend_recognized_by_admitted_input_id(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1"))
        h.advance(60)
        h.feed({"type": "user", "uuid": "w", "isMeta": True, "origin": {"kind": "peer"},
                "message": {"content": "[ciel-wake] pending_ids=501"}}, api_error("e2"))
        self.assertEqual(2, h.advance(60)["attempt"])

    def test_session_change_cancels(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1"))
        h.scope = CompactionScope("claude", "other-session", "epoch-1")
        h.advance(60)
        self.assertEqual([], h.submitted)
        self.assertIsNone(h.resend.status())

    def test_failed_auto_compaction_resumes_any_turn(self):
        # Work continuations, channel wakes and peer prompts are not typed; a
        # failed auto-compaction still resumes them (2026-11-16).
        for opener in ({"type": "user", "uuid": "p", "isMeta": True, "origin": {"kind": "peer"},
                        "message": {"content": "[ciel-side-wake] continue the Work item"}},
                       typed("c1", "[ciel-runtime external channel message] from walkie"),
                       None):
            with self.subTest(opener=opener):
                h = Harness()
                if opener is not None:
                    h.feed(opener)
                h.feed(tool_result("r1"), compact_failed("e1"))
                self.assertIsNone(h.advance(59))
                self.assertEqual(1, h.advance(1)["attempt"])
                body = h.submitted[0]["message"]
                self.assertIn("자동 컴팩트 실패", body)
                self.assertIn("이어서 진행", body.split("\n\n", 1)[1])

    def test_failed_auto_compaction_resends_a_typed_turn_with_its_text(self):
        h = Harness()
        h.feed(typed("u1"), compact_failed("e1"))
        h.advance(60)
        self.assertTrue(h.submitted[0]["message"].endswith("\n\nFix the analytics boundary and run the tests"))

    def test_the_resends_own_compaction_does_not_cancel_and_an_answer_recovers(self):
        h = Harness()
        h.feed(typed("u1"), compact_failed("e1"))
        h.advance(60)
        h.feed(resent("s1", 1), {"type": "system", "subtype": "compact_boundary"})
        self.assertIsNotNone(h.resend.status())
        h.feed(answer("a1"))
        self.assertIsNone(h.resend.status())
        self.assertTrue(any("claude_typed_resend_recovered" in line for line in h.logs))

    def test_compaction_failing_again_reschedules_up_to_twenty(self):
        h = Harness()
        h.feed(compact_failed("e0"))
        for attempt in range(1, 21):
            due = h.resend.status()["due"] - h.clock.value
            self.assertEqual(attempt, h.advance(due)["attempt"])
            h.feed(resent(f"s{attempt}", attempt), compact_failed(f"e{attempt}"))
        self.assertIsNone(h.resend.status())
        self.assertEqual(20, len(h.submitted))
        self.assertTrue(any("claude_typed_resend_exhausted reason=last_error=auto_compact_failed" in line
                            for line in h.logs))

    def test_a_typed_chain_hitting_a_failed_compaction_continues(self):
        h = Harness()
        h.feed(typed("u1"), api_error("e1"))
        h.advance(60)
        h.feed(resent("s1", 1), compact_failed("e2"), {"type": "system", "subtype": "compact_boundary"})
        self.assertEqual(2, h.advance(60)["attempt"])

    def test_stream_recovery_monitor_drives_the_resend(self):
        import tempfile
        from pathlib import Path
        clock, submitted, logs = Clock(), [], []
        with tempfile.TemporaryDirectory() as raw:
            ledger = StreamRecoveryLedger(Path(raw) / "ledger.json", lambda *_: None, clock)
            monitor = ClaudeStreamRecoveryMonitor(ledger, lambda: {}, lambda: SCOPE,
                                                  lambda body: submitted.append(body) or {"id": 9},
                                                  lambda level, text: logs.append(text), now=clock)
            monitor.set_terminal_admission({"ready": True, "manual_input": False, "busy": False, "compaction": False,
                                            "pending": False, "at": clock.value})
            monitor.observe(typed("u1"))
            monitor.observe(api_error("e1"))
            clock.value += 60
            monitor.set_terminal_admission({"ready": True, "manual_input": False, "busy": False, "compaction": False,
                                            "pending": False, "at": clock.value})
            monitor.tick()
        self.assertEqual(1, len(submitted))
        self.assertEqual("typed_turn_resend", submitted[0]["kind"])


if __name__ == "__main__":
    unittest.main()
