import tempfile
import unittest
from pathlib import Path
from threading import Condition

from ciel_runtime_support.chat_http_controller import (
    ChatHttpController,
    ChatHttpReadServices,
    ChatHttpWriteServices,
)


class Handler:
    def __init__(self, path: str = "/ca/chat/messages"):
        self.path = path


class ChatHttpInjectionModeTests(unittest.TestCase):
    def controller(self, calls, responses, default_transport="session_socket"):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)

        def record(name):
            def callback(*args):
                calls.append((name, args))
                return {"id": 9, **(args[0] if args and isinstance(args[0], dict) else {})}

            return callback

        return ChatHttpController(
            router_base="http://router",
            reads=ChatHttpReadServices(
                read_after=lambda *_args: [],
                read_before=lambda *_args: [],
                condition=Condition(),
                safe_segment=lambda value, _label: value,
                files_dir=Path(temp_dir.name),
            ),
            writes=ChatHttpWriteServices(
                write_json=lambda _handler, payload, status=200: responses.append((status, payload)),
                append_message=record("append"),
                store_upload=record("upload"),
                submit_message=record("web"),
                submit_notify=record("notify"),
                submit_tty=record("tty"),
                default_input_transport=lambda: default_transport,
            ),
        )

    def test_default_prefers_session_socket_and_allows_tty_override(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses, "session_socket")

        self.assertTrue(
            controller.post(
                Handler(), "/ca/chat/messages", {"message": "socket default"}
            )
        )
        self.assertEqual(
            "session_socket", calls[0][1][0]["meta"]["input_transport"]
        )
        self.assertEqual("session_socket", responses[0][1]["input_transport"])

        calls.clear()
        responses.clear()
        self.assertTrue(
            controller.post(
                Handler(),
                "/ca/chat/messages",
                {"message": "tty override", "input_transport": "tty"},
            )
        )
        self.assertEqual("tty", calls[0][1][0]["meta"]["input_transport"])

        calls.clear()
        responses.clear()
        self.assertTrue(
            controller.post(
                Handler("/ca/channel/messages"),
                "/ca/channel/messages",
                {"message": "socket default through channel alias"},
            )
        )
        self.assertEqual(
            "session_socket", calls[0][1][0]["meta"]["input_transport"]
        )

    def test_tty_body_parameter_bypasses_public_web_chat(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {"injection_mode": "tty", "message": "wake and inspect"}

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        self.assertEqual(["tty"], [name for name, _args in calls])
        self.assertEqual("tty", calls[0][1][0]["meta"]["response_mode"])
        self.assertEqual(200, responses[0][0])
        self.assertEqual("tty", responses[0][1]["injection_mode"])

    def test_tty_query_parameter_works_on_channel_alias(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {"message": "from SSE bridge"}
        handler = Handler("/ca/channel/messages?injection_mode=tty")

        self.assertTrue(controller.post(handler, "/ca/channel/messages", body))

        self.assertEqual(["tty"], [name for name, _args in calls])
        self.assertEqual("tty", calls[0][1][0]["meta"]["injection_mode"])

    def test_default_keeps_existing_web_chat_path(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {"message": "hello"}

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        self.assertEqual("append", calls[0][0])
        self.assertEqual("web", calls[1][0])
        self.assertEqual("web_chat", responses[0][1]["injection_mode"])

    def test_raw_injection_is_admitted_as_an_explicit_orthogonal_option(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {
            "message": "  exact text\nwith spacing  ",
            "raw_injection": True,
            "input_transport": "session_socket",
            "response_mode": "web_chat",
        }

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        admitted = calls[0][1][0]
        self.assertIs(admitted["meta"]["raw_injection"], True)
        self.assertEqual("session_socket", admitted["meta"]["input_transport"])
        self.assertEqual("web_chat", admitted["meta"]["response_mode"])
        self.assertIs(responses[0][1]["raw_injection"], True)

    def test_invalid_raw_injection_is_rejected_before_admission(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(
            controller.post(
                Handler(),
                "/ca/chat/messages",
                {"message": "reject", "raw_injection": "sometimes"},
            )
        )

        self.assertEqual([], calls)
        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_raw_injection", responses[0][1]["error"])

    def test_invalid_mode_is_rejected_without_admission(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(
            controller.post(
                Handler(),
                "/ca/chat/messages",
                {"injection_mode": "unknown", "message": "do not admit"},
            )
        )

        self.assertEqual([], calls)
        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_injection_mode", responses[0][1]["error"])

    def test_structured_input_can_request_plain_tty_response(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(controller.post(
            Handler(),
            "/ca/chat/messages",
            {"input_mode": "structured", "response_mode": "tty", "message": "inspect"},
        ))

        self.assertEqual(["append", "web"], [name for name, _args in calls])
        admitted = calls[0][1][0]
        self.assertEqual("structured", admitted["meta"]["injection_mode"])
        self.assertEqual("tty", admitted["meta"]["response_mode"])
        self.assertEqual("structured", responses[0][1]["input_mode"])
        self.assertEqual("session_socket", responses[0][1]["input_transport"])
        self.assertEqual("tty", responses[0][1]["response_mode"])

    def test_router_input_transport_is_stored_independently_from_format(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(controller.post(
            Handler(),
            "/ca/chat/messages",
            {
                "input_mode": "structured",
                "input_transport": "router",
                "response_mode": "web_chat",
                "message": "inject this through the request body",
            },
        ))

        admitted = calls[0][1][0]
        self.assertEqual("router", admitted["meta"]["input_transport"])
        self.assertEqual("router", responses[0][1]["input_transport"])

    def test_router_transport_alias_and_invalid_value(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(controller.post(
            Handler("/ca/channel/messages?input_transport=context"),
            "/ca/channel/messages",
            {"message": "alias"},
        ))
        self.assertEqual("router", calls[0][1][0]["meta"]["input_transport"])

        calls.clear()
        responses.clear()
        self.assertTrue(controller.post(
            Handler(),
            "/ca/chat/messages",
            {"input_transport": "unknown", "message": "reject"},
        ))
        self.assertEqual([], calls)
        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_input_transport", responses[0][1]["error"])

    def test_tty_input_can_keep_web_chat_response_correlation(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(controller.post(
            Handler(),
            "/ca/chat/messages",
            {"input_mode": "tty", "response_mode": "ai-net", "message": "raw request"},
        ))

        self.assertEqual(["append", "tty"], [name for name, _args in calls])
        public = calls[1][1][1]
        self.assertEqual(9, public["id"])
        self.assertEqual("raw request", public["message"])
        self.assertEqual("web_chat", responses[0][1]["response_mode"])
        self.assertNotIn("runtime_message", responses[0][1])

    def test_mcp_response_hint_is_normalized_and_forwarded(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {
            "input_mode": "structured",
            "response_mode": "mcp",
            "response_mcp": {"server": "ai-net", "tool": "send_message", "hint": "reply to room-7"},
            "message": "status",
        }

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        admitted = calls[0][1][0]
        self.assertEqual(body["response_mcp"], admitted["meta"]["response_mcp"])
        self.assertEqual("mcp", responses[0][1]["response_mode"])

    def test_mcp_response_requires_server(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)

        self.assertTrue(controller.post(
            Handler(),
            "/ca/chat/messages",
            {"response_mode": "mcp", "response_mcp": {"tool": "send"}, "message": "no"},
        ))

        self.assertEqual([], calls)
        self.assertEqual(400, responses[0][0])
        self.assertEqual("mcp_response_requires_server", responses[0][1]["error"])

    def test_web_only_delivery_is_published_but_never_mirrored_to_the_input_queue(self):
        # The reply tool posts the agent's acks/replies with delivery=["web"].
        # They are model output: publishing to chat is correct, mirroring into
        # the runtime-input queue replays them as pending model input and
        # blocks later router-transport deliveries.
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {
            "kind": "ack",
            "sender_id": "claude-code",
            "recipients": ["web"],
            "delivery": ["web"],
            "message": "received",
            "meta": {"source": "ciel-runtime-router-tool"},
        }

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        self.assertEqual(["append"], [name for name, _args in calls])
        self.assertEqual(200, responses[0][0])

    def test_llm_routed_and_route_less_deliveries_still_reach_the_input_queue(self):
        for delivery in (None, ["llm", "native"], ["all"]):
            calls = []
            responses = []
            controller = self.controller(calls, responses)
            body = {"message": "question"}
            if delivery is not None:
                body["delivery"] = delivery

            self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

            self.assertEqual(["append", "web"], [name for name, _args in calls], delivery)
            self.assertEqual(200, responses[0][0])


if __name__ == "__main__":
    unittest.main()
