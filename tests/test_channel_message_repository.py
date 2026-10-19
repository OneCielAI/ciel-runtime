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

    def test_rotation_preserves_monotonic_message_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.jsonl"
            path.write_text(json.dumps({"id": 9, "message": "old"}) + "\n", encoding="utf-8")
            ports = ChannelMessageAppendPorts(
                threading.Condition(),
                contextlib.nullcontext,
                lambda _message: None,
                lambda value: [str(value)] if value else [],
            )
            repository = ChannelMessageRepository(
                path=path,
                log=lambda _level, _message: None,
                max_bytes=1,
            )

            saved = repository.append({"text": "new"}, ports)

            self.assertEqual(10, saved["id"])
            self.assertTrue(path.with_suffix(".jsonl.1").exists())


if __name__ == "__main__":
    unittest.main()
