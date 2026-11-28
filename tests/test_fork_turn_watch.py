import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import fork_turn_watch as ftw
from ciel_runtime_support.agent_turn_events import TranscriptTurnTracker, claude_record_closing_reason
from ciel_runtime_support.channel_message_prompt import format_wake_batch_prompt, llm_message_skip_reason
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway

SECRET = "FORK_SECRET_TEXT_91c2"
SESSION = "11111111-2222-4333-8444-555555555555"
A, B, C = "a" + "0" * 16, "a" + "1" * 16, "a" + "2" * 16


def assistant(stop, uuid, ts="2026-11-14T02:00:00Z", **extra):
    content = ([{"type": "tool_use", "id": "toolu_secret", "name": "Bash", "input": {"command": SECRET}}]
               if stop == "tool_use" else [{"type": "text", "text": SECRET}])
    return {"type": "assistant", "uuid": uuid, "timestamp": ts, "message": {"stop_reason": stop, "content": content},
            **extra}


def user(uuid, content=SECRET, ts="2026-11-14T02:00:00Z", **extra):
    return {"type": "user", "uuid": uuid, "timestamp": ts, "message": {"content": content}, **extra}


def tool_result(uuid, ts="2026-11-14T02:00:00Z", **extra):
    return user(uuid, [{"type": "tool_result", "tool_use_id": "toolu_secret", "content": SECRET}], ts, **extra)


INTERRUPT = "[Request interrupted by user for tool use]"


