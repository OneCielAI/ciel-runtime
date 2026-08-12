import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path

from ciel_runtime_support.external_event_receiver import (
    EventReceiverSecretVault,
    ExternalEventReceiverService,
    parse_sse_frames,
    validate_cloud_event,
    verify_standard_webhook,
)
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway


class _Headers(dict):
    def get(self, key, default=None):
        lowered = key.lower()
        for name, value in self.items():
            if str(name).lower() == lowered:
                return value
        return default


class _Handler:
    def __init__(self, headers=None):
        self.headers = _Headers(headers or {})
        self.response = None


class ExternalEventReceiverTests(unittest.TestCase):
    def test_cloud_event_validation_preserves_original_text(self):
        raw = '{\n  "specversion": "1.0", "id": "evt-1", "source": "/tests", "type": "demo", "data": {"한글": true}\n}'
        projected = validate_cloud_event(raw)
        admitted = []
        saved = RuntimeInputGateway(lambda value: admitted.append(value) or {"id": 1, **value}).submit_external_event(
            raw,
            receiver_id="default",
            transport="webhook",
            event_id=projected["id"],
            event_type=projected["type"],
            event_source=projected["source"],
        )
        self.assertEqual(raw, saved["message"])
        self.assertEqual("private_runtime", saved["visibility"])
        self.assertEqual("external_event", saved["kind"])

    def test_standard_webhook_signature_is_over_exact_raw_bytes(self):
        raw = b'{"specversion":"1.0","id":"1","source":"/x","type":"x"}\n'
        key = b"test-secret-key"
        secret = "whsec_" + base64.b64encode(key).decode("ascii")
        timestamp = str(int(time.time()))
        webhook_id = "msg_123"
        signed = webhook_id.encode() + b"." + timestamp.encode() + b"." + raw
        signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
        headers = _Headers({
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": f"v1,{signature}",
        })
        self.assertEqual((webhook_id, timestamp), verify_standard_webhook(raw, headers, secret))
        with self.assertRaisesRegex(ValueError, "verification failed"):
            verify_standard_webhook(raw + b" ", headers, secret)

    def test_sse_parser_joins_data_lines_and_tracks_last_event_id(self):
        frames = list(parse_sse_frames([
            b"id: first\n",
            b"data: {\"a\":1,\n",
            b"data: \"b\":2}\n",
            b"\n",
            b": heartbeat\n",
            b"data: second\n",
            b"\n",
        ]))
        self.assertEqual([("first", '{"a":1,\n"b":2}'), ("first", "second")], frames)

    def test_webhook_admission_never_writes_a_public_chat_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"external_event_receivers": {"workspace": {}}}
            private_inputs = []
            public_transcript = []

            def write_json(handler, value, status=200):
                handler.response = (status, value)

            def save_config(value):
                copied = json.loads(json.dumps(value))
                config.clear()
                config.update(copied)

            vault = EventReceiverSecretVault(Path(directory) / "events.vault.json")
            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=save_config,
                write_json=write_json,
                submit_event=lambda raw, **meta: private_inputs.append((raw, meta)) or {"id": 1},
                vault=vault,
                workspace_key="workspace",
                log=lambda *_args: None,
            )
            key = b"receiver-key"
            secret = "whsec_" + base64.b64encode(key).decode("ascii")
            service.save_receiver("default", {"enabled": True, "transport": "webhook", "webhook_secret": secret})
            raw = json.dumps({"specversion": "1.0", "id": "evt", "source": "/test", "type": "demo"}, separators=(",", ":")).encode()
            timestamp = str(int(time.time()))
            webhook_id = "delivery-1"
            signed = webhook_id.encode() + b"." + timestamp.encode() + b"." + raw
            signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
            handler = _Handler({
                "webhook-id": webhook_id,
                "webhook-timestamp": timestamp,
                "webhook-signature": f"v1,{signature}",
            })
            self.assertTrue(service.handle_raw_post(handler, "/ca/events/webhooks/default", raw))
            self.assertEqual(202, handler.response[0])
            self.assertEqual(1, len(private_inputs))
            self.assertEqual(raw.decode(), private_inputs[0][0])
            self.assertEqual([], public_transcript)


if __name__ == "__main__":
    unittest.main()
