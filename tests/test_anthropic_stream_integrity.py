"""Native Anthropic SSE relay must not report an incomplete upstream as a completed turn.

Every case drives the real ``ciel_runtime._rebatch_anthropic_sse_text`` with a
scripted upstream iterable and inspects the downstream SSE bytes plus the
channel-delivery guard attributes the router uses to commit channel cursors.
"""

import http.client
import json
import os
import ssl
import unittest
from io import BytesIO
from unittest import mock

import ciel_runtime


def sse(event: str, payload: dict) -> list[bytes]:
    return [
        f"event: {event}\n".encode("utf-8"),
        f"data: {json.dumps(payload)}\n".encode("utf-8"),
        b"\n",
    ]


def message_start() -> list[bytes]:
    return sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_upstream",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-test",
                "stop_reason": None,
                "usage": {"input_tokens": 3, "output_tokens": 0},
            },
        },
    )


def text_start(index: int = 0) -> list[bytes]:
    return sse(
        "content_block_start",
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
    )


def text_delta(text: str, index: int = 0) -> list[bytes]:
    return sse(
        "content_block_delta",
        {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}},
    )


def block_stop(index: int = 0) -> list[bytes]:
    return sse("content_block_stop", {"type": "content_block_stop", "index": index})


def message_delta(stop_reason: str = "end_turn") -> list[bytes]:
    return sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 7},
        },
    )


def message_stop() -> list[bytes]:
    return sse("message_stop", {"type": "message_stop"})


def upstream_error_event(error_type: str = "overloaded_error") -> list[bytes]:
    return sse("error", {"type": "error", "error": {"type": error_type, "message": "Overloaded"}})


def complete_text_stream(text: str = "complete answer") -> list[bytes]:
    return message_start() + text_start() + text_delta(text) + block_stop() + message_delta() + message_stop()


def upstream_read_errors() -> list[BaseException]:
    return [
        ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
        ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host"),
        TimeoutError("timed out"),
        http.client.IncompleteRead(b"event: content"),
    ]


class ScriptedUpstream:
    def __init__(self, lines: list[bytes], error: BaseException | None = None):
        self.lines = lines
        self.error = error
        self.closed = False

    def __iter__(self):
        yield from self.lines
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed = True


class GuardedHandler:
    """Handler with an active channel-delivery guard, as begin_pending_channel_delivery leaves it."""

    def __init__(self, wfile=None):
        self.wfile = wfile if wfile is not None else BytesIO()
        self._ciel_runtime_channel_delivery_guard = True
        self._ciel_runtime_channel_delivery_ok = False
        self._ciel_runtime_channel_delivery_reason = "pending"


def parse_sse(raw: bytes) -> list[tuple[str | None, object]]:
    events: list[tuple[str | None, object]] = []
    for frame in raw.decode("utf-8").split("\n\n"):
        name = None
        data: list[str] = []
        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)
        if name is None and not data:
            continue
        body = "\n".join(data)
        try:
            payload: object = json.loads(body)
        except ValueError:
            payload = body
        events.append((name, payload))
    return events


def delivered_text(events, index: int = 0) -> str:
    return "".join(
        payload["delta"].get("text", "")
        for name, payload in events
        if name == "content_block_delta"
        and isinstance(payload, dict)
        and payload.get("index") == index
        and payload.get("delta", {}).get("type") == "text_delta"
    )