class Harness:
    def __init__(self, started_at=0.0, leases=None):
        self.root = Path(tempfile.mkdtemp(prefix="fork-turn-watch-"))
        self.config = self.root / "claude"
        self.cwd = self.root / "work space"
        self.scope = {"runtime": "claude", "session_id": SESSION, "cwd": str(self.cwd),
                      "claude_config_dir": str(self.config), "epoch": "e1", "started_at": started_at}
        self.dir = ftw.subagents_dir(self.config, self.cwd, SESSION)
        self.submitted = []
        self.fail_submit = False
        self.leases = leases or {}
        self.clock = [1000.0]
        self.state_root = self.root / "ledger"
        self.state_path = ftw.state_path_for(self.state_root, "claude:" + SESSION)

    def submit(self, body):
        if self.fail_submit:
            raise OSError("queue unavailable")
        self.submitted.append(body)
        return {"id": len(self.submitted)}

    def watch(self):
        ports = ftw.ForkTurnWatchPorts(lambda: dict(self.scope), self.submit, lambda scope: self.leases,
                                       lambda level, line: None, now=lambda: self.clock[0])
        return ftw.ForkTurnWatch(self.state_root, ports)

    def fork(self, agent, records, meta=None):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"agent-{agent}.meta.json").write_text(json.dumps(meta or {"agentType": "fork", "isFork": True}))
        with (self.dir / f"agent-{agent}.jsonl").open("a", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def polls(self, watch, n=2):
        result = None
        for _ in range(n):
            self.clock[0] += 2
            result = watch.poll()
        return result


class ClosingPredicateParityTests(unittest.TestCase):
    """Amendment 1: one closing definition, shared with TranscriptTurnTracker._claude."""

    FIXTURES = [
        (assistant("end_turn", "a1"), "end_turn"),
        (assistant("stop_sequence", "a2"), "end_turn"),
        (assistant("refusal", "a3"), "end_turn"),
        (assistant("max_tokens", "a4"), "max_tokens"),
        (assistant("stop_sequence", "a5", isApiErrorMessage=True), "error"),
        (assistant("tool_use", "a6", isApiErrorMessage=True), "error"),
        (assistant("tool_use", "a7"), None),
        (assistant(None, "a8"), None),
        (user("u1", INTERRUPT), "interrupted"),
        (user("u2", [{"type": "text", "text": INTERRUPT}]), "interrupted"),
        (user("u3", "  " + INTERRUPT), "interrupted"),
        (tool_result("u4"), None),
        (user("u5", "resume", isMeta=True, origin={"kind": "coordinator"}), None),
        ({"type": "attachment", "uuid": "x1"}, None),
        ({"type": "system", "subtype": "api_error", "uuid": "x2"}, None),
    ]

    def test_predicate_and_tracker_agree_on_every_fixture(self):
        for record, expected in self.FIXTURES:
            with self.subTest(record=record["uuid"]):
                self.assertEqual(expected, claude_record_closing_reason(record))
                tracker = TranscriptTurnTracker("claude")
                tracker.feed(assistant("tool_use", "open"))
                events = tracker.feed(record)
                tracker_reason = events[-1]["reason"] if events else None
                self.assertEqual(expected, tracker_reason)
                state, reason = ftw.record_state(record)
                if record.get("type") in ("assistant", "user"):
                    self.assertEqual(("ended", expected) if expected else ("active", "open"), (state, reason))
                else:
                    self.assertEqual("unknown", state)


class ForkTurnWatchTests(unittest.TestCase):
    def test_info_checkpoints_bracket_scan_without_logging_transcript_or_paths(self):
        h = Harness()
        self.addCleanup(shutil.rmtree, h.root)
        w = h.watch()
        log = mock.Mock()
        w.ports = ftw.ForkTurnWatchPorts(w.ports.scope, h.submit, lambda scope: {}, log,
                                       now=lambda: h.clock[0])
        h.fork(A, [assistant('end_turn', 'a1')])
        h.polls(w, 3)
        messages = [call.args[1] for call in log.call_args_list if call.args[0] == 'INFO']
        self.assertEqual(2, len(messages), messages)
        self.assertRegex(messages[0], r'fork_turn_watch_scan_begin pid=\d+ poll=1')
        self.assertRegex(messages[1], r'fork_turn_watch_scan_end pid=\d+ poll=1 .*meta_stats=1')
        self.assertNotIn(SECRET, str(messages))
        self.assertNotIn(str(h.root), str(messages))

    def test_two_forks_one_ends_then_both_end_exactly_one_notice(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.fork(B, [assistant("tool_use", "b1")])
        h.polls(w, 1)  # baseline: active forks, no notice
        h.fork(A, [tool_result("a2"), assistant("end_turn", "a3")])
        h.polls(w, 3)
        self.assertEqual([], h.submitted)
        h.fork(B, [tool_result("b2"), assistant("end_turn", "b3")])
        h.polls(w, 1)
        self.assertEqual([], h.submitted, "one-period debounce")
        h.polls(w, 1)
        self.assertEqual(1, len(h.submitted))
        h.polls(w, 5)
        self.assertEqual(1, len(h.submitted), "unchanged state never repeats")

    def test_resumed_fork_rearms_and_fires_again_with_a_new_key(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))
        h.fork(A, [user("a3", "resume", isMeta=True, origin={"kind": "coordinator"})])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a4")])
        h.polls(w, 2)
        self.assertEqual(2, len(h.submitted))
        keys = [b["meta"]["event_id"] for b in h.submitted]
        self.assertNotEqual(keys[0], keys[1])

    def test_m2_resume_and_close_inside_one_poll_is_keyed_on_the_uuid(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        h.fork(A, [user("a3", "resume", isMeta=True), assistant("end_turn", "a4")])  # both inside one period
        h.polls(w, 2)
        self.assertEqual(2, len(h.submitted))

    def test_long_tool_use_never_ends(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1"), tool_result("a2")])
        h.polls(w, 1)
        h.clock[0] += 600
        h.polls(w, 30)
        self.assertEqual([], h.submitted)
        self.assertEqual({"ended": 0, "active": 1, "unknown": 0, "orphaned": 0}, w.status()["counts"])

    def test_error_max_tokens_and_interrupt_are_counted(self):
        h = Harness()
        w = h.watch()
        for agent in (A, B, C):
            h.fork(agent, [assistant("tool_use", agent + "-0")])
        h.polls(w, 1)
        h.fork(A, [assistant("stop_sequence", "a9", isApiErrorMessage=True, error="unknown")])
        h.fork(B, [assistant("max_tokens", "b9")])
        h.fork(C, [tool_result("c8"), user("c9", [{"type": "text", "text": INTERRUPT}])])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))
        self.assertIn("ended their turns: 1 error, 1 interrupted, 1 max_tokens.", h.submitted[0]["message"])

    def test_restart_with_fired_key_does_not_refire_and_baseline_sends_nothing(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))
        h.polls(h.watch(), 4)
        self.assertEqual(1, len(h.submitted), "persisted fired_key")
        h2 = Harness()
        h2.fork(A, [assistant("end_turn", "a1")])
        h2.polls(h2.watch(), 4)
        self.assertEqual([], h2.submitted, "no persisted file: old all-ended directory is a baseline only (R3.3)")

    def test_restart_gap_active_then_ended_fires_once(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 2)
        h.fork(A, [tool_result("a2"), assistant("end_turn", "a3")])  # during the router restart
        w2 = h.watch()
        h.polls(w2, 3)
        self.assertEqual(1, len(h.submitted))

    def test_restart_gap_new_fork_already_ended_fires_once(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))
        h.fork(B, [assistant("tool_use", "b1"), tool_result("b2"), assistant("end_turn", "b3")])  # gap
        h.polls(h.watch(), 3)
        self.assertEqual(2, len(h.submitted))

    def test_fast_fork_inside_one_poll_rearms_and_fires(self):
        h = Harness()
        w = h.watch()
        h.dir.mkdir(parents=True)
        h.polls(w, 1)  # baseline: no forks yet
        h.fork(A, [assistant("tool_use", "a1"), tool_result("a2"), assistant("end_turn", "a3")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))

    def test_side_actor_shaped_fork_is_included_and_non_fork_subagents_ignored(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")], {"agentType": "fork", "isFork": True,
                                                    "description": "Side SSE 0123456789ab"})
        h.fork(B, [assistant("tool_use", "b1")], {"agentType": "general-purpose", "isFork": False})
        h.polls(w, 1)
        self.assertEqual(1, w.status()["forks"])
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted), "the non-fork subagent never blocks")

    def test_missing_known_meta_blocks_until_it_returns_then_fires_once(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.fork(B, [assistant("tool_use", "b1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        meta_b = h.dir / f"agent-{B}.meta.json"
        saved = meta_b.read_text()
        meta_b.unlink()
        h.polls(w, 3)
        self.assertEqual([], h.submitted)
        self.assertEqual("meta_missing", w._state["forks"][B]["reason"])
        self.assertEqual(1, w.status()["counts"]["unknown"])
        meta_b.write_text(saved)
        h.polls(w, 2)
        for _ in range(5):
            if w._state["forks"][B]["state"] != "unknown":
                break
            h.polls(w, 1)
        self.assertEqual([], h.submitted)
        self.assertEqual("active", w._state["forks"][B]["state"])
        h.fork(B, [assistant("end_turn", "b2")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))
        h.polls(w, 4)
        self.assertEqual(1, len(h.submitted))

    def test_directory_listing_failure_retains_state_blocks_and_does_not_rearm(self):
        from unittest.mock import patch

        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        before = json.loads(json.dumps(w._state["forks"]))
        with patch.object(ftw.os, "listdir", side_effect=OSError("fixture")):
            h.polls(w, 2)
            self.assertEqual(before, w._state["forks"])
            self.assertFalse(w._state["armed"])
            self.assertEqual(1, w.status()["counts"]["unknown"])
            self.assertEqual([], h.submitted)
        h.polls(w, 1)
        self.assertEqual("active", w._state["forks"][A]["state"])

    def test_directory_missing_on_first_poll_is_unreadable_and_empty(self):
        h = Harness()
        w = h.watch()
        result = h.polls(w, 2)
        self.assertEqual(0, result["forks"])
        self.assertEqual([], h.submitted)
        self.assertTrue(w._state.get("directory_unreadable"))

    def test_known_meta_malformed_nonfork_and_transcript_missing_are_unknown(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        meta = h.dir / f"agent-{A}.meta.json"
        transcript = h.dir / f"agent-{A}.jsonl"
        meta.write_text("{")
        h.polls(w, 1)
        self.assertEqual("meta_unreadable", w._state["forks"][A]["reason"])
        meta.write_text(json.dumps({"isFork": False}))
        h.polls(w, 1)
        self.assertEqual("meta_identity_changed", w._state["forks"][A]["reason"])
        meta.write_text(json.dumps({"isFork": True}))
        transcript.unlink()
        h.polls(w, 1)
        self.assertEqual("transcript_missing", w._state["forks"][A]["reason"])
        self.assertEqual([], h.submitted)
        h.fork(B, [assistant("end_turn", "b1")], {"isFork": False})
        h.polls(w, 1)
        self.assertNotIn(B, w._state["forks"], "initial unrelated non-fork remains ignored")

    def test_other_sessions_are_ignored(self):
        h = Harness()
        w = h.watch()
        other = ftw.subagents_dir(h.config, h.cwd, "99999999-2222-4333-8444-555555555555")
        other.mkdir(parents=True)
        (other / f"agent-{A}.meta.json").write_text(json.dumps({"isFork": True}))
        (other / f"agent-{A}.jsonl").write_text(json.dumps(assistant("tool_use", "z1")) + "\n")
        h.fork(B, [assistant("tool_use", "b1")])
        h.polls(w, 1)
        h.fork(B, [assistant("end_turn", "b2")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))

    def test_partial_trailing_line_is_read_whole_next_poll(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        line = json.dumps(assistant("end_turn", "a2")) + "\n"
        path = h.dir / f"agent-{A}.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line[:30])
        h.polls(w, 3)
        self.assertEqual([], h.submitted)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line[30:])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))

    def test_oversized_partial_trailing_line_fails_closed_then_resolves(self):
        old = ftw.MAX_RECORD_BYTES
        try:
            ftw.MAX_RECORD_BYTES = 512
            h = Harness()
            w = h.watch()
            h.fork(A, [assistant("tool_use", "a1")])
            h.polls(w, 1)
            path = h.dir / f"agent-{A}.jsonl"
            with path.open("ab") as stream:
                stream.write(b'{"type":"attachment","pad":"' + b"x" * 70000)
            h.polls(w, 1)
            self.assertEqual("unknown", w._state["forks"][A]["state"])
            with path.open("ab") as stream:
                stream.write(b'"}\n' + (json.dumps(assistant("end_turn", "a2")) + "\n").encode())
            h.polls(w, 1)
            self.assertEqual(
                ("ended", "a2"),
                (w._state["forks"][A]["state"], w._state["forks"][A]["uuid"]),
            )
        finally:
            ftw.MAX_RECORD_BYTES = old

    def test_attachment_after_end_turn_is_ignored(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2"), {"type": "attachment", "uuid": "x1"}])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted))

    def test_unreadable_meta_and_missing_transcript_block(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        (h.dir / f"agent-{B}.meta.json").write_text("{not json")
        h.polls(w, 4)
        self.assertEqual([], h.submitted)
        self.assertEqual(1, w.status()["counts"]["unknown"])
        (h.dir / f"agent-{B}.meta.json").write_text(json.dumps({"isFork": True}))
        h.polls(w, 3)
        self.assertEqual([], h.submitted, "fork B has no transcript: unknown still blocks")

    def test_supervised_restart_same_launcher_new_started_at_relabels_and_rearms(self):
        """Lead 04:10Z: the bind record's started_at is the epoch; the launcher identity stays the same."""
        from datetime import datetime

        def at(text):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()

        class Scope:
            runtime, session_id = "claude", SESSION

            def __init__(self, epoch):
                self.epoch = epoch

        class Compaction:
            """One launcher (same pid and creation identity) binding a new epoch per claude.exe run."""

            def __init__(self, cwd, config):
                self.base = {"runtime": "claude", "cwd": cwd, "claude_config_dir": config,
                             "launcher_pid": 4242, "launcher_identity": "133000000000000000"}
                self.bind("e1", at("2026-11-14T00:40:00Z"))

            def bind(self, epoch, started_at):
                self.launch = {**self.base, "epoch": epoch, "started_at": started_at}

            def scope(self):
                return Scope(self.launch["epoch"])

            def current_launch_scope(self):
                return dict(self.launch)

        h = Harness()
        compaction = Compaction(str(h.cwd), str(h.config))
        ports = ftw.ForkTurnWatchPorts(lambda: ftw.bound_claude_scope(compaction), h.submit, lambda scope: {},
                                       lambda level, line: None, now=lambda: h.clock[0])
        w = ftw.ForkTurnWatch(h.state_root, ports)
        h.fork(A, [assistant("tool_use", "a1", ts="2026-11-14T00:43:48Z"),
                   tool_result("a2", ts="2026-11-14T00:43:49Z")])
        h.polls(w, 1)
        h.clock[0] += 3 * 3600
        h.polls(w, 30)
        self.assertEqual({"ended": 0, "active": 1, "unknown": 0, "orphaned": 0}, w.status()["counts"],
                         "the same started_at never relabels, however long the fork is silent")
        launcher = (compaction.launch["launcher_pid"], compaction.launch["launcher_identity"])
        compaction.bind("e2", at("2026-11-14T03:00:00Z"))  # supervised claude.exe restart
        self.assertEqual(launcher, (compaction.launch["launcher_pid"], compaction.launch["launcher_identity"]))
        h.polls(w, 10)
        self.assertEqual({"ended": 0, "active": 0, "unknown": 0, "orphaned": 1}, w.status()["counts"])
        self.assertEqual([], h.submitted, "an orphan blocks exactly like active")
        h.fork(A, [user("a3", "Work recovery", ts="2026-11-14T03:05:00Z", isMeta=True),
                   assistant("end_turn", "a4", ts="2026-11-14T03:05:05Z")])
        h.polls(w, 2)
        self.assertEqual(1, len(h.submitted), "resumed under the new launch: re-armed and fired once")
        self.assertEqual({"ended": 1, "active": 0, "unknown": 0, "orphaned": 0}, w.status()["counts"])
        h.polls(w, 5)
        self.assertEqual(1, len(h.submitted))

    def test_first_observation_milliseconds_before_epoch_and_same_epoch_stay_active(self):
        h = Harness(started_at=1794620000.010)
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1", ts="2026-11-14T00:13:20.005Z")])
        h.polls(w, 1)
        self.assertEqual({"ended": 0, "active": 1, "unknown": 0, "orphaned": 0}, w.status()["counts"])
        for _ in range(20):
            h.polls(w, 1)
        self.assertEqual(1, w.status()["counts"]["active"], "same launch_started_at never orphan-relabels")

    def test_legacy_state_without_epoch_establishes_safe_baseline(self):
        h = Harness(started_at=1794620000.0)
        h.fork(A, [assistant("tool_use", "a1", ts="2026-11-14T00:13:00Z")])
        legacy = {"schema": 1, "session_id": SESSION, "forks": {}, "armed": True,
                  "candidate": {"key": "old", "at": 1}, "pending": None, "fired_key": "", "fired_at": None}
        h.state_path.parent.mkdir(parents=True)
        h.state_path.write_text(json.dumps(legacy))
        w = h.watch()
        h.polls(w, 1)
        self.assertEqual(1, w.status()["counts"]["active"])
        self.assertFalse(w._state["armed"])
        self.assertIsNone(w._state["candidate"])
        self.assertEqual(h.scope["started_at"], w._state["launch_started_at"])

    def test_restart_gap_transition_key_cannot_cross_epochs(self):
        h = Harness(started_at=100.0)
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1", ts="1970-01-01T00:03:20Z")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2", ts="1970-01-01T00:03:21Z")])
        h.polls(w, 1)
        key1 = w._state["candidate"]["key"]
        h.scope["started_at"] = 150.0
        h.polls(w, 1)
        key2 = w._state["candidate"]["key"]
        self.assertNotEqual(key1, key2)
        self.assertEqual([], h.submitted, "one stable poll in each epoch cannot combine to fire")
        h.polls(w, 1)
        self.assertEqual(1, len(h.submitted))

    def test_epoch_change_then_listing_failure_still_orphans_on_recovery(self):
        from unittest.mock import patch

        h = Harness(started_at=100.0)
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1", ts="1970-01-01T00:01:50Z")])
        h.polls(w, 1)
        h.scope["started_at"] = 200.0
        with patch.object(ftw.os, "listdir", side_effect=OSError("fixture")):
            h.polls(w, 1)
        self.assertEqual(100.0, w._state["launch_started_at"], "unreadable sample does not consume epoch change")
        h.polls(w, 1)
        self.assertEqual(200.0, w._state["launch_started_at"])
        self.assertEqual("orphaned", w._state["forks"][A]["state"])

    def test_notice_content_is_bounded_and_carries_no_fork_content_or_ids(self):
        leases = {A: {"lease_id": "lease-0", "item_id": "item-0"}}
        for n in range(20):
            leases["a" + format(n + 100, "016x")] = {"lease_id": f"lease-{n + 1:02d}", "item_id": f"item-{n + 1:02d}"}
        h = Harness(leases=leases)
        w = h.watch()
        agents = list(leases)
        for agent in agents:
            h.fork(agent, [assistant("tool_use", agent + "-1")])
        h.polls(w, 1)
        for agent in agents:
            h.fork(agent, [assistant("end_turn", agent + "-2")])
        h.polls(w, 2)
        body = h.submitted[0]
        raw = json.dumps(body)
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("toolu_secret", raw)
        for agent in agents:
            self.assertNotIn(agent, raw)
        # 21 leases and 21 items: 16 values each are listed, then "+5 more".
        self.assertEqual(16, body["message"].count("lease-"))
        self.assertEqual(16, body["message"].count("item-"))
        self.assertEqual(2, body["message"].count("+5 more"))
        self.assertTrue(body["meta"]["event_id"].startswith(ftw.EVENT_PREFIX))
        self.assertEqual(True, body["meta"]["raw_injection"])

    def test_submit_failure_keeps_the_exact_pending_text_and_retries_once(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.fail_submit = True
        h.polls(w, 3)
        self.assertEqual([], h.submitted)
        self.assertTrue(w.status()["notice_pending"])
        pending = json.loads(h.state_path.read_text())["pending"]
        self.assertNotIn(SECRET, json.dumps(pending))
        h.fail_submit = False
        w2 = h.watch()  # also survives a restart
        h.polls(w2, 3)
        self.assertEqual(1, len(h.submitted))
        self.assertEqual(pending["text"], h.submitted[0]["message"])

    def test_state_file_holds_no_transcript_text(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1"), tool_result("a2")])
        h.polls(w, 2)
        raw = h.state_path.read_text()
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("toolu_secret", raw)
        self.assertNotIn(ftw.EMPTY_STREAM_ERROR, raw)
        state = json.loads(raw)
        fork = state["forks"][A]
        self.assertEqual({"source", "anchor"} <= set(fork), True)
        self.assertEqual(2, len(fork["source"]))
        self.assertRegex(fork["anchor"], r"^[0-9a-f]{64}$")

    def test_replace_same_or_larger_rewrite_and_truncate_regrow_reset_source_cursor(self):
        cases = ("larger_replace", "same_size_rewrite", "truncate_regrow")
        for case in cases:
            with self.subTest(case=case):
                h = Harness()
                w = h.watch()
                h.fork(A, [assistant("tool_use", "a1")])
                h.polls(w, 1)
                path = h.dir / f"agent-{A}.jsonl"
                old_size = path.stat().st_size
                row = json.dumps(assistant("tool_use", "b1")) + "\n"
                if case == "larger_replace":
                    temp = path.with_suffix(".replacement")
                    temp.write_text(
                        json.dumps({"type": "attachment", "pad": "x" * old_size})
                        + "\n"
                        + row
                    )
                    old_source = ftw.source_snapshot(path)["source"]
                    temp.replace(path)
                    self.assertNotEqual(old_source, ftw.source_snapshot(path)["source"], "atomic replace changes source identity")
                elif case == "same_size_rewrite":
                    raw = row.encode()
                    path.write_bytes(raw + b" " * max(0, old_size - len(raw)))
                else:
                    path.write_bytes(b"")
                    path.write_text(
                        json.dumps({"type": "attachment", "pad": "x" * old_size})
                        + "\n"
                        + row
                    )
                h.polls(w, 1)
                self.assertEqual("b1", w._state["forks"][A]["uuid"])
                self.assertEqual("active", w._state["forks"][A]["state"])

    def test_full_prefix_continuity_detects_beginning_gap_and_pre_offset_mutations(self):
        for location in ("beginning", "old_sample_gap", "pre_offset"):
            with self.subTest(location=location):
                h = Harness()
                w = h.watch()
                # This is the ctl 72 KiB shape: the semantic record begins at 12037
                # and the stop reason is outside the former four sampled ranges.
                path = h.dir / f"agent-{A}.jsonl"
                h.dir.mkdir(parents=True)
                (h.dir / f"agent-{A}.meta.json").write_text(
                    json.dumps({"isFork": True})
                )
                attachment = {"type": "attachment", "pad": "x" * 12000}
                tail = {"type": "attachment", "pad": "x" * 60000}
                path.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in
                                          (attachment, assistant("end_turn", "end"), tail)) + "\n")
                h.polls(w, 2)
                self.assertEqual("ended", w._state["forks"][A]["state"])
                raw = bytearray(path.read_bytes())
                offset = w._state["forks"][A]["offset"]
                if location == "beginning":
                    position = 8
                elif location == "old_sample_gap":
                    position = raw.index(b"end_turn")
                else:
                    position = offset - 1
                before_source = w._state["forks"][A]["source"]
                raw[position] = ord("X") if raw[position] != ord("X") else ord("Y")
                path.write_bytes(raw)
                h.polls(w, 1)
                self.assertEqual(before_source, w._state["forks"][A]["source"], "same file identity")
                self.assertTrue(w._state["forks"][A].get("source_reset"), "changed prefix starts a semantic rescan")

    def test_rewrite_plus_small_growth_verifies_prefix_before_append(self):
        h = Harness()
        w = h.watch()
        path = h.dir / f"agent-{A}.jsonl"
        h.dir.mkdir(parents=True)
        (h.dir / f"agent-{A}.meta.json").write_text(json.dumps({"isFork": True}))
        path.write_text(json.dumps(assistant("end_turn", "a1"), separators=(",", ":")) + "\n" +
                        json.dumps({"type": "attachment", "pad": "x" * 70000}, separators=(",", ":")) + "\n")
        for _ in range(5):
            h.polls(w, 1)
            if w._state["forks"][A]["state"] != "unknown":
                break
        self.assertEqual("ended", w._state["forks"][A]["state"])
        raw = path.read_bytes()
        position = raw.index(b"end_turn")
        path.write_bytes(raw[:position] + b"tool_use" + raw[position + 8:] +
                         json.dumps({"type": "attachment", "pad": "new"}, separators=(",", ":")).encode() + b"\n")
        h.polls(w, 1)
        self.assertNotEqual(("ended", "end_turn"),
                            (w._state["forks"][A]["state"], w._state["forks"][A]["reason"]))
        self.assertTrue(w._state["forks"][A].get("source_reset"))

    def test_restart_and_growth_during_bounded_continuity_verification(self):
        old_budget, old_segment = ftw.MAX_TAIL_BYTES, ftw.CONTINUITY_SEGMENT_BYTES
        try:
            ftw.MAX_TAIL_BYTES, ftw.CONTINUITY_SEGMENT_BYTES = 1024, 256
            h = Harness()
            w = h.watch()
            h.fork(A, [assistant("tool_use", "a1")])
            path = h.dir / f"agent-{A}.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                for n in range(20):
                    stream.write(
                        json.dumps({"type": "attachment", "n": n, "pad": "x" * 400})
                        + "\n"
                    )
            for _ in range(30):
                h.polls(w, 1)
                if w._state["forks"][A]["state"] == "active":
                    break
            self.assertEqual("active", w._state["forks"][A]["state"])
            w2 = h.watch()
            h.polls(w2, 1)
            self.assertEqual(("unknown", "continuity_verifying"),
                             tuple(w2._state["forks"][A][k] for k in ("state", "reason")))
            first_index = w2._state["forks"][A]["verify"]["index"]
            w3 = h.watch()
            h.polls(
                w3, 1
            )  # restart while verification is incomplete resumes its durable cursor
            self.assertGreater(w3._state["forks"][A]["verify"]["index"], first_index)
            h.fork(A, [assistant("end_turn", "a2")])  # growth must not invalidate verified prefix pages
            before_growth = w3._state["forks"][A]["verify"]["index"]
            h.polls(w3, 1)
            self.assertGreater(w3._state["forks"][A]["verify"]["index"], before_growth)
            w2 = w3
            for _ in range(30):
                h.polls(w2, 1)
                if w2._state["forks"][A]["state"] != "unknown":
                    break
            self.assertEqual(("ended", "a2"),
                             (w2._state["forks"][A]["state"], w2._state["forks"][A]["uuid"]))
        finally:
            ftw.MAX_TAIL_BYTES, ftw.CONTINUITY_SEGMENT_BYTES = old_budget, old_segment

    def test_continuity_state_has_no_text_and_normal_append_converges(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        fork = w._state["forks"][A]
        self.assertNotIn(SECRET, json.dumps(fork["continuity"]))
        before = w.bytes_read
        size = (h.dir / f"agent-{A}.jsonl").stat().st_size
        h.fork(A, [assistant("end_turn", "a2")])
        added = (h.dir / f"agent-{A}.jsonl").stat().st_size - size
        h.polls(w, 1)
        # Every content change verifies the committed prefix before the append is
        # trusted. A small prefix verifies and commits in this same poll.
        self.assertLessEqual(w.bytes_read - before, size + added + ftw.CONTINUITY_SEGMENT_BYTES)
        self.assertEqual("ended", w._state["forks"][A]["state"])
        self.assertNotIn(SECRET, h.state_path.read_text())

    def test_large_growth_does_not_skip_earlier_closing_record(self):
        old_budget, old_chunk = ftw.MAX_TAIL_BYTES, ftw.TAIL_CHUNK
        try:
            ftw.MAX_TAIL_BYTES, ftw.TAIL_CHUNK = 1024, 512
            h = Harness()
            w = h.watch()
            h.fork(A, [assistant("tool_use", "a1")])
            h.polls(w, 1)
            h.fork(A, [assistant("end_turn", "a2")])
            path = h.dir / f"agent-{A}.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                for n in range(12):
                    stream.write(
                        json.dumps({"type": "attachment", "n": n, "pad": "x" * 300})
                        + "\n"
                    )
            h.polls(w, 1)
            self.assertEqual("unknown", w._state["forks"][A]["state"], "large growth resets to bounded backscan")
            for _ in range(30):
                h.polls(w, 1)
                if w._state["forks"][A]["state"] != "unknown":
                    break
            self.assertEqual(("ended", "a2"), (w._state["forks"][A]["state"], w._state["forks"][A]["uuid"]))
        finally:
            ftw.MAX_TAIL_BYTES, ftw.TAIL_CHUNK = old_budget, old_chunk

    def test_cold_backscan_converges_across_budget_and_growth_resets_or_resolves(self):
        old_budget = ftw.MAX_TAIL_BYTES
        try:
            ftw.MAX_TAIL_BYTES = 1024
            ftw.TAIL_CHUNK = 512
            h = Harness()
            w = h.watch()
            h.fork(A, [assistant("end_turn", "a1")])
            path = h.dir / f"agent-{A}.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                for n in range(12):
                    stream.write(
                        json.dumps({"type": "attachment", "n": n, "pad": "x" * 700})
                        + "\n"
                    )
            h.polls(w, 1)
            self.assertEqual("unknown", w._state["forks"][A]["state"])
            reads = []
            for _ in range(20):
                before = w.bytes_read
                h.polls(w, 1)
                reads.append(w.bytes_read - before)
                if w._state["forks"][A]["state"] != "unknown":
                    break
            self.assertEqual("ended", w._state["forks"][A]["state"])
            self.assertGreater(len(reads), 1, "ignored tail converges across multiple bounded polls")
            self.assertTrue(all(value <= 1024 + 2 * ftw.SOURCE_ANCHOR_BYTES for value in reads))
            # Growth during a second cold scan is incorporated without skipping
            # its fresh relevant tail; bounded prefix work may require more polls.
            h2 = Harness()
            w2 = h2.watch()
            h2.fork(A, [assistant("tool_use", "x1")])
            p2 = h2.dir / f"agent-{A}.jsonl"
            with p2.open("a", encoding="utf-8") as stream:
                for n in range(8):
                    stream.write(
                        json.dumps({"type": "attachment", "n": n, "pad": "y" * 700})
                        + "\n"
                    )
            h2.polls(w2, 1)
            self.assertEqual("unknown", w2._state["forks"][A]["state"])
            h2.fork(A, [assistant("end_turn", "x2")])
            h2.polls(w2, 1)
            for _ in range(30):
                if w2._state["forks"][A]["state"] != "unknown":
                    break
                h2.polls(w2, 1)
            self.assertEqual(("ended", "x2"), (w2._state["forks"][A]["state"], w2._state["forks"][A]["uuid"]))
        finally:
            ftw.MAX_TAIL_BYTES = old_budget
            ftw.TAIL_CHUNK = 65536

    def test_poll_reads_only_new_bytes_of_a_large_transcript(self):
        h = Harness()
        w = h.watch()
        big = [assistant("tool_use", f"p{n}") if n % 2 == 0 else tool_result(f"p{n}") for n in range(2000)]
        h.fork(A, big)
        path = h.dir / f"agent-{A}.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "attachment", "pad": "x" * (50 * 1024 * 1024)}) + "\n")
        h.fork(A, [assistant("tool_use", "last")])
        h.polls(w, 1)
        first = w.bytes_read
        self.assertLessEqual(first, ftw.MAX_TAIL_BYTES + 2 * ftw.SOURCE_ANCHOR_BYTES,
                             "initial digest/backscan work stays within one poll budget plus bounded anchors")
        before = w.bytes_read
        h.polls(w, 5)
        self.assertEqual(before, w.bytes_read, "an unchanged file is not reopened")
        h.fork(A, [assistant("end_turn", "end")])
        h.polls(w, 1)
        self.assertLessEqual(w.bytes_read - before, ftw.MAX_TAIL_BYTES + 2 * ftw.SOURCE_ANCHOR_BYTES,
                             "growth verification stays within one poll budget")
        self.assertEqual("ended", next(iter(w._state["forks"].values()))["state"])


class EmptyStreamRetryTests(unittest.TestCase):
    def error(self, uuid, ts="2026-11-14T04:28:41.376Z"):
        return {"type": "assistant", "uuid": uuid, "timestamp": ts, "isApiErrorMessage": True,
                "message": {"stop_reason": "stop_sequence", "content": [{"type": "text", "text": ftw.EMPTY_STREAM_ERROR}]}}

    def test_limit_zero_and_counts_one_through_limit_plus_one(self):
        for limit in (0, 3):
            records = []
            for n in range(1, 5):
                records.append(self.error(f"e{n}"))
                state, reason, count = ftw.records_state(records, limit)
                expected = "active" if limit > 0 and n <= limit else "ended"
                self.assertEqual((expected, "empty_stream_retry" if expected == "active" else "error", n),
                                 (state, reason, count), (limit, n))
        other = assistant("stop_sequence", "other", isApiErrorMessage=True)
        self.assertEqual(("ended", "error", 0), ftw.records_state([other], 3))

    def test_cold_interrupt_safe_shape_closes_interrupted(self):
        h = Harness()
        h.fork(
            A,
            [
                assistant("tool_use", "a1"),
                user("u1", [{"type": "text", "text": INTERRUPT}]),
            ],
        )
        w = h.watch()
        h.polls(w, 1)
        self.assertEqual(("ended", "interrupted"),
                         tuple(w._state["forks"][A][k] for k in ("state", "reason")))
        raw = h.state_path.read_text()
        self.assertNotIn(INTERRUPT, raw)
        self.assertIn('"reason":"interrupted"', raw)

    def test_success_after_interim_error_resets_and_closes(self):
        state, reason, count = ftw.records_state([self.error("e1"), assistant("end_turn", "done")], 3)
        self.assertEqual(("ended", "end_turn", 0), (state, reason, count))

    def test_restart_rebuild_from_current_turn_tail(self):
        h = Harness()
        h.scope["direct_empty_stream_retries"] = 3
        h.fork(A, [assistant("tool_use", "a1"), tool_result("a2"), self.error("e1"), self.error("e2")])
        w = h.watch()
        h.polls(w, 1)
        self.assertEqual(("active", "empty_stream_retry", 2),
                         tuple(w._state["forks"][A][k] for k in ("state", "reason", "empty_count")))
        w2 = h.watch()
        h.polls(w2, 1)
        self.assertEqual(2, w2._state["forks"][A]["empty_count"])
        h.fork(A, [self.error("e3")])
        h.polls(w2, 1)
        self.assertEqual("active", w2._state["forks"][A]["state"])
        h.fork(A, [self.error("e4")])
        h.polls(w2, 1)
        self.assertEqual("ended", w2._state["forks"][A]["state"])

    def test_backscan_growth_uses_accumulated_exact_error_count(self):
        old_budget, old_chunk = ftw.MAX_TAIL_BYTES, ftw.TAIL_CHUNK
        try:
            ftw.MAX_TAIL_BYTES, ftw.TAIL_CHUNK = 512, 256
            h = Harness()
            h.scope["direct_empty_stream_retries"] = 2
            h.fork(A, [assistant("tool_use", "a1"), tool_result("a2"), self.error("e1"), self.error("e2")])
            path = h.dir / f"agent-{A}.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                for n in range(8):
                    stream.write(
                        json.dumps({"type": "attachment", "n": n, "pad": "x" * 300})
                        + "\n"
                    )
            w = h.watch()
            h.polls(w, 1)
            self.assertEqual("unknown", w._state["forks"][A]["state"])
            # Let bounded scan accumulate the two outer errors, then append the third.
            for _ in range(20):
                h.polls(w, 1)
                scan = w._state["forks"][A].get("backscan") or {}
                if int(scan.get("empty_count") or 0) >= 2:
                    break
            h.fork(A, [self.error("e3")])
            h.polls(w, 1)
            for _ in range(30):
                if w._state["forks"][A]["state"] != "unknown":
                    break
                h.polls(w, 1)
            self.assertEqual(("ended", "error", 3), tuple(w._state["forks"][A][k] for k in ("state", "reason", "empty_count")))
        finally:
            ftw.MAX_TAIL_BYTES, ftw.TAIL_CHUNK = old_budget, old_chunk


class DeliveryIntegrationTests(unittest.TestCase):
    """The watcher's body through the real gateway into a real queue."""

    def test_real_gateway_row_is_private_llm_notification_with_exact_text(self):
        rows = []

        def append(payload):
            message = {"id": len(rows) + 1, **payload}
            rows.append(message)
            return message

        gateway = RuntimeInputGateway(append, None, lambda: "session_socket", None, lambda: {"enabled": True})
        h = Harness()
        h.submit = gateway.submit_notification
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(("notification", ["llm"], "private_runtime"), (row["kind"], row["delivery"], row["visibility"]))
        self.assertIsNone(row["meta"].get("side_intake_only"))
        self.assertIsNone(llm_message_skip_reason(row))
        self.assertEqual(row["message"], format_wake_batch_prompt([row]))


class _Crash(BaseException):
    """A process death at a chosen point: not an Exception, so nothing in the watch catches it."""


def real_queue(root):
    """The runtime-input queue exactly as ciel_runtime.runtime_input_message_context builds it.

    The production ChannelMessageContext with the production constants
    (CHAT_MESSAGE_DEDUPE_SCAN_LIMIT, CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS,
    CHAT_MESSAGES_MAX_BYTES) and the production identity keys, under a temp path.
    """
    import contextlib
    import threading
    import time
    from ciel_runtime_support.channel_event_identity import fallback_dedupe_key, message_time_seconds, stable_dedupe_key
    from ciel_runtime_support.channel_message_context import (
        ChannelMessageCachePorts, ChannelMessageContext, ChannelMessageIdentityPorts, ChannelMessageLaunchPorts,
        ChannelMessageStoragePorts)
    from ciel_runtime_support.runtime_constants import (CHAT_MESSAGE_DEDUPE_SCAN_LIMIT,
                                                        CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS, CHAT_MESSAGES_MAX_BYTES)

    path = Path(root) / "runtime-inputs.jsonl"
    next_id = [None]
    context = ChannelMessageContext(
        storage=ChannelMessageStoragePorts(lambda: path, CHAT_MESSAGES_MAX_BYTES, threading.Condition(),
                                           lambda _p: contextlib.nullcontext(), lambda level, line: None),
        identity=ChannelMessageIdentityPorts(stable_dedupe_key, fallback_dedupe_key, message_time_seconds,
                                             lambda v: [v] if isinstance(v, str) else list(v or [])),
        launch=ChannelMessageLaunchPorts(lambda: Path(root) / "launch-guard.json", time.time,
                                         CHAT_MESSAGE_DEDUPE_SCAN_LIMIT, CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS),
        cache=ChannelMessageCachePorts(lambda: next_id[0], lambda value: next_id.__setitem__(0, value)))
    gateway = RuntimeInputGateway(context.append, None, lambda: "session_socket", None, lambda: {"enabled": True})

    def rows():
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    return gateway, rows, path


class DeliveryCrashTests(unittest.TestCase):
    """Amendment 5: persist {K, text} pending → submit → persist fired_key; a crash between steps."""

    def _to_debounced(self, h, submit):
        h.submit = submit
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.fork(B, [assistant("tool_use", "b1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "uuid-closing-A")])
        h.fork(B, [assistant("stop_sequence", "uuid-closing-B", isApiErrorMessage=True)])
        h.polls(w, 1)  # candidate recorded, nothing submitted yet
        return w

    def _assert_one_clean_row(self, h, rows, path, sent):
        self.assertEqual(1, len(rows()), "at most one queue row")
        self.assertTrue(all(text == sent[0] for text in sent), "every resubmit carries the identical text")
        raw = path.read_text(encoding="utf-8")
        for value in (A, B, "uuid-closing-A", "uuid-closing-B", SECRET, "toolu_secret"):
            self.assertNotIn(value, raw)
        state = json.loads(h.state_path.read_text(encoding="utf-8"))
        self.assertIsNone(state["pending"])
        self.assertTrue(state["fired_key"])
        h.polls(h.watch(), 4)
        self.assertEqual(1, len(rows()), "a later restart never adds a row")

    def test_crash_after_pending_persist_before_submit(self):
        h = Harness()
        gateway, rows, path = real_queue(h.root)
        sent, crash = [], [True]

        def submit(body):
            if crash[0]:
                crash[0] = False
                raise _Crash()
            sent.append(body["message"])
            return gateway.submit_notification(body)

        w = self._to_debounced(h, submit)
        with self.assertRaises(_Crash):
            h.polls(w, 1)
        pending = json.loads(h.state_path.read_text(encoding="utf-8"))["pending"]
        self.assertIsInstance(pending, dict)
        self.assertEqual([], rows())
        h.submit = submit
        h.polls(h.watch(), 1)  # restart: resubmits the persisted text unchanged
        self.assertEqual([pending["text"]], sent)
        self._assert_one_clean_row(h, rows, path, sent)

    def test_crash_after_submit_before_fired_key_persist(self):
        h = Harness()
        gateway, rows, path = real_queue(h.root)
        sent, crash = [], [True]

        def submit(body):
            sent.append(body["message"])
            row = gateway.submit_notification(body)
            if crash[0]:
                crash[0] = False
                raise _Crash()
            return row

        w = self._to_debounced(h, submit)
        with self.assertRaises(_Crash):
            h.polls(w, 1)
        self.assertEqual(1, len(rows()))
        self.assertIsInstance(json.loads(h.state_path.read_text(encoding="utf-8"))["pending"], dict)
        h.submit = submit
        h.polls(h.watch(), 1)  # restart: the identical resubmit is the queue's duplicate
        self.assertEqual(2, len(sent))
        self._assert_one_clean_row(h, rows, path, sent)

    def test_crash_after_fired_key_persist(self):
        h = Harness()
        gateway, rows, path = real_queue(h.root)
        sent = []

        def submit(body):
            sent.append(body["message"])
            return gateway.submit_notification(body)

        w = self._to_debounced(h, submit)
        h.polls(w, 1)
        self.assertEqual(1, len(rows()))
        h.submit = submit
        h.polls(h.watch(), 3)  # restart after every step completed
        self.assertEqual(1, len(sent))
        self._assert_one_clean_row(h, rows, path, sent)

    def test_resubmit_dedupe_bound_is_the_runtime_input_scan_limit(self):
        """Lead 04:10Z: a known bound, pinned to CHAT_MESSAGE_DEDUPE_SCAN_LIMIT (runtime_constants.py:125).

        A resubmit after a crash between submit and the fired_key persist is the
        queue's duplicate while the original row is among the newest LIMIT rows;
        once LIMIT newer rows exist it is appended again.
        """
        from ciel_runtime_support.runtime_constants import CHAT_MESSAGE_DEDUPE_SCAN_LIMIT as LIMIT
        self.assertEqual(500, LIMIT)
        gateway, rows, _ = real_queue(Path(tempfile.mkdtemp()))
        body = ftw.notification_body("f" * 64, "[ciel-runtime fork watch] fixture")
        first = gateway.submit_notification(body)

        def other(n):
            gateway.submit_notification({"channel": "other", "kind": "notification", "message": f"other {n}",
                                         "meta": {"source": "fixture", "event_id": f"other-{n}"}})

        for n in range(LIMIT - 1):
            other(n)
        again = gateway.submit_notification(body)
        self.assertTrue(again.get("_ciel_runtime_duplicate"), "original is still among the newest LIMIT rows")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(LIMIT, len(rows()))
        other(LIMIT - 1)
        beyond = gateway.submit_notification(body)
        self.assertFalse(beyond.get("_ciel_runtime_duplicate"), "the documented bound: LIMIT newer rows")
        self.assertEqual(LIMIT + 2, len(rows()))

    def test_text_is_deterministic_per_transition_and_uses_record_times(self):
        texts = []
        for _ in range(2):
            h = Harness()
            h.submit = lambda body: texts.append(body["message"]) or {"id": 1}
            w = h.watch()
            h.fork(A, [assistant("tool_use", "a1")])
            h.polls(w, 1)
            h.fork(A, [assistant("end_turn", "a2", ts="2026-11-14T03:37:34.241Z")])
            h.clock[0] += 5000
            h.polls(w, 2)
        self.assertEqual(texts[0], texts[1])
        self.assertIn("2026-11-14T03:37:34Z", texts[0])


class PendingLaneIntegrationTests(unittest.TestCase):
    """Design test 9: the watcher's real queue row through the real pending-injection code."""

    def _row(self):
        h = Harness()
        gateway, rows, _ = real_queue(h.root)
        h.submit = gateway.submit_notification
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        h.fork(A, [assistant("end_turn", "a2")])
        h.polls(w, 2)
        written = rows()
        self.assertEqual(1, len(written))
        return written[0]

    def _deliver(self, row, *, busy):
        import dataclasses
        import threading
        from unittest.mock import Mock
        from ciel_runtime_support.channel_message_prompt import format_wake_batch_prompt
        from ciel_runtime_support.channel_pending_injection import (
            ChannelInjectionIO, ChannelInjectionPolicy, ChannelInjectionPrompts, ChannelInjectionServices,
            ChannelInjectionState, ChannelInjectionWakeStore, inject_pending_channel_messages)
        from ciel_runtime_support.channel_wake_delivery_repository import ChannelWakeDeliveryRepository

        commits = []
        lifecycle = ChannelWakeDeliveryRepository(lock=threading.Lock(), delivered=set(), prompts={}, batches={},
                                                  clear_claim=lambda _id: None, commit_cursor=commits.append)
        send = Mock(return_value=True)
        typed = []
        services = ChannelInjectionServices(
            state=ChannelInjectionState(
                active_tool_call=lambda: False, active_turn=lambda: busy, recover_cursor=lambda last: last,
                pending_scan_limit=lambda: 50, superseded_ids=lambda _c: set(),
                message_is_web_chat=lambda _m: False, message_skip_reason=llm_message_skip_reason,
                event_identity_key=lambda _m: (), wake_state_for_message=lambda _m, _p=None: "missing",
                queued_wake_is_stale=lambda _m, _p=None: False),
            prompts=ChannelInjectionPrompts(llm_delivery=format_wake_batch_prompt,
                                            visible_llm_delivery=format_wake_batch_prompt,
                                            web_chat=format_wake_batch_prompt, standard=format_wake_batch_prompt,
                                            enter_bytes=lambda _v: b"\r", enter_label=lambda _v: "cr"),
            wake_store=ChannelInjectionWakeStore(
                claim_for_nonblocking_scan=lambda _id: False, claim_prompt=lambda _id, _p: True,
                clear_claim=lambda _id: None, release_stale=lambda _id, _c: None, lifecycle=lifecycle,
                commit_cursor=commits.append),
            io=ChannelInjectionIO(inject_lock=threading.Lock(),
                                  read_messages=lambda last, *_a, **_k: [row] if int(row["id"]) > last else [],
                                  write_prompt=lambda _fd, prompt, *_a, **_k: typed.append(prompt) or True,
                                  log=lambda *_a: None, write_session_socket=send,
                                  session_socket_available=lambda: True),
            policy=ChannelInjectionPolicy(wake_batch_limit=lambda: 10, replay_skip_reason=lambda _m: ""))
        services = dataclasses.replace(services)
        returned = inject_pending_channel_messages(None, int(row["id"]) - 1, services=services,
                                                   terminal_available=False, wake_for_llm_delivery=True)
        return returned, send, typed, commits

    def test_idle_and_busy_main_both_receive_the_exact_text_over_the_session_socket(self):
        for busy in (False, True):
            with self.subTest(busy=busy):
                row = self._row()
                returned, send, typed, commits = self._deliver(row, busy=busy)
                self.assertEqual(int(row["id"]), returned)
                send.assert_called_once()
                self.assertEqual(row["message"], send.call_args.args[0])
                self.assertEqual([int(row["id"])], [int(r["id"]) for r in send.call_args.args[1]])
                self.assertEqual([], typed, "never typed into the terminal")
                self.assertEqual([int(row["id"])], commits)
                for value in (A, "a1", "a2", SECRET):
                    self.assertNotIn(value, send.call_args.args[0])


class StatusViewTests(unittest.TestCase):
    """Amendment 4: the read-only view the menu, web, REST and ciel-runtimectl share."""

    def test_view_has_counts_last_notice_time_and_key_only(self):
        h = Harness()
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.fork(B, [assistant("tool_use", "b1")])
        h.polls(w, 1)
        view = ftw.status_view(h.state_root, "claude:" + SESSION)
        self.assertEqual({"ended": 0, "active": 2, "unknown": 0, "orphaned": 0}, view["counts"])
        self.assertIsNone(view["last_notice_at"])
        h.fork(A, [assistant("end_turn", "uuid-closing-A")])
        h.fork(B, [assistant("end_turn", "uuid-closing-B")])
        h.polls(w, 2)
        view = ftw.status_view(h.state_root, "claude:" + SESSION)
        self.assertEqual(2, view["counts"]["ended"])
        self.assertRegex(view["last_notice_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(h.submitted[0]["meta"]["event_id"][len(ftw.EVENT_PREFIX):][:16], view["last_notice_key"])
        self.assertEqual({"available", "forks", "counts", "armed", "notice_pending", "last_notice_at",
                          "last_notice_key", "observed_at"}, set(view))
        raw = json.dumps(view)
        for value in (A, B, SECRET, SESSION, "uuid-closing-A", "uuid-closing-B"):
            self.assertNotIn(value, raw)

    def test_other_or_missing_session_is_unavailable(self):
        h = Harness()
        self.assertFalse(ftw.status_view(h.state_root, "claude:" + SESSION)["available"])
        w = h.watch()
        h.fork(A, [assistant("tool_use", "a1")])
        h.polls(w, 1)
        self.assertFalse(ftw.status_view(h.state_root, "claude:other")["available"])
        self.assertTrue(ftw.status_view(h.state_root, "claude:" + SESSION)["available"])


def observed_state(state_root, session=SESSION, fired=True):
    """A real watch run that leaves one persisted state for ``session`` under ``state_root``."""
    h = Harness()
    h.state_root = Path(state_root)
    h.scope["session_id"] = session
    h.dir = ftw.subagents_dir(h.config, h.cwd, session)
    w = h.watch()
    h.fork(A, [assistant("tool_use", "a1")])
    h.fork(B, [assistant("tool_use", "b1")])
    h.polls(w, 1)
    if fired:
        h.fork(A, [assistant("end_turn", "uuid-closing-A")])
        h.fork(B, [assistant("end_turn", "uuid-closing-B")])
        h.polls(w, 2)
    return h


class FourSurfaceStatusTests(unittest.TestCase):
    """Amendment 4: the same read-only view on REST, web, ciel-runtimectl and the menu."""

    FORBIDDEN = (A, B, "uuid-closing-A", "uuid-closing-B", SECRET, "toolu_secret")

    def setUp(self):
        from ciel_runtime_support import runtime_paths
        self.config_dir = Path(runtime_paths.CONFIG_DIR)
        self.assertTrue(os.environ.get("CIEL_RUNTIME_TEST_ISOLATED") == "1", "isolated env required")
        shutil.rmtree(self.config_dir / ftw.STATE_DIR_NAME, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.config_dir / ftw.STATE_DIR_NAME, True)

    def test_status_rows_lists_sessions_newest_first_bounded_and_id_free(self):
        root = Path(tempfile.mkdtemp())
        other = "22222222-2222-4333-8444-555555555555"
        observed_state(root, SESSION)
        observed_state(root, other, fired=False)
        listing = ftw.status_rows(root)
        self.assertEqual(["claude:" + SESSION, "claude:" + other], [r["session"] for r in listing["sessions"]])
        self.assertEqual({"ended": 2, "active": 0, "unknown": 0, "orphaned": 0}, listing["sessions"][0]["counts"])
        self.assertEqual({"ended": 0, "active": 2, "unknown": 0, "orphaned": 0}, listing["sessions"][1]["counts"])
        self.assertIsNone(listing["sessions"][1]["last_notice_at"])
        self.assertEqual(1, len(ftw.status_rows(root, limit=1)["sessions"]))
        self.assertEqual(1, ftw.status_rows(root, limit=1)["more"])
        (root / ftw.STATE_DIR_NAME / "not-a-digest.json").write_text(json.dumps({"session_id": SESSION}))
        self.assertEqual(2, len(ftw.status_rows(root)["sessions"]), "only files named by their session digest")
        raw = json.dumps(listing)
        for value in self.FORBIDDEN:
            self.assertNotIn(value, raw)

    def test_rest_runtime_carries_the_view(self):
        from ciel_runtime_support.work_http_runtime import build_work_http
        observed_state(self.config_dir, SESSION)
        root = Path(tempfile.mkdtemp())
        instance = root / "instance"
        instance.mkdir()
        (instance / "compaction-scope.json").write_text(json.dumps({"runtime": "claude", "session_id": SESSION,
                                                                     "epoch": "e1"}))
        replies = []

        class Observer:
            @staticmethod
            def snapshot(scope):
                return {}

        routes = build_work_http(root / "state", instance, lambda: {}, lambda cfg: ("fixture", {}), lambda cfg: "alias",
                                 lambda handler, payload, status=200: replies.append((status, payload)),
                                 "v", "fp", "inst", Observer())
        handler = type("H", (), {"path": "/ca/work/api/v1/runtime?session_id=claude%3A" + SESSION, "headers": {}})()
        self.assertTrue(routes.get(handler, "/ca/work/api/v1/runtime"))
        status, payload = replies[-1]
        self.assertEqual(200, status, payload)
        view = payload["runtime"]["fork_watch"]
        self.assertEqual(ftw.status_view(self.config_dir, "claude:" + SESSION), view)
        self.assertEqual(2, view["counts"]["ended"])
        raw = json.dumps(payload)
        for value in self.FORBIDDEN:
            self.assertNotIn(value, raw)

    def test_ctl_work_status_carries_the_view(self):
        from ciel_runtime_support.work_ctl import WorkCtl, text_lines
        observed_state(self.config_dir, SESSION)
        result = WorkCtl(lambda: {}, lambda cfg: None, Path(tempfile.mkdtemp())).operational(None)
        listing = result["fork_watch"]
        self.assertEqual(ftw.status_rows(self.config_dir), listing)
        self.assertEqual("claude:" + SESSION, listing["sessions"][0]["session"])
        lines = "\n".join(text_lines("status", result))
        self.assertIn("fork_watch:", lines)
        for value in self.FORBIDDEN:
            self.assertNotIn(value, lines)

    def test_menu_rows_are_read_only_and_bounded(self):
        from ciel_runtime_support.work_settings_menu import description, panel_rows
        observed_state(self.config_dir, SESSION)
        rows, values = panel_rows({"language": "en"})
        info = [row for row, value in zip(rows, values) if value == "__info__"]
        self.assertEqual(1, len(info))
        self.assertIn("Fork watch claude:" + SESSION[:8], info[0])
        self.assertRegex(info[0], r"ended 2 · active 0 · unknown 0 · orphaned 0; last notice "
                                  r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ \([0-9a-f]{16}\)$")
        self.assertEqual("back", values[-1])
        self.assertNotIn("__info__", panel_rows({"language": "en"}, side=True)[1])
        self.assertIn("Read only", description({"language": "en"}, "__info__"))
        for value in self.FORBIDDEN:
            self.assertNotIn(value, "\n".join(rows))
        shutil.rmtree(self.config_dir / ftw.STATE_DIR_NAME)
        rows, values = panel_rows({"language": "ko"})
        self.assertIn("포크 감시: 관측된 세션 없음", rows)

    def test_prelaunch_ignores_info_rows(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "prelaunch.py").read_text(encoding="utf-8")
        start = source.index("elif panel in ('work-settings', 'side-settings'):\n                    if value == 'back':")
        block = source[start:start + 400]
        self.assertIn("if value == '__info__':\n                        continue", block)

    def test_web_console_renders_the_view_with_text_apis(self):
        from ciel_runtime_support.work_web_ui import render_work_web_ui
        html = render_work_web_ui()
        self.assertIn('id="forkwatch-stat"', html)
        self.assertIn("renderForkWatch(snapshot.runtime&&snapshot.runtime.fork_watch)", html)
        body = html[html.index("function renderForkWatch"):html.index("function nodeMap")]
        self.assertNotIn("innerHTML", body)
        self.assertIn("['ended','active','unknown','orphaned']", body)
        # The counts wrap instead of sharing one ellipsized line (lead 04:10Z defect).
        self.assertIn(".stat strong.fw{display:flex;flex-wrap:wrap;", html)
        self.assertIn("white-space:normal;overflow:visible;text-overflow:clip}", html)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_web_console_browser_shows_counts_and_last_notice(self):
        from playwright.sync_api import sync_playwright
        from ciel_runtime_support.work_web_ui import render_work_web_ui
        html = render_work_web_ui()
        view = {"available": True, "forks": 3, "counts": {"ended": 2, "active": 0, "unknown": 1, "orphaned": 0},
                "armed": True, "notice_pending": False, "last_notice_at": "2026-11-14T03:37:34Z",
                "last_notice_key": "0123456789abcdef", "observed_at": "2026-11-14T03:37:36Z"}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing",
                 "runtime": {"fork_watch": view}}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 900}), ("desktop", {"width": 1440, "height": 900})):
                with self.subTest(viewport=label):
                    page = browser.new_page(viewport=viewport)

                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready\n\n")
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.wait_for_function("document.querySelectorAll('#forkwatch-stat .fw-count').length === 4")
                    self.assertEqual(["2 ended", "0 active", "1 unknown", "0 orphaned"],
                                     page.locator("#forkwatch-stat .fw-count").all_inner_texts())
                    self.assertEqual("fork watch · last notice 2026-11-14T03:37:34Z (0123456789abcdef)",
                                     page.locator("#forkwatch-note").inner_text())
                    # Amendment 4 (lead 04:10Z): every count fully rendered, none clipped by
                    # its own box, the stat cell or an ellipsis; the note is not clipped either.
                    clipped = page.evaluate("""() => {
                        const cell = document.querySelector('.stat.forkwatch').getBoundingClientRect();
                        const out = [];
                        for (const node of [...document.querySelectorAll('#forkwatch-stat .fw-count'),
                                            document.getElementById('forkwatch-stat'),
                                            document.getElementById('forkwatch-note')]) {
                            const box = node.getBoundingClientRect();
                            const style = getComputedStyle(node);
                            if (node.scrollWidth > node.clientWidth || node.scrollHeight > node.clientHeight
                                || box.width === 0 || box.left < cell.left - 0.5 || box.right > cell.right + 0.5
                                || box.top < cell.top - 0.5 || box.bottom > cell.bottom + 0.5
                                || style.textOverflow === 'ellipsis' || style.visibility !== 'visible')
                                out.push(node.id || node.dataset.state);
                        }
                        return out;
                    }""")
                    self.assertEqual([], clipped)
                    evidence = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence:
                        page.screenshot(path=os.path.join(evidence, "fork-watch-" + label + ".png"))
                    self.assertLessEqual(page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)
                    page.close()
            browser.close()


class RouterScopeTests(unittest.TestCase):
    def test_bound_scope_requires_matching_claude_epoch(self):
        class Scope:
            runtime, session_id, epoch = "claude", SESSION, "e1"

        class Compaction:
            def __init__(self, launch):
                self.launch = launch

            def scope(self):
                return Scope()

            def current_launch_scope(self):
                return self.launch

        launch = {"epoch": "e1", "cwd": "C:/w", "claude_config_dir": "C:/c", "started_at": 5.0}
        self.assertEqual({"runtime": "claude", "session_id": SESSION, "epoch": "e1", "cwd": "C:/w",
                          "claude_config_dir": "C:/c", "started_at": 5.0, "direct_empty_stream_retries": 0},
                         ftw.bound_claude_scope(Compaction(launch)))
        self.assertEqual({}, ftw.bound_claude_scope(Compaction({**launch, "epoch": "e2"})))
        Scope.runtime = "codex"
        self.assertEqual({}, ftw.bound_claude_scope(Compaction(launch)))


class LaunchScopeRetryLimitTests(unittest.TestCase):
    def test_projected_retry_limit_is_bounded_and_routed_is_zero(self):
        from ciel_runtime_support.runtime_launch import _direct_empty_stream_retries
        self.assertEqual(3, _direct_empty_stream_retries({"CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES": "3"}))
        self.assertEqual(0, _direct_empty_stream_retries({"CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES": "0"}))
        self.assertEqual(0, _direct_empty_stream_retries({}))
        self.assertEqual(10, _direct_empty_stream_retries({"CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES": "99"}))
        self.assertEqual(0, _direct_empty_stream_retries({"CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES": "bad"}))

    def test_binding_persists_and_exposes_projected_value(self):
        from types import SimpleNamespace
        from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
        from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
        from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

        root = Path(tempfile.mkdtemp())
        home = root / "home"
        logs = []
        artifact = SecureJsonRepository(root / "command.json", SecureJsonEffects(lambda *a: logs.append(a)))
        requests = ChannelCompactRequestRepository(artifact.path, __import__("threading").Lock(), artifact.save,
                                                   lambda text, limit: text[:limit], lambda *a: logs.append(a), lambda: 600)
        budget = SimpleNamespace(compact_kind=lambda body: None)
        binding = RuntimeCompactionBinding(root, home, requests, lambda: SimpleNamespace(budget=budget),
                                           lambda body: 0, lambda: ("fixture", {}), lambda *a: logs.append(a))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home / ".claude")}):
            binding.bind("claude", session_id=SESSION, cwd="C:/w", direct_empty_stream_retries=3)
        self.assertEqual(3, binding.current_launch_scope()["direct_empty_stream_retries"])
        binding.bind("claude", session_id=SESSION, cwd="C:/w", direct_empty_stream_retries=0)
        self.assertEqual(0, binding.current_launch_scope()["direct_empty_stream_retries"])
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "runtime_launch.py").read_text(
            encoding="utf-8")
        self.assertIn('direct_empty_stream_retries=_direct_empty_stream_retries(env)', source)


class WorkLeaseMapTests(unittest.TestCase):
    def test_only_this_session_scope_and_bound_hosts(self):
        snapshot = {"leases": {
            "L1": {"scope": "claude:" + SESSION, "host_id": A, "item_id": "n1"},
            "L2": {"scope": "claude:other", "host_id": B, "item_id": "n2"},
            "L3": {"scope": "claude:" + SESSION, "host_id": None, "item_id": "n3"},
        }}
        self.assertEqual({A: {"lease_id": "L1", "item_id": "n1"}}, ftw.work_lease_map(snapshot, "claude:" + SESSION))


if __name__ == "__main__":
    unittest.main()
