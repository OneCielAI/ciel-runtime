import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.transcript_delta_delivery import TranscriptDeltaDeliveryService, TranscriptDeliveryPorts


class LargeTranscriptRecordTests(unittest.TestCase):
    def make_service(self, root, path):
        return TranscriptDeltaDeliveryService(root / "cursor.json", "test", TranscriptDeliveryPorts(
            load_config=lambda: {"transcript_events": {"enabled": True, "url": "http://fixture.invalid/", "start_mode": "beginning"}},
            latest_transcript=lambda: path, scope=lambda: {"runtime": "codex", "session_id": "test"},
            log=lambda *_: None,
        ))

    def test_large_record_delivered_atomically_and_next_record_advances(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "transcript.jsonl"
            big = (json.dumps({"data": "x" * 1154200}) + "\n").encode()
            path.write_bytes(big + b'{"next":true}\n')
            service = self.make_service(root, path)
            events = []
            service._post = lambda settings, event: events.append(event) or True
            self.assertTrue(service.poll_once())
            self.assertEqual(len(big), events[0]["data"]["end_offset"])
            self.assertEqual(big.decode(), events[0]["data"]["content"])
            self.assertTrue(service.poll_once())
            self.assertEqual(len(big), events[1]["data"]["start_offset"])
            self.assertFalse(service.poll_once())

    def test_partial_large_record_waits_and_failed_delivery_retries_same_id(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "transcript.jsonl"
            path.write_bytes(b'{"data":"' + b'x' * 1154200)
            service = self.make_service(root, path)
            self.assertFalse(service.poll_once())
            with path.open('ab') as f:
                f.write(b'"}\n')
            events = []
            service._post = lambda settings, event: events.append(event) or len(events) > 1
            self.assertFalse(service.poll_once())
            self.assertTrue(service.poll_once())
            self.assertEqual(events[0]["id"], events[1]["id"])


if __name__ == "__main__":
    unittest.main()
