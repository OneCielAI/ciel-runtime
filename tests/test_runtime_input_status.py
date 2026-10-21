import json
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository, WakeHistoryUnavailable


class RuntimeInputStatusRepositoryTests(unittest.TestCase):
    def repository(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        events = []
        repository = RuntimeInputStatusRepository(
            Path(temp_dir.name) / "runtime-input-status.jsonl",
            lambda **event: events.append(event),
            lambda _level, _message: None,
            threading.RLock(),
        )
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

    def test_nul_candidates_are_payload_free_and_never_admitted(self):
        for status in ("queued", "submitted", "replied"):
            with self.subTest(status=status):
                repository, events = self.repository()
                repository.transition(1, "queued", reason="private payload")
                prefix = repository.path.read_bytes()
                row = {"request_id": 40, "status": status,
                       "updated_at": "2026-10-20T09:00:00+00:00", "updated_at_epoch": 1792486800,
                       "reason": "private reason", "data": {"prompt": "private message"}}
                repository.path.write_bytes(prefix + bytes(17) + json.dumps(row).encode() + bytes([10]))
                before = repository.path.read_bytes()
                files = set(repository.path.parent.iterdir())
                events.clear()
                health = repository.history_health()
                self.assertFalse(health["complete"])
                self.assertEqual("partial", health["coverage"])
                self.assertEqual([{"line_number": 2, "byte_offset": len(prefix),
                                  "failure_category": "leading_nul_prefix_candidate",
                                  "remainder_request_id": 40, "remainder_status": status}],
                                 health["diagnostics"])
                self.assertNotIn("private", json.dumps(health))
                self.assertIsNone(repository.get(40))
                self.assertEqual([1], [r["request_id"] for r in repository.list_latest()])
                with self.assertRaises(WakeHistoryUnavailable):
                    repository.begin_attempt([1, 40], "child")
                with self.assertRaises(WakeHistoryUnavailable):
                    repository.transition(40, "queued")
                with self.assertRaises(WakeHistoryUnavailable):
                    repository.reconcile_child_exit("child", verified_exit=True)
                self.assertEqual(before, repository.path.read_bytes())
                self.assertEqual(files, set(repository.path.parent.iterdir()))
                self.assertEqual([], events)

    def test_noncanonical_nul_remainders_remain_unknown(self):
        rows = [
            {"request_id": 40, "status": "queued"},
            {"request_id": 40, "status": "queued", "updated_at": "bad", "updated_at_epoch": 1},
            {"request_id": 40, "status": "queued", "updated_at": "2026-10-20T00:00:00+00:00",
             "updated_at_epoch": 1, "unknown": "private"},
            {"request_id": 40, "status": "queued", "updated_at": "2026-10-20T00:00:00+00:00",
             "updated_at_epoch": float("nan")},
        ]
        for row in rows:
            with self.subTest(row=row):
                repository, events = self.repository()
                damaged = bytes(3) + json.dumps(row).encode() + bytes([10])
                repository.path.write_bytes(damaged)
                diagnostic = repository.history_health()["diagnostics"][0]
                self.assertEqual("nul_byte", diagnostic["failure_category"])
                self.assertNotIn("remainder_request_id", diagnostic)
                with self.assertRaises(WakeHistoryUnavailable):
                    repository.begin_attempt([40], "child")
                self.assertEqual(damaged, repository.path.read_bytes())
                self.assertEqual([], events)

    def test_tolerant_read_reports_partial_coverage_without_payload(self):
        repository, _ = self.repository()
        repository.transition(1, "queued")
        with repository.path.open("ab") as stream:
            stream.write(b"private invalid payload" + bytes([10]))
        logs = []
        repository.log = lambda *args: logs.append(args)
        self.assertEqual("queued", repository.get(1)["status"])
        self.assertEqual(1, len(repository.list_latest()))
        self.assertEqual(2, len(logs))
        self.assertTrue(all("coverage=partial" in message for _, message in logs))
        self.assertTrue(all("private" not in message for _, message in logs))

    def test_legacy_records_keep_ownership_without_schema_rewrite(self):
        repository, _ = self.repository()
        rows = [{"request_id": "40", "status": "submitted", "legacy_field": True},
                {"request_id": 41, "status": "queued"}]
        before = bytes([10]).join(json.dumps(row).encode() for row in rows) + bytes([10])
        repository.path.write_bytes(before)
        self.assertTrue(repository.history_health()["complete"])
        self.assertEqual("submitted", repository.get(40)["status"])
        from ciel_runtime_support.runtime_input_status import WakeAttemptConflict
        with self.assertRaises(WakeAttemptConflict):
            repository.begin_attempt([40, 41], "new")
        self.assertEqual(before, repository.path.read_bytes())

    def test_health_diagnostics_and_scanning_are_bounded(self):
        repository, _ = self.repository()
        damaged = (b"not-json" + bytes([10])) * 50
        repository.path.write_bytes(damaged)
        health = repository.history_health()
        self.assertEqual(50, health["failure_count"])
        self.assertEqual(32, len(health["diagnostics"]))
        self.assertTrue(health["diagnostics_truncated"])
        with mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_BYTE_BUDGET", 20):
            health = repository.history_health()
        self.assertLessEqual(health["scanned_bytes"], 20)
        self.assertFalse(health["complete"])
        self.assertEqual("byte_budget_exceeded", health["diagnostics"][-1]["failure_category"])
        with mock.patch("ciel_runtime_support.runtime_input_status._HISTORY_LINE_BUDGET", 4):
            health = repository.history_health()
        self.assertLessEqual(health["scanned_bytes"], 5)
        self.assertEqual("line_budget_exceeded", health["diagnostics"][0]["failure_category"])
        self.assertEqual(damaged, repository.path.read_bytes())

    def test_io_and_concurrent_read_failures_are_explicit_unknowns(self):
        repository, _ = self.repository()
        repository.transition(1, "queued")
        before = repository.path.read_bytes()
        with mock.patch.object(Path, "open", side_effect=PermissionError):
            self.assertEqual("unknown", repository.history_health()["coverage"])
            with self.assertRaises(WakeHistoryUnavailable):
                repository._items(strict=True)
        import os
        original_fstat = os.fstat
        calls = 0
        def growing_stat(fd):
            nonlocal calls
            calls += 1
            result = original_fstat(fd)
            if calls == 2:
                from types import SimpleNamespace
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino,
                                       st_size=result.st_size + 1, st_mtime_ns=result.st_mtime_ns)
            return result
        with mock.patch("ciel_runtime_support.runtime_input_status.os.fstat", growing_stat):
            health = repository.history_health()
            self.assertFalse(health["complete"])
            self.assertEqual("changed_during_read", health["diagnostics"][0]["failure_category"])
        original_stat = Path.stat
        def disappear(path, *args, **kwargs):
            if path == repository.path:
                raise FileNotFoundError
            return original_stat(path, *args, **kwargs)
        with mock.patch.object(Path, "stat", disappear):
            health = repository.history_health()
            self.assertFalse(health["complete"])
            self.assertEqual("changed_during_read", health["diagnostics"][0]["failure_category"])
        self.assertEqual(before, repository.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
