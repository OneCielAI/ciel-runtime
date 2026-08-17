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
    def controller(self, calls, responses):
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
            ),
        )

    def test_tty_body_parameter_bypasses_public_web_chat(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {"injection_mode": "tty", "message": "wake and inspect"}

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        self.assertEqual([("tty", (body,))], calls)
        self.assertEqual(200, responses[0][0])
        self.assertEqual("tty", responses[0][1]["injection_mode"])

    def test_tty_query_parameter_works_on_channel_alias(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {"message": "from SSE bridge"}
        handler = Handler("/ca/channel/messages?injection_mode=tty")

        self.assertTrue(controller.post(handler, "/ca/channel/messages", body))

        self.assertEqual([("tty", (body,))], calls)

    def test_default_keeps_existing_web_chat_path(self):
        calls = []
        responses = []
        controller = self.controller(calls, responses)
        body = {"message": "hello"}

        self.assertTrue(controller.post(Handler(), "/ca/chat/messages", body))

        self.assertEqual("append", calls[0][0])
        self.assertEqual("web", calls[1][0])
        self.assertEqual("web_chat", responses[0][1]["injection_mode"])

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


if __name__ == "__main__":
    unittest.main()
