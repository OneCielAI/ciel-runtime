import errno
import io
import json
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
from ciel_runtime_support.runtime_input_status import (
    RuntimeInputStatusRepository, StatusHistoryUnreadable, WakeAttemptConflict,
)


class RuntimeInputStatusRepositoryTests(unittest.TestCase):
    def repository(self, *, request_floor=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        events = []
        repository = RuntimeInputStatusRepository(
            Path(temp_dir.name) / "runtime-input-status.jsonl",
            lambda **event: events.append(event),
            lambda _level, _message: None,
            threading.RLock(),
        )
        if request_floor is not None:
            repository.request_floor = request_floor
        return repository, events

    def test_queued_submitted_replied_is_queryable_and_streamed(self):
        repository, events = self.repository()

        repository.transition(12, "queued")
        repository.transition(12, "submitted")
        repository.transition(12, "replied")

        self.assertEqual("replied", repository.get(12)["status"])
        self.assertEqual(["queued", "submitted", "replied"], [e["data"]["status"] for e in events])
        self.assertTrue(all(e["category"] == "runtime_input.status" for e in events))

    def test_failed_state_does_not_regress_to_queued(self):
        repository, events = self.repository()

        repository.transition(13, "queued")
        repository.transition(13, "failed", reason="prompt_not_submitted")
        repository.transition(13, "queued")

        self.assertEqual("failed", repository.get(13)["status"])
        self.assertEqual(2, len(events))
        self.assertEqual("prompt_not_submitted", repository.get(13)["reason"])

    def test_health_is_read_only_for_absent_history(self):
        repository, events = self.repository()
        self.assertEqual("complete", repository.history_health()["coverage"])
        self.assertFalse(repository.path.exists())
        self.assertFalse(repository.path.with_suffix(".jsonl.lock").exists())
        self.assertEqual([], events)

    def test_nul_prefixed_surviving_row_is_used_as_status(self):
        for prefix_size in (17, 3 * 256 * 1024):
            for status in ("queued", "submitted", "replied", "interrupted_uncertain"):
                with self.subTest(prefix_size=prefix_size, status=status):
                    repository, events = self.repository()
                    repository.transition(1, "queued")
                    row = {"request_id": 40, "status": status,
                           "updated_at": "2026-10-20T09:00:00+00:00", "updated_at_epoch": 1,
                           "reason": "private reason", "data": {"prompt": "private message"}}
                    before = repository.path.read_bytes() + bytes(prefix_size) + json.dumps(row).encode() + bytes([10])
                    repository.path.write_bytes(before)
                    events.clear()
                    files = set(repository.path.parent.iterdir())
                    health = repository.history_health()
                    self.assertTrue(health["complete"])
                    self.assertEqual(1, health["recovered_count"])
                    self.assertEqual(len(before), health["scanned_bytes"])
                    self.assertNotIn("private", json.dumps(health))
                    self.assertEqual(row, repository.get(40))
                    self.assertEqual([1, 40], [r["request_id"] for r in repository.list_latest()])
                    self.assertEqual(before, repository.path.read_bytes())
                    self.assertEqual(files, set(repository.path.parent.iterdir()))
                    self.assertEqual([], events)
                    if status == "queued":
                        attempt = repository.begin_attempt([1, 40], "child")
                        self.assertEqual(attempt, repository.get(40)["data"]["attempt_id"])
                    else:
                        with self.assertRaises(WakeAttemptConflict):
                            repository.begin_attempt([1, 40], "child")
                        self.assertEqual(before, repository.path.read_bytes())
                        self.assertEqual("queued", repository.get(1)["status"])

    def test_sparse_and_legacy_nul_prefixed_rows_are_recovered(self):
        rows = [{"request_id": 40, "status": "queued"},
                {"request_id": "40", "status": "submitted", "legacy_field": True},
                {"request_id": 40, "status": "queued", "updated_at": "bad", "updated_at_epoch": 1}]
        for row in rows:
            with self.subTest(row=row):
                repository, events = self.repository()
                before = bytes(3) + json.dumps(row).encode() + bytes([10])
                repository.path.write_bytes(before)
                self.assertEqual(row, repository.get(40))
                self.assertEqual(1, repository.history_health()["recovered_count"])
                self.assertEqual(before, repository.path.read_bytes())
                self.assertEqual([], events)

    def test_arbitrary_invalid_and_unknown_rows_are_ignored(self):
        rows = [b"not json", b"[]", b"{}", b"", bytes([255]), bytes(3),
                b'{"request_id":40,"status":"unknown"}',
                b'{"request_id":true,"status":"queued"}',
                b'{"request_id":40,"status":"queued","data":[]}',
                b'{"request_id":40,"status":"queued","status":"replied"}',
                b'{"request_id":40,"status":"submitted","data":{"x":NaN}}',
                b'{"request_id":40,"status":"submitted","data":{"x":1e999}}',
                b'{"request_id":40,"status":"sub' + bytes(1) + b'mitted"}',
                bytes(3) + b'{"request_id":40,"status":"unknown"}']
        for raw in rows:
            with self.subTest(raw=raw):
                repository, _ = self.repository()
                repository.path.write_bytes(raw + bytes([10]))
                self.assertIsNone(repository.get(40))
                self.assertFalse(repository.history_health()["complete"])
                attempt = repository.begin_attempt([40], "child")
                self.assertEqual(attempt, repository.get(40)["data"]["attempt_id"])

    def test_damage_warning_is_payload_free_and_deduplicated(self):
        repository, _ = self.repository()
        repository.transition(1, "queued")
        with repository.path.open("ab") as stream:
            stream.write(b"private invalid payload" + bytes([10]))
        logs = []
        repository.log = lambda *args: logs.append(args)
        self.assertEqual("queued", repository.get(1)["status"])
        self.assertEqual(1, len(repository.list_latest()))
        self.assertEqual(1, len(logs))
        self.assertIn("history_damage_ignored", logs[0][1])
        self.assertNotIn("private", logs[0][1])
        with repository.path.open("ab") as stream:
            stream.write(b"other invalid payload" + bytes([10]))
        repository.get(1)
        self.assertEqual(2, len(logs))
        self.assertIn("lines=2", logs[-1][1])

    def test_complete_tail_without_newline_blocks_readmission_and_append_is_separated(self):
        for status in ("submitted", "replied", "interrupted_uncertain"):
            with self.subTest(status=status):
                repository, _ = self.repository()
                tail = json.dumps({"request_id": 40, "status": status}).encode()
                repository.path.write_bytes(tail)
                self.assertTrue(repository.history_health()["complete"])
                self.assertEqual(status, repository.get(40)["status"])
                with self.assertRaises(WakeAttemptConflict):
                    repository.begin_attempt([40], "child")
                self.assertEqual(tail, repository.path.read_bytes())
                attempt = repository.begin_attempt([41], "child")
                self.assertTrue(repository.path.read_bytes().startswith(tail + bytes([10]) + b"{"))
                self.assertEqual(attempt, repository.get(41)["data"]["attempt_id"])
                self.assertEqual(status, repository.get(40)["status"])

    def test_malformed_unterminated_tail_does_not_swallow_append(self):
        repository, _ = self.repository()
        tail = b'{"request_id":'
        repository.path.write_bytes(tail)
        self.assertEqual("unfinished_eof", repository.history_health()["diagnostics"][0]["failure_category"])
        attempt = repository.begin_attempt([41], "child")
        self.assertTrue(repository.path.read_bytes().startswith(tail + bytes([10]) + b"{"))
        self.assertEqual(attempt, repository.get(41)["data"]["attempt_id"])

    def test_legacy_records_keep_ownership_without_schema_rewrite(self):
        repository, _ = self.repository()
        rows = [{"request_id": "40", "status": "submitted", "legacy_field": True},
                {"request_id": 41, "status": "queued"}]
        before = bytes([10]).join(json.dumps(row).encode() for row in rows) + bytes([10])
        repository.path.write_bytes(before)
        self.assertTrue(repository.history_health()["complete"])
        self.assertEqual("submitted", repository.get(40)["status"])
        with self.assertRaises(WakeAttemptConflict):
            repository.begin_attempt([40, 41], "new")
        self.assertEqual(before, repository.path.read_bytes())

    def test_diagnostics_are_bounded_and_scan_continues_after_oversized_line(self):
        repository, _ = self.repository()
        damaged = (b"not-json" + bytes([10])) * 50
        repository.path.write_bytes(damaged)
        health = repository.history_health()
        self.assertEqual(50, health["failure_count"])
        self.assertEqual(32, len(health["diagnostics"]))
        self.assertTrue(health["diagnostics_truncated"])
        self.assertEqual(len(damaged), health["scanned_bytes"])
        before = b"x" * 300 + bytes([10]) + b'{"request_id":40,"status":"submitted"}' + bytes([10])
        repository.path.write_bytes(before)
        with mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_LINE_BUDGET", 64):
            health = repository.history_health()
            self.assertEqual("submitted", repository.get(40)["status"])
        self.assertEqual(len(before), health["scanned_bytes"])
        self.assertEqual(1, health["failure_count"])
        self.assertEqual("line_budget_exceeded", health["diagnostics"][0]["failure_category"])
        self.assertEqual(before, repository.path.read_bytes())

    def test_history_larger_than_eight_mib_sees_latest_owner(self):
        repository, _ = self.repository()
        old = json.dumps({"request_id": 1, "status": "replied", "data": {"padding": "x" * 2000}}).encode() + bytes([10])
        latest = {"request_id": 40, "status": "submitted",
                  "data": {"child_epoch": "old-child", "attempt_id": "original"}}
        before = old * 4300 + json.dumps(latest).encode() + bytes([10])
        self.assertGreater(len(before), 8 * 1024 * 1024)
        repository.path.write_bytes(before)
        health = repository.history_health()
        self.assertTrue(health["complete"])
        self.assertEqual(len(before), health["scanned_bytes"])
        self.assertEqual(latest, repository.get(40))
        with self.assertRaises(WakeAttemptConflict):
            repository.begin_attempt([40], "new-child")
        self.assertEqual(before, repository.path.read_bytes())
        self.assertEqual([40], repository.reconcile_child_exit("old-child", verified_exit=True))
        self.assertEqual("interrupted_uncertain", repository.get(40)["status"])
        self.assertEqual("original", repository.get(40)["data"]["attempt_id"])

    def test_unreadable_history_raises_busy_and_writes_nothing(self):
        repository, _ = self.repository()
        repository.transition(1, "queued")
        before = repository.path.read_bytes()
        original_open = Path.open

        class BrokenRead(io.BytesIO):
            def readline(self, *args):
                raise PermissionError("fixture read failure")

        for phase in ("open", "read"):
            def unavailable(path, mode="r", *args, **kwargs):
                if path == repository.path and mode == "rb":
                    if phase == "open":
                        raise PermissionError("fixture open failure")
                    return BrokenRead(before)
                return original_open(path, mode, *args, **kwargs)
            with self.subTest(phase=phase), mock.patch.object(Path, "open", unavailable):
                self.assertEqual("unknown", repository.history_health()["coverage"])
                operations = (repository._items, lambda: repository.get(1), repository.list_latest,
                              lambda: repository.begin_attempt([1], "child"),
                              lambda: repository.transition(1, "submitted"))
                for operation in operations:
                    with self.assertRaises(StatusHistoryUnreadable) as caught:
                        operation()
                    self.assertIsInstance(caught.exception, ChannelFileLockBusy)
                    self.assertEqual(errno.EIO, caught.exception.errno)
            self.assertEqual(before, repository.path.read_bytes())
        self.assertEqual("queued", repository.get(1)["status"])

    def test_oversized_history_compacts_in_place_without_archive(self):
        repository, _ = self.repository()
        old = json.dumps({"request_id": 1, "status": "queued", "data": {"padding": "x" * 2000}}).encode() + bytes([10])
        repository.path.write_bytes(old * 2200 + b"invalid payload" + bytes([10]))
        self.assertGreater(repository.path.stat().st_size, 4 * 1024 * 1024)
        logs = []
        repository.log = lambda *args: logs.append(args)
        attempt = repository.begin_attempt([1], "child")
        rows = [json.loads(raw) for raw in repository.path.read_bytes().splitlines()]
        self.assertEqual(1, len(rows))
        self.assertEqual("submitted", rows[0]["status"])
        self.assertEqual(attempt, rows[0]["data"]["attempt_id"])
        self.assertTrue(repository.history_health()["complete"])
        self.assertTrue(any("history_compacted" in text for _, text in logs))
        self.assertEqual({repository.path.name, repository.path.name + ".lock"},
                         {path.name for path in repository.path.parent.iterdir()})

    def test_compaction_keeps_latest_row_at_or_above_request_floor(self):
        repository, _ = self.repository(request_floor=lambda: 5)
        rows = [{"request_id": 1, "status": "replied"},
                {"request_id": 2, "status": "queued"},
                {"request_id": 4, "status": "failed"},
                {"request_id": 5, "status": "queued"},
                {"request_id": 5, "status": "replied", "data": {"attempt_id": "old-reply"}},
                {"request_id": 6, "status": "submitted", "data": {"attempt_id": "owner"}},
                {"request_id": 7, "status": "interrupted_uncertain"}]
        repository.path.write_bytes(b"invalid" + bytes([10]) + bytes(5) + b"".join(
            json.dumps(row).encode() + bytes([10]) for row in rows))
        logs = []
        repository.log = lambda *args: logs.append(args)
        with mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_COMPACT_BYTES", 1):
            repository.transition(8, "queued")
        compacted = [json.loads(raw) for raw in repository.path.read_bytes().splitlines()]
        self.assertEqual([5, 6, 7, 8], [row["request_id"] for row in compacted])
        self.assertEqual("replied", repository.get(5)["status"])
        self.assertEqual("old-reply", repository.get(5)["data"]["attempt_id"])
        self.assertEqual("owner", repository.get(6)["data"]["attempt_id"])
        for request_id in (5, 6, 7):
            with self.assertRaises(WakeAttemptConflict):
                repository.begin_attempt([request_id], "new")
        for request_id in (1, 2, 4):
            self.assertIsNone(repository.get(request_id))
        self.assertTrue(repository.history_health()["complete"])
        self.assertTrue(any("history_compacted kept=4" in text for _, text in logs))
        self.assertEqual({repository.path.name, repository.path.name + ".lock"},
                         {path.name for path in repository.path.parent.iterdir()})

    def test_compaction_default_floor_keeps_every_request(self):
        repository, _ = self.repository()
        rows = [{"request_id": 1, "status": "replied"}, {"request_id": 2, "status": "queued"},
                {"request_id": 2, "status": "skipped"}]
        repository.path.write_bytes(b"".join(json.dumps(row).encode() + bytes([10]) for row in rows))
        with mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_COMPACT_BYTES", 1):
            repository.transition(3, "queued")
        compacted = [json.loads(raw) for raw in repository.path.read_bytes().splitlines()]
        self.assertEqual([(1, "replied"), (2, "skipped"), (3, "queued")],
                         [(row["request_id"], row["status"]) for row in compacted])

    def test_compaction_failure_keeps_append_defers_retry_and_removes_temporary(self):
        for failing in ("replace", "fsync"):
            with self.subTest(failing=failing):
                repository, _ = self.repository()
                repository.transition(1, "queued")
                fsync = __import__("os").fsync
                calls = []

                def fail_temporary_fsync(descriptor):
                    calls.append(descriptor)
                    if len(calls) == 2:
                        raise OSError("fixture fsync failure")
                    return fsync(descriptor)

                target = ("ciel_runtime_support.runtime_input_status.os.replace" if failing == "replace"
                          else "ciel_runtime_support.runtime_input_status.os.fsync")
                effect = {"side_effect": PermissionError} if failing == "replace" else {"side_effect": fail_temporary_fsync}
                logs = []
                repository.log = lambda *args: logs.append(args)
                compact = mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_COMPACT_BYTES", 1)
                with compact, mock.patch(target, **effect):
                    attempt = repository.begin_attempt([1], "child")
                    repository.transition(2, "queued")
                self.assertEqual(1, sum("compact_deferred" in text for _, text in logs))
                self.assertEqual(attempt, repository.get(1)["data"]["attempt_id"])
                self.assertEqual("queued", repository.get(2)["status"])
                self.assertEqual(3, len(repository.path.read_bytes().splitlines()))
                self.assertEqual({repository.path.name, repository.path.name + ".lock"},
                                 {path.name for path in repository.path.parent.iterdir()})


    def test_compaction_waits_for_file_to_double_after_success(self):
        repository, _ = self.repository()
        rows = [{"request_id": request_id, "status": "queued"} for request_id in range(1, 21)]
        repository.path.write_bytes(b"".join(json.dumps(row).encode() + bytes([10]) for row in rows))
        logs = []
        repository.log = lambda *args: logs.append(args)
        with mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_COMPACT_BYTES", 1):
            repository.transition(1, "skipped")
            compacted = repository.path.stat().st_size
            self.assertEqual(20, len(repository.path.read_bytes().splitlines()))
            self.assertEqual(1, sum("history_compacted" in text for _, text in logs))
            request_id = 21
            appends = 0
            while sum("history_compacted" in text for _, text in logs) == 1 and appends < 1000:
                # Below twice the compacted size every append stays in place.
                self.assertLessEqual(repository.path.stat().st_size, 2 * compacted)
                repository.transition(request_id, "queued")
                request_id += 1
                appends += 1
        self.assertGreater(appends, 1)
        self.assertEqual(2, sum("history_compacted" in text for _, text in logs))
        self.assertFalse(any("compact_deferred" in text for _, text in logs))
        self.assertEqual(list(range(1, request_id)),
                         [json.loads(raw)["request_id"] for raw in repository.path.read_bytes().splitlines()])
        self.assertEqual({repository.path.name, repository.path.name + ".lock"},
                         {path.name for path in repository.path.parent.iterdir()})


if __name__ == "__main__":
    unittest.main()
