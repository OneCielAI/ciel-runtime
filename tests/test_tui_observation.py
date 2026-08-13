import io
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from ciel_runtime_support.request_body_policy import RouterRequestBodyPolicy
from ciel_runtime_support.router_http import (
    RouterHttpCore,
    RouterHttpErrors,
    RouterHttpGetEndpoints,
    RouterHttpHandler,
    RouterHttpPostEndpoints,
    RouterHttpPresentation,
    RouterHttpServices,
)
from ciel_runtime_support.tui_observation import (
    ObservedResponseWriter,
    TuiObservationBus,
    TuiObservationHttpAdapter,
    TuiObservationHttpPorts,
    observe_runtime_response,
    publish_latest_input,
)


class TuiObservationBusTests(unittest.TestCase):
    def test_turn_lifecycle_and_filters_are_cursor_based(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        bus.begin(
            request_id="turn-1",
            protocol="anthropic_messages",
            path="/v1/messages",
            provider="ollama",
            model="model-a",
        )
        bus.publish(
            kind="output.text.delta",
            request_id="turn-1",
            role="assistant",
            text="hello",
        )
        bus.finish("turn-1", status=200)

        self.assertEqual(0, bus.status()["active_count"])
        self.assertEqual(
            ["turn.started", "turn.completed"],
            [event["kind"] for event in bus.recent(kind="turn")],
        )
        self.assertEqual(
            ["hello"],
            [event["text"] for event in bus.recent(after=1, request_id="turn-1") if event["text"]],
        )

    def test_latest_input_excludes_replayed_history_and_tool_result_body(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        body = {
            "messages": [
                {"role": "user", "content": "old secret transcript"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "latest question"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "sensitive file contents",
                        },
                    ],
                },
            ]
        }
        publish_latest_input(
            bus, body, request_id="turn-1", provider="test", model="model-a"
        )
        encoded = json.dumps(bus.recent())

        self.assertIn("latest question", encoded)
        self.assertNotIn("old secret transcript", encoded)
        self.assertNotIn("sensitive file contents", encoded)
        result = next(event for event in bus.recent() if event["kind"] == "tool.result")
        self.assertEqual(len("sensitive file contents"), result["data"]["content_chars"])

    def test_top_level_responses_function_output_is_metadata_only(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        publish_latest_input(
            bus,
            {
                "input": [
                    {"role": "user", "content": "old"},
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "private result",
                    },
                ]
            },
            request_id="turn-1",
            provider="codex",
            model="model-a",
        )

        encoded = json.dumps(bus.recent())
        self.assertNotIn("private result", encoded)
        self.assertEqual("tool.result", bus.recent()[0]["kind"])


