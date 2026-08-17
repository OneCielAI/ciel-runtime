import unittest

from ciel_runtime_support.channel_message_prompt import (
    format_llm_batch_prompt,
    format_web_chat_wake_batch_prompt,
    format_wake_prompt,
    llm_message_skip_reason,
    prompt_metadata,
)


class ChannelMessagePromptTests(unittest.TestCase):
    def test_external_event_keeps_exact_raw_body_inside_transport_boundaries(self):
        raw = '{\n  "specversion": "1.0",\n  "id": "evt-1",\n  "source": "/test",\n  "type": "demo",\n  "data": {"text": "그대로"}\n}'
        prompt = format_llm_batch_prompt(
            [{
                "id": 7,
                "kind": "external_event",
                "message": raw,
                "meta": {"source": "ciel-runtime-external-event", "receiver_id": "default", "transport": "sse"},
                "delivery": ["llm"],
            }]
        )
        self.assertIn(raw, prompt)
        self.assertEqual(1, prompt.count(raw))
        self.assertIn("untrusted external event", prompt)

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
                    "id": 42,
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
        self.assertIn('"parent_id":"42"', prompt)
        self.assertIn('kind="ack"', prompt)
        self.assertIn('"spoken"', prompt)
        self.assertIn('"overview"', prompt)
        self.assertIn('"details"', prompt)
        self.assertIn("VOICE conversation turn", prompt)
        self.assertIn("browser speaks only spoken", prompt)

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
        self.assertIn('"parent_id":"130"', prompt)
        self.assertIn("[ciel-runtime web voice]", prompt)
        self.assertIn('asr_transcript="안녕."', prompt)

    def test_typed_web_chat_uses_screen_first_prompt(self):
        prompt = format_web_chat_wake_batch_prompt(
            [
                {
                    "id": 131,
                    "channel": "web-chat-session",
                    "thread_id": "thread-8",
                    "kind": "web_chat",
                    "message": "상세히 설명해줘",
                    "meta": {
                        "source": "ciel-runtime-web-chat",
                        "reply_channel": "web-chat-session",
                        "input_mode": "text",
                    },
                }
            ]
        )

        self.assertIn("[ciel-runtime web text]", prompt)
        self.assertIn("TYPED WEB CHAT turn", prompt)
        self.assertIn('text="상세히 설명해줘"', prompt)
        self.assertNotIn("VOICE conversation turn", prompt)
        self.assertNotIn("asr_transcript=", prompt)

    def test_structured_request_with_tty_response_has_no_web_reply_contract(self):
        prompt = format_web_chat_wake_batch_prompt([{
            "id": 141,
            "channel": "web-chat-session",
            "kind": "web_chat",
            "message": "show this only in the terminal",
            "meta": {
                "source": "ciel-runtime-web-chat",
                "input_mode": "text",
                "injection_mode": "structured",
                "response_mode": "tty",
            },
        }])

        self.assertIn("show this only in the terminal", prompt)
        self.assertNotIn("web reply required", prompt)
        self.assertNotIn("send_message", prompt)

    def test_tty_request_keeps_user_text_raw_and_adds_one_shot_mcp_hint(self):
        prompt = format_web_chat_wake_batch_prompt([{
            "id": 142,
            "message": "raw terminal text",
            "meta": {
                "injection_mode": "tty",
                "response_mode": "mcp",
                "response_mcp": {
                    "server": "ai-net",
                    "tool": "send_message",
                    "hint": "reply to the originating room",
                },
            },
        }])

        self.assertTrue(prompt.startswith("raw terminal text\n\n"))
        self.assertNotIn("[ciel-runtime web text]", prompt)
        self.assertIn('"server":"ai-net"', prompt)
        self.assertIn('"tool":"send_message"', prompt)
        self.assertIn("one-shot", prompt)

    def test_cielarvis_internal_capability_prompt_is_tty_safe_and_correlated(self):
        prompt = format_web_chat_wake_batch_prompt(
            [{
                "id": 23,
                "channel": "cielarvis-session-1",
                "thread_id": "session-1",
                "kind": "web_chat",
                "message": "[CIELARVIS voice recovery] Check speech health and guide Colab setup.",
                "meta": {
                    "source": "cielarvis-desktop",
                    "reply_channel": "cielarvis-session-1",
                    "input_mode": "voice",
                    "cielarvis_ui_visibility": "internal",
                    "web_reply_token": "opaque-token",
                },
            }]
        )

        self.assertLess(len(prompt), 600)
        self.assertNotIn("\n", prompt)
        self.assertIn("Check speech health", prompt)
        self.assertIn('"parent_id":"23"', prompt)
        self.assertIn('"reply_token":"opaque-token"', prompt)
        self.assertIn("kind=ack, then kind=reply", prompt)

    def test_web_chat_image_attachment_requires_native_visual_inspection(self):
        prompt = format_web_chat_wake_batch_prompt(
            [
                {
                    "id": 132,
                    "channel": "web-chat-session",
                    "thread_id": "thread-9",
                    "kind": "web_chat",
                    "message": "이 이미지를 확인해줘",
                    "meta": {
                        "source": "ciel-runtime-web-chat",
                        "reply_channel": "web-chat-session",
                        "input_mode": "text",
                        "runtime_attachments": [
                            {
                                "name": "screen.png",
                                "content_type": "image/png",
                                "bytes": 123,
                                "local_path": "C:\\private\\screen.png",
                                "url": "http://router/ca/chat/files/screen.png",
                            }
                        ],
                    },
                }
            ]
        )

        self.assertIn('"content_type":"image/png"', prompt)
        self.assertIn('"local_path":"C:\\\\private\\\\screen.png"', prompt)
        self.assertNotIn("http://router", prompt)
        self.assertIn("inspect every image/* attachment", prompt)
        self.assertIn("native image-reading tool", prompt)
        self.assertNotIn("\n", prompt)

    def test_skip_policy_rejects_control_and_self_echo_messages(self):
        control = {"message": "ready", "meta": {"sse_source": "remote", "kind": "status"}}
        self_echo = {"message": "update", "meta": {"sse_source": "ciel-runtime-router"}}
        self.assertEqual("status", llm_message_skip_reason(control))
        self.assertEqual("native_router_self_echo", llm_message_skip_reason(self_echo))


if __name__ == "__main__":
    unittest.main()
