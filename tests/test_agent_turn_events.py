import json
import unittest

from ciel_runtime_support.agent_turn_events import (
    AppServerTurnTracker,
    RouterTurnPoster,
    SeenTurns,
    TranscriptTurnTracker,
    turn_event,
    typed_by_person,
    user_input_text,
    valid_turn_event,
)


def _claude_user(uuid, text, **extra):
    return {"type": "user", "uuid": uuid, "timestamp": f"2026-10-07T03:00:{uuid[-2:]}Z", "message": {"role": "user", "content": text}, **extra}


def _claude_tool_result(uuid):
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def _claude_answer(uuid, stop, message_id="msg-1", **extra):
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": "2026-10-07T03:01:00Z",
        "message": {"id": message_id, "role": "assistant", "stop_reason": stop, "content": [{"type": "text", "text": "x"}]},
        **extra,
    }


def _feed(tracker, records):
    events = []
    for record in records:
        events.extend(tracker.feed(record))
    return events


def _codex(kind, payload, ts="2026-10-07T03:00:00Z"):
    return {"type": kind, "timestamp": ts, "payload": payload}


def _codex_user(text):
    return _codex("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})


class ClaudeTranscriptTurnTests(unittest.TestCase):
    def test_tool_steps_then_end_turn_is_one_turn_typed_by_a_person(self):
        events = _feed(
            TranscriptTurnTracker("claude"),
            [
                _claude_user("u-01", "fix the build"),
                _claude_answer("a-01", "tool_use", "m1"),
                _claude_tool_result("r-01"),
                _claude_answer("a-02", "tool_use", "m2"),
                _claude_tool_result("r-02"),
                _claude_answer("a-03", "end_turn", "m3"),
                {"type": "system", "subtype": "turn_duration", "timestamp": "2026-10-07T03:01:01Z"},
            ],
        )
        self.assertEqual(1, len(events))
        self.assertEqual(
            {
                "turn_id": "u-01",
                "runtime": "claude",
                "ended_at": "2026-10-07T03:01:00Z",
                "reason": "end_turn",
                "by_user_input": True,
                "stop_check_blocked": False,
            },
            events[0],
        )

    def test_content_blocks_of_the_closing_answer_do_not_open_a_new_turn(self):
        events = _feed(
            TranscriptTurnTracker("claude"),
            [_claude_user("u-02", "hi"), _claude_answer("a-1", "end_turn", "m9"), _claude_answer("a-2", "end_turn", "m9")],
        )
        self.assertEqual(["u-02"], [event["turn_id"] for event in events])

    def test_reasons(self):
        cases = {
            "interrupted": [_claude_user("u-03", "go"), _claude_answer("a", "tool_use"), _claude_user("i-03", "[Request interrupted by user for tool use]")],
            "error": [_claude_user("u-04", "go"), _claude_answer("a", "stop_sequence", isApiErrorMessage=True)],
            "max_tokens": [_claude_user("u-05", "go"), _claude_answer("a", "max_tokens")],
        }
        for reason, records in cases.items():
            with self.subTest(reason=reason):
                events = _feed(TranscriptTurnTracker("claude"), records)
                self.assertEqual([reason], [event["reason"] for event in events])

    def test_turns_not_typed_by_a_person(self):
        openers = {
            "session socket": _claude_user("u-11", "hello", isMeta=True, origin={"kind": "peer"}),
            "scheduled task": _claude_user("u-12", "JUN follow-up", isMeta=True),
            "terminal wake": _claude_user("u-13", "[ciel-wake] pending_ids=4"),
            "channel text": _claude_user("u-14", "[ciel-runtime external channel message] channel=external:default"),
            "task notification": _claude_user("u-15", "<task-notification> <task-id>b1</task-id>"),
            "compact continuation": _claude_user("u-16", "This session is being continued from a previous conversation"),
        }
        for name, opener in openers.items():
            with self.subTest(opener=name):
                events = _feed(TranscriptTurnTracker("claude"), [opener, _claude_answer("a", "end_turn")])
                self.assertEqual([False], [event["by_user_input"] for event in events])

    def test_local_command_records_do_not_open_turns(self):
        events = _feed(
            TranscriptTurnTracker("claude"),
            [_claude_user("u-21", "<command-name>/compact</command-name>"), {"type": "system", "subtype": "turn_duration"}],
        )
        self.assertEqual([], events)


class CodexRolloutTurnTests(unittest.TestCase):
    def _turn(self, opener, end):
        return [
            _codex("event_msg", {"type": "task_started", "turn_id": "t-1"}),
            _codex_user("<environment_context> <cwd>C:/x</cwd> </environment_context>"),
            _codex_user(opener),
            _codex("response_item", {"type": "function_call", "name": "exec"}),
            end,
        ]

    def test_typed_turn_ends_normally(self):
        events = _feed(TranscriptTurnTracker("codex"), self._turn("continue", _codex("event_msg", {"type": "task_complete", "turn_id": "t-1"}, "2026-10-07T03:05:00Z")))
        self.assertEqual(
            [{"turn_id": "t-1", "runtime": "codex", "ended_at": "2026-10-07T03:05:00Z", "reason": "end_turn", "by_user_input": True, "stop_check_blocked": False}],
            events,
        )

    def test_channel_and_goal_turns_are_not_typed(self):
        for opener in ("[ciel-runtime external channel message] channel=external:default id=5", '<codex_internal_context source="goal">'):
            with self.subTest(opener=opener[:20]):
                events = _feed(TranscriptTurnTracker("codex"), self._turn(opener, _codex("event_msg", {"type": "task_complete", "turn_id": "t-1"})))
                self.assertFalse(events[0]["by_user_input"])

    def test_aborts_and_errors(self):
        interrupted = _feed(TranscriptTurnTracker("codex"), self._turn("go", _codex("event_msg", {"type": "turn_aborted", "turn_id": "t-1", "reason": "interrupted"})))
        self.assertEqual("interrupted", interrupted[0]["reason"])
        records = self._turn("go", _codex("event_msg", {"type": "task_complete", "turn_id": "t-1"}))
        records.insert(4, _codex("event_msg", {"type": "error", "message": "stream disconnected"}))
        self.assertEqual("error", _feed(TranscriptTurnTracker("codex"), records)[0]["reason"])

    def test_turn_that_started_before_the_tail_is_still_reported(self):
        events = _feed(TranscriptTurnTracker("codex"), [_codex("event_msg", {"type": "task_complete", "turn_id": "t-9"})])
        self.assertEqual([("t-9", False)], [(event["turn_id"], event["by_user_input"]) for event in events])


class EventValidationTests(unittest.TestCase):
    def test_valid_event_round_trips_and_bad_ones_are_rejected(self):
        event = turn_event("t-1", "codex", "end_turn", by_user_input=False, ended_at="2026-10-07T03:00:00Z")
        self.assertEqual(event, valid_turn_event(json.loads(json.dumps(event))))
        self.assertIsNone(valid_turn_event({**event, "reason": "done"}))
        self.assertIsNone(valid_turn_event({**event, "by_user_input": "no"}))
        self.assertIsNone(valid_turn_event({**event, "turn_id": ""}))
        self.assertIsNone(valid_turn_event(["not", "a", "dict"]))

    def test_seen_turns_reports_each_id_once(self):
        seen = SeenTurns(limit=2)
        self.assertTrue(seen.first("a"))
        self.assertFalse(seen.first("a"))
        self.assertTrue(seen.first("b"))
        self.assertTrue(seen.first("c"))
        self.assertTrue(seen.first("a"))  # evicted by the limit

    def test_typed_by_person(self):
        self.assertTrue(typed_by_person("계속"))
        self.assertFalse(typed_by_person("  <hook_prompt hook_run_id=1>check</hook_prompt>"))


class AppServerTurnTests(unittest.TestCase):
    def test_reasons_and_who_started_the_turn(self):
        tracker = AppServerTurnTracker("codex-desktop")
        tracker.note_input(*user_input_text({"turnId": "t-1", "item": {"type": "userMessage", "content": [{"type": "text", "text": "please continue"}]}}))
        typed = tracker.completed({"id": "t-1", "status": "completed"}, started_by_ciel=False)
        self.assertEqual(("end_turn", True, "codex-desktop"), (typed["reason"], typed["by_user_input"], typed["runtime"]))
        tracker.note_input("t-2", '<codex_internal_context source="goal">')
        self.assertFalse(tracker.completed({"id": "t-2", "status": "interrupted"}, started_by_ciel=False)["by_user_input"])
        ours = tracker.completed({"id": "t-3", "status": "failed"}, started_by_ciel=True)
        self.assertEqual(("error", False), (ours["reason"], ours["by_user_input"]))
        self.assertIsNone(tracker.completed({"status": "completed"}, started_by_ciel=False))


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RouterTurnPosterTests(unittest.TestCase):
    def test_posts_to_the_router_and_retries_then_drops(self):
        sent, logs, failing = [], [], [True]

        def urlopen(request, timeout):
            if failing[0]:
                raise OSError("connection refused")
            sent.append((request.full_url, json.loads(request.data)))
            return _Response()

        poster = RouterTurnPoster(lambda: "http://127.0.0.1:9611", lambda level, message: logs.append(level), urlopen=urlopen)
        poster({"turn_id": "t-1"})
        self.assertEqual([], sent)
        failing[0] = False
        poster.flush()
        self.assertEqual([("http://127.0.0.1:9611/ca/tui/agent-turn", {"turn_id": "t-1"})], sent)

        failing[0] = True
        poster({"turn_id": "t-2"})
        for _ in range(5):
            poster.flush()
        self.assertEqual(["WARN"], logs)
        failing[0] = False
        poster.flush()
        self.assertEqual(1, len(sent))


if __name__ == "__main__":
    unittest.main()
