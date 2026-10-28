"""Failure metadata reported by the native Anthropic SSE relay.

Each case drives the real ``streaming_anthropic.rebatch_anthropic_sse_text``
with the router's own services and a scripted upstream, then checks the dict
handed to ``stream_failure_observer`` and that the observer leaves the
downstream bytes untouched.
"""

import http.client
import json
import os
import unittest
from io import BytesIO
from unittest import mock

import ciel_runtime
from ciel_runtime_support import streaming_anthropic

SECRET_TEXT = "SECRET_TEXT_partial answer"
SECRET_TOOL_ARG = "SECRET_TOOL_ARG_rm -rf /tmp/scr"
SECRET_CREDENTIAL = "Bearer sk-ant-SECRET-CREDENTIAL"
SESSION_ID = "3f6c1a2e-0000-4000-8000-session"

METADATA_KEYS = {
    "event",
    "provider",
    "model",
    "session_id",
    "upstream_message_id",
    "confirmed_incomplete",
    "text_len",
    "visible_text_len",
    "emitted_tool_use",
    "saw_tool_use",
    "saw_tool_like_block",
    "saw_pseudo_tool_text",
    "held_pseudo_tool_text",
    "tool_uncertain",
    "open_blocks",
    "held_tool_uses",
    "upstream_error",
    "upstream_error_type",
    "client_disconnected",
    "saw_message_start",
    "saw_message_stop",
    "failure_reason",
}


def sse(event: str, payload: dict) -> list[bytes]:
    return [f"event: {event}\n".encode("utf-8"), f"data: {json.dumps(payload)}\n".encode("utf-8"), b"\n"]


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


def tool_start(index: int = 0) -> list[bytes]:
    return sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": "toolu_cut", "name": "Bash", "input": {}},
        },
    )


SERVER_SIDE_TOOL_BLOCKS = {
    "server_tool_use": {"type": "server_tool_use", "id": "srvtoolu_cut", "name": "web_search", "input": {}},
    "mcp_tool_use": {"type": "mcp_tool_use", "id": "mcptoolu_cut", "name": "lookup", "server_name": "docs", "input": {}},
}


def server_side_tool_start(block_type: str, index: int = 0) -> list[bytes]:
    return sse(
        "content_block_start",
        {"type": "content_block_start", "index": index, "content_block": dict(SERVER_SIDE_TOOL_BLOCKS[block_type])},
    )


def tool_partial(index: int = 0) -> list[bytes]:
    return sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": '{"command": "' + SECRET_TOOL_ARG},
        },
    )


def message_delta(stop_reason: str = "end_turn") -> list[bytes]:
    return sse(
        "message_delta",
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 7}},
    )


def message_stop() -> list[bytes]:
    return sse("message_stop", {"type": "message_stop"})


