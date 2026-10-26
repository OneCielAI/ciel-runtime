import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support import claude_stream_recovery_runtime as recovery
from ciel_runtime_support.runtime_compaction_control import CompactionScope
from ciel_runtime_support.transcript_delta_delivery import TranscriptDeliveryPorts, TranscriptDeltaDeliveryService


SESSION = "3dfc2a86-85c0-400c-ae40-14e508c835a3"
SCOPE = CompactionScope("claude", SESSION, "epoch-1")


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def metadata(**overrides):
    value = {
        "upstream_message_id": "msg_owned_4",
        "provider": "cielairouter",
        "failure_reason": "anthropic_stream_upstream_incomplete",
        "confirmed_incomplete": True,
        "client_disconnected": False,
        "text_len": 2382,
        "visible_text_len": 2382,
        "tool_uncertain": False,
        "emitted_tool_use": False,
        "saw_tool_use": False,
        "upstream_error_type": "",
    }
    value.update(overrides)
    return value


def assistant(uuid, parent, message_id, **extra):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "sessionId": SESSION, "isSidechain": False,
            "message": {"id": message_id, "role": "assistant", "content": [{"type": "text", "text": "partial"}]}, **extra}


def native_error(uuid="err-1", parent="a-1", **extra):
    record = assistant(uuid, parent, "f9c24f1a-synthetic", isApiErrorMessage=True, error="server_error",
                       truncatedAfterOutput=True)
    record.update(extra)
    return record


def user(uuid, text, parent="x"):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "sessionId": SESSION, "isSidechain": False,
            "message": {"role": "user", "content": text}}


class HarnessRecorder:
    """The router callback as wired: scope captured at request start."""

    def __init__(self, harness: "Harness") -> None:
        self.harness = harness
        self.recorder = recovery.ClaudeStreamFailureRecorder(harness.ledger, lambda: harness.scope, harness.log)

    def __call__(self, _headers, _body, metadata, *, start_scope=...):
        self.recorder(self.harness.scope if start_scope is ... else start_scope, metadata)


class Harness:
    def __init__(self, root: Path, *, config=None, scope=SCOPE) -> None:
        self.clock = Clock()
        self.logs: list[str] = []
        self.submitted: list[dict] = []
        self.config = config if config is not None else {}
        self.scope = scope
        self.terminal = {"ready": True, "manual_input": False, "busy": False, "compaction": False, "pending": False}
        self.ledger = recovery.StreamRecoveryLedger(root / recovery.LEDGER_NAME, self.log, self.clock)
        self.recorder = HarnessRecorder(self)
        self.monitor = self.new_monitor()

    def new_monitor(self):
        monitor = recovery.ClaudeStreamRecoveryMonitor(
            self.ledger, lambda: self.config, lambda: self.scope, self.submit, self.log, self.clock
        )
        # Fresh idle prompt report, refreshed on every tick like the proxy loop.
        original_tick = monitor.tick

        def tick():
            if self.terminal is not None:
                monitor.set_terminal_admission({**self.terminal, "at": self.clock()})
            return original_tick()

        monitor.tick = tick
        return monitor

    def log(self, level: str, message: str) -> None:
        self.logs.append(f"{level} {message}")

    def submit(self, body: dict) -> dict:
        self.submitted.append(body)
        return {"id": len(self.submitted)}

    def fail_and_report(self, monitor=None, *, failure=None, message_id="msg_owned_4", error_uuid="err-1", prompt="go"):
        monitor = monitor or self.monitor
        monitor.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
        self.recorder({}, {}, {**(failure or metadata()), "upstream_message_id": message_id})
        monitor.observe(user(f"u-{error_uuid}", prompt))
        monitor.observe(assistant(f"a-{error_uuid}", f"u-{error_uuid}", message_id))
        monitor.observe(native_error(error_uuid, f"a-{error_uuid}"))
        self.clock.value += recovery.QUIET_SECONDS + 0.1
        return monitor.tick()


class StreamRecoveryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.h = Harness(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_text_only_break_is_continued_once_after_a_quiet_interval(self):
        h = self.h
        h.recorder({}, {}, metadata())
        h.monitor.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
        h.monitor.observe(user("u-1", "do the task"))
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-1", "a-1"))
        self.assertIsNone(h.monitor.tick())
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        result = h.monitor.tick()
        self.assertEqual(1, result["attempt"])
        self.assertEqual(1, len(h.submitted))
        body = h.submitted[0]
        self.assertTrue(body["message"].startswith(recovery.RECOVERY_MARKER))
        # Delivery matches raw injections by text; each continuation names its error.
        self.assertIn("err-1", body["message"])
        other_text = recovery.recovery_notice_body(1, 1, error_uuid="c8c211b6-af47", message_id="m")["message"]
        self.assertNotEqual(body["message"], other_text)
        self.assertTrue(body["meta"]["raw_injection"])
        self.assertEqual("stream_recovery", body["meta"]["source_kind"])
        self.assertEqual("err-1", body["meta"]["native_error_uuid"])
        self.assertIsNone(h.monitor.tick())
        # A second launcher watching the same transcript cannot admit it again.
        other = h.new_monitor()
        other.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
        other.observe(assistant("a-1", "u-1", "msg_owned_4"))
        other.observe(native_error("err-1", "a-1"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(other.tick())
        self.assertEqual(1, len(h.submitted))

    def test_any_tool_block_excludes_the_failure(self):
        cases = [
            metadata(emitted_tool_use=True, saw_tool_use=True, tool_uncertain=True),
            metadata(saw_tool_use=True, tool_uncertain=True),  # block held by the relay
            metadata(tool_uncertain=True),  # server/mcp tool block or pseudo-tool text
            metadata(tool_uncertain=None),  # relay without the field: unknown
            metadata(text_len=0, visible_text_len=0, emitted_tool_use=True, saw_tool_use=True, tool_uncertain=True),
        ]
        for index, failure in enumerate(cases):
            with self.subTest(index=index):
                self.assertIsNone(self.h.fail_and_report(failure=failure, message_id=f"t{index}", error_uuid=f"te{index}"))
        self.assertEqual([], self.h.submitted)
        self.assertEqual(5, sum("reason=tool_use_seen" in line for line in self.h.logs))

    def test_nothing_shown_relay_or_client_failures_are_not_continued(self):
        cases = [
            metadata(text_len=0, visible_text_len=0),
            metadata(text_len=500, visible_text_len=0),  # received but held back, never shown
            metadata(client_disconnected=True),
            metadata(confirmed_incomplete=False),
            metadata(failure_reason="anthropic_stream_relay_error:ValueError"),
            metadata(failure_reason="anthropic_stream_upstream_error_event", upstream_error_type="invalid_request_error"),
        ]
        for index, failure in enumerate(cases):
            with self.subTest(index=index):
                self.assertIsNone(self.h.fail_and_report(failure=failure, message_id=f"m{index}", error_uuid=f"e{index}"))
        self.assertEqual([], self.h.submitted)
        transient = metadata(failure_reason="anthropic_stream_upstream_error_event", upstream_error_type="overloaded_error")
        self.assertEqual(1, self.h.fail_and_report(failure=transient, message_id="m9", error_uuid="e9")["attempt"])

    def test_activity_after_the_error_cancels_the_continuation(self):
        activities = [
            user("u-new", "계속"),
            {"type": "queue-operation", "operation": "enqueue", "sessionId": SESSION},
            assistant("a-retry", "err-1", "msg_retry"),
            {"type": "permission-mode", "permissionMode": "plan", "sessionId": SESSION},
            {"type": "system", "subtype": "compact_boundary", "sessionId": SESSION},
            {"type": "user", "uuid": "tr", "sessionId": SESSION, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}},
        ]
        for index, activity in enumerate(activities):
            with self.subTest(index=index):
                h = Harness(self.root / str(index))
                h.recorder({}, {}, metadata())
                h.monitor.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
                h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
                h.monitor.observe(native_error("err-1", "a-1"))
                h.monitor.observe(activity)
                h.clock.value += recovery.QUIET_SECONDS + 0.1
                self.assertIsNone(h.monitor.tick())
                self.assertEqual([], h.submitted)

    def test_runtime_input_admitted_after_the_error_cancels_the_continuation(self):
        h = self.h
        queue = {"max_id": 7}
        h.monitor.input_watermark = lambda: queue["max_id"]
        h.recorder({}, {}, metadata())
        h.monitor.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-1", "a-1"))
        queue["max_id"] = 8  # an external or Web Chat input still waiting for delivery
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        self.assertEqual([], h.submitted)
        self.assertEqual("newer_input", h.ledger.recent()[-1]["reason"])

    def test_unreadable_runtime_input_queue_fails_closed(self):
        h = self.h

        def unreadable():
            raise OSError("locked")

        h.monitor.input_watermark = unreadable
        self.assertIsNone(h.fail_and_report())
        self.assertEqual([], h.submitted)

    def test_unknown_permission_mode_fails_closed(self):
        h = Harness(self.root / "unknown")
        h.recorder({}, {}, metadata())
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-1", "a-1"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        self.assertEqual("permission_mode_unknown", h.ledger.recent()[-1]["reason"])

    def test_chain_budget_survives_marker_less_delivery_record(self):
        h = self.h
        self.assertEqual(1, h.fail_and_report()["attempt"])
        # Router delivery records a wake marker, not the continuation text.
        # The harness gateway returned id 1 for the continuation.
        self.assertIsNone(h.fail_and_report(message_id="msg_2", error_uuid="err-2", prompt="[ciel-wake] pending_ids=1"))
        self.assertEqual(1, len(h.submitted))
        # A wake marker for other inputs is new work and opens a new chain.
        self.assertEqual(1, h.fail_and_report(message_id="msg_3", error_uuid="err-3", prompt="[ciel-wake] pending_ids=9")["attempt"])

    def test_launcher_epoch_must_match_shared_scope(self):
        h = self.h
        h.monitor.bound_epoch = lambda: "older-epoch"
        self.assertIsNone(h.fail_and_report())
        self.assertEqual("scope", h.ledger.recent()[-1]["reason"])

    def test_queued_notice_expires_when_scope_or_time_changes(self):
        body = recovery.recovery_notice_body(1, 1, error_uuid="err-1", message_id="msg", scope=SCOPE, created_at=100.0)
        self.assertEqual("", recovery.recovery_notice_skip_reason(body, SCOPE, 101.0))
        self.assertEqual("stream_recovery_scope_changed", recovery.recovery_notice_skip_reason(
            body, CompactionScope("claude", SESSION, "epoch-2"), 101.0))
        self.assertEqual("stream_recovery_expired", recovery.recovery_notice_skip_reason(
            body, SCOPE, 100.0 + recovery.RECOVERY_NOTICE_TTL_SECONDS + 1))
        self.assertEqual("", recovery.recovery_notice_skip_reason({"meta": {"source_kind": "notification"}}, None, 0.0))

    def test_terminal_must_report_an_idle_prompt(self):
        cases = {
            "terminal_unknown": None,
            "manual_input": {"manual_input": True},
            "terminal_busy": {"busy": True},
            "compaction": {"compaction": True},
            "pending_input": {"pending": True},
            "terminal_not_ready": {"ready": False},
        }
        for index, (reason, change) in enumerate(cases.items()):
            with self.subTest(reason=reason):
                h = Harness(self.root / f"terminal-{index}")
                h.terminal = None if change is None else {**h.terminal, **change}
                self.assertIsNone(h.fail_and_report())
                self.assertEqual([], h.submitted)
                self.assertEqual(reason, h.ledger.recent()[-1]["reason"])

    def test_stale_terminal_report_fails_closed(self):
        h = self.h
        h.terminal = None
        h.monitor.set_terminal_admission({"ready": True, "manual_input": False, "busy": False,
                                          "compaction": False, "pending": False, "at": h.clock()})
        self.assertIsNone(h.fail_and_report())
        self.assertEqual("terminal_stale", h.ledger.recent()[-1]["reason"])

    def test_meta_reminders_are_not_input(self):
        h = self.h
        h.recorder({}, {}, metadata())
        h.monitor.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-1", "a-1"))
        h.monitor.observe({"type": "user", "uuid": "meta", "sessionId": SESSION, "isMeta": True,
                           "message": {"role": "user", "content": "<local-command-caveat>x</local-command-caveat>"}})
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertEqual(1, h.monitor.tick()["attempt"])

    def test_error_shown_in_plan_mode_is_not_continued(self):
        h = self.h
        h.recorder({}, {}, metadata())
        h.monitor.observe({"type": "permission-mode", "permissionMode": "plan", "sessionId": SESSION})
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-1", "a-1"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        self.assertEqual([], h.submitted)

    def test_unmatched_sidechain_or_untruncated_errors_are_ignored(self):
        h = self.h
        h.recorder({}, {}, metadata())
        h.monitor.observe({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION})
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-side", "a-1", isSidechain=True))
        h.monitor.observe(native_error("err-plain", "a-1", truncatedAfterOutput=None))
        h.monitor.observe(native_error("err-orphan", "unknown-parent"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        # A matching native error without a router failure (another route, a
        # remote bridge, or a request this router never saw) is not ours.
        h.monitor.observe(assistant("a-2", "u-2", "msg_not_recorded"))
        h.monitor.observe(native_error("err-2", "a-2"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        self.assertEqual([], h.submitted)
        self.assertTrue(any("reason=no_router_failure" in line for line in h.logs))

    def test_other_session_epoch_or_subagent_failure_is_not_continued(self):
        h = self.h
        h.recorder({}, {}, metadata())
        h.scope = CompactionScope("claude", SESSION, "epoch-2")
        h.monitor.observe(assistant("a-1", "u-1", "msg_owned_4"))
        h.monitor.observe(native_error("err-1", "a-1"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        h.scope = None
        h.recorder({}, {}, metadata(upstream_message_id="msg_sub"))
        self.assertTrue(any("reason=not_main_session" in line for line in h.logs))
        # A request that started in epoch-1 and failed after a relaunch is not recorded.
        h.scope = CompactionScope("claude", SESSION, "epoch-2")
        h.recorder({}, {}, metadata(upstream_message_id="msg_late"), start_scope=SCOPE)
        self.assertTrue(any("reason=scope_changed" in line for line in h.logs))
        self.assertIsNone(h.ledger.failure(h.scope, "msg_late"))
        self.assertEqual([], h.submitted)

    def test_chain_budget_counts_failures_answering_a_continuation(self):
        h = self.h
        self.assertEqual(1, h.fail_and_report()["attempt"])
        # Claude Code records session-socket input as a meta peer message
        # (owned E2E 212228); it still opens the next turn.
        continuation = {"type": "user", "uuid": "u-cont", "sessionId": SESSION, "isMeta": True,
                        "origin": {"kind": "peer", "from": "unknown"},
                        "message": {"role": "user", "content": "Another Claude session sent a message:" + chr(10) + h.submitted[0]["message"]}}
        h.recorder({}, {}, metadata(upstream_message_id="msg_2"))
        h.monitor.observe(continuation)
        h.monitor.observe(assistant("a-2", "u-cont", "msg_2"))
        h.monitor.observe(native_error("err-2", "a-2"))
        h.clock.value += recovery.QUIET_SECONDS + 0.1
        self.assertIsNone(h.monitor.tick())
        self.assertEqual(1, len(h.submitted))
        self.assertTrue(any("claimed_or_exhausted" in line for line in h.logs))
        # A person's new prompt starts a new chain.
        self.assertEqual(1, h.fail_and_report(message_id="msg_3", error_uuid="err-3", prompt="new work")["attempt"])

    def test_two_attempts_when_configured_and_never_more(self):
        h = Harness(self.root / "two", config={recovery.CONFIG_KEY: {"max_attempts": 9}})
        self.assertEqual((True, recovery.MAX_ATTEMPTS), recovery.settings(h.config))
        self.assertEqual(1, h.fail_and_report()["attempt"])
        second = h.fail_and_report(message_id="m2", error_uuid="e2", prompt=h.submitted[-1]["message"])
        self.assertEqual(2, second["attempt"])
        self.assertIsNone(h.fail_and_report(message_id="m3", error_uuid="e3", prompt=h.submitted[-1]["message"]))
        self.assertEqual(2, len(h.submitted))

    def test_disabled_setting_admits_nothing(self):
        h = Harness(self.root / "off", config={recovery.CONFIG_KEY: {"enabled": False}})
        self.assertIsNone(h.fail_and_report())
        self.assertEqual([], h.submitted)

    def test_failed_submission_is_recorded_and_not_retried(self):
        h = self.h

        def broken(_body):
            raise OSError("queue unavailable")

        h.monitor.submit = broken
        self.assertIsNone(h.fail_and_report())
        value = json.loads((self.root / recovery.LEDGER_NAME).read_text(encoding="utf-8"))
        self.assertEqual("submit_failed", value["handled"]["err-1"]["status"])

    def test_ledger_keeps_flags_only(self):
        self.h.recorder({}, {}, metadata())
        text = (self.root / recovery.LEDGER_NAME).read_text(encoding="utf-8")
        self.assertNotIn("partial", text.replace("partial_tool_use", ""))
        entry = json.loads(text)["failures"]["msg_owned_4"]
        self.assertEqual(SESSION, entry["session_id"])
        self.assertTrue(entry["visible_text"])


class TranscriptPollTests(unittest.TestCase):
    def test_poll_starts_at_the_end_and_feeds_new_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / f"{SESSION}.jsonl"
            transcript.write_text(json.dumps(native_error("old", "a-old")) + "\n", encoding="utf-8")
            h = Harness(root)
            h.recorder({}, {}, metadata())
            service = TranscriptDeltaDeliveryService(
                root / "cursors.json", "workspace",
                TranscriptDeliveryPorts(lambda: {}, lambda: transcript, lambda: {"runtime": "claude", "session_id": SESSION},
                                        h.log, stream_recovery=lambda: h.monitor),
            )
            self.assertEqual(0, service.poll_stream_recovery())
            with transcript.open("a", encoding="utf-8") as stream:
                for record in ({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION},
                               user("u-1", "go"), assistant("a-1", "u-1", "msg_owned_4"), native_error("err-1", "a-1")):
                    stream.write(json.dumps(record) + "\n")
            self.assertEqual(4, service.poll_stream_recovery())
            self.assertEqual([], h.submitted)
            h.clock.value += recovery.QUIET_SECONDS + 0.1
            service.poll_stream_recovery()
            self.assertEqual(1, len(h.submitted))

    def test_poll_waits_for_transcript_tail_before_deciding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transcript = root / f"{SESSION}.jsonl"
            transcript.write_text("", encoding="utf-8")
            h = Harness(root)
            h.recorder({}, {}, metadata())
            service = TranscriptDeltaDeliveryService(
                root / "cursors.json", "workspace",
                TranscriptDeliveryPorts(lambda: {}, lambda: transcript, lambda: {"runtime": "claude", "session_id": SESSION},
                                        h.log, stream_recovery=lambda: h.monitor),
            )
            service.poll_stream_recovery()
            with transcript.open("a", encoding="utf-8") as stream:
                for record in ({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION},
                               assistant("a-1", "u-1", "msg_owned_4"), native_error("err-1", "a-1")):
                    stream.write(json.dumps(record) + chr(10))
                stream.write(json.dumps({"type": "progress", "pad": "x" * 1_100_000}) + chr(10))
                stream.write(json.dumps(user("u-new", "person input")) + chr(10))
            h.clock.value += recovery.QUIET_SECONDS + 0.1
            service.poll_stream_recovery()
            self.assertEqual([], h.submitted)
            service.poll_stream_recovery()
            h.clock.value += recovery.QUIET_SECONDS + 0.1
            service.poll_stream_recovery()
            self.assertEqual([], h.submitted)

    def test_poll_does_nothing_for_other_runtimes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            h = Harness(root)
            service = TranscriptDeltaDeliveryService(
                root / "cursors.json", "workspace",
                TranscriptDeliveryPorts(lambda: {}, lambda: root / "x.jsonl", lambda: {"runtime": "codex"},
                                        h.log, stream_recovery=lambda: h.monitor),
            )
            self.assertEqual(0, service.poll_stream_recovery())


if __name__ == "__main__":
    unittest.main()
