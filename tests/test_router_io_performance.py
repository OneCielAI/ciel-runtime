"""Fail-first regression tests for router/launcher I/O and CPU hot-path defects.

Reproduces the five live-measured causes of router CPU saturation, 200-638 MB/3s
read storms, and Windows lock/fsync stalls observed on Andy and Celly (2026-11-21):
1. host_result_pages: per-part full-file reopen/parse + per-bound rewrite/prune.
2. work_parallel_status / work_status: narrow unpaged host projections for ciel-work-gate.
3. WorkParallel / Coordination / Finalization stores: uncached full-file lock+parse+validate.
4. ChannelMessageRepository._scan: uncached full-file JSONL parse on every poll.
5. ForkTurnWatch: 2-second rescan loop on unchanged no_record & ended forks.
6. RouterFileLogger: repeated failing replace() syscalls on every log line.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import fork_turn_watch as ftw
from ciel_runtime_support import host_result_pages as pages
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.channel_message_repository import (
    ChannelMessageAppendPorts,
    ChannelMessageRepository,
)
from ciel_runtime_support.durable_file_lock import exclusive_file_lock
from ciel_runtime_support.runtime_logging import LogLevelRepository, RouterFileLogger
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore
from ciel_runtime_support.work_plan_store import session_work_plan_store

TOKEN = "perf-host-token-" * 3
IDENTITY = "claude:perf-fixture"


def _respond(_request_id, text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _age_file(path: Path, seconds: float = 5.0) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


class HostResultPagesCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)

    def test_page_uses_memory_cache_without_reopening_payload_per_part(self):
        text = json.dumps({"payload": "x" * 60_000})
        env = json.loads(pages.bound(self.state, IDENTITY, text))["paged"]
        self.assertGreater(env["parts"], 2)
        with mock.patch.object(pages, "reader", wraps=pages.reader) as reader_spy:
            pieces = [
                pages.page(self.state, IDENTITY, env["id"], idx)["text"]
                for idx in range(env["parts"])
            ]
        self.assertEqual(text, "".join(pieces))
        self.assertEqual(0, reader_spy.call_count)
        # Removing the file on disk still expires the page cleanly.
        target = pages._directory(self.state, IDENTITY) / (env["id"] + ".json")
        target.unlink()
        with self.assertRaises(tree.WorkPlanError):
            pages.page(self.state, IDENTITY, env["id"], 0)

    def test_bound_skips_identical_rewrite_and_throttles_prune(self):
        text = json.dumps({"payload": "y" * 30_000})
        with mock.patch.object(pages, "save", wraps=pages.save) as save_spy, \
             mock.patch.object(pages, "_prune", wraps=pages._prune) as prune_spy:
            first = pages.bound(self.state, IDENTITY, text)
            second = pages.bound(self.state, IDENTITY, text)
        self.assertEqual(first, second)
        self.assertEqual(1, save_spy.call_count)
        self.assertEqual(1, prune_spy.call_count)


class GateNarrowProjectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.config = {
            "work_mode": True,
            "work_parallel_host": {"token": TOKEN},
            "work_parallel": {"enabled": True},
        }
        store = session_work_plan_store(self.state, IDENTITY)

        def grow(doc):
            for idx in range(250):
                tree.add_node(
                    doc,
                    None,
                    f"목표 {idx} " + ("상세설명 " * 8),
                    note="메모 " * 30,
                )

        store.transaction(grow)

    def _call(self, name, args):
        return dispatch_work_tool(
            1,
            name,
            {**args, "session_id": IDENTITY},
            load_config=lambda: self.config,
            state_dir=self.state,
            respond=_respond,
        )

    def test_work_status_phase_projection_stays_unpaged_and_skips_parallel_ledger(self):
        from ciel_runtime_support import work_parallel_mcp
        with mock.patch.object(work_parallel_mcp, "project", wraps=work_parallel_mcp.project) as proj_spy:
            res = self._call(
                "work_status",
                {"format": "json", "projection": "phase", "_host": {"token": TOKEN}},
            )
        self.assertFalse(res["isError"], res)
        body = json.loads(res["content"][0]["text"])
        self.assertNotIn("paged", body)
        self.assertEqual("phase", body.get("projection"))
        self.assertTrue(body["enabled"])
        self.assertIn(body["phase"], ("planning", "executing"))
        self.assertEqual(0, proj_spy.call_count)

    def test_work_parallel_status_gate_projection_is_unpaged_and_skips_sidecar_archives(self):
        ledger_path = self.state / "work-parallel.json"
        wp_store = WorkParallelStore(ledger_path)
        wp_store.configure({"enabled": True, "paused": False}, capability_ready=True, expected_version=0)
        doc = session_work_plan_store(self.state, IDENTITY).read()
        item_id = doc["nodes"][0]["id"]
        _, lease_id = wp_store.reserve(
            [{
                "item_id": item_id,
                "priority": "P2",
                "created_at": 100.0,
                "approved": True,
                "focused": True,
                "dependencies_done": True,
                "plan_version": doc["version"],
                "usage": 0.1,
                "role": "worker",
                "mode": "fork",
            }],
            scope=IDENTITY,
            expected_version=wp_store.read()["version"],
            dispatch_name="work-gate-test",
        )
        wp_store.attach_workspace(
            lease_id,
            {"kind": "worktree", "path": str(self.state / "wt-1"), "repo": str(self.state), "branch": "work-parallel/test", "base": "0" * 40},
            expected_version=wp_store.read()["version"],
        )
        wp_store.bind(
            lease_id,
            "agent-gate-1",
            expected_version=wp_store.read()["version"],
            agent_name="work-gate-test",
        )
        from ciel_runtime_support import work_parallel_mcp
        with mock.patch.object(work_parallel_mcp, "ledger", return_value=wp_store), \
             mock.patch.object(work_parallel_mcp, "coordination", wraps=work_parallel_mcp.coordination) as coord_spy, \
             mock.patch.object(work_parallel_mcp, "finalization", wraps=work_parallel_mcp.finalization) as fin_spy, \
             mock.patch.object(work_parallel_mcp, "stranded", wraps=work_parallel_mcp.stranded) as strd_spy:
            # 1) Lease lookup by host_id
            res = self._call(
                "work_parallel_status",
                {"projection": "gate", "host_id": "agent-gate-1", "_host": {"token": TOKEN}},
            )
            self.assertFalse(res["isError"], res)
            body = json.loads(res["content"][0]["text"])
            self.assertNotIn("paged", body)
            self.assertEqual("gate", body.get("projection"))
            self.assertIn(lease_id, body["leases"])
            self.assertEqual(str(self.state / "wt-1"), body["leases"][lease_id]["workspace_path"])
            # 2) Models-only lookup for main / side
            mres = self._call(
                "work_parallel_status",
                {"projection": "gate", "models_only": True, "_host": {"token": TOKEN}},
            )
            self.assertFalse(mres["isError"], mres)
            mbody = json.loads(mres["content"][0]["text"])
            self.assertNotIn("paged", mbody)
            self.assertIn("fork_models", mbody)
            self.assertEqual(0, coord_spy.call_count)
            self.assertEqual(0, fin_spy.call_count)
            self.assertEqual(0, strd_spy.call_count)
            # 3) Unauthenticated caller must not receive workspace_path via gate projection
            unauth = self._call("work_parallel_status", {"projection": "gate", "host_id": "agent-gate-1"})
            self.assertFalse(unauth["isError"], unauth)
            ubody = json.loads(unauth["content"][0]["text"])
            for row in (ubody.get("leases") or {}).values():
                self.assertNotIn("workspace_path", row)


class WorkParallelStoreReadCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_parallel_stores_cache_unchanged_reads_and_invalidate_on_write(self):
        wp = WorkParallelStore(self.root / "work-parallel.json")
        wp.configure({"paused": True}, capability_ready=True, expected_version=0)
        _age_file(wp.path, 5.0)
        with mock.patch.object(wp, "_read", wraps=wp._read) as read_spy:
            first = wp.read()
            first["version"] = 999999  # caller mutation must not corrupt cache
            second = wp.read()
        self.assertEqual(1, second["version"])
        self.assertEqual(1, read_spy.call_count)
        # Next write immediately updates/invalidates the cache.
        wp.configure({"paused": False}, capability_ready=True, expected_version=1)
        third = wp.read()
        self.assertEqual(2, third["version"])
        self.assertFalse(third["policy"]["paused"])

        coord = WorkCoordinationStore(self.root / "work-parallel-coordination.json")
        coord._change(lambda s: None, 0)
        # Seed file via atomic write so stat exists
        coord.path.write_text(json.dumps(coord.read()), encoding="utf-8")
        _age_file(coord.path, 5.0)
        with mock.patch.object(coord, "_read", wraps=coord._read) as c_spy:
            c1 = coord.read()
            c1["version"] = -1
            c2 = coord.read()
        self.assertEqual(0, c2["version"])
        self.assertEqual(1, c_spy.call_count)

        fin = WorkParallelFinalizationStore(self.root / "work-parallel-finalization.json")
        fin.path.write_text(json.dumps(fin.read()), encoding="utf-8")
        _age_file(fin.path, 5.0)
        with mock.patch.object(fin, "_read", wraps=fin._read) as f_spy:
            f1 = fin.read()
            f1["version"] = -1
            f2 = fin.read()
        self.assertEqual(0, f2["version"])
        self.assertEqual(1, f_spy.call_count)


class ChannelMessageRepositoryCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "runtime-inputs.jsonl"
        self.repo = ChannelMessageRepository(self.path, lambda *_args: None)
        self.ports = ChannelMessageAppendPorts(
            condition=threading.Condition(),
            file_lock=lambda: exclusive_file_lock(self.path),
            duplicate=lambda _msg: None,
            normalize_recipients=lambda v: list(v) if isinstance(v, list) else [],
        )

    def test_scan_caches_unchanged_queue_and_sees_new_appends(self):
        self.repo.append({"channel": "c", "message": "hello-1"}, self.ports)
        _age_file(self.path, 5.0)
        with mock.patch("ciel_runtime_support.channel_message_repository.json.loads", wraps=json.loads) as loads_spy:
            rows1 = self.repo.read(after_id=0)
            rows1[0]["message"] = "mutated"
            rows2 = self.repo.read(after_id=0)
        self.assertEqual("hello-1", rows2[0]["message"])
        self.assertEqual(1, loads_spy.call_count)
        self.repo.append({"channel": "c", "message": "hello-2"}, self.ports)
        rows3 = self.repo.read(after_id=0)
        self.assertEqual(["hello-1", "hello-2"], [r["message"] for r in rows3])


class ForkTurnWatchIdlePollTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.claude_dir = self.root / ".claude"
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.session = "sess-idle-poll"
        self.sub_dir = ftw.subagents_dir(str(self.claude_dir), str(self.cwd), self.session)
        self.sub_dir.mkdir(parents=True)

    def _watch(self):
        scope = {
            "runtime": "claude",
            "session_id": self.session,
            "claude_config_dir": str(self.claude_dir),
            "cwd": str(self.cwd),
            "started_at": 100.0,
        }
        ports = ftw.ForkTurnWatchPorts(
            scope=lambda: scope,
            submit=lambda body: {"id": 1},
            work_leases=lambda _id: {},
            log=lambda *_a: None,
        )
        return ftw.ForkTurnWatch(self.root / "state", ports)

    def test_unchanged_no_record_and_ended_forks_do_not_reopen_or_rescan(self):
        a_ended = "a" * 17
        a_norec = "b" * 17
        (self.sub_dir / f"agent-{a_ended}.meta.json").write_text(json.dumps({"isFork": True}), encoding="utf-8")
        (self.sub_dir / f"agent-{a_norec}.meta.json").write_text(json.dumps({"isFork": True}), encoding="utf-8")
        p_ended = self.sub_dir / f"agent-{a_ended}.jsonl"
        p_norec = self.sub_dir / f"agent-{a_norec}.jsonl"
        p_ended.write_text(
            json.dumps({"type": "assistant", "uuid": "u1", "timestamp": "2026-11-21T00:00:00Z",
                        "message": {"stop_reason": "end_turn"}}) + "\n",
            encoding="utf-8",
        )
        p_norec.write_text(
            json.dumps({"type": "attachment", "pad": "z" * 20_000}) + "\n",
            encoding="utf-8",
        )
        w = self._watch()
        w.poll()
        self.assertEqual("ended", w._state["forks"][a_ended]["state"])
        self.assertEqual("unknown", w._state["forks"][a_norec]["state"])
        self.assertEqual("no_record", w._state["forks"][a_norec]["reason"])
        _age_file(p_ended, 5.0)
        _age_file(p_norec, 5.0)
        # Re-poll once to capture the aged mtime_ns, then spy on subsequent polls.
        w.poll()
        with mock.patch.object(ftw, "source_snapshot", wraps=ftw.source_snapshot) as snap_spy, \
             mock.patch.object(w, "_fresh_backscan", wraps=w._fresh_backscan) as back_spy:
            before_bytes = w.bytes_read
            w.poll()
            w.poll()
        self.assertEqual(before_bytes, w.bytes_read)
        self.assertEqual(0, snap_spy.call_count)
        self.assertEqual(0, back_spy.call_count)
        # Once the no_record file actually grows with an end_turn record, it resolves.
        with p_norec.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "uuid": "u2", "timestamp": "2026-11-21T00:01:00Z",
                                 "message": {"stop_reason": "end_turn"}}) + "\n")
        w.poll()
        self.assertEqual("ended", w._state["forks"][a_norec]["state"])


class RouterFileLoggerRotationCooldownTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_failed_rotation_backs_off_instead_of_retrying_every_line(self):
        log_path = self.root / "router.log"
        log_path.write_bytes(b"x" * 2048)
        levels = LogLevelRepository(
            self.root,
            self.root / "log-level",
            {"value": None, "checked_at": 0.0, "file_mtime": 0.0},
            3,
            {},
        )
        logger = RouterFileLogger(self.root, log_path, 1024, levels)
        with mock.patch.object(Path, "replace", side_effect=PermissionError("locked")) as rep_spy:
            for idx in range(10):
                logger.write("INFO", f"line-{idx}")
        # Only the first line should attempt the 2 replace targets; lines 1..9 back off.
        self.assertEqual(2, rep_spy.call_count)


class WorkMcpLockBusyHandlingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.config = {
            "work_mode": True,
            "work_parallel_host": {"token": TOKEN},
            "work_side_intake": {"enabled": True, "type": "btw_with_mcp"},
        }

    def test_channel_file_lock_busy_returns_mcp_error_instead_of_uncaught_500(self):
        import errno
        from ciel_runtime_support.durable_file_lock import ChannelFileLockBusy

        with mock.patch(
            "ciel_runtime_support.side_intake_mcp.run",
            side_effect=ChannelFileLockBusy(errno.EDEADLK, "channel file lock acquisition busy: C:\\secret\\path"),
        ):
            res = dispatch_work_tool(
                1,
                "work_side_claim",
                {"owner": "side-1", "session_id": IDENTITY, "_host": {"token": TOKEN}},
                load_config=lambda: self.config,
                state_dir=self.state,
                respond=_respond,
            )
        self.assertTrue(res["isError"])
        text = res["content"][0]["text"]
        self.assertIn("busy", text.lower())
        self.assertNotIn("secret", text)


if __name__ == "__main__":
    unittest.main()
