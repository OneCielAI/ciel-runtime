import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path

from ciel_runtime_support.external_event_receiver import (
    CLOUDEVENTS_SSE_ACCEPT,
    EventReceiverSecretVault,
    ExternalEventReceiverService,
    cloud_event_cursor,
    json_pointer_value,
    parse_sse_frames,
    sse_reconnect_url,
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


class _SseResponse:
    def __init__(self, lines):
        self.lines = [part for line in lines for part in line.splitlines(keepends=True)]
        self.headers = {"content-type": "text/event-stream; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.lines)


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

    def test_json_pointer_cursor_and_query_projection_are_provider_neutral(self):
        value = {"data": {"stream/id": "42-7", "items": [{"cursor": 9}]}}
        self.assertEqual("42-7", json_pointer_value(value, "/data/stream~1id"))
        self.assertEqual(9, json_pointer_value(value, "/data/items/0/cursor"))
        self.assertIsNone(json_pointer_value(value, "/data/missing"))
        raw = json.dumps({
            "specversion": "1.0",
            "id": "event-id",
            "source": "/source",
            "type": "example.event",
            "data": {"cursor": "17-2"},
        })
        self.assertEqual("17-2", cloud_event_cursor(raw, "/data/cursor"))
        self.assertEqual(
            "https://events.example/stream?format=cloudevents&after=17-2",
            sse_reconnect_url("https://events.example/stream?format=cloudevents", "after", "17-2"),
        )

    def test_sse_structured_mode_negotiation_and_configurable_cursor_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"external_event_receivers": {"workspace": {}}}
            requests = []
            admitted = []
            cursor_path = root / "cursors.json"
            cursor_path.write_text(json.dumps({"default": "10-4"}), encoding="utf-8")
            service = None

            def save_config(value):
                copied = json.loads(json.dumps(value))
                config.clear()
                config.update(copied)

            def submit_event(raw, **meta):
                admitted.append((raw, meta))
                service._stop.set()
                return {"id": 1}

            event_text = json.dumps({
                "specversion": "1.0",
                "id": "11-1",
                "source": "/agents/example",
                "type": "example.message",
                "data": {"stream_id": "11-1", "payload": {"text": "hello"}},
            }, separators=(",", ":"))

            def urlopen(request, timeout):
                requests.append((request, timeout))
                return _SseResponse([f"event: example.message\ndata: {event_text}\n\n".encode()])

            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=save_config,
                write_json=lambda *_args: None,
                submit_event=submit_event,
                vault=EventReceiverSecretVault(root / "events.vault.json"),
                workspace_key="workspace",
                log=lambda *_args: None,
                cursor_path=cursor_path,
                urlopen=urlopen,
            )
            service.save_receiver("default", {
                "enabled": True,
                "transport": "sse",
                "url": "https://events.example/stream?format=cloudevents",
                "authorization": "secret-token",
                "cursor_json_pointer": "/data/stream_id",
                "cursor_query_parameter": "after",
            })
            service._run_sse("default")

            request, timeout = requests[0]
            self.assertEqual(90, timeout)
            self.assertEqual(
                "https://events.example/stream?format=cloudevents&after=10-4",
                request.full_url,
            )
            self.assertEqual(CLOUDEVENTS_SSE_ACCEPT, request.get_header("Accept"))
            self.assertEqual("Bearer secret-token", request.get_header("Authorization"))
            self.assertIsNone(request.get_header("Last-event-id"))
            self.assertEqual(event_text, admitted[0][0])
            self.assertEqual("11-1", json.loads(cursor_path.read_text(encoding="utf-8"))["default"])

    def test_standard_sse_cursor_uses_last_event_id_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"external_event_receivers": {"workspace": {}}}
            requests = []
            cursor_path = root / "cursors.json"
            cursor_path.write_text(json.dumps({"default": "event-9"}), encoding="utf-8")
            service = None

            def save_config(value):
                copied = json.loads(json.dumps(value))
                config.clear()
                config.update(copied)

            def submit_event(raw, **meta):
                service._stop.set()
                return {"id": 1}

            raw = '{"specversion":"1.0","id":"event-10","source":"/x","type":"demo"}'

            def urlopen(request, timeout):
                requests.append(request)
                return _SseResponse([f"id: event-10\ndata: {raw}\n\n".encode()])

            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=save_config,
                write_json=lambda *_args: None,
                submit_event=submit_event,
                vault=EventReceiverSecretVault(root / "events.vault.json"),
                workspace_key="workspace",
                log=lambda *_args: None,
                cursor_path=cursor_path,
                urlopen=urlopen,
            )
            service.save_receiver("default", {
                "enabled": True,
                "transport": "sse",
                "url": "https://events.example/stream",
            })
            service._run_sse("default")
            self.assertEqual("event-9", requests[0].get_header("Last-event-id"))

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

    def test_legacy_port_scoped_config_is_copied_to_stable_workspace_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "external_event_receivers": {
                    "8804-workspace": {
                        "default": {
                            "enabled": True,
                            "transport": "sse",
                            "url": "https://events.example/stream",
                        }
                    }
                }
            }

            def save_config(value):
                copied = json.loads(json.dumps(value))
                config.clear()
                config.update(copied)

            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=save_config,
                write_json=lambda *_args: None,
                submit_event=lambda *_args, **_kwargs: {"id": 1},
                vault=EventReceiverSecretVault(Path(directory) / "events.vault.json"),
                workspace_key="workspace",
                legacy_workspace_keys=("8804-workspace",),
                log=lambda *_args: None,
            )

            self.assertTrue(service.receiver_configs()["default"]["enabled"])
            self.assertTrue(service.migrate_legacy_config())
            self.assertFalse(service.migrate_legacy_config())
            service.save_receiver("default", {"enabled": False, "transport": "webhook"})

            self.assertIn("workspace", config["external_event_receivers"])
            self.assertFalse(config["external_event_receivers"]["workspace"]["default"]["enabled"])


if __name__ == "__main__":
    unittest.main()
