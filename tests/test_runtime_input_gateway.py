import unittest

from ciel_runtime_support.channel_message_policy import message_is_web_chat_request
from ciel_runtime_support.channel_message_prompt import format_llm_batch_prompt
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway


class RuntimeInputGatewayTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
