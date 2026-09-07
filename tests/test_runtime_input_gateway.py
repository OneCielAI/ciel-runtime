import unittest
from unittest import mock

from ciel_runtime_support.channel_message_policy import message_is_web_chat_request
from ciel_runtime_support.channel_message_prompt import format_llm_batch_prompt
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway


class RuntimeInputGatewayTests(unittest.TestCase):
    def test_admission_records_queued_lifecycle_on_private_request_id(self):
        status = mock.Mock()
        gateway = RuntimeInputGateway(
            lambda value: {"id": 27, **value},
            status=status,
        )

        saved = gateway.submit_tty({"message": "한 번만 제출"})

        self.assertEqual(27, saved["id"])
        status.transition.assert_called_once_with(
            27,
            "queued",
            data={"channel": "default", "input_transport": "session_socket"},
        )

    def test_claude_default_transport_is_stamped_on_all_external_inputs(self):
        gateway = RuntimeInputGateway(
            lambda value: value,
            default_input_transport=lambda: "session_socket",
        )

        web = gateway.submit_web_chat({"id": 1, "message": "web"})
        event = gateway.submit_external_event(
            '{"specversion":"1.0"}',
            receiver_id="default",
            transport="sse",
            event_id="evt-1",
            event_type="example.created",
            event_source="example",
        )
        mcp = gateway.submit_stream_input({"message": "mcp"})
        notification = gateway.submit_notification({"message": "notification"})
        tty = gateway.submit_tty({"message": "plain input"})
        telemetry = gateway.submit_telemetry_notice([], 1)

        for admitted in (web, event, mcp, notification, tty, telemetry):
            self.assertEqual("session_socket", admitted["meta"]["input_transport"])

    def test_web_chat_keeps_local_attachment_projection_private(self):
        admitted = []
        public_message = {
            "id": 7,
            "channel": "web-chat-session",
            "thread_id": "thread-1",
            "kind": "web_chat",
            "message": "inspect",
            "meta": {
                "attachments": [
                    {
                        "name": "stored.png",
                        "original_name": "screen.png",
                        "content_type": "image/png",
                    }
                ]
            },
        }
        gateway = RuntimeInputGateway(
            lambda value: admitted.append(value) or {"id": 8, **value},
            lambda attachment: {
                "name": attachment["original_name"],
                "content_type": attachment["content_type"],
                "local_path": "C:\\private\\stored.png",
            },
        )

        saved = gateway.submit_web_chat(public_message)

        self.assertNotIn("runtime_attachments", public_message["meta"])
        self.assertEqual(
            "C:\\private\\stored.png",
            saved["meta"]["runtime_attachments"][0]["local_path"],
        )
        self.assertEqual("private_runtime", saved["visibility"])

    def test_invalid_runtime_attachment_does_not_block_the_message(self):
        gateway = RuntimeInputGateway(
            lambda value: value,
            lambda _attachment: (_ for _ in ()).throw(ValueError("invalid")),
        )

        saved = gateway.submit_web_chat(
            {"message": "still deliver", "meta": {"attachments": [{"name": "bad"}]}}
        )

        self.assertEqual("still deliver", saved["message"])
        self.assertNotIn("runtime_attachments", saved["meta"])

    def test_tty_input_is_private_structured_and_has_no_web_reply_contract(self):
        gateway = RuntimeInputGateway(lambda value: {"id": 11, **value})

        saved = gateway.submit_tty(
            {
                "channel": "automation",
                "sender_id": "scheduler",
                "kind": "web_chat",
                "message": {"event": "deploy", "attempt": 2},
                "meta": {
                    "source": "ciel-runtime-web-chat",
                    "reply_channel": "browser",
                    "reply_recipient": "web",
                    "web_reply_token": "secret",
                    "response_contract": {"version": 1},
                },
            }
        )

        self.assertEqual("tty_input", saved["kind"])
        self.assertEqual('{"event":"deploy","attempt":2}', saved["message"])
        self.assertEqual(["llm"], saved["delivery"])
        self.assertEqual("private_runtime", saved["visibility"])
        self.assertEqual("ciel-runtime-api-tty", saved["meta"]["source"])
        self.assertEqual("application/json", saved["meta"]["content_type"])
        self.assertEqual("ciel-runtime-web-chat", saved["meta"]["declared_source"])
        self.assertNotIn("reply_channel", saved["meta"])
        self.assertNotIn("reply_recipient", saved["meta"])
        self.assertNotIn("web_reply_token", saved["meta"])
        self.assertNotIn("response_contract", saved["meta"])
        self.assertFalse(message_is_web_chat_request(saved))
        self.assertEqual(saved["message"], format_llm_batch_prompt([saved]))

    def test_tty_input_with_web_response_uses_public_message_correlation(self):
        gateway = RuntimeInputGateway(lambda value: {"id": 12, **value})
        public = {
            "id": 41,
            "channel": "web-chat-session",
            "thread_id": "thread-1",
            "message": "raw request",
        }

        saved = gateway.submit_tty(
            {
                "channel": "web-chat-session",
                "thread_id": "thread-1",
                "message": "raw request",
                "meta": {"response_mode": "web_chat", "injection_mode": "tty"},
            },
            public,
        )

        self.assertEqual(41, saved["meta"]["reply_parent_id"])
        self.assertEqual("web-chat-session", saved["meta"]["reply_channel"])
        self.assertTrue(saved["meta"]["web_reply_token"])
        prompt = format_llm_batch_prompt([saved])
        self.assertTrue(prompt.startswith("raw request\n\n"))
        self.assertIn("web reply required", prompt)

    def test_telemetry_notice_contains_cursor_but_not_log_body_and_requires_no_ack(self):
        gateway = RuntimeInputGateway(lambda value: {"id": 13, **value})

        saved = gateway.submit_telemetry_notice(
            [
                {
                    "file": "server.log",
                    "segment": 4,
                    "line_start": 11,
                    "line_end": 15,
                    "offset_start": 100,
                    "offset_end": 180,
                    "records": 5,
                }
            ],
            5,
        )

        self.assertEqual("telemetry_notice", saved["kind"])
        self.assertFalse(saved["meta"]["ack_required"])
        self.assertFalse(saved["meta"]["response_expected"])
        self.assertFalse(saved["meta"]["logs_embedded"])
        self.assertIn("offsets=100-180", saved["message"])
        self.assertEqual("private_runtime", saved["visibility"])
        prompt = format_llm_batch_prompt([saved])
        self.assertIn("do not acknowledge or respond", prompt)


if __name__ == "__main__":
    unittest.main()
