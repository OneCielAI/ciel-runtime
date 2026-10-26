import contextlib
import errno
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from ciel_runtime_support.channel_message_repository import (
    ChannelFileLockBusy,
    ChannelMessageAppendPorts,
    ChannelMessageRepository,
    exclusive_file_lock,
)


class ChannelMessageRepositoryTests(unittest.TestCase):
    def test_exclusive_file_lock_creates_parent_and_lock_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "messages.jsonl"

            with exclusive_file_lock(path):
                self.assertTrue(path.with_name("messages.jsonl.lock").exists())

            self.assertTrue(path.parent.exists())

    def test_windows_acquisition_contention_is_typed_with_errno(self):
        windows = Mock(LK_LOCK=1, LK_UNLCK=0)
        for code in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            with self.subTest(errno=code), tempfile.TemporaryDirectory() as tmp:
                failure = OSError(code, "lock busy")
                windows.locking.reset_mock(side_effect=True)
                windows.locking.side_effect = failure
                path = Path(tmp) / "messages.jsonl"
                with (
                    patch("ciel_runtime_support.channel_message_repository.os.name", "nt"),
                    patch.dict("sys.modules", msvcrt=windows),
                ):
                    with self.assertRaises(ChannelFileLockBusy) as caught:
                        with exclusive_file_lock(path):
                            self.fail("lock body must not run before acquisition")
                self.assertEqual(code, caught.exception.errno)
                self.assertIn(str(path) + ".lock", str(caught.exception))
                self.assertIs(failure, caught.exception.__cause__)
                self.assertEqual(1, windows.locking.call_count)

    def test_windows_noncontention_acquisition_error_is_not_converted(self):
        windows = Mock(LK_LOCK=1, LK_UNLCK=0)
        failure = OSError(errno.EIO, "not contention")
        windows.locking.side_effect = failure
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            with (
                patch("ciel_runtime_support.channel_message_repository.os.name", "nt"),
                patch.dict("sys.modules", msvcrt=windows),
            ):
                with self.assertRaises(OSError) as caught:
                    with exclusive_file_lock(path):
                        self.fail("acquisition failed")
        self.assertIs(failure, caught.exception)
        self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)

    def test_windows_lock_body_error_is_not_converted(self):
        windows = Mock(LK_LOCK=1, LK_UNLCK=0)
        failure = OSError(errno.EACCES, "write denied")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            with (
                patch("ciel_runtime_support.channel_message_repository.os.name", "nt"),
                patch.dict("sys.modules", msvcrt=windows),
            ):
                with self.assertRaises(OSError) as caught:
                    with exclusive_file_lock(path):
                        raise failure
        self.assertIs(failure, caught.exception)
        self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)
        self.assertEqual([1, 0], [call.args[1] for call in windows.locking.call_args_list])

    def test_windows_unlock_error_is_not_converted(self):
        windows = Mock(LK_LOCK=1, LK_UNLCK=0)
        failure = OSError(errno.EACCES, "unlock denied")
        windows.locking.side_effect = [None, failure]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            with (
                patch("ciel_runtime_support.channel_message_repository.os.name", "nt"),
                patch.dict("sys.modules", msvcrt=windows),
            ):
                with self.assertRaises(OSError) as caught:
                    with exclusive_file_lock(path):
                        pass
        self.assertIs(failure, caught.exception)
        self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)
        self.assertEqual(2, windows.locking.call_count)

    def test_lock_file_open_error_is_not_converted(self):
        failure = OSError(errno.EACCES, "open denied")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            with patch.object(Path, "open", side_effect=failure):
                with self.assertRaises(OSError) as caught:
                    with exclusive_file_lock(path):
                        self.fail("open failed")
        self.assertIs(failure, caught.exception)
        self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)

    def repository(self, path: Path) -> ChannelMessageRepository:
        return ChannelMessageRepository(path=path, log=lambda _level, _message: None)

    def test_scans_ids_and_preserves_unknown_timestamp_as_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        {"id": 1, "time": "2020-01-01T00:00:00"},
                        {"id": 2, "time": "unknown"},
                        {"id": 3, "time": "2030-01-01T00:00:00"},
                    )
                ),
                encoding="utf-8",
            )
            repository = self.repository(path)
            self.assertEqual(3, repository.max_id())
            self.assertEqual(1, repository.max_id_before_epoch(1_700_000_000))

    def test_read_filters_room_alias_and_recipient(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "id": 4,
                        "channel": "transport",
                        "recipients": ["agent"],
                        "meta": {"room_id": "ops"},
                    }
                ),
                encoding="utf-8",
            )
            repository = self.repository(path)
            self.assertEqual([4], [item["id"] for item in repository.read(0, "ops", "agent")])
            self.assertEqual([], repository.read(0, "other", "agent"))
            self.assertEqual([], repository.read(0, "ops", "other"))

    def test_read_before_keeps_latest_rows_within_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_text(
                "\n".join(json.dumps({"id": value, "channel": "ops"}) for value in range(1, 6)),
                encoding="utf-8",
            )
            rows = self.repository(path).read_before(5, "ops", None, 2)
            self.assertEqual([3, 4], [item["id"] for item in rows])

    def test_append_resyncs_id_and_normalizes_payload_inside_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_text(json.dumps({"id": 9, "message": "old"}) + "\n", encoding="utf-8")
            ports = ChannelMessageAppendPorts(
                threading.Condition(),
                contextlib.nullcontext,
                lambda _message: None,
                lambda value: [str(value)] if value else [],
            )
            saved = self.repository(path).append(
                {"text": "new", "sender": "agent", "recipients": "web"},
                ports,
            )
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(10, saved["id"])
        self.assertEqual("new", saved["message"])
        self.assertEqual(["web"], saved["recipients"])
        self.assertEqual([9, 10], [row["id"] for row in rows])

    def ports(self) -> ChannelMessageAppendPorts:
        return ChannelMessageAppendPorts(
            threading.Condition(),
            contextlib.nullcontext,
            lambda _message: None,
            lambda value: [str(value)] if value else [],
        )

    def test_oversized_queue_keeps_newest_tail_without_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_bytes(b"".join(
                json.dumps({"id": i, "message": "old" * 20}).encode() + b"\n"
                for i in range(1, 21)
            ) + b'{"id":70,"broken":\n')
            repository = ChannelMessageRepository(path, lambda *_: None, max_bytes=1200)
            saved = repository.append({"text": "new"}, self.ports())
            self.assertEqual(71, saved["id"])
            self.assertLessEqual(path.stat().st_size, 600)
            rows = repository.read(0)
            self.assertEqual(71, rows[-1]["id"])
            self.assertGreater(rows[0]["id"], 1)
            self.assertEqual([path.name], [entry.name for entry in Path(tmp).iterdir()])
            self.assertNotIn("_ciel_runtime_generation", saved)
            self.assertEqual(72, repository.append({"text": "next"}, self.ports())["id"])

    def test_compaction_keeps_at_least_newest_row_above_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_bytes(b'{"id":9}\n')
            repository = ChannelMessageRepository(path, lambda *_: None, max_bytes=1)
            saved = repository.append({"text": "new"}, self.ports())
            self.assertEqual([10], [item["id"] for item in repository.read(0)])
            self.assertEqual(saved, repository.read(0)[0])
            self.assertEqual(11, repository.append({"text": "next"}, self.ports())["id"])

    def test_compaction_refuses_to_drop_a_higher_damaged_frontier(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            original = b'{"id":9}\n{"id":10}\n{"id":500,"torn":\n'
            path.write_bytes(original)
            log = Mock()
            ChannelMessageRepository(path, log, max_bytes=1)._compact_if_oversized()
            self.assertEqual(original, path.read_bytes())
            self.assertTrue(any("compaction refused" in call.args[1] for call in log.call_args_list))
            self.assertEqual(500, self.repository(path).max_id())

    def test_failed_replace_keeps_append_and_retries_compaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_bytes(b'{"id":9}\n')
            log = Mock()
            repository = ChannelMessageRepository(path, log, max_bytes=1)
            with patch("ciel_runtime_support.channel_message_repository.os.replace",
                       side_effect=PermissionError("sharing violation")):
                self.assertEqual(10, repository.append({"text": "new"}, self.ports())["id"])
            self.assertEqual([9, 10], [item["id"] for item in repository.read(0)])
            self.assertTrue(any("compaction deferred" in call.args[1] for call in log.call_args_list))
            self.assertEqual([path.name], [entry.name for entry in Path(tmp).iterdir()])
            self.assertEqual(11, repository.append({"text": "next"}, self.ports())["id"])
            self.assertEqual([11], [item["id"] for item in repository.read(0)])

    def test_min_id_uses_positive_decodable_live_rows_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            repository = self.repository(path)
            self.assertEqual(0, repository.min_id())
            path.write_bytes(b'{"id":9}\n{"id":-3}\n{"id":"4"}\n'
                             b'{"id":1,"broken":\n{"id":0}\n{"id":null}\n')
            path.with_suffix(".jsonl.1").write_bytes(b'{"id":1}\n')
            self.assertEqual(4, repository.min_id())
            path.write_bytes(b'{"id":0}\n{"id":null}\n{"id":-5}\n')
            self.assertEqual(0, repository.min_id())

    def test_legacy_floor_cache_is_shared_and_invalidates_on_file_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            rotated = path.with_suffix(".jsonl.1")
            rotated.write_bytes(b'{"id":90}\n')
            repository = self.repository(path)
            original = ChannelMessageRepository._scan
            scans = []
            def scan(instance, source, operation):
                if source == rotated:
                    scans.append(source)
                return original(instance, source, operation)
            with patch.object(ChannelMessageRepository, "_scan", scan):
                self.assertEqual(90, repository.max_id())
                self.assertEqual(90, self.repository(path).max_id())
                self.assertEqual(1, len(scans))
                rotated.write_bytes(b'{"id":150}\n')
                self.assertEqual(150, repository.max_id())
                self.assertEqual(2, len(scans))

    def test_damaged_rows_are_ignored_but_their_ids_are_never_reissued(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_bytes(
                b'{"id":3,"message":"ok"}\n'
                b'\x00\x00{"id":41,"message":"nul prefixed"}\n'
                b'{"id":"42","message":"\xff invalid utf8"}\n'
                b'{"id":43,"meta":{}}{"id":44,"message":"merged"}\n'
                b'{"id":45,"message":"truncated'
            )
            repository = self.repository(path)

            self.assertEqual([3], [item["id"] for item in repository.read(0)])
            self.assertEqual(45, repository.max_id())
            saved = repository.append({"text": "new"}, self.ports())

            self.assertEqual(46, saved["id"])
            self.assertEqual([3, 46], [item["id"] for item in repository.read(0)])
            self.assertEqual(
                [b'{"id":45,"message":"truncated', b'{"id":46,'],
                [line[:9] if line.startswith(b'{"id":46') else line
                 for line in path.read_bytes().splitlines()[-2:]],
            )

    def test_previously_rotated_and_reset_ids_stay_reserved_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            rotated = path.with_suffix(".jsonl.1")
            rotated.write_bytes(b'{"id":70}\n{"id":71,"message":"torn')
            repository = self.repository(path)
            self.assertEqual(71, repository.max_id())

            marker = path.with_name(path.name + ".recovery.json")
            marker.write_text(json.dumps({"id_floor": 90}), encoding="utf-8")
            before = {entry.name: entry.read_bytes() for entry in Path(tmp).iterdir()}
            self.assertEqual(89, repository.max_id())
            self.assertEqual(before, {entry.name: entry.read_bytes() for entry in Path(tmp).iterdir()})

            saved = repository.append({"text": "new"}, self.ports())
            self.assertEqual(90, saved["id"])
            self.assertEqual([90], [item["id"] for item in repository.read(0)])
            self.assertEqual(before[rotated.name], rotated.read_bytes())
            self.assertEqual(before[marker.name], marker.read_bytes())

            marker.write_bytes(b"\xff damaged")
            self.assertEqual(90, repository.max_id())
            self.assertEqual(91, repository.append({"text": "next"}, self.ports())["id"])


if __name__ == "__main__":
    unittest.main()
