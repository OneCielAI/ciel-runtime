"""Resend of injected runtime inputs whose turn failed on a transient upstream error.

User decision 2026-10-24: resend the original message, 10 attempts, 10 s apart;
anything else fails at once and delivery moves to the next input.  .14 evidence
(2026-10-23): Ara 53 rows "unexpected status 504", 26 "high demand", 3 "stream
disconnected"; Kobe 53 "turn_failed" - every one recorded failed, none retried.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import unittest
from typing import Any
from unittest.mock import patch

from ciel_runtime_support import runtime_input_retry as retry_policy
from ciel_runtime_support.channel_inflight import (
    ChannelInflightEffects,
    ChannelInflightPolicy,
    ChannelInflightSnapshot,
    advance_channel_inflight,
)
from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages
from ciel_runtime_support.channel_transcript import ChannelWakeTranscriptServices, wake_state_evidence_from_text
from ciel_runtime_support.channel_wake_delivery_repository import ChannelWakeDeliveryRepository, DeliveryAttempts
from ciel_runtime_support.codex_desktop_injection import CodexDesktopChannelInjector, CodexDesktopChannelPorts
from ciel_runtime_support.runtime_input_delivery_health import delivery_health_text, runtime_input_delivery_health
from ciel_runtime_support.runtime_input_retry import RetrySettings, TurnFailure
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
from test_channel_pending_injection import _InjectionHarness, _message as _plain_message
from test_codex_desktop_injection import _Client, _message


def _repository(root: pathlib.Path) -> RuntimeInputStatusRepository:
    return RuntimeInputStatusRepository(root / "runtime-input-status.jsonl", lambda **_kw: None,
                                        lambda *_args: None, threading.RLock())


def _failed(turn_id: str, info: Any, message: str = "upstream error") -> dict[str, Any]:
    return {"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {
        "id": turn_id, "status": "failed", "error": {"message": message, "codexErrorInfo": info}}}}


def _completed(turn_id: str) -> dict[str, Any]:
    return {"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {"id": turn_id, "status": "completed"}}}


class ClassificationTests(unittest.TestCase):
    def test_codex_typed_transient_kinds_and_statuses(self):
        cases = [
            ("serverOverloaded", True, "serverOverloaded"),
            ({"responseStreamConnectionFailed": {"httpStatusCode": 504}}, True, "responseStreamConnectionFailed 504"),
            ({"responseStreamDisconnected": {"httpStatusCode": None}}, True, "responseStreamDisconnected"),
            ({"httpConnectionFailed": {"httpStatusCode": 401}}, False, "httpConnectionFailed 401"),
            ("badRequest", False, "badRequest"),
            ("other", False, "other"),  # real codex 0.158.0 for an upstream 400
            ("contextWindowExceeded", False, "contextWindowExceeded"),
            ("usageLimitExceeded", False, "usageLimitExceeded"),
            ("unauthorized", False, "unauthorized"),
        ]
        for info, transient, label in cases:
            with self.subTest(info=info):
                failure = retry_policy.classify_codex_turn({"status": "failed", "error": {"codexErrorInfo": info}})
                self.assertEqual((transient, label), (failure.transient, failure.label))

    def test_codex_status_prefix_fallback_interrupt_and_unknown(self):
        # .14 verbatim reason: "unexpected status 504 Gateway Timeout".
        failure = retry_policy.classify_codex_turn(
            {"status": "failed", "error": {"message": "unexpected status 504 Gateway Timeout: body"}})
        self.assertEqual(("httpStatus 504", True), (failure.label, failure.transient))
        self.assertFalse(retry_policy.classify_codex_turn(
            {"status": "failed", "error": {"message": "unexpected status 400 Bad Request"}}).transient)
        self.assertEqual("turn_interrupted", retry_policy.classify_codex_turn({"status": "interrupted"}).kind)
        unknown = retry_policy.classify_codex_turn({"status": "failed", "error": {"message": "high demand"}})
        self.assertEqual(("turn_failed", False), (unknown.kind, unknown.transient))

    def test_claude_api_error_records(self):
        cases = [
            ({"error": "server_error", "apiErrorStatus": 503}, True),
            ({"error": "server_error"}, True),  # "Connection refused": nothing reached the model
            ({"error": "server_error", "apiErrorStatus": 500, "truncatedAfterOutput": True}, False),
            ({"error": "rate_limit", "apiErrorStatus": 429, "apiErrorIsTransient": True}, True),
            ({"error": "rate_limit", "apiErrorStatus": 429, "apiErrorIsTransient": False}, False),
            ({"error": "rate_limit", "apiErrorStatus": 429}, False),  # weekly limit
            ({"error": "unknown", "apiErrorStatus": 400}, False),
            ({"error": "billing_error", "apiErrorStatus": 400, "resendCannotCure": True}, False),
            ({"error": "authentication_failed"}, False),
        ]
        for record, transient in cases:
            with self.subTest(record=record):
                self.assertEqual(transient, retry_policy.classify_claude_api_error(record).transient)

    def test_failed_state_round_trip_and_marker(self):
        state = retry_policy.encode_failed_state(TurnFailure("server_error", 503, True), 1700000000.5)
        self.assertEqual("turn_failed:1:1700000000.500:server_error 503", state)
        self.assertEqual(TurnFailure("server_error", 503, True), retry_policy.decode_failed_state(state))
        self.assertEqual(1700000000.5, retry_policy.failed_state_epoch(state))
        self.assertEqual("[ciel-runtime 재전송 2/10 · 이전 시도 업스트림 오류: serverOverloaded]",
                         retry_policy.marker(2, 10, "serverOverloaded"))

    def test_settings_defaults_bounds_and_menu(self):
        self.assertEqual(RetrySettings(10, 10.0), retry_policy.settings({}))
        self.assertEqual(RetrySettings(30, 600.0), retry_policy.settings(
            {"runtime_input_retry": {"limit": 99, "interval_seconds": 9999}}))
        config: dict[str, Any] = {}
        retry_policy.apply(config, retry_policy.VALUE_LIMIT, "0")
        self.assertFalse(retry_policy.settings(config).enabled)
        self.assertEqual("resend off", retry_policy.short_summary(config))
        retry_policy.apply(config, retry_policy.VALUE_LIMIT, "10")
        retry_policy.apply(config, retry_policy.VALUE_INTERVAL, "15")
        self.assertEqual("resend 10x/15s", retry_policy.short_summary(config))
        rows, values = retry_policy.with_panel_rows(["A", "Back"], ["a", "back"], config)
        self.assertEqual("back", values[-1])
        self.assertIn(retry_policy.VALUE_LIMIT, values)
        self.assertIn("Resend after upstream error  [10 attempts]", rows[1])


class StatusRepositoryTests(unittest.TestCase):
    def test_schedule_and_claim_are_compare_and_set(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = _repository(pathlib.Path(temp))
            repo.transition(5, "queued")
            repo.transition(5, "submitted")
            retry = {"attempt": 1, "limit": 10, "due_epoch": 10.0, "claim": ""}
            self.assertEqual("queued", repo.schedule_retry(5, retry, reason="retry_scheduled:x").get("status"))
            self.assertEqual({}, repo.schedule_retry(5, retry, reason="retry_scheduled:x"))
            claim = repo.claim_retry(5, 1)
            self.assertTrue(claim)
            self.assertEqual("", repo.claim_retry(5, 1))
            self.assertTrue(repo.release_retry_claim(5, claim, 20.0))
            self.assertEqual(20.0, repo.get(5)["data"]["retry"]["due_epoch"])

    def test_launch_backlog_below_the_floor_is_recorded_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = _repository(pathlib.Path(temp))
            for request_id in (1, 2, 3, 4):
                repo.transition(request_id, "queued")
            repo.transition(2, "submitted")
            self.assertEqual([1, 3], repo.expire_launch_backlog(3, 123.0))
            self.assertEqual(("skipped", "launch_replay_window_expired"),
                             (repo.get(1)["status"], repo.get(1)["reason"]))
            self.assertEqual("submitted", repo.get(2)["status"])
            self.assertEqual("queued", repo.get(4)["status"])
            health = runtime_input_delivery_health(repo, now=200.0)
            self.assertEqual(2, health["launch_expired_count"])
            self.assertIn("expired at launch 2", delivery_health_text(health))


class CodexResendTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._temp.name)
        self.repo = _repository(self.root)
        self.clock = [1000.0]
        self.messages: list[dict[str, Any]] = []
        self.cursor = 0
        self.logs: list[str] = []
        self.paused = [False]
        self.settings = RetrySettings()
        self.client = _Client()
        self.injector = self._injector()

    def tearDown(self):
        self._temp.cleanup()

    def _injector(self) -> CodexDesktopChannelInjector:
        control = type("Gate", (), {"external_admission_paused": lambda _self: self.paused[0]})()
        injector = CodexDesktopChannelInjector(
            lambda: self.client,  # type: ignore[arg-type,return-value]
            CodexDesktopChannelPorts(
                read_messages=lambda last_id, limit: [m for m in self.messages if m["id"] > last_id][:limit],
                read_cursor=lambda: self.cursor,
                commit_cursor=lambda message_id: setattr(self, "cursor", max(self.cursor, message_id)),
                status=self.repo,
                log=lambda level, message: self.logs.append(f"{level} {message}"),
                compaction=control,  # type: ignore[arg-type]
                retry_settings=lambda: self.settings,
            ),
            cwd="C:/work", version="test", wall_clock=lambda: self.clock[0],
        )
        injector.client = self.client  # type: ignore[assignment]
        injector.thread_id = "thread-a"
        return injector

    def _queue(self, message_id: int, text: str) -> None:
        self.messages.append(_message(message_id, text))
        self.repo.transition(message_id, "queued")

    def _turn_of(self, message_id: int) -> str:
        return next(turn for turn, ids in self.injector._delivery.by_turn.items() if message_id in ids)

    def _starts(self) -> list[str]:
        return [text for kind, _thread, text in self.client.calls if kind == "start"]

    def test_transient_failures_resend_in_order_until_success(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self.assertEqual(1, len(self._starts()))
        self._queue(2, "second body")
        for attempt in (1, 2, 3):
            sent = len(self._starts())
            self.client.notifications.put(_failed(self._turn_of(1),
                                                  {"responseStreamConnectionFailed": {"httpStatusCode": 504}}))
            self.injector.poll_once()
            row = self.repo.get(1)
            self.assertEqual(("queued", attempt), (row["status"], row["data"]["retry"]["attempt"]))
            # Not due yet: nothing is sent, and the later input waits behind it.
            self.clock[0] += 9.0
            self.injector.poll_once()
            self.assertEqual(sent, len(self._starts()))
            if attempt == 1:
                self.assertFalse(any("second body" in text for text in self._starts()))
            self.clock[0] += 1.0
            self.injector.poll_once()
            resend = self._starts()[sent]
            # The delivery prompt keeps its own first line (id, automatic
            # prefix); the marker opens the message body.
            self.assertTrue(resend.startswith("[ciel-runtime external channel message]"))
            self.assertIn(f"[ciel-runtime 재전송 {attempt}/10 · 이전 시도 업스트림 오류: "
                          "responseStreamConnectionFailed 504] first body", resend)
            self.assertEqual("submitted", self.repo.get(1)["status"])
            if attempt == 1:
                # Once the resend is out, the later input follows.
                self.assertIn("second body", self._starts()[-1])
        self.client.notifications.put(_completed(self._turn_of(1)))
        self.injector.poll_once()
        self.assertEqual("replied", self.repo.get(1)["status"])
        self.assertEqual(3, self.repo.get(1)["data"]["retry"]["attempt"])

    def test_non_transient_failure_is_not_resent_and_the_next_input_proceeds(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self._queue(2, "second body")
        self.client.notifications.put(_failed("turn-1", "badRequest", "invalid input"))
        self.injector.poll_once()
        row = self.repo.get(1)
        self.assertEqual(("failed", "turn_failed:badRequest invalid input"), (row["status"], row["reason"]))
        self.assertEqual(2, len(self._starts()))
        self.assertIn("second body", self._starts()[1])

    def test_ten_failures_end_retry_exhausted_and_delivery_moves_on(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        for _ in range(10):
            self.client.notifications.put(_failed(self._turn_of(1), "serverOverloaded"))
            self.injector.poll_once()
            self.clock[0] += 10.0
            self.injector.poll_once()
        self.assertEqual(11, len(self._starts()))
        self._queue(2, "second body")
        self.client.notifications.put(_failed(self._turn_of(1), "serverOverloaded"))
        self.injector.poll_once()
        row = self.repo.get(1)
        self.assertEqual(("failed", "retry_exhausted:serverOverloaded"), (row["status"], row["reason"]))
        self.assertIn("second body", self._starts()[-1])
        health = runtime_input_delivery_health(self.repo, inputs_path=self.root / "none.jsonl")
        self.assertEqual(1, health["retry_exhausted_count"])
        self.assertIn("resend exhausted 1", delivery_health_text(health))

    def test_will_retry_error_notification_does_not_fail_the_input(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self.client.notifications.put({"method": "error", "params": {
            "threadId": "thread-a", "turnId": "turn-1", "willRetry": True,
            "error": {"message": "reconnecting", "codexErrorInfo": "serverOverloaded"}}})
        self.injector.poll_once()
        self.assertEqual("submitted", self.repo.get(1)["status"])
        self.assertTrue(any("turn_error_retrying" in line for line in self.logs))

    def test_final_error_notification_types_an_untyped_turn_failure(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self.client.notifications.put({"method": "error", "params": {
            "threadId": "thread-a", "turnId": "turn-1", "willRetry": False,
            "error": {"message": "x", "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 502}}}}})
        self.client.notifications.put({"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {
            "id": "turn-1", "status": "failed", "error": {"message": "stream disconnected"}}}})
        self.injector.poll_once()
        self.assertEqual("retry_scheduled:responseStreamDisconnected 502", self.repo.get(1)["reason"])

    def test_paused_admission_defers_a_due_resend_without_spending_it(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self.client.notifications.put(_failed("turn-1", "serverOverloaded"))
        self.injector.poll_once()
        self.paused[0] = True
        self.clock[0] += 60.0
        self.injector.poll_once()
        self.assertEqual(1, len(self._starts()))
        retry = self.repo.get(1)["data"]["retry"]
        self.assertEqual((1, ""), (retry["attempt"], retry["claim"]))
        self.paused[0] = False
        self.injector.poll_once()
        self.assertIn("[ciel-runtime 재전송 1/10", self._starts()[1])

    def test_limit_zero_disables_resending(self):
        self.settings = RetrySettings(limit=0)
        self._queue(1, "first body")
        self.injector.poll_once()
        self.client.notifications.put(_failed("turn-1", "serverOverloaded"))
        self.injector.poll_once()
        self.assertEqual(("failed", "turn_failed:serverOverloaded upstream error"),
                         (self.repo.get(1)["status"], self.repo.get(1)["reason"]))

    def test_user_interrupt_is_never_resent(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self.client.notifications.put({"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {
            "id": "turn-1", "status": "interrupted"}}})
        self.injector.poll_once()
        self.assertEqual(("failed", "turn_interrupted"), (self.repo.get(1)["status"], self.repo.get(1)["reason"]))

    def test_tool_activity_is_recorded_on_the_resend(self):
        self._queue(1, "first body")
        self.injector.poll_once()
        self.client.notifications.put({"method": "item/started", "params": {
            "threadId": "thread-a", "turnId": "turn-1", "item": {"type": "commandExecution", "id": "c1"}}})
        self.client.notifications.put(_failed("turn-1", "serverOverloaded"))
        self.injector.poll_once()
        self.assertTrue(self.repo.get(1)["data"]["retry"]["tool_activity"])

    def test_a_new_injector_adopts_the_resend_and_a_stale_claim_is_never_resent(self):
        self._queue(1, "first body")
        self._queue(2, "second body")
        self.injector.poll_once()
        self.client.notifications.put(_failed("turn-1", "serverOverloaded"))
        self.client.notifications.put(_failed("turn-2", "serverOverloaded"))
        self.injector.poll_once()
        self.assertTrue(self.repo.claim_retry(2, 1, now=self.clock[0]))
        self.client = _Client()
        self.clock[0] += 200.0
        restarted = self._injector()
        restarted._initial_thread_id = "thread-a"
        restarted.open()
        self.assertEqual(("failed", "retry_send_uncertain"), (self.repo.get(2)["status"], self.repo.get(2)["reason"]))
        restarted.poll_once()
        self.assertIn("[ciel-runtime 재전송 1/10", self._starts()[0])
        self.assertIn("first body", self._starts()[0])


def _transcript(*records: dict[str, Any]) -> str:
    return "\n".join(json.dumps(record) for record in records) + "\n"


def _user(text: str, stamp: str) -> dict[str, Any]:
    return {"type": "user", "timestamp": stamp, "message": {"role": "user", "content": text}}


def _api_error(stamp: str, **fields: Any) -> dict[str, Any]:
    return {"type": "assistant", "isApiErrorMessage": True, "timestamp": stamp, "error": "server_error",
            "message": {"role": "assistant", "model": "<synthetic>",
                        "content": [{"type": "text", "text": "API Error: 503"}]}, **fields}


def _answer(stamp: str) -> dict[str, Any]:
    return {"type": "assistant", "timestamp": stamp, "message": {"role": "assistant", "content": "ok"}}


class ClaudeTranscriptTests(unittest.TestCase):
    services = ChannelWakeTranscriptServices(
        claim_prompt=lambda _message_id: "",
        prompt_references_message_id=lambda text, message_id, _prompts: f"#{message_id}" in text,
        prompt_message_ids=lambda text: {int(t[1:]) for t in text.split() if t.startswith("#") and t[1:].isdigit()},
        now=lambda: 100.0,
    )

    def _state(self, *records: dict[str, Any]) -> str:
        return wake_state_evidence_from_text(7, _transcript(*records), None, self.services).state

    def test_an_api_error_answering_the_prompt_is_a_failed_turn_not_a_reply(self):
        state = self._state(_user("input #7", "2026-10-24T01:00:00Z"),
                            _api_error("2026-10-24T01:00:01Z", apiErrorStatus=503))
        failure = retry_policy.decode_failed_state(state)
        self.assertEqual(TurnFailure("server_error", 503, True), failure)
        self.assertIsNotNone(retry_policy.failed_state_epoch(state))

    def test_a_real_reply_still_completes_and_the_latest_attempt_decides(self):
        self.assertEqual("completed", self._state(_user("input #7", "2026-10-24T01:00:00Z"),
                                                  _answer("2026-10-24T01:00:02Z")))
        first = (_user("input #7", "2026-10-24T01:00:00Z"), _api_error("2026-10-24T01:00:01Z"))
        resent = _user("[ciel-runtime 재전송 1/10] input #7", "2026-10-24T01:00:12Z")
        self.assertEqual("pending", self._state(*first, resent))
        self.assertEqual("completed", self._state(*first, resent, _answer("2026-10-24T01:00:13Z")))
        self.assertTrue(retry_policy.is_failed_state(
            self._state(*first, resent, _api_error("2026-10-24T01:00:13Z"))))


class _Effects:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.commits: list[int] = []
        self.completed: list[int] = []

    def build(self) -> ChannelInflightEffects:
        return ChannelInflightEffects(
            commit_cursor=self.commits.append, complete_wake=self.completed.append,
            release_wake=lambda _message_id: None, ensure_cursor=lambda: 4, log=lambda *_args: None,
            turn_failed=lambda _message_id, _state, _started: self.outcome,
        )


class ClaudeInflightTests(unittest.TestCase):
    policy = ChannelInflightPolicy(unseen_retry_seconds=20.0, waiting_log_interval=30.0,
                                   is_stale=lambda *_args: False, commit_cursor_on_stale=False,
                                   log_namespace="t", stale_event="stale")

    def _advance(self, outcome: str, now: float = 105.0):
        effects = _Effects(outcome)
        state = retry_policy.encode_failed_state(TurnFailure("server_error", 503, True), 101.0)
        update = advance_channel_inflight(
            ChannelInflightSnapshot(7, 7, state, started_at=100.0, logged_at=100.0, now=now, last_id=7),
            self.policy, effects.build())
        return update, effects

    def test_retry_keeps_the_cursor_and_rescans_from_the_durable_cursor(self):
        update, effects = self._advance("retry")
        self.assertEqual(("retrying", None, 4), (update.action, update.message_id, update.last_id))
        self.assertEqual([], effects.commits)

    def test_terminal_failure_commits_and_moves_on(self):
        update, effects = self._advance("failed")
        self.assertEqual("failed", update.action)
        self.assertEqual([7], effects.commits)

    def test_an_earlier_attempts_error_is_treated_as_missing_evidence(self):
        update, _ = self._advance("stale", now=101.0)
        self.assertEqual("none", update.action)
        update, _ = self._advance("stale", now=125.0)
        self.assertEqual("failed", update.action)


class ClaudeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.repo = _repository(pathlib.Path(self._temp.name))
        self.delivery = ChannelWakeDeliveryRepository(
            lock=threading.RLock(), delivered=set(), prompts={}, batches={},
            clear_claim=lambda _message_id: None, commit_cursor=lambda _message_id: None,
            status=self.repo, attempts=DeliveryAttempts(child_epoch="epoch-1"))

    def tearDown(self):
        self._temp.cleanup()

    def _submit(self, message_id: int) -> None:
        self.repo.transition(message_id, "queued")
        self.delivery.begin_attempt([{"id": message_id}])

    def _state(self, transient: bool = True, at: float | None = None) -> str:
        return retry_policy.encode_failed_state(TurnFailure("server_error", 503, transient), at)

    def test_transient_failure_queues_a_resend_and_others_fail_typed(self):
        self._submit(7)
        self.assertEqual("retry", self.delivery.turn_failed(7, self._state()))
        retry = self.delivery.pending_retry(7)
        self.assertEqual((1, "claude", "server_error 503"), (retry["attempt"], retry["runtime"], retry["last_failure"]))
        self._submit(8)
        self.assertEqual("failed", self.delivery.turn_failed(8, self._state(False)))
        self.assertEqual(("failed", "turn_failed:server_error 503"),
                         (self.repo.get(8)["status"], self.repo.get(8)["reason"]))

    def test_an_error_older_than_the_attempt_is_stale(self):
        self._submit(7)
        self.assertEqual("stale", self.delivery.turn_failed(7, self._state(at=50.0), 100.0))
        self.assertEqual("submitted", self.repo.get(7)["status"])

    def test_exhausted_after_the_limit(self):
        self._submit(7)
        settings = RetrySettings(limit=1)
        self.assertEqual("retry", self.delivery.turn_failed(7, self._state(), retry_settings=settings))
        self.delivery.begin_attempt([{"id": 7}])
        self.assertEqual("failed", self.delivery.turn_failed(7, self._state(), retry_settings=settings))
        self.assertEqual("retry_exhausted:server_error 503", self.repo.get(7)["reason"])

    def test_input_without_a_status_row_keeps_the_old_completion_reading(self):
        self.assertEqual("completed", self.delivery.turn_failed(9, self._state()))


class ClaudeInjectionTests(unittest.TestCase):
    def _harness(self, temp: str, due_epoch: float) -> _InjectionHarness:
        repo = _repository(pathlib.Path(temp))
        repo.transition(10, "queued")
        repo.transition(10, "submitted", data={"attempt_id": "a"})
        repo.schedule_retry(10, {"attempt": 1, "limit": 10, "due_epoch": due_epoch, "claim": "",
                                 "last_failure": "server_error 503"}, reason="retry_scheduled:x")
        repo.transition(11, "queued")
        harness = _InjectionHarness([_plain_message(10), _plain_message(11)])
        harness.delivery.status = repo
        return harness

    def test_a_resend_not_yet_due_holds_every_later_input(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = self._harness(temp, due_epoch=2000.0)
            with patch("ciel_runtime_support.channel_pending_injection.time.time", return_value=1995.0):
                returned = inject_pending_channel_messages(99, 9, None, services=harness.services())
        self.assertEqual([], harness.written)
        self.assertEqual(9, returned)

    def test_a_due_resend_goes_first_with_its_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = self._harness(temp, due_epoch=2000.0)
            with patch("ciel_runtime_support.channel_pending_injection.time.time", return_value=2001.0):
                inject_pending_channel_messages(99, 9, None, services=harness.services())
            status = harness.delivery.status.get(10)["status"]
        self.assertEqual(["[ciel-runtime 재전송 1/10 · 이전 시도 업스트림 오류: server_error 503]\nbody-10"],
                         harness.written)
        self.assertEqual("submitted", status)

    def test_a_failed_turn_without_a_resend_is_never_replayed(self):
        harness = _InjectionHarness([_plain_message(12)])
        services = harness.services()
        state = retry_policy.encode_failed_state(TurnFailure("unknown", 400), 1.0)
        services = type(services)(**{**{f: getattr(services, f) for f in services.__dataclass_fields__},
                                     "state": type(services.state)(**{
                                         **{f: getattr(services.state, f) for f in services.state.__dataclass_fields__},
                                         "wake_state_for_message": lambda _m, _p=None: state})})
        inject_pending_channel_messages(99, 11, None, services=services)
        self.assertEqual([], harness.written)


class CompactionDiagnosticsTests(unittest.TestCase):
    """Ara .14: admission state still held an earlier Codex scope at `compacting`."""

    def _status(self, state: dict[str, Any] | None) -> dict[str, Any]:
        from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
        from ciel_runtime_support.runtime_compaction_control import CompactionScope

        scope = CompactionScope("claude", "ad4b04e9", "e2")
        stub = type("Stub", (), {"scope": lambda _self: scope, "_admission_state": lambda _self: state})()
        return RuntimeCompactionBinding.recovery_status(stub)  # type: ignore[arg-type]

    def test_an_earlier_scopes_unfinished_state_is_shown_as_not_gating(self):
        status = self._status({"scope": {"runtime": "codex", "session_id": "01a0f637", "epoch": "e1"},
                               "phase": "compacting", "requested_at": 5.0, "deadline": 9.0})
        self.assertEqual(("stale_scope", "earlier_codex_scope_compacting_not_gating", 5.0),
                         (status["phase"], status["failure"], status["failure_at"]))
        self.assertEqual({}, self._status({"scope": {"runtime": "codex"}, "phase": "verified"}))

    def test_the_current_scope_is_reported_as_before(self):
        state = {"scope": {"runtime": "claude", "session_id": "ad4b04e9", "epoch": "e2"},
                 "phase": "blocked", "failure": "checkpoint_unconfirmed", "failure_at": 7.0}
        self.assertEqual(("blocked", "checkpoint_unconfirmed", 7.0),
                         tuple(self._status(state)[key] for key in ("phase", "failure", "failure_at")))


if __name__ == "__main__":
    unittest.main()
