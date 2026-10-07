import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.transcript_delta_delivery import (
    TranscriptDeliveryPorts,
    TranscriptDeltaDeliveryService,
)
from ciel_runtime_support.transcript_public_projection import content_filter, public_records


def _jsonl(*records):
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _channel_line(ticket, body="secret customer text"):
    event = {"specversion": "1.0", "data": {"activity_context": ticket, "body": body}}
    return "[ciel-runtime external channel message] channel=external:default text=" + json.dumps(json.dumps(event))


class PublicProjectionTests(unittest.TestCase):
    def test_codex_rollout_keeps_only_public_parts(self):
        projected = public_records(
            _jsonl(
                {"type": "session_meta", "payload": {"cwd": "C:/secret"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "please deploy with password hunter2"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": _channel_line("wkp_abc")}]}},
                {"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "gAAAA"}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": "{\"cmd\":\"cat .env\"}"}},
                {"type": "response_item", "payload": {"type": "function_call_output", "output": "TOKEN=abc"}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "rm -rf /"}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "Checking the logs."}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": "Final private answer"}]}},
                {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "Final private answer"}},
            )
        )
        rows = [json.loads(line) for line in projected.splitlines()]
        self.assertEqual("", rows[0]["payload"]["content"][0]["text"])
        ticket_text = rows[1]["payload"]["content"][0]["text"]
        self.assertIn("wkp_abc", ticket_text)
        self.assertNotIn("secret customer text", ticket_text)
        self.assertEqual(["exec_command", "running"], [r["payload"]["name"] for r in rows if r["payload"].get("type") == "function_call"])
        self.assertEqual("Checking the logs.", rows[4]["payload"]["content"][0]["text"])
        self.assertEqual({"type": "event_msg", "payload": {"type": "task_complete"}}, rows[-1])
        for secret in ("hunter2", "gAAAA", "cat .env", "TOKEN=abc", "Final private answer", "C:/secret"):
            self.assertNotIn(secret, projected)

    def test_claude_transcript_ends_each_turn_once(self):
        projected = public_records(
            _jsonl(
                {"type": "user", "message": {"role": "user", "content": "fix it, key=sk-123"}},
                {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "thinking", "thinking": "private"}, {"type": "tool_use", "name": "Bash", "input": {"command": "cat secrets"}}]}},
                {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "secret output"}]}},
                {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}},
                {"type": "system", "subtype": "turn_duration"},
            )
        )
        rows = [json.loads(line) for line in projected.splitlines()]
        self.assertEqual(
            ["message", "function_call", "message", "task_complete"],
            [row["payload"]["type"] for row in rows],
        )
        for secret in ("sk-123", "private", "cat secrets", "secret output"):
            self.assertNotIn(secret, projected)

    def test_filter_setting_defaults_to_public_only(self):
        self.assertEqual("public_only", content_filter(None))
        self.assertEqual("public_only", content_filter("everything"))
        self.assertEqual("raw", content_filter(" RAW "))


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return b"{}"


class PublicOnlyDeliveryTests(unittest.TestCase):
    def test_default_delivery_sends_the_projection_and_skips_empty_batches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "rollout.jsonl"
            transcript.write_text("", encoding="utf-8")
            config = {"transcript_events": {"enabled": True, "url": "https://walkie.example/api/agent-transcripts", "start_mode": "beginning"}}
            service = TranscriptDeltaDeliveryService(
                root / "cursors.json",
                "workspace-1",
                TranscriptDeliveryPorts(
                    load_config=lambda: config,
                    latest_transcript=lambda: transcript,
                    scope=lambda: {"runtime": "codex", "session_id": "s-1"},
                    log=lambda *_args: None,
                ),
            )
            requests = []
            with mock.patch(
                "ciel_runtime_support.transcript_delta_delivery.urllib.request.urlopen",
                side_effect=lambda request, timeout: requests.append(request) or _Response(),
            ):
                transcript.write_text(_jsonl({"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "gAAAA"}}), encoding="utf-8")
                self.assertFalse(service.poll_once())  # nothing public: no request, cursor moves
                with transcript.open("a", encoding="utf-8") as stream:
                    stream.write(_jsonl({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": "{\"cmd\":\"secret\"}"}}))
                self.assertTrue(service.poll_once())
            self.assertEqual(1, len(requests))
            data = json.loads(requests[0].data)["data"]
            self.assertEqual("public_only", data["content_filter"])
            self.assertEqual('{"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}}\n', data["content"])
            self.assertNotIn("secret", requests[0].data.decode("utf-8"))
            self.assertNotIn("gAAAA", requests[0].data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
