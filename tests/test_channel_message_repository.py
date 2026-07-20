import contextlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from ciel_runtime_support.channel_message_repository import ChannelMessageAppendPorts, ChannelMessageRepository


class ChannelMessageRepositoryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