class ObservedResponseWriterTests(unittest.TestCase):
    def test_anthropic_sse_captures_visible_text_without_thinking_or_arguments(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        target = io.BytesIO()
        writer = ObservedResponseWriter(
            target, bus, request_id="turn-1", provider="test", model="model-a"
        )
        chunks = [
            b"HTTP/1.0 200 OK\r\ncontent-type: text/event-stream\r\n\r\n",
            b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hidden"}}\n\n',
            b'data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"tool-1","name":"Read"}}\n\n',
            b'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\\"secret\\":\\"value\\"}"}}\n\n',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"vis',
            b'ible"}}\n\n',
        ]
        for chunk in chunks:
            writer.write(chunk)
        writer.finish()
        encoded = json.dumps(bus.recent())

        self.assertEqual(b"".join(chunks), target.getvalue())
        self.assertIn("visible", encoded)
        self.assertIn('"name": "Read"', encoded)
        self.assertNotIn("hidden", encoded)
        self.assertNotIn("secret", encoded)
        argument_event = next(
            event for event in bus.recent() if event["kind"] == "tool.arguments.delta"
        )
        self.assertGreater(argument_event["data"]["chars"], 0)

    def test_responses_sse_captures_text_and_tool_name(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        writer = ObservedResponseWriter(
            io.BytesIO(), bus, request_id="turn-1", provider="codex", model="model-a"
        )
        writer.write(b"HTTP/1.0 200 OK\r\ncontent-type: text/event-stream\r\n\r\n")
        writer.write(
            b'event: response.output_item.added\ndata: {"type":"response.output_item.added","item":{"type":"function_call","name":"shell","call_id":"call-1"}}\n\n'
        )
        writer.write(
            b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"done"}\n\n'
        )
        writer.finish()

        self.assertEqual(
            ["tool.started", "output.text.delta"],
            [event["kind"] for event in bus.recent()],
        )

    def test_json_response_is_observed_when_context_finishes(self):
        bus = TuiObservationBus(enabled=True, capacity=100)

        class Handler:
            def __init__(self):
                self.wfile = io.BytesIO()
                self._ciel_runtime_response_status = 200

        handler = Handler()
        original = handler.wfile
        body = {"messages": [{"role": "user", "content": "question"}]}
        with observe_runtime_response(
            handler, "/v1/messages", "test", "model-a", body, bus
        ):
            handler.wfile.write(
                b'HTTP/1.0 200 OK\r\ncontent-type: application/json\r\n\r\n'
                b'{"content":[{"type":"text","text":"answer"}]}'
            )

        self.assertIs(original, handler.wfile)
        self.assertEqual(
            ["turn.started", "input.text", "output.text.delta", "turn.completed"],
            [event["kind"] for event in bus.recent()],
        )


class TuiObservationHttpAdapterTests(unittest.TestCase):
    def test_status_and_recent_endpoints(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        bus.publish(kind="input.text", request_id="turn-1", role="user", text="hello")
        writes = []
        adapter = self._adapter(bus, write_json=lambda handler, body: writes.append(body))

        self.assertTrue(adapter.handle_get(object(), "/ca/tui/status", {}))
        self.assertTrue(
            adapter.handle_get(object(), "/ca/tui/recent", {"after": ["0"], "limit": ["5"]})
        )
        self.assertTrue(writes[0]["enabled"])
        self.assertEqual("hello", writes[1]["events"][0]["text"])

    def test_stream_writes_sse_and_treats_disconnect_as_success(self):
        bus = TuiObservationBus(enabled=True, capacity=100)
        first = bus.publish(
            kind="input.text", request_id="turn-1", role="user", text="already seen"
        )
        bus.publish(kind="input.text", request_id="turn-1", role="user", text="new text")

        class DisconnectingFile(io.BytesIO):
            def flush(self):
                raise BrokenPipeError()

        class Handler:
            def __init__(self):
                self.wfile = DisconnectingFile()
                self.status = None
                self.headers = {"last-event-id": str(first["id"])}

            def send_response(self, status):
                self.status = status

            def send_header(self, _name, _value):
                pass

            def end_headers(self):
                pass

        handler = Handler()
        self.assertTrue(self._adapter(bus).handle_get(handler, "/ca/tui/stream", {}))
        self.assertEqual(200, handler.status)
        self.assertIn(b"event: tui", handler.wfile.getvalue())
        self.assertNotIn(b"already seen", handler.wfile.getvalue())
        self.assertIn(b"new text", handler.wfile.getvalue())

    @staticmethod
    def _adapter(bus, *, write_json=lambda *_args: None):
        return TuiObservationHttpAdapter(
            TuiObservationHttpPorts(
                bus=bus,
                write_json=write_json,
                write_text=lambda *_args, **_kwargs: None,
                log=lambda *_args: None,
            )
        )


class TuiObservationRouterIntegrationTests(unittest.TestCase):
    def test_routed_sse_turn_is_available_from_recent_api(self):
        bus = TuiObservationBus(enabled=True, capacity=100)

        def write_json(handler, value, status=200):
            payload = json.dumps(value).encode()
            handler.send_response(status)
            handler.send_header("content-type", "application/json")
            handler.send_header("content-length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        adapter = TuiObservationHttpAdapter(
            TuiObservationHttpPorts(
                bus=bus,
                write_json=write_json,
                write_text=lambda *_args, **_kwargs: None,
                log=lambda *_args: None,
            )
        )

        def runtime_post(handler, _cfg, _provider, _pcfg, path, _body):
            if path != "/v1/messages":
                return False
            handler.send_response(200)
            handler.send_header("content-type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(
                b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"remote visible"}}\n\n'
            )
            return True

        def false_get(*_args, **_kwargs):
            return False

        def false_post(*_args, **_kwargs):
            return False

        services = RouterHttpServices(
            core=RouterHttpCore(
                load_config=lambda: {},
                reject_external=lambda *_args: False,
                get_current_provider=lambda _cfg: ("test", {"current_model": "model-a"}),
                parse_json_body=lambda raw: json.loads(raw),
                is_client_disconnect=lambda _error: False,
                log=lambda *_args: None,
                observe_runtime=lambda handler, path, provider, model, body: observe_runtime_response(
                    handler, path, provider, model, body, bus
                ),
                request_body_policy=RouterRequestBodyPolicy({}),
            ),
            get=RouterHttpGetEndpoints(
                tui=adapter.handle_get,
                events=false_get,
                llm_config=false_get,
                channel_mcp=false_get,
                web=false_get,
                speech=false_get,
                chat=false_get,
                plan=false_get,
                runtime=false_get,
            ),
            post=RouterHttpPostEndpoints(
                speech=false_post,
                llm_config=false_post,
                channel_mcp=false_post,
                chat=false_post,
                plan=false_post,
                runtime=runtime_post,
            ),
            presentation=RouterHttpPresentation(
                home_html=lambda *_args: "",
                health_payload=lambda *_args: {},
                write_text=lambda *_args, **_kwargs: None,
                write_json=write_json,
                list_models=lambda *_args: [],
                resolve_model=lambda *_args: "",
                model_object=lambda *_args: {},
            ),
            errors=RouterHttpErrors(
                write_responses_error=lambda *_args, **_kwargs: None,
                try_write_json=lambda *_args, **_kwargs: True,
            ),
        )

        class Handler(RouterHttpHandler):
            services_factory = staticmethod(lambda: services)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            request = urllib.request.Request(
                base + "/v1/messages",
                data=json.dumps(
                    {
                        "model": "model-a",
                        "messages": [{"role": "user", "content": "remote question"}],
                    }
                ).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertIn(b"remote visible", response.read())
            with urllib.request.urlopen(base + "/ca/tui/recent", timeout=2) as response:
                observed = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(
            ["turn.started", "input.text", "output.text.delta", "turn.completed"],
            [event["kind"] for event in observed["events"]],
        )
        self.assertEqual("remote question", observed["events"][1]["text"])
        self.assertEqual("remote visible", observed["events"][2]["text"])


if __name__ == "__main__":
    unittest.main()
