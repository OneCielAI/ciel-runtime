import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.transcript_delta_delivery import (
    TranscriptDeliveryPorts,
    TranscriptDeltaDeliveryService,
)


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return b"{}"


class TranscriptDeltaDeliveryTests(unittest.TestCase):
    def service(self, root, transcript, config, logs):
        launch_offset = transcript.stat().st_size
        return TranscriptDeltaDeliveryService(
            root / "cursors.json",
            "workspace-1",
            TranscriptDeliveryPorts(
                load_config=lambda: config,
                latest_transcript=lambda: transcript,
                scope=lambda: {
                    "runtime": "codex",
                    "session_id": "session-1",
                    "turn_scan_path": transcript,
                    "turn_scan_offset": launch_offset,
                },
                log=lambda level, message: logs.append((level, message)),
            ),
        )

    def test_tail_mode_sends_only_complete_records_appended_after_registration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            transcript.write_text('{"old":1}\n', encoding="utf-8")
            config = {
                "transcript_events": {
                    "enabled": True,
                    "url": "https://memory.example/transcripts",
                    "start_mode": "tail",
                }
            }
            logs = []
            service = self.service(root, transcript, config, logs)
            requests = []

            def send(request, timeout):
                requests.append((request, timeout))
                return _Response()

            with mock.patch(
                "ciel_runtime_support.transcript_delta_delivery.urllib.request.urlopen",
                side_effect=send,
            ):
                self.assertFalse(service.poll_once())
                with transcript.open("ab") as stream:
                    stream.write(b'{"new":2}\n{"partial":')
                self.assertTrue(service.poll_once())
                self.assertFalse(service.poll_once())
                with transcript.open("ab") as stream:
                    stream.write(b'3}\n')
                self.assertTrue(service.poll_once())

            self.assertEqual(2, len(requests))
            first = json.loads(requests[0][0].data)
            second = json.loads(requests[1][0].data)
            self.assertEqual("ai.oneciel.ciel-runtime.transcript.delta", first["type"])
            self.assertEqual('{"new":2}\n', first["data"]["content"])
            self.assertEqual('{"partial":3}\n', second["data"]["content"])
            self.assertEqual(first["data"]["end_offset"], second["data"]["start_offset"])
            self.assertEqual(first["id"], requests[0][0].headers["Idempotency-key"])
            self.assertTrue(any("transcript_delta_delivered" in message for _, message in logs))

    def test_failed_delivery_keeps_offset_and_retries_the_same_event_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            transcript.write_text('{"one":1}\n', encoding="utf-8")
            config = {
                "transcript_events": {
                    "enabled": True,
                    "url": "https://memory.example/transcripts",
                    "start_mode": "beginning",
                    "authorization": "Bearer {TRANSCRIPT_TEST_TOKEN}",
                }
            }
            logs = []
            service = self.service(root, transcript, config, logs)
            attempted = []

            def fail_then_succeed(request, timeout):
                attempted.append(json.loads(request.data))
                if len(attempted) == 1:
                    raise OSError("offline")
                self.assertEqual("Bearer secret", request.headers["Authorization"])
                return _Response()

            with (
                mock.patch.dict(os.environ, {"TRANSCRIPT_TEST_TOKEN": "secret"}),
                mock.patch(
                    "ciel_runtime_support.transcript_delta_delivery.urllib.request.urlopen",
                    side_effect=fail_then_succeed,
                ),
            ):
                self.assertFalse(service.poll_once())
                self.assertTrue(service.poll_once())

            self.assertEqual(attempted[0]["id"], attempted[1]["id"])
            self.assertEqual(0, attempted[1]["data"]["start_offset"])

    def test_missing_authorization_environment_value_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            transcript.write_text('{"one":1}\n', encoding="utf-8")
            config = {
                "transcript_events": {
                    "enabled": True,
                    "url": "https://memory.example/transcripts",
                    "start_mode": "beginning",
                    "authorization": "Bearer {TRANSCRIPT_MISSING_TOKEN}",
                }
            }
            logs = []
            service = self.service(root, transcript, config, logs)
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch(
                    "ciel_runtime_support.transcript_delta_delivery.urllib.request.urlopen"
                ) as send,
            ):
                self.assertFalse(service.poll_once())

            send.assert_not_called()
            self.assertTrue(any("TRANSCRIPT_MISSING_TOKEN" in message for _, message in logs))

    def test_truncation_restarts_at_zero_and_marks_rotated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            transcript.write_text('{"first":"long record"}\n', encoding="utf-8")
            config = {
                "transcript_events": {
                    "enabled": True,
                    "url": "https://memory.example/transcripts",
                    "start_mode": "tail",
                }
            }
            service = self.service(root, transcript, config, [])
            with mock.patch(
                "ciel_runtime_support.transcript_delta_delivery.urllib.request.urlopen",
                return_value=_Response(),
            ) as send:
                self.assertFalse(service.poll_once())
                transcript.write_text('{"new":1}\n', encoding="utf-8")
                self.assertTrue(service.poll_once())

            event = json.loads(send.call_args.args[0].data)
            self.assertTrue(event["data"]["rotated"])
            self.assertEqual(0, event["data"]["start_offset"])


if __name__ == "__main__":
    unittest.main()
