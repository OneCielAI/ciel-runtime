"""Ollama's native chat wire keeps inline images beside the message text."""
import unittest

import ciel_runtime


class OllamaImageInputTests(unittest.TestCase):
    def converted(self, messages):
        return ciel_runtime.anthropic_messages_to_ollama({"messages": messages})

    def user_message(self, messages):
        return next(message for message in self.converted(messages)
                    if message.get("role") == "user" and "images" in message)

    def test_inline_base64_image_reaches_the_ollama_message(self):
        message = self.user_message([
            {"role": "user", "content": [
                {"type": "text", "text": "what is in this picture?"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": "iVBORw0KGgo="}},
            ]},
        ])
        self.assertIn("what is in this picture?", message["content"])
        self.assertEqual(["iVBORw0KGgo="], message["images"])

    def test_image_only_message_keeps_the_image(self):
        message = self.user_message([
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": "/9j/4AAQ"}},
            ]},
        ])
        self.assertEqual("", message["content"])
        self.assertEqual(["/9j/4AAQ"], message["images"])

    def test_remote_url_and_empty_source_add_no_image_payload(self):
        converted = self.converted([
            {"role": "user", "content": [
                {"type": "text", "text": "describe"},
                {"type": "image", "source": {"type": "url", "url": "https://example.test/a.png"}},
                {"type": "image", "source": {"type": "base64", "data": ""}},
            ]},
        ])
        self.assertEqual(["describe"], [message["content"] for message in converted
                                        if message.get("role") == "user"])
        self.assertFalse(any("images" in message for message in converted))

    def test_text_only_history_is_unchanged(self):
        converted = self.converted([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ])
        self.assertFalse(any("images" in message for message in converted))


if __name__ == "__main__":
    unittest.main()
