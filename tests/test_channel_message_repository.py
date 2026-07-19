import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_message_repository import ChannelMessageRepository


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


if __name__ == "__main__":
    unittest.main()
