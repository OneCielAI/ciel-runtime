import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.channel_session_repository import ChannelSessionRepository


class ChannelSessionRepositoryTests(unittest.TestCase):
    def test_record_replaces_same_url_and_forget_removes_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sessions.json"
            repository = ChannelSessionRepository(
                path=path,
                default_protocol_version="2025-03-26",
                log=mock.Mock(),
                process_id=lambda: 42,
                timestamp=lambda: "2026-07-18T00:00:00",
            )

            repository.record("first", "https://mcp.test", "old", "")
            repository.record("second", "https://mcp.test", "new", "2026-01-01")
            records = repository.records()

            self.assertEqual(1, len(records))
            self.assertEqual("new", records[0]["session_id"])
            self.assertEqual(42, records[0]["pid"])
            repository.forget("new")
            self.assertEqual([], repository.records())

    def test_invalid_json_and_permission_failure_are_observable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sessions.json"
            path.write_text("not-json", encoding="utf-8")
            log = mock.Mock()
            repository = ChannelSessionRepository(
                path=path,
                default_protocol_version="v1",
                log=log,
                chmod=mock.Mock(side_effect=OSError("denied")),
            )

            self.assertEqual([], repository.records())
            self.assertIn("record_read_failed", log.call_args.args[1])
            repository.write([{"session_id": "session"}])
            self.assertIn("record_chmod_failed", log.call_args.args[1])
            self.assertEqual("session", json.loads(path.read_text(encoding="utf-8"))["sessions"][0]["session_id"])


if __name__ == "__main__":
    unittest.main()
