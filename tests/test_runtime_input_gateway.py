import unittest

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


if __name__ == "__main__":
    unittest.main()