def upstream_error_event() -> list[bytes]:
    return sse("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})


TOOL_BODY = {
    "messages": [{"role": "user", "content": "clean up"}],
    "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
}


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
    """Handler with an active channel-delivery guard and real request headers."""

    def __init__(self, wfile=None):
        self.wfile = wfile if wfile is not None else BytesIO()
        self.headers = http.client.HTTPMessage()
        self.headers["X-Claude-Code-Session-Id"] = SESSION_ID
        self.headers["Authorization"] = SECRET_CREDENTIAL
        self._ciel_runtime_channel_delivery_guard = True
        self._ciel_runtime_channel_delivery_ok = False
        self._ciel_runtime_channel_delivery_reason = "pending"


def event_names(raw: bytes) -> list[str]:
    return [
        line[len("event: "):]
        for line in raw.decode("utf-8").split("\n")
        if line.startswith("event: ")
    ]


class StreamFailureMetadataTests(unittest.TestCase):
    def run_stream(self, upstream_factory, *, observe=True, observer=None, handler_factory=GuardedHandler, **options):
        """Returns (metadata reports, downstream bytes, handler, logs)."""

        reports: list[dict] = []
        handler = handler_factory()
        kwargs = {
            "word_chunking": options.pop("word_chunking", False),
            "source_body": options.pop("body", {"messages": [{"role": "user", "content": "hi"}]}),
            "preserve_thinking": options.pop("preserve_thinking", True),
            "normalize_tool_use": options.pop("normalize_tool_use", False),
            "provider": options.pop("provider", "anthropic"),
        }
        self.assertFalse(options, options)
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": "0"}), mock.patch.object(
            ciel_runtime, "router_log"
        ) as router_log:
            services = ciel_runtime._RESPONSE_STREAM_API.context().anthropic_stream_services()
            if observe:
                kwargs["stream_failure_observer"] = observer or reports.append
            streaming_anthropic.rebatch_anthropic_sse_text(
                handler, upstream_factory(), "claude-test", services=services, **kwargs
            )
        raw = handler.wfile.getvalue() if isinstance(handler.wfile, BytesIO) else b""
        logs = [(str(call.args[0]), str(call.args[1])) for call in router_log.call_args_list if len(call.args) > 1]
        return reports, raw, handler, logs

    def single_report(self, upstream_factory, **options) -> tuple[dict, bytes, GuardedHandler]:
        reports, raw, handler, _logs = self.run_stream(upstream_factory, **options)
        self.assertEqual(1, len(reports), reports)
        report = reports[0]
        self.assert_safe_metadata(report)
        self.assertEqual(handler._ciel_runtime_channel_delivery_reason, report["failure_reason"])
        # The observer must not alter what the client received.
        _unobserved_reports, unobserved_raw, _handler, _logs = self.run_stream(upstream_factory, observe=False, **options)
        self.assertEqual(unobserved_raw, raw)
        return report, raw, handler

    def assert_safe_metadata(self, report: dict):
        self.assertEqual(METADATA_KEYS, set(report))
        for key, value in report.items():
            self.assertIsInstance(value, (bool, int, str, type(None)), key)
        encoded = json.dumps(report)
        for secret in (SECRET_TEXT, SECRET_TOOL_ARG, SECRET_CREDENTIAL, "sk-ant", "rm -rf"):
            self.assertNotIn(secret, encoded)
        self.assertEqual("anthropic_stream_failure", report["event"])
        self.assertEqual("claude-test", report["model"])
        self.assertEqual(SESSION_ID, report["session_id"])

    def assert_no_fabricated_terminal(self, raw: bytes):
        names = event_names(raw)
        self.assertNotIn("message_stop", names)
        self.assertEqual("error", names[-1], names)
        self.assertEqual(1, names.count("error"), names)

    def relay_with_model_observer(self, observer):
        handler = GuardedHandler()
        upstream = ScriptedUpstream(message_start() + text_start() + text_delta("done") + block_stop()
                                    + message_delta() + message_stop())
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": "0"}), mock.patch.object(
            ciel_runtime, "router_log"
        ) as router_log:
            services = ciel_runtime._RESPONSE_STREAM_API.context().anthropic_stream_services()
            options = {"stream_model_observer": observer} if observer is not None else {}
            streaming_anthropic.rebatch_anthropic_sse_text(
                handler, upstream, "claude-test", word_chunking=False, provider="cielairouter",
                source_body={"messages": [{"role": "user", "content": "hi"}]}, services=services, **options)
        logs = [(str(call.args[0]), str(call.args[1])) for call in router_log.call_args_list if len(call.args) > 1]
        return handler.wfile.getvalue(), logs

    def test_stream_model_observer_receives_answering_model_without_changing_bytes(self):
        seen: list[str] = []
        observed, _logs = self.relay_with_model_observer(seen.append)
        self.assertEqual(["claude-test"], seen)
        unobserved, _logs = self.relay_with_model_observer(None)
        self.assertEqual(unobserved, observed)

        def broken(_model):
            raise RuntimeError("observer failure")

        failed, logs = self.relay_with_model_observer(broken)
        self.assertEqual(unobserved, failed)
        self.assertIn(("WARN", "anthropic_stream_model_observer_failed error=RuntimeError"), logs)

    def test_text_only_eof_reports_confirmed_incomplete_without_tool_exposure(self):
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT))
        )

        self.assertTrue(report["confirmed_incomplete"])
        self.assertEqual(len(SECRET_TEXT), report["text_len"])
        self.assertEqual(len(SECRET_TEXT), report["visible_text_len"])
        self.assertFalse(report["emitted_tool_use"])
        self.assertFalse(report["saw_tool_use"])
        self.assertFalse(report["saw_tool_like_block"])
        self.assertFalse(report["saw_pseudo_tool_text"])
        self.assertEqual(0, report["held_pseudo_tool_text"])
        self.assertFalse(report["tool_uncertain"])
        self.assertEqual(1, report["open_blocks"])
        self.assertEqual(0, report["held_tool_uses"])
        self.assertFalse(report["upstream_error"])
        self.assertFalse(report["client_disconnected"])
        self.assertTrue(report["saw_message_start"])
        self.assertFalse(report["saw_message_stop"])
        self.assertEqual("msg_upstream", report["upstream_message_id"])
        self.assertEqual("anthropic", report["provider"])
        self.assertEqual("anthropic_stream_upstream_incomplete", report["failure_reason"])
        self.assert_no_fabricated_terminal(raw)
        self.assertNotIn("message_delta", event_names(raw))
        self.assertNotIn("content_block_stop", event_names(raw))

    def test_passthrough_tool_use_cut_mid_input_reports_exposed_tool(self):
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + tool_start() + tool_partial(), ConnectionResetError(10054, "reset")),
            body=TOOL_BODY,
        )

        self.assertTrue(report["confirmed_incomplete"])
        self.assertTrue(report["saw_tool_use"])
        self.assertTrue(report["emitted_tool_use"])
        self.assertTrue(report["saw_tool_like_block"])
        self.assertTrue(report["tool_uncertain"])
        self.assertEqual(1, report["open_blocks"])
        self.assertEqual(0, report["held_tool_uses"])
        self.assertEqual(0, report["text_len"])
        self.assertEqual(0, report["visible_text_len"])
        self.assertEqual("anthropic_stream_upstream_error:ConnectionResetError", report["failure_reason"])
        self.assertIn("content_block_start", event_names(raw))
        self.assertNotIn("content_block_stop", event_names(raw))
        self.assert_no_fabricated_terminal(raw)

    def test_buffered_tool_use_cut_mid_input_reports_tool_never_exposed(self):
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(
                message_start() + text_start() + text_delta(SECRET_TEXT) + block_stop() + tool_start(1) + tool_partial(1)
            ),
            body=TOOL_BODY,
            provider="deepseek",
            normalize_tool_use=True,
        )

        self.assertTrue(report["confirmed_incomplete"])
        self.assertTrue(report["saw_tool_use"])
        self.assertFalse(report["emitted_tool_use"])
        self.assertTrue(report["tool_uncertain"])
        self.assertEqual(0, report["open_blocks"])
        self.assertEqual(1, report["held_tool_uses"])
        self.assertEqual(len(SECRET_TEXT), report["text_len"])
        self.assertEqual(len(SECRET_TEXT), report["visible_text_len"])
        self.assertEqual("deepseek", report["provider"])
        self.assertNotIn(b'"tool_use"', raw)
        self.assert_no_fabricated_terminal(raw)

    def test_server_and_mcp_tool_blocks_mark_tool_uncertain(self):
        """Server-side tool blocks are relayed unchanged but must exclude recovery."""

        for block_type in SERVER_SIDE_TOOL_BLOCKS:
            for with_input in (False, True):
                with self.subTest(block_type=block_type, with_input=with_input):
                    report, raw, _handler = self.single_report(
                        lambda block_type=block_type, with_input=with_input: ScriptedUpstream(
                            message_start()
                            + text_start()
                            + text_delta(SECRET_TEXT)
                            + block_stop()
                            + server_side_tool_start(block_type, 1)
                            + (tool_partial(1) if with_input else [])
                        ),
                        body=TOOL_BODY,
                    )

                    self.assertTrue(report["confirmed_incomplete"])
                    # The exact-"tool_use" flags keep their meaning...
                    self.assertFalse(report["saw_tool_use"])
                    self.assertFalse(report["emitted_tool_use"])
                    # ...and the conservative flags catch the server-side block,
                    # even if no input_json_delta arrived before the failure.
                    self.assertTrue(report["saw_tool_like_block"])
                    self.assertTrue(report["tool_uncertain"])
                    self.assertEqual(len(SECRET_TEXT), report["visible_text_len"])
                    self.assertEqual(1, report["open_blocks"])
                    # No stop_reason patching or tool synthesis.
                    self.assertIn(f'"type": "{block_type}"'.encode(), raw)
                    self.assertNotIn(b"TaskList", raw)
                    self.assert_no_fabricated_terminal(raw)

    def test_orphan_tool_input_delta_marks_tool_uncertain(self):
        report, _raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + tool_partial(0)),
            body=TOOL_BODY,
        )

        self.assertFalse(report["saw_tool_use"])
        self.assertTrue(report["saw_tool_like_block"])
        self.assertTrue(report["tool_uncertain"])

    def test_held_pseudo_tool_text_is_flagged_and_not_counted_visible(self):
        shown = "Checking the workspace now."
        held = "\n<Bash>\n<command>" + SECRET_TOOL_ARG
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(shown + held)),
            body=TOOL_BODY,
            provider="deepseek",
        )

        self.assertTrue(report["confirmed_incomplete"])
        self.assertTrue(report["saw_pseudo_tool_text"])
        self.assertEqual(1, report["held_pseudo_tool_text"])
        self.assertTrue(report["tool_uncertain"])
        self.assertFalse(report["saw_tool_use"])
        self.assertFalse(report["saw_tool_like_block"])
        # text_len counts what arrived; visible_text_len only what was relayed.
        self.assertEqual(len(shown + held), report["text_len"])
        self.assertEqual(len(shown), report["visible_text_len"])
        self.assertIn(shown.encode(), raw)
        self.assertNotIn(b"<Bash>", raw)
        self.assertNotIn(SECRET_TOOL_ARG.encode(), raw)
        self.assert_no_fabricated_terminal(raw)

    def test_visible_text_len_counts_initial_text_block_text(self):
        initial = "Opening line. "

        def upstream():
            start = sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": initial}},
            )
            return ScriptedUpstream(message_start() + start + text_delta(SECRET_TEXT))

        report, raw, _handler = self.single_report(upstream)

        self.assertIn(initial.encode(), raw)
        self.assertEqual(len(initial) + len(SECRET_TEXT), report["visible_text_len"])
        self.assertEqual(len(SECRET_TEXT), report["text_len"])
        self.assertFalse(report["tool_uncertain"])

    def test_visible_text_len_excludes_text_whose_flush_failed(self):
        class FlushFailsAfterText(BytesIO):
            def __init__(self):
                super().__init__()
                self.last_write = b""

            def write(self, data):
                self.last_write = bytes(data)
                return super().write(data)

            def flush(self):
                if b'"text_delta"' in self.last_write:
                    raise BrokenPipeError(32, "client closed")

        reports, raw, _handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT)),
            handler_factory=lambda: GuardedHandler(FlushFailsAfterText()),
        )

        self.assertEqual(1, len(reports))
        report = reports[0]
        self.assert_safe_metadata(report)
        # The bytes reached the write buffer, but the flush failed: not counted.
        self.assertIn(SECRET_TEXT.encode(), raw)
        self.assertEqual(len(SECRET_TEXT), report["text_len"])
        self.assertEqual(0, report["visible_text_len"])
        self.assertTrue(report["client_disconnected"])
        self.assertFalse(report["confirmed_incomplete"])

    def test_visible_text_len_excludes_filtered_artifact_text(self):
        shown = "Checking the workspace."
        artifact = "\ncall\nignore\n"
        body = {"messages": [{"role": "user", "content": "hi"}], "tools": [{"name": "Workflow", "input_schema": {"type": "object"}}]}
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(shown + artifact)),
            body=body,
        )

        # EOF releases the filter's pending text, but strips the artifact suffix.
        self.assertEqual(len(shown + artifact), report["text_len"])
        self.assertEqual(len(shown), report["visible_text_len"])
        self.assertIn(shown.encode(), raw)
        self.assertNotIn(b"ignore", raw)
        self.assertFalse(report["tool_uncertain"])

    def test_visible_text_len_counts_word_buffer_flush_once(self):
        chunks = ["Partial", " answer", " with unicode: 한글."]
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + text_start() + sum((text_delta(t) for t in chunks), [])),
            word_chunking=True,
        )

        self.assertEqual(len("".join(chunks)), report["text_len"])
        self.assertEqual(len("".join(chunks)), report["visible_text_len"])
        self.assertIn("한글".encode(), raw)
        self.assertFalse(report["tool_uncertain"])

    def test_visible_text_len_preserves_successful_prefix_after_later_flush_failure(self):
        shown = "First delta."
        not_flushed = "Second delta."

        class FailSecondTextFlush(BytesIO):
            def __init__(self):
                super().__init__()
                self.last_write = b""
                self.text_flushes = 0

            def write(self, data):
                self.last_write = bytes(data)
                return super().write(data)

            def flush(self):
                if b'"text_delta"' in self.last_write:
                    self.text_flushes += 1
                    if self.text_flushes == 2:
                        raise ConnectionResetError(10054, "client closed")

        reports, _raw, _handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(shown) + text_delta(not_flushed)),
            handler_factory=lambda: GuardedHandler(FailSecondTextFlush()),
        )

        self.assertEqual(1, len(reports))
        self.assertEqual(len(shown + not_flushed), reports[0]["text_len"])
        self.assertEqual(len(shown), reports[0]["visible_text_len"])
        self.assertTrue(reports[0]["client_disconnected"])

    def test_initial_text_is_not_counted_when_start_flush_fails(self):
        initial = "Not flushed."

        class FailStartFlush(BytesIO):
            def __init__(self):
                super().__init__()
                self.last_write = b""

            def write(self, data):
                self.last_write = bytes(data)
                return super().write(data)

            def flush(self):
                if b"event: content_block_start" in self.last_write:
                    raise BrokenPipeError(32, "client closed")

        start = sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": initial}},
        )
        reports, _raw, _handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(message_start() + start),
            handler_factory=lambda: GuardedHandler(FailStartFlush()),
        )

        self.assertEqual(1, len(reports))
        self.assertEqual(0, reports[0]["visible_text_len"])
        self.assertTrue(reports[0]["client_disconnected"])

    def test_upstream_error_event_is_reported_as_upstream_error(self):
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT) + upstream_error_event())
        )

        self.assertTrue(report["upstream_error"])
        self.assertTrue(report["confirmed_incomplete"])
        self.assertEqual("anthropic_stream_upstream_error_event", report["failure_reason"])
        self.assertEqual("overloaded_error", report["upstream_error_type"])
        self.assert_no_fabricated_terminal(raw)

    def test_eof_after_message_delta_keeps_message_delta_and_reports_incomplete(self):
        report, raw, _handler = self.single_report(
            lambda: ScriptedUpstream(
                message_start() + text_start() + text_delta(SECRET_TEXT) + block_stop() + message_delta()
            )
        )

        self.assertTrue(report["confirmed_incomplete"])
        self.assertEqual(0, report["open_blocks"])
        self.assertEqual(1, event_names(raw).count("message_delta"))
        self.assert_no_fabricated_terminal(raw)

    def test_client_disconnect_does_not_confirm_upstream_incomplete(self):
        class FailingWFile:
            def write(self, data):
                raise ConnectionResetError(10054, "client closed")

            def flush(self):
                pass

        upstream = ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT) + block_stop() + message_delta() + message_stop())
        reports, _raw, handler, _logs = self.run_stream(lambda: upstream, handler_factory=lambda: GuardedHandler(FailingWFile()))

        self.assertEqual(1, len(reports))
        report = reports[0]
        self.assert_safe_metadata(report)
        self.assertTrue(report["client_disconnected"])
        self.assertFalse(report["confirmed_incomplete"])
        self.assertFalse(report["saw_message_stop"])
        self.assertEqual("anthropic_stream_client_disconnected", report["failure_reason"])
        self.assertEqual(handler._ciel_runtime_channel_delivery_reason, report["failure_reason"])
        self.assertTrue(upstream.closed)

    def test_client_gone_while_reporting_upstream_failure_keeps_upstream_confirmation(self):
        class FailOnErrorEvent(BytesIO):
            def write(self, data):
                if b"event: error" in data:
                    raise BrokenPipeError(32, "client closed")
                return super().write(data)

        reports, raw, _handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT), ConnectionResetError(10054, "reset")),
            handler_factory=lambda: GuardedHandler(FailOnErrorEvent()),
        )

        self.assertEqual(1, len(reports))
        self.assert_safe_metadata(reports[0])
        self.assertTrue(reports[0]["confirmed_incomplete"])
        self.assertTrue(reports[0]["client_disconnected"])
        self.assertEqual("anthropic_stream_upstream_error:ConnectionResetError", reports[0]["failure_reason"])
        self.assertNotIn("message_stop", event_names(raw))

    def test_transport_error_after_message_stop_reports_no_failure(self):
        reports, _raw, _handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(
                message_start() + text_start() + text_delta(SECRET_TEXT) + block_stop() + message_delta() + message_stop(),
                ConnectionResetError(10054, "reset"),
            )
        )

        self.assertEqual([], reports)

    def test_complete_stream_does_not_call_observer(self):
        reports, raw, handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(
                message_start() + text_start() + text_delta(SECRET_TEXT) + block_stop() + message_delta() + message_stop()
            )
        )

        self.assertEqual([], reports)
        self.assertTrue(handler._ciel_runtime_channel_delivery_ok)
        self.assertEqual("message_stop", event_names(raw)[-1])

    def test_failing_observer_is_contained_and_changes_nothing_downstream(self):
        def broken_observer(_metadata):
            raise RuntimeError(SECRET_TEXT)

        def upstream_factory():
            return ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT))

        _reports, raw, handler, logs = self.run_stream(upstream_factory, observer=broken_observer)
        _reports, unobserved_raw, unobserved_handler, _logs = self.run_stream(upstream_factory, observe=False)

        self.assertEqual(unobserved_raw, raw)
        self.assertEqual(
            unobserved_handler._ciel_runtime_channel_delivery_reason, handler._ciel_runtime_channel_delivery_reason
        )
        failures = [message for level, message in logs if "anthropic_stream_failure_observer_failed" in message]
        self.assertEqual(["anthropic_stream_failure_observer_failed error=RuntimeError"], failures)
        self.assertFalse(any(SECRET_TEXT in message for _level, message in logs))

    def test_default_observer_keeps_existing_callers_compatible(self):
        handler = GuardedHandler()
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": "0"}), mock.patch.object(
            ciel_runtime, "router_log"
        ):
            ciel_runtime._rebatch_anthropic_sse_text(
                handler,
                ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT)),
                "claude-test",
                word_chunking=False,
                source_body={"messages": [{"role": "user", "content": "hi"}]},
                provider="anthropic",
            )

        self.assertFalse(handler._ciel_runtime_channel_delivery_ok)
        self.assertEqual("anthropic_stream_upstream_incomplete", handler._ciel_runtime_channel_delivery_reason)

    def test_missing_headers_report_no_session(self):
        class HeaderlessHandler(GuardedHandler):
            def __init__(self):
                super().__init__()
                del self.headers

        reports, _raw, _handler, _logs = self.run_stream(
            lambda: ScriptedUpstream(message_start() + text_start() + text_delta(SECRET_TEXT)),
            handler_factory=HeaderlessHandler,
        )

        self.assertEqual(1, len(reports))
        self.assertIsNone(reports[0]["session_id"])


if __name__ == "__main__":
    unittest.main()