class AnthropicStreamIntegrityTests(unittest.TestCase):
    def run_stream(
        self,
        upstream,
        *,
        provider: str = "anthropic",
        body: dict | None = None,
        word_chunking: bool = False,
        preserve_thinking: bool = True,
        normalize_tool_use: bool = False,
        keepalive: str = "0",
        handler: GuardedHandler | None = None,
    ):
        handler = handler or GuardedHandler()
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": keepalive}), mock.patch.object(
            ciel_runtime, "router_log"
        ) as router_log:
            ciel_runtime._rebatch_anthropic_sse_text(
                handler,
                upstream,
                "claude-test",
                word_chunking=word_chunking,
                source_body=body if body is not None else {"messages": [{"role": "user", "content": "hi"}]},
                preserve_thinking=preserve_thinking,
                normalize_tool_use=normalize_tool_use,
                provider=provider,
            )
        raw = handler.wfile.getvalue() if isinstance(handler.wfile, BytesIO) else b""
        logs = [(str(call.args[0]), str(call.args[1])) for call in router_log.call_args_list if len(call.args) > 1]
        return handler, parse_sse(raw), logs

    def test_error_shape_records_guard_reason_without_private_strings(self):
        error = {"type": "error", "PRIVATE-FIELD": "PRIVATE-VALUE",
                 "error": {"type": "api_error", "message": "PRIVATE-MESSAGE", "code": 504}}
        upstream = ScriptedUpstream(sse("error", error))
        upstream.headers = {"content-type": "text/event-stream; PRIVATE-HEADER"}
        upstream._ciel_preoutput_diagnostic = {"state": "committed", "reason": "unrecognized_frame"}
        _handler, events, logs = self.run_stream(upstream, provider="cielairouter")
        shape_logs = [msg for _, msg in logs if msg.startswith("anthropic_sse_error_shape ")]
        self.assertEqual(len(shape_logs), 1)
        shape = json.loads(shape_logs[0].split(" ", 1)[1])
        self.assertEqual(shape["payload"]["error"]["fields"]["code"], "number")
        self.assertEqual(shape["response"]["preoutput"]["reason"], "unrecognized_frame")
        self.assertNotIn("PRIVATE", shape_logs[0])
        self.assertEqual(events, [("error", error)])

    def assert_wire_contract(self, events):
        """At most one terminal; nothing after it; block events only for open blocks."""

        names = [name for name, _payload in events]
        self.assertLessEqual(names.count("message_stop"), 1, names)
        self.assertLessEqual(names.count("message_delta"), 1, names)
        self.assertLessEqual(names.count("error"), 1, names)
        if "message_stop" in names:
            self.assertEqual("message_stop", names[-1], names)
            self.assertNotIn("error", names)
        if "error" in names:
            self.assertEqual("error", names[-1], names)
        started: set[int] = set()
        stopped: set[int] = set()
        for name, payload in events:
            if name not in {"content_block_start", "content_block_delta", "content_block_stop"}:
                continue
            index = payload["index"]
            if name == "content_block_start":
                self.assertNotIn(index, started, names)
                started.add(index)
                continue
            self.assertIn(index, started, f"{name} for unstarted block {index}: {names}")
            self.assertNotIn(index, stopped, f"{name} for closed block {index}: {names}")
            if name == "content_block_stop":
                stopped.add(index)

    def assert_failed_delivery(self, handler, reason_prefix: str):
        self.assertFalse(handler._ciel_runtime_channel_delivery_ok)
        self.assertTrue(
            str(handler._ciel_runtime_channel_delivery_reason).startswith(reason_prefix),
            handler._ciel_runtime_channel_delivery_reason,
        )

    def assert_single_api_error(self, events):
        errors = [payload for name, payload in events if name == "error"]
        self.assertEqual(1, len(errors), [name for name, _ in events])
        self.assertEqual("error", errors[0]["type"])
        self.assertEqual("api_error", errors[0]["error"]["type"])
        return errors[0]

    # --- upstream read failures before message_stop -----------------------

    def test_upstream_read_failure_mid_text_reports_error_not_end_turn(self):
        for keepalive in ("0", "15"):
            for error in upstream_read_errors():
                with self.subTest(keepalive=keepalive, error=type(error).__name__):
                    lines = message_start() + text_start() + text_delta("partial answer")
                    upstream = ScriptedUpstream(lines, error)
                    handler, events, _logs = self.run_stream(upstream, keepalive=keepalive)
                    names = [name for name, _ in events]

                    self.assert_wire_contract(events)
                    self.assertEqual("partial answer", delivered_text(events))
                    self.assertNotIn("message_delta", names)
                    self.assertNotIn("message_stop", names)
                    self.assertNotIn("content_block_stop", names)
                    error_event = self.assert_single_api_error(events)
                    self.assertIn(type(error).__name__, error_event["error"]["message"])
                    self.assert_failed_delivery(handler, "anthropic_stream_upstream_error:")
                    self.assertTrue(upstream.closed)

    def test_upstream_read_failure_before_message_start_emits_only_error(self):
        handler, events, _logs = self.run_stream(ScriptedUpstream([], ConnectionResetError(10054, "reset")))
        names = [name for name, _ in events]

        self.assertEqual(["error"], names)
        self.assert_single_api_error(events)
        self.assertNotIn("Upstream stream error", json.dumps([payload for _, payload in events]))
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_error:")

    def test_upstream_read_failure_after_message_delta_does_not_duplicate_or_fabricate_stop(self):
        lines = message_start() + text_start() + text_delta("answer") + block_stop() + message_delta("end_turn")
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines, ssl.SSLEOFError(8, "EOF")))
        names = [name for name, _ in events]

        self.assert_wire_contract(events)
        self.assertEqual("answer", delivered_text(events))
        self.assertEqual(1, names.count("message_delta"))
        self.assertEqual("end_turn", next(p for n, p in events if n == "message_delta")["delta"]["stop_reason"])
        self.assertNotIn("message_stop", names)
        self.assertLess(names.index("message_delta"), names.index("error"))
        self.assert_single_api_error(events)
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_error:")

    def test_open_tool_use_is_never_closed_after_upstream_failure(self):
        tool_start = sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_cut", "name": "Bash", "input": {}},
            },
        )
        partial = sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"command": "rm -rf /tmp/scr'},
            },
        )
        body = {
            "messages": [{"role": "user", "content": "clean up"}],
            "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
        }
        cases = (
            ("passthrough", "anthropic", False),
            ("buffered", "deepseek", True),
        )
        for label, provider, normalize in cases:
            with self.subTest(label):
                lines = message_start() + tool_start + partial
                handler, events, _logs = self.run_stream(
                    ScriptedUpstream(lines, ConnectionResetError(10054, "reset")),
                    provider=provider,
                    body=body,
                    normalize_tool_use=normalize,
                )
                names = [name for name, _ in events]

                self.assert_wire_contract(events)
                self.assertNotIn("content_block_stop", names)
                self.assertNotIn("message_delta", names)
                self.assertNotIn("message_stop", names)
                self.assert_single_api_error(events)
                self.assert_failed_delivery(handler, "anthropic_stream_upstream_error:")

    def test_word_chunked_text_received_before_failure_is_flushed_before_error(self):
        lines = message_start() + text_start() + text_delta("hello wor")
        _handler, events, _logs = self.run_stream(
            ScriptedUpstream(lines, TimeoutError("timed out")), word_chunking=True
        )

        self.assert_wire_contract(events)
        self.assertEqual("hello wor", delivered_text(events))
        self.assertEqual("error", events[-1][0])

    def test_text_held_by_the_artifact_filter_is_released_before_error(self):
        body = {
            "messages": [{"role": "user", "content": "check"}],
            "tools": [{"name": "Workflow", "input_schema": {"type": "object"}}],
        }
        partial = "The deployment check found three issues: first, the "
        for label, error in (("read_failure", ConnectionResetError(10054, "reset")), ("clean_eof", None)):
            with self.subTest(label):
                lines = message_start() + text_start() + text_delta(partial)
                _handler, events, _logs = self.run_stream(ScriptedUpstream(lines, error), body=body)

                self.assert_wire_contract(events)
                self.assertEqual(partial, delivered_text(events))
                self.assertEqual("error", events[-1][0])

    def test_upstream_failure_does_not_run_hidden_only_recovery(self):
        body = {
            "model": "deepseek-test",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "continue implementation"}]},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_health", "name": "Bash", "input": {"command": "x"}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_health", "content": "healthy"}],
                },
            ],
            "tools": [{"name": name, "input_schema": {"type": "object"}} for name in ("TaskList", "Read", "Bash")],
        }
        thinking = (
            sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            )
            + sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}},
            )
            + block_stop(0)
        )
        handler, events, _logs = self.run_stream(
            ScriptedUpstream(message_start() + thinking, ConnectionResetError(10054, "reset")),
            provider="deepseek",
            body=body,
            preserve_thinking=False,
            normalize_tool_use=True,
        )

        self.assert_wire_contract(events)
        self.assertNotIn("TaskList", json.dumps([payload for _, payload in events]))
        self.assertNotIn("private", json.dumps([payload for _, payload in events]))
        self.assert_single_api_error(events)
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_error:")

    def test_message_delta_recovery_waits_for_message_stop(self):
        body = {
            "messages": [{"role": "user", "content": "continue"}],
            "tools": [{"name": "TaskList", "input_schema": {"type": "object"}}],
        }
        lines = message_start() + text_start() + text_delta("partial") + block_stop() + message_delta("tool_use")
        policy = mock.patch.object(ciel_runtime, "should_keep_work_alive_with_tasklist", return_value=True)
        synth = mock.patch.object(ciel_runtime, "should_synthesize_tasklist_for_provider", return_value=True)
        with policy, synth:
            interrupted = self.run_stream(ScriptedUpstream(lines, ConnectionResetError(10054, "reset")), provider="deepseek", body=body)
            completed = self.run_stream(ScriptedUpstream(lines + message_stop()), provider="deepseek", body=body)

        self.assertNotIn("TaskList", json.dumps([payload for _, payload in interrupted[1]]))
        self.assertEqual("error", interrupted[1][-1][0])
        self.assertIn("TaskList", json.dumps([payload for _, payload in completed[1]]))
        self.assertEqual("message_stop", completed[1][-1][0])

    def test_events_after_message_stop_are_not_relayed(self):
        lines = complete_text_stream() + text_start(1) + text_delta("late", 1)
        handler, events, logs = self.run_stream(ScriptedUpstream(lines))

        self.assert_wire_contract(events)
        self.assertEqual("message_stop", events[-1][0])
        self.assertNotIn("late", json.dumps(events))
        self.assertTrue(handler._ciel_runtime_channel_delivery_ok)
        self.assertTrue(any("anthropic_sse_event_after_message_stop_ignored" in message for _, message in logs))

    # --- clean EOF without message_stop -------------------------------------

    def test_clean_eof_mid_text_reports_incomplete_stream(self):
        lines = message_start() + text_start() + text_delta("cut short")
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines))
        names = [name for name, _ in events]

        self.assert_wire_contract(events)
        self.assertEqual("cut short", delivered_text(events))
        self.assertNotIn("content_block_stop", names)
        self.assertNotIn("message_stop", names)
        self.assert_single_api_error(events)
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_incomplete")

    def test_clean_eof_after_message_delta_does_not_fabricate_message_stop(self):
        lines = message_start() + text_start() + text_delta("answer") + block_stop() + message_delta("end_turn")
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines))
        names = [name for name, _ in events]

        self.assert_wire_contract(events)
        self.assertEqual(1, names.count("message_delta"))
        self.assertNotIn("message_stop", names)
        self.assert_single_api_error(events)
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_incomplete")

    def test_truncated_final_data_line_is_not_forwarded(self):
        lines = message_start() + text_start() + text_delta("kept") + [
            b"event: content_block_delta\n",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_del',
        ]
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines))

        self.assert_wire_contract(events)
        self.assertEqual("kept", delivered_text(events))
        self.assertFalse(any(isinstance(payload, str) for _name, payload in events), events)
        self.assert_single_api_error(events)
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_incomplete")

    def test_final_message_stop_without_trailing_blank_line_still_completes(self):
        lines = complete_text_stream()[:-1]
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines))

        self.assert_wire_contract(events)
        self.assertEqual("message_stop", events[-1][0])
        self.assertTrue(handler._ciel_runtime_channel_delivery_ok)

    # --- upstream error events ------------------------------------------------

    def test_upstream_error_event_is_forwarded_once_and_never_followed_by_a_terminal(self):
        cases = {
            "mid_text_then_eof": (message_start() + text_start() + text_delta("partial") + upstream_error_event(), None),
            "after_message_delta_then_eof": (
                message_start() + text_start() + text_delta("partial") + block_stop() + message_delta()
                + upstream_error_event(),
                None,
            ),
            "then_read_failure": (
                message_start() + text_start() + text_delta("partial") + upstream_error_event(),
                ssl.SSLEOFError(8, "EOF"),
            ),
        }
        for label, (lines, error) in cases.items():
            with self.subTest(label):
                handler, events, _logs = self.run_stream(ScriptedUpstream(lines, error))
                names = [name for name, _ in events]

                self.assert_wire_contract(events)
                self.assertEqual("partial", delivered_text(events))
                self.assertEqual(1, names.count("error"))
                self.assertEqual("overloaded_error", events[-1][1]["error"]["type"])
                self.assertNotIn("message_stop", names)
                self.assert_failed_delivery(handler, "anthropic_stream_upstream_error_event")

    def test_data_only_upstream_error_is_forwarded_as_an_error_event(self):
        lines = message_start() + [
            f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': 'boom'}})}\n".encode(),
            b"\n",
        ]
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines))

        self.assertEqual(("error", "api_error"), (events[-1][0], events[-1][1]["error"]["type"]))
        self.assertEqual(1, [name for name, _ in events].count("error"))
        self.assert_failed_delivery(handler, "anthropic_stream_upstream_error_event")

    # --- completed streams ---------------------------------------------------

    def test_trailing_transport_error_after_message_stop_keeps_the_completed_turn(self):
        for keepalive in ("0", "15"):
            for error in upstream_read_errors():
                with self.subTest(keepalive=keepalive, error=type(error).__name__):
                    handler, events, logs = self.run_stream(
                        ScriptedUpstream(complete_text_stream(), error), keepalive=keepalive
                    )
                    names = [name for name, _ in events]

                    self.assert_wire_contract(events)
                    self.assertEqual("complete answer", delivered_text(events))
                    self.assertEqual(["message_start", "content_block_start", "content_block_delta",
                                      "content_block_stop", "message_delta", "message_stop"], names)
                    self.assertTrue(handler._ciel_runtime_channel_delivery_ok)
                    self.assertFalse(any(level == "ERROR" for level, _message in logs), logs)

    def test_message_stop_received_without_blank_line_before_read_failure_completes(self):
        lines = complete_text_stream()[:-1]
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines, ssl.SSLEOFError(8, "EOF")))

        self.assert_wire_contract(events)
        self.assertEqual("message_stop", events[-1][0])
        self.assertTrue(handler._ciel_runtime_channel_delivery_ok)

    def test_complete_stream_preserves_thinking_signature_and_tool_use(self):
        lines = (
            message_start()
            + sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            )
            + sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "plan"}},
            )
            + sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-123"}},
            )
            + block_stop(0)
            + sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "id": "toolu_ok", "name": "Read", "input": {}},
                },
            )
            + sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"file_path": "a.txt"}'},
                },
            )
            + block_stop(1)
            + message_delta("tool_use")
            + message_stop()
        )
        body = {
            "messages": [{"role": "user", "content": "read a.txt"}],
            "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
        }
        handler, events, _logs = self.run_stream(ScriptedUpstream(lines), body=body)
        names = [name for name, _ in events]

        self.assert_wire_contract(events)
        payloads = json.dumps([payload for _, payload in events])
        self.assertIn("sig-123", payloads)
        self.assertIn('"toolu_ok"', payloads)
        self.assertIn("a.txt", payloads)
        self.assertEqual(2, names.count("content_block_stop"))
        self.assertEqual("tool_use", next(p for n, p in events if n == "message_delta")["delta"]["stop_reason"])
        self.assertEqual("message_stop", names[-1])
        self.assertNotIn("error", names)
        self.assertTrue(handler._ciel_runtime_channel_delivery_ok)

    # --- downstream failures --------------------------------------------------

    def test_downstream_write_failure_stays_a_client_disconnect(self):
        class FailingWFile:
            def __init__(self):
                self.writes = 0

            def write(self, data):
                self.writes += 1
                raise ConnectionResetError(10054, "client closed")

            def flush(self):
                pass

        wfile = FailingWFile()
        handler = GuardedHandler(wfile)
        _handler, _events, logs = self.run_stream(
            ScriptedUpstream(message_start() + text_start() + text_delta("hello")), handler=handler
        )

        self.assertEqual(1, wfile.writes)
        self.assert_failed_delivery(handler, "anthropic_stream_client_disconnected")
        messages = [message for _level, message in logs]
        self.assertTrue(any("anthropic_sse_client_disconnected" in item for item in messages))
        self.assertFalse(any("anthropic_sse_forward_error" in item for item in messages))

    def test_client_gone_while_reporting_upstream_failure_is_contained(self):
        class FailOnErrorEvent(BytesIO):
            def write(self, data):
                if b"event: error" in data:
                    raise BrokenPipeError(32, "client closed")
                return super().write(data)

        handler = GuardedHandler(FailOnErrorEvent())
        lines = message_start() + text_start() + text_delta("partial")
        _handler, events, _logs = self.run_stream(
            ScriptedUpstream(lines, ConnectionResetError(10054, "reset")), handler=handler
        )

        self.assertNotIn("error", [name for name, _ in events])
        self.assertNotIn("message_stop", [name for name, _ in events])
        self.assertFalse(handler._ciel_runtime_channel_delivery_ok)


if __name__ == "__main__":
    unittest.main()
