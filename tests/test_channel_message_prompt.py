import unittest

from ciel_runtime_support.channel_message_prompt import (
    format_llm_batch_prompt,
    format_web_chat_wake_batch_prompt,
    format_wake_prompt,
    llm_message_skip_reason,
    prompt_metadata,
)


class ChannelMessagePromptTests(unittest.TestCase):
    def test_prompt_metadata_keeps_identity_and_excludes_sensitive_keys(self):
        message = {"meta": {"room_id": "ops", "message_id": "42", "authorization": "secret"}}
        self.assertEqual('{"room_id":"ops","message_id":"42"}', prompt_metadata(message))

    def test_standard_prompt_projects_channel_identity(self):
        prompt = format_wake_prompt(
            {"id": 7, "channel": "ops", "sender_id": "agent", "message": "  deploy\nnow  "}
        )
        self.assertIn("channel=ops room=ops from=agent id=7", prompt)
        self.assertIn('text="deploy now"', prompt)

    def test_llm_envelope_preserves_json_with_source_header(self):
        prompt = format_llm_batch_prompt(
            [
                {
                    "channel": "ops",
                    "meta": {
                        "room_name": "Operations",
                        "room_id": "room-1",
                        "sse_json": {"event": "deploy"},
                    },
                }
            ]
        )
        self.assertTrue(prompt.startswith("[Source channel] Operations (room_id=room-1)\n\n"))
        self.assertIn('"event": "deploy"', prompt)

    def test_web_chat_llm_prompt_requires_routed_mcp_reply(self):
        prompt = format_llm_batch_prompt(
            [
                {
                    "channel": "web-chat-session",
                    "thread_id": "thread-7",
                    "kind": "web_chat",
                    "message": "status?",
                    "meta": {
                        "source": "ciel-runtime-web-chat",
                        "reply_channel": "web-chat-session",
                        "input_mode": "voice",
                    },
                }
            ]
        )

        self.assertIn("status?", prompt)
        self.assertIn("MCP server `ciel-runtime-router`", prompt)
        self.assertIn('"channel":"web-chat-session"', prompt)
        self.assertIn('"thread_id":"thread-7"', prompt)
        self.assertIn('recipients=["web"]', prompt)
        self.assertIn('delivery=["web"]', prompt)
        self.assertIn('"input_mode":"voice"', prompt)
        self.assertIn('kind="ack"', prompt)
        self.assertIn('"spoken"', prompt)
        self.assertIn('"overview"', prompt)
        self.assertIn('"details"', prompt)
        self.assertIn("browser speaks only this field", prompt)

    def test_web_chat_console_wake_is_one_atomic_line_with_contract_first(self):
        prompt = format_web_chat_wake_batch_prompt(
            [
                {
                    "id": 130,
                    "channel": "web-chat-session",
                    "thread_id": "thread-7",
                    "kind": "web_chat",
                    "message": "안녕.",
                    "meta": {
                        "source": "ciel-runtime-web-chat",
                        "reply_channel": "web-chat-session",
                        "input_mode": "voice",
                    },
                }
            ]
        )

        self.assertNotIn("\n", prompt)
        self.assertLess(prompt.index("web reply required"), prompt.index("안녕."))
        self.assertIn('"channel":"web-chat-session"', prompt)

    def test_skip_policy_rejects_control_and_self_echo_messages(self):
        control = {"message": "ready", "meta": {"sse_source": "remote", "kind": "status"}}
        self_echo = {"message": "update", "meta": {"sse_source": "ciel-runtime-router"}}
        self.assertEqual("status", llm_message_skip_reason(control))
        self.assertEqual("native_router_self_echo", llm_message_skip_reason(self_echo))


if __name__ == "__main__":
    unittest.main()
