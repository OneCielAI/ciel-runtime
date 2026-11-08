import base64
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from pathlib import Path

from ciel_runtime_support.external_event_receiver import (
    CLOUDEVENTS_SSE_ACCEPT,
    EventReceiverSecretVault,
    ExternalEventReceiverService,
    MAX_EVENT_BYTES,
    cloud_event_cursor,
    json_pointer_value,
    parse_sse_frames,
    environment_reference_name,
    resolve_environment_reference,
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
    def test_webhook_receiver_rejects_max_event_bytes_plus_one(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "external_event_receivers": {
                    "workspace": {
                        "default": {
                            "enabled": True,
                            "transport": "webhook",
                        }
                    }
                }
            }

            def write_json(handler, value, status=200):
                handler.response = (status, value)

            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=lambda _value: None,
                write_json=write_json,
                submit_event=lambda *_args, **_kwargs: self.fail(
                    "an oversized event must not be admitted"
                ),
                vault=EventReceiverSecretVault(Path(directory) / "events.vault.json"),
                workspace_key="workspace",
                log=lambda *_args: None,
            )
            handler = _Handler()

            self.assertTrue(
                service.handle_raw_post(
                    handler,
                    "/ca/events/webhooks/default",
                    b"x" * (MAX_EVENT_BYTES + 1),
                )
            )
            self.assertEqual(
                (413, {"ok": False, "error": "event_too_large"}),
                handler.response,
            )

    def test_exact_environment_reference_syntaxes_are_cross_platform(self):
        environment = {"AI_NET_AUTHORIZATION": "secret-token"}
        for reference in (
            "%AI_NET_AUTHORIZATION%",
            "${AI_NET_AUTHORIZATION}",
            "{AI_NET_AUTHORIZATION}",
        ):
            with self.subTest(reference=reference):
                self.assertEqual(
                    "AI_NET_AUTHORIZATION", environment_reference_name(reference)
                )
                self.assertEqual(
                    "secret-token",
                    resolve_environment_reference(reference, environment),
                )
        self.assertEqual(
            "Bearer literal-token",
            resolve_environment_reference("Bearer literal-token", environment),
        )

    def test_missing_environment_reference_is_explicit(self):
        with self.assertRaisesRegex(RuntimeError, "AI_NET_AUTHORIZATION is not set"):
            resolve_environment_reference("%AI_NET_AUTHORIZATION%", {})

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
            with mock.patch.dict(
                os.environ,
                {
                    "TEST_EVENT_SSE_URL": "https://events.example/stream?format=cloudevents",
                    "TEST_EVENT_SSE_AUTH": "secret-token",
                },
            ):
                service.save_receiver("default", {
                    "enabled": True,
                    "transport": "sse",
                    "url": "%TEST_EVENT_SSE_URL%",
                    "authorization": "${TEST_EVENT_SSE_AUTH}",
                    "cursor_json_pointer": "/data/stream_id",
                    "cursor_query_parameter": "after",
                    "input_transport": "session_socket",
                })
                public = service.list_public()[0]
                self.assertEqual(
                    {"name": "TEST_EVENT_SSE_URL", "available": True},
                    public["environment_references"]["url"],
                )
                self.assertEqual(
                    {"name": "TEST_EVENT_SSE_AUTH", "available": True},
                    public["environment_references"]["authorization"],
                )
                self.assertNotIn("secret-token", json.dumps(public))
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
            self.assertEqual("session_socket", admitted[0][1]["input_transport"])
            self.assertEqual("11-1", json.loads(cursor_path.read_text(encoding="utf-8"))["default"])

    def test_invalid_frame_with_transport_id_becomes_side_text_and_canary_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            config = {'external_event_receivers': {'workspace': {}}}
            gateway = RuntimeInputGateway(lambda row: rows.append({'id': len(rows) + 1, **row}) or rows[-1],
                                          load_side_settings=lambda: {'enabled': True})
            service = None
            canary = json.dumps({'specversion': '1.0', 'id': 'canary', 'source': 'walkie',
                                  'type': 'allowed', 'data': {'stream_id': 'canary'}})
            response = _SseResponse([b'id: bad-1\ndata: not-json\n\n',
                                     ('id: canary\ndata: ' + canary + '\n\n').encode()])
            def submit(raw, **meta):
                result = gateway.submit_external_event(raw, **meta)
                if len(rows) == 2:
                    service._stop.set()
                return result
            service = ExternalEventReceiverService(
                load_config=lambda: config, save_config=lambda value: config.update(value),
                write_json=lambda *_: None, submit_event=submit,
                vault=EventReceiverSecretVault(root / 'vault.json'), workspace_key='workspace',
                cursor_path=root / 'cursor.json', urlopen=lambda *_args, **_kwargs: response,
                log=lambda *_: None)
            service.save_receiver('default', {'enabled': True, 'transport': 'sse', 'url': 'https://events.example/stream',
                                               'event_types': ['allowed'], 'cursor_query_parameter': 'after'})
            service._run_sse('default')
            self.assertEqual(2, len(rows))
            self.assertEqual('not-json', rows[0]['message'])
            self.assertEqual('raw_text', rows[0]['meta']['side_intake_fallback'])
            self.assertTrue(rows[0]['meta']['side_intake_only'])
            self.assertEqual(canary, rows[1]['message'])
            self.assertEqual('canary', json.loads((root / 'cursor.json').read_text())['default'])

    def _run_frames(self, root, frames, submit, *, event_types=None, logs=None):
        config = {'external_event_receivers': {'workspace': {}}}
        response = _SseResponse([frame.encode() for frame in frames])
        service = ExternalEventReceiverService(
            load_config=lambda: config, save_config=lambda value: config.update(value),
            write_json=lambda *_: None, submit_event=lambda raw, **meta: submit(service, raw, meta),
            vault=EventReceiverSecretVault(root / 'vault.json'), workspace_key='workspace',
            cursor_path=root / 'cursor.json', urlopen=lambda *_args, **_kwargs: response,
            log=lambda level, message: (logs if logs is not None else []).append((level, message)))
        service.save_receiver('default', {'enabled': True, 'transport': 'sse', 'url': 'https://events.example/stream',
                                           'event_types': event_types or [], 'cursor_query_parameter': 'after'})
        service._run_sse('default')
        cursor = root / 'cursor.json'
        return json.loads(cursor.read_text())['default'] if cursor.exists() else None

    def test_prody_shape_event_and_next_event_both_advance_the_cursor(self):
        # Prody 2026-11-08 transport id 13492: 16,055 characters pinned the stream.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            gateway = RuntimeInputGateway(lambda row: rows.append({'id': len(rows) + 1, **row}) or rows[-1],
                                          load_side_settings=lambda: {'enabled': True})
            big = json.dumps({'specversion': '1.0', 'id': '13492', 'source': 'walkie', 'type': 'allowed',
                              'data': {'text': '한' * 15967}}, ensure_ascii=False, separators=(',', ':'))
            nxt = json.dumps({'specversion': '1.0', 'id': '13493', 'source': 'walkie', 'type': 'allowed'})
            def submit(service, raw, meta):
                result = gateway.submit_external_event(raw, **meta)
                if len(rows) == 2:
                    service._stop.set()
                return result
            cursor = self._run_frames(root, ['id: 13492\ndata: ' + big + '\n\n',
                                             'id: 13493\ndata: ' + nxt + '\n\n'], submit)
            self.assertEqual(16055, len(big))
            self.assertEqual([big, nxt], [row['message'] for row in rows])
            self.assertTrue(all(row['meta']['side_intake_only'] for row in rows))
            self.assertEqual('13493', cursor)

    def test_filtered_type_is_dead_lettered_without_content_and_canary_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, logs = [], []
            blocked = json.dumps({'specversion': '1.0', 'id': 'f-1', 'source': 'walkie', 'type': 'other',
                                  'data': {'text': 'private-canary'}})
            canary = json.dumps({'specversion': '1.0', 'id': 'c-1', 'source': 'walkie', 'type': 'allowed'})
            def submit(service, raw, meta):
                rows.append(raw)
                service._stop.set()
                return {'id': 1}
            cursor = self._run_frames(root, ['id: f-1\ndata: ' + blocked + '\n\n',
                                             'id: c-1\ndata: ' + canary + '\n\n'], submit,
                                      event_types=['allowed'], logs=logs)
            self.assertEqual([canary], rows)
            self.assertEqual('c-1', cursor)
            letter = (root / 'external-event-dead-letters.jsonl').read_text(encoding='utf-8')
            self.assertEqual('other', json.loads(letter)['event_type'])
            self.assertNotIn('private-canary', letter)
            self.assertNotIn('private-canary', json.dumps(logs))

    def test_dead_letter_storage_failure_keeps_cursor_before_filtered_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = json.dumps({'specversion': '1.0', 'id': 'f-1', 'source': 'walkie', 'type': 'other'})
            service = None
            config = {'external_event_receivers': {'workspace': {}}}
            response = _SseResponse([('id: f-1\ndata: ' + event + '\n\n').encode()])
            service = ExternalEventReceiverService(
                load_config=lambda: config, save_config=lambda value: config.update(value),
                write_json=lambda *_: None, submit_event=lambda *_args, **_kwargs: self.fail('filtered row queued'),
                vault=EventReceiverSecretVault(root / 'vault.json'), workspace_key='workspace',
                cursor_path=root / 'missing' / 'cursor.json', urlopen=lambda *_args, **_kwargs: response,
                log=lambda *_: service._stop.set())
            service.save_receiver('default', {'enabled': True, 'transport': 'sse',
                                               'url': 'https://events.example/stream', 'event_types': ['allowed']})
            with mock.patch('pathlib.Path.mkdir', side_effect=OSError('dead-letter disk full')):
                service._run_sse('default')
            self.assertFalse((root / 'missing' / 'cursor.json').exists())

    def test_storage_failure_keeps_cursor_before_the_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = json.dumps({'specversion': '1.0', 'id': 'e-1', 'source': 'walkie', 'type': 'allowed'})
            def submit(service, raw, meta):
                service._stop.set()
                raise OSError('queue disk full')
            self.assertIsNone(self._run_frames(root, ['id: e-1\ndata: ' + event + '\n\n'], submit))

    def test_empty_frame_with_id_is_dead_lettered_and_canary_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            canary = json.dumps({'specversion': '1.0', 'id': 'c-1', 'source': 'walkie', 'type': 'allowed'})
            def submit(service, raw, meta):
                rows.append(raw)
                service._stop.set()
                return {'id': 1}
            cursor = self._run_frames(root, ['id: empty-1\ndata:  \n\n', 'id: c-1\ndata: ' + canary + '\n\n'], submit)
            self.assertEqual([canary], rows)
            self.assertEqual('c-1', cursor)
            letter = json.loads((root / 'external-event-dead-letters.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(('empty_frame', 'empty-1'), (letter['reason'], letter['transport_event_id']))

    def test_invalid_frame_with_side_off_is_an_ordinary_row_and_canary_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            gateway = RuntimeInputGateway(lambda row: rows.append({'id': len(rows) + 1, **row}) or rows[-1],
                                          load_side_settings=lambda: {'enabled': False})
            canary = json.dumps({'specversion': '1.0', 'id': 'c-1', 'source': 'walkie', 'type': 'allowed'})
            def submit(service, raw, meta):
                result = gateway.submit_external_event(raw, **meta)
                if len(rows) == 2:
                    service._stop.set()
                return result
            cursor = self._run_frames(root, ['id: bad-1\ndata: not-json\n\n', 'id: c-1\ndata: ' + canary + '\n\n'], submit)
            self.assertEqual(['not-json', canary], [row['message'] for row in rows])
            self.assertFalse(any(row['meta'].get('side_intake_only') for row in rows))
            self.assertEqual('c-1', cursor)

    def test_invalid_frame_redelivery_keeps_one_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keys = []
            def submit(service, raw, meta):
                keys.append(meta['event_id'])
                if len(keys) == 2:
                    service._stop.set()
                return {'id': len(keys)}
            self._run_frames(root, ['id: bad-1\ndata: not-json\n\n', 'id: bad-1\ndata: not-json\n\n'], submit)
            self.assertEqual(1, len(set(keys)))
            self.assertTrue(keys[0].startswith('invalid:bad-1:'))

    def test_invalid_frame_without_transport_id_has_no_identity_for_fallback(self):
        self.assertEqual([('', 'not-json')], list(parse_sse_frames([b'data: not-json\n\n'])))
        with self.assertRaisesRegex(ValueError, 'must be JSON'):
            validate_cloud_event('not-json')

    def test_real_http_sse_reconnects_after_activity_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            delivered = threading.Event()
            release = threading.Event()
            requests = []
            errors = []
            event_text = '{"specversion":"1.0","id":"http-1","source":"/http","type":"demo"}'

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    requests.append(self.path)
                    number = len(requests)
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("connection", "close")
                    self.end_headers()
                    if number == 1:
                        self.wfile.write(b": heartbeat\n\n")
                    else:
                        self.wfile.write(f"id: http-1\ndata: {event_text}\n\n".encode())
                    self.wfile.flush()
                    release.wait(1.5)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            config = {"external_event_receivers": {"workspace": {}}}

            def submit(raw, **_meta):
                self.assertEqual(event_text, raw)
                delivered.set()
                return {"id": 1}

            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=lambda value: config.update(value),
                write_json=lambda *_args: None,
                submit_event=submit,
                vault=EventReceiverSecretVault(root / "events.vault.json"),
                workspace_key="workspace",
                log=lambda _level, text: errors.append(text),
                cursor_path=root / "cursors.json",
            )
            try:
                service.save_receiver("default", {
                    "enabled": True,
                    "transport": "sse",
                    "url": f"http://127.0.0.1:{server.server_port}/events",
                    "idle_timeout_seconds": 1,
                })
                service.start()
                first_thread = service._threads["default"]
                self.assertTrue(delivered.wait(8), f"requests={requests}, logs={errors}")
                self.assertEqual(2, len(requests))
                self.assertIs(first_thread, service._threads["default"])
                self.assertTrue(any("external_event_sse_disconnected receiver=default error=" in text for text in errors), errors)
            finally:
                service.stop()
                release.set()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)
            self.assertFalse(first_thread.is_alive())

    def test_stop_then_start_does_not_create_a_second_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered = threading.Event()
            release = threading.Event()
            replacement_entered = threading.Event()
            replacement_release = threading.Event()
            calls = []
            config = {"external_event_receivers": {"workspace": {}}}

            class BlockingResponse:
                headers = {"content-type": "text/event-stream"}

                def __init__(self, number):
                    self.number = number

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def __iter__(self):
                    if self.number == 1:
                        entered.set()
                        release.wait(10)
                    else:
                        replacement_entered.set()
                        replacement_release.wait(10)
                    return iter(())

            def urlopen(_request, timeout):
                calls.append(threading.get_ident())
                return BlockingResponse(len(calls))

            service = ExternalEventReceiverService(
                load_config=lambda: config,
                save_config=lambda value: config.update(value),
                write_json=lambda *_args: None,
                submit_event=lambda *_args, **_kwargs: {},
                vault=EventReceiverSecretVault(root / "events.vault.json"),
                workspace_key="workspace",
                log=lambda *_args: None,
                urlopen=urlopen,
            )
            try:
                service.save_receiver("default", {
                    "enabled": True,
                    "transport": "sse",
                    "url": "https://events.example/stream",
                })
                service.start()
                self.assertTrue(entered.wait(2))
                old_thread = service._threads["default"]
                old_worker = service._workers["default"]
                service.stop()
                self.assertTrue(old_thread.is_alive())
                service.start()
                self.assertIs(old_thread, service._threads["default"])
                self.assertIs(old_worker, service._workers["default"])
                self.assertTrue(old_worker.stop.is_set())
                self.assertEqual(1, len(calls))
                release.set()
                self.assertTrue(replacement_entered.wait(3))
                old_thread.join(timeout=1)
                self.assertFalse(old_thread.is_alive())
                self.assertEqual(2, len(calls))
                self.assertIsNot(old_worker, service._workers["default"])
            finally:
                release.set()
                replacement_release.set()
                service.stop()
            self.assertFalse(any(thread.is_alive() for thread in service._threads.values()))

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
