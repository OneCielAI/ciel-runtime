"""The side tells main once about a running fork that went silent (user 2026-11-24).

Prody n1200 2026-11-24: lease 6ebe7e68 (P1) stayed running with its slot held
while the fork's own transcript ended at a Bash tool call (10:58:19Z) with no
result for 57+ minutes, no process of that call alive and the ledger unchanged.
The deployed stall watch (943c3e2) lists only parked forks, so nothing caught it.

The blocker check now also lists a running, slot-holding fork whose transcript
has not grown for SILENT_MINUTES and whose last record does not close its turn.
The side files a stall notice of kind "silent"; the router re-reads the ledger
and the transcript, records that exact silence on the item as told, and main
hears of it once per silence.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ciel_runtime_support import work_gate_check as gate
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support import work_silent_forks as silent
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

SCOPE = "claude:parent"
HOST = {"token": "x" * 40}
MINUTE = 60.0
NOW = 1_795_500_000.0


def receipt(name):
    return hashlib.sha256(name.encode()).hexdigest()


def iso(stamp):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def lease(lease_id, item_id, *, state="running", slot=True, scope=SCOPE, evidence=None):
    return {"id": lease_id, "scope": scope, "item_id": item_id, "state": state, "slot": slot,
            "host_id": "a" + lease_id.lower(), "role": "worker", "priority": "P1", "mode": "fork",
            "evidence": evidence, "created_at": NOW - 120 * MINUTE}


def bash_call(at, uid="u1"):
    return {"type": "assistant", "uuid": uid, "timestamp": iso(at),
            "message": {"role": "assistant", "stop_reason": None, "content": [
                {"type": "tool_use", "id": "toolu_" + uid, "name": "Bash",
                 "input": {"command": "node probe-SECRET.js"}}]}}


def end_turn(at, uid="u9"):
    return {"type": "assistant", "uuid": uid, "timestamp": iso(at),
            "message": {"role": "assistant", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "done"}]}}


def write_transcript(directory, agent, records, *, grown_at):
    path = Path(directory) / f"agent-{agent}.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    os.utime(path, (grown_at, grown_at))
    return path


class SilentForkUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.dir = root / "subagents"
        self.dir.mkdir()
        self.store = WorkPlanStore(root / "board.json")
        s = self.store
        _, self.goal = s.transaction(lambda d: tree.add_node(d, None, "probe the page", priority="P1"))
        _, self.item = s.transaction(lambda d: tree.add_node(d, self.goal, "run the playwright probe"))
        _, self.other = s.transaction(lambda d: tree.add_node(d, self.goal, "other item"))
        _, self.done = s.transaction(lambda d: tree.add_node(d, self.goal, "done item"))
        s.transaction(lambda d: tree.update_node(d, self.done, status="done", evidence="ok"))
        self.stopped_at = NOW - 57 * MINUTE
        write_transcript(self.dir, "as1", [bash_call(self.stopped_at)], grown_at=self.stopped_at)
        write_transcript(self.dir, "as2", [bash_call(NOW - 5 * MINUTE)], grown_at=NOW - 5 * MINUTE)
        write_transcript(self.dir, "as3", [end_turn(self.stopped_at)], grown_at=self.stopped_at)
        write_transcript(self.dir, "as5", [bash_call(self.stopped_at)], grown_at=self.stopped_at)
        write_transcript(self.dir, "as6", [bash_call(self.stopped_at)], grown_at=self.stopped_at)
        write_transcript(self.dir, "as7", [bash_call(self.stopped_at)], grown_at=self.stopped_at)

    def leases(self):
        return {
            "S1": lease("S1", self.item, evidence="probing the page"),
            "S2": lease("S2", self.other),                       # transcript grew 5 minutes ago
            "S3": lease("S3", self.other),                       # its turn ended (fork_turn_watch's case)
            "S4": lease("S4", self.other),                       # no transcript yet
            "S5": lease("S5", self.other, state="waiting_input", slot=False),  # parked, not running
            "S6": lease("S6", self.other, scope="claude:other"),  # another conversation
            "S7": lease("S7", self.done),                        # its item is done
        }

    def rows(self, **extra):
        return silent.silent_forks(self.leases(), SCOPE, self.store.read(), now=NOW, directory=self.dir, **extra)

    def test_a_running_fork_whose_transcript_stopped_growing_is_listed(self):
        rows = self.rows()
        self.assertEqual(["S1"], [row["lease_id"] for row in rows])
        row = rows[0]
        self.assertEqual((self.item, self.goal, "silent", ""), (row["item_id"], row["root_id"], row["kind"],
                                                                row["waiting_on"]))
        self.assertEqual(self.stopped_at, row["since"])
        self.assertEqual(57, row["minutes"])
        self.assertEqual("tool_use:Bash", row["last"])
        self.assertEqual("lease S1 silent since " + iso(self.stopped_at), row["mark"])
        self.assertEqual("probing the page", row["question"])
        # Payload-free: the tool input never leaves the transcript.
        self.assertNotIn("SECRET", json.dumps(rows))

    def test_thirty_minutes_of_silence_is_the_threshold(self):
        self.assertEqual(30, silent.SILENT_MINUTES)
        write_transcript(self.dir, "as1", [bash_call(NOW - 29 * MINUTE)], grown_at=NOW - 29 * MINUTE)
        self.assertEqual([], self.rows())
        write_transcript(self.dir, "as1", [bash_call(NOW - 31 * MINUTE)], grown_at=NOW - 31 * MINUTE)
        self.assertEqual(["S1"], [row["lease_id"] for row in self.rows()])

    def test_a_told_silence_is_not_listed_until_the_fork_records_again(self):
        mark = "lease S1 silent since " + iso(self.stopped_at)
        self.store.transaction(lambda d: tree.update_node(
            d, self.item, note="progress\n" + gate.STALL_TOLD + receipt("e1")[:12] + "): " + mark + " — checked"))
        self.assertEqual([], self.rows())
        # A later question stall on the same item does not make the told silence new.
        before = self.node(self.item)["note"]
        self.store.transaction(lambda d: tree.update_node(
            d, self.item, note=before + "\n" + gate.STALL_TOLD + receipt("e2")[:12] + "): lease S9 — other"))
        self.assertEqual([], self.rows())
        # The fork recorded again and then went silent again: a new silence.
        later = NOW - 40 * MINUTE
        write_transcript(self.dir, "as1", [bash_call(self.stopped_at), bash_call(later, "u2")], grown_at=later)
        rows = self.rows()
        self.assertEqual(["S1"], [row["lease_id"] for row in rows])
        self.assertEqual("lease S1 silent since " + iso(later), rows[0]["mark"])

    def node(self, node_id):
        return next(n for n in self.store.read()["nodes"] if n["id"] == node_id)

    def test_no_transcript_directory_lists_nothing(self):
        self.assertEqual([], silent.silent_forks(self.leases(), SCOPE, self.store.read(), now=NOW, directory=None))
        self.assertEqual([], silent.silent_forks(self.leases(), SCOPE, self.store.read(), now=NOW,
                                                 directory=self.dir / "missing"))

    def test_the_gate_event_names_the_silent_fork(self):
        rows = self.rows()
        tick = gate.tick_rows({}, self.store.read(), None, now=NOW, stalled=rows)
        self.assertIsNotNone(tick)
        event = gate.tick_event({}, [], tick[1], NOW, stalled=rows)
        self.assertIn("응답 없이 멈춘 포크", event["text"])
        self.assertIn("lease S1", event["text"])
        self.assertIn("tool_use:Bash", event["text"])
        self.assertIn("57분", event["text"])
        self.assertIn("전사가 30분 넘게 늘지 않음", event["text"])
        self.assertNotIn("오래 멈춘 포크 (", event["text"])
        self.assertEqual(["S1"], event["stalled"])

    def test_the_projection_keeps_a_told_silence_for_the_router_to_refuse(self):
        mark = "lease S1 silent since " + iso(self.stopped_at)
        self.store.transaction(lambda d: tree.update_node(
            d, self.item, note=gate.STALL_TOLD + receipt("e1")[:12] + "): " + mark + " — checked"))
        rows = silent.silent_projection({"leases": self.leases()}, self.store.read(), SCOPE, now=NOW,
                                        directory=self.dir)
        self.assertEqual([{"lease_id": "S1", "item_id": self.item, "mark": mark, "since": iso(self.stopped_at),
                           "last": "tool_use:Bash"}], rows)
        self.assertIsNone(silent.silent_projection({"leases": self.leases()}, self.store.read(), SCOPE, now=NOW,
                                                   directory=None))


class SilentNoticeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = session_work_plan_store(Path(self.tmp.name), SCOPE)
        _, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, "probe", priority="P1"))
        _, self.item = self.store.transaction(lambda d: tree.add_node(d, self.goal, "run the probe"))
        self.mark = "lease S1 silent since 2026-11-24T10:58:19Z"

    def decision(self, **extra):
        return {"action": "answer", "kind": "status", "summary": self.item + " 유지: fork silent",
                "reply": "silent fork", **extra}

    def notice(self, **over):
        row = {"item_id": self.item, "lease_id": "S1", "kind": "silent", "waiting_on": "",
               "checked": "n progress unchanged since 10:31Z", "needed": "check the fork, resume or stop it"}
        row.update(over)
        return row

    def projection(self, silent_rows):
        return {"rows": [], **({"silent": silent_rows} if silent_rows is not None else {})}

    def live(self):
        return [{"lease_id": "S1", "item_id": self.item, "mark": self.mark, "since": "2026-11-24T10:58:19Z",
                 "last": "tool_use:Bash"}]

    def apply(self, decision, key="silent-one", parked=None):
        return apply(self.store, receipt(key), decision, self.store.read()["version"],
                     gate_check=True, parked_projection=parked)

    def node(self):
        return next(n for n in self.store.read()["nodes"] if n["id"] == self.item)

    def test_a_silence_the_router_still_sees_is_recorded_and_reported_once(self):
        result = self.apply(self.decision(stall_notices=[self.notice()]), parked=self.projection(self.live()))
        self.assertEqual([{**self.notice(), "since": "2026-11-24T10:58:19Z", "last": "tool_use:Bash"}],
                         result["stall_notices"])
        self.assertIn(gate.STALL_TOLD + receipt("silent-one")[:12] + "): " + self.mark, self.node()["note"])
        again = self.apply(self.decision(stall_notices=[self.notice()]), key="silent-two",
                           parked=self.projection(self.live()))
        self.assertNotIn("stall_notices", again)
        self.assertEqual([{"item_id": self.item, "reason": "already_notified"}], again["unnoticed_stalls"])

    def test_a_silent_notice_is_refused_when_the_router_cannot_confirm_the_silence(self):
        result = self.apply(self.decision(stall_notices=[self.notice()]), parked=self.projection([]))
        self.assertEqual([{"item_id": self.item, "reason": "not a silent running worker of this item"}],
                         result["unnoticed_stalls"])
        result = self.apply(self.decision(stall_notices=[self.notice()]), key="silent-three",
                            parked=self.projection(None))
        self.assertEqual([{"item_id": self.item, "reason": "the fork transcript was unreadable"}],
                         result["unnoticed_stalls"])
        result = self.apply(self.decision(stall_notices=[self.notice()]), key="silent-four")
        self.assertEqual([{"item_id": self.item, "reason": "the Work ledger was unreadable"}],
                         result["unnoticed_stalls"])
        other = [{**self.live()[0], "item_id": self.goal}]
        result = self.apply(self.decision(stall_notices=[self.notice()]), key="silent-five",
                            parked=self.projection(other))
        self.assertEqual([{"item_id": self.item, "reason": "not a silent running worker of this item"}],
                         result["unnoticed_stalls"])
        self.assertNotIn(gate.STALL_TOLD, str(self.node().get("note") or ""))

    def test_silent_notices_are_validated(self):
        validate(self.decision(stall_notices=[self.notice()]))
        with self.assertRaisesRegex(tree.WorkPlanError, "invalid side decision stall_notices"):
            validate(self.decision(stall_notices=[self.notice(kind="silence")]))
        with self.assertRaisesRegex(tree.WorkPlanError, "invalid side decision stall_notices"):
            validate(self.decision(stall_notices=[self.notice(needed="")]))


class SilentForkMcpTests(unittest.TestCase):
    """The router's gate check lists the silent fork; its resolve re-verifies and records it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {"work_mode": True, "work_side_intake": {"enabled": True},
                       "work_parallel_host": {"token": "x" * 40}}
        self.store = session_work_plan_store(self.root, SCOPE)
        _, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, "probe", priority="P1"))
        _, self.leaf = self.store.transaction(lambda d: tree.add_node(d, self.goal, "run the probe"))
        self.claude_cfg = self.root / "claude-cfg"
        self.workspace = self.root / "repo"
        self.workspace.mkdir()
        from ciel_runtime_support.fork_turn_watch import subagents_dir
        directory = subagents_dir(self.claude_cfg, self.workspace, "parent")
        directory.mkdir(parents=True)
        self.stopped_at = time.time() - 57 * MINUTE
        write_transcript(directory, "as1", [bash_call(self.stopped_at)], grown_at=self.stopped_at)
        self.ledger = Mock()
        self.ledger.read.return_value = {"version": 3, "leases": {"S1": lease("S1", self.leaf)}}
        self.reviews = Mock()
        self.reviews.read.return_value = {"reviews": {}}
        self.scope = {"runtime": "claude", "session_id": "parent", "cwd": str(self.workspace),
                      "claude_config_dir": str(self.claude_cfg)}

    def invoke(self, name, args, host=None):
        return dispatch_work_tool(1, name, {**args, "session_id": SCOPE, "_host": host or HOST},
                                  load_config=lambda: self.config, state_dir=self.root,
                                  respond=lambda _id, text, error=False: {"text": text, "error": error})

    def value(self, name, args, host=None):
        result = self.invoke(name, args, host)
        self.assertFalse(result["error"], result)
        value = json.loads(result["text"])
        if isinstance(value, dict) and "paged" in value:
            pages = [self.value("work_host_page", {"id": value["paged"]["id"], "part": part})["text"]
                     for part in range(value["paged"]["parts"])]
            value = json.loads("".join(pages))
        return value

    def test_a_silent_fork_is_checked_and_main_is_told_once(self):
        with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=self.ledger), \
                patch("ciel_runtime_support.work_parallel_mcp.finalization", return_value=self.reviews), \
                patch("ciel_runtime_support.work_fork_api_retry._active_claude_scope", return_value=self.scope):
            queued = json.loads(self.invoke("work_side_gate_check", {"idle": True, "forks_running": 1})["text"])
            self.assertTrue(queued.get("queued"), queued)
            self.assertEqual(["S1"], queued["stalled"])
            claimed = self.value("work_side_claim", {"owner": "actor"})["claim"]
            self.assertIn("응답 없이 멈춘 포크", claimed["text"])
            self.assertIn("tool_use:Bash", claimed["text"])
            self.assertNotIn("SECRET", claimed["text"])
            host = {**HOST, "receipt_id": claimed["receipt_id"], "generation": claimed["generation"],
                    "work_digest": claimed["work_digest"]}
            resolved = self.invoke("work_side_resolve", {"decision": {
                "action": "answer", "kind": "status", "summary": self.leaf + " 유지: fork silent",
                "reply": "silent",
                "stall_notices": [{"item_id": self.leaf, "lease_id": "S1", "kind": "silent", "waiting_on": "",
                                   "checked": "progress unchanged", "needed": "check the fork"}]},
                "expected_version": claimed["workload"]["version"]}, host)
            self.assertFalse(resolved["error"], resolved)
            result = json.loads(resolved["text"])["result"]
            self.assertEqual([("S1", "silent", iso(self.stopped_at), "tool_use:Bash")],
                             [(row["lease_id"], row["kind"], row["since"], row["last"])
                              for row in result["stall_notices"]])
            node = next(n for n in self.store.read()["nodes"] if n["id"] == self.leaf)
            self.assertIn("lease S1 silent since " + iso(self.stopped_at), node["note"])
            # Told: the next check no longer lists it.
            self.assertEqual([], silent.silent_forks(self.ledger.read()["leases"], SCOPE, self.store.read()))


if __name__ == "__main__":
    unittest.main()
