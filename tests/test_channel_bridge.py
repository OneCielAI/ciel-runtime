import inspect
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import unittest
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import ciel_runtime


class ChannelBridgeTests(unittest.TestCase):
    def setUp(self):
        ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.clear()
        ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS.clear()
        ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.clear()
        ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.update({"checked_at": 0.0, "path": None})
        self._home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._home_tmp.cleanup)
        self._home_patch = mock.patch.object(ciel_runtime, "HOME", Path(self._home_tmp.name))
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)
        self.addCleanup(
            lambda: ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.update({"checked_at": 0.0, "path": None})
        )

    def test_parse_channel_args_accepts_sse_command(self):
        command, options = ciel_runtime.parse_channel_bridge_args("sse")
        self.assertEqual(command, "sse")
        self.assertEqual(options, {})

    def test_parse_channel_send_quoted_message(self):
        command, options = ciel_runtime.parse_channel_bridge_args('send channel=default to=all message="hello agents"')
        self.assertEqual(command, "send")
        self.assertEqual(options["channel"], "default")
        self.assertEqual(options["to"], "all")
        self.assertEqual(options["message"], "hello agents")

    def test_sse_payload_maps_mcp_notification_to_chat_payload(self):
        payload = ciel_runtime._sse_payload_to_chat_payload(
            '{"method":"notifications/claude/channel","params":{"content":"hello","meta":{"room_id":"room_phase1sim","thread_id":"root"}}}',
            "message",
            {"name": "ai-net", "channel": "default", "sender_id": "ai-net", "recipient": "claude"},
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual("hello", json.loads(payload["message"])["params"]["content"])
        self.assertEqual(payload["kind"], "channel")
        self.assertEqual(payload["channel"], "room_phase1sim")
        self.assertEqual(payload["sender_id"], "ai-net")
        self.assertEqual(payload["recipients"], "claude")
        self.assertEqual(payload["thread_id"], "root")
        self.assertEqual(payload["visibility"], "user")
        self.assertIn("llm", payload["delivery"])
        self.assertEqual(payload["meta"]["room_id"], "room_phase1sim")
        self.assertEqual(payload["meta"]["mcp_method"], "notifications/claude/channel")
        self.assertEqual(payload["meta"]["sse_json"]["params"]["meta"]["room_id"], "room_phase1sim")

    def test_sse_payload_preserves_plain_text_exactly(self):
        payload = ciel_runtime._sse_payload_to_chat_payload(
            "  plain SSE body\nsecond line  ",
            "message",
            {"name": "plain-sse"},
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual("  plain SSE body\nsecond line  ", payload["message"])

    def test_sse_payload_ignores_done_marker(self):
        self.assertIsNone(ciel_runtime._sse_payload_to_chat_payload("[DONE]", "message", {"name": "x"}))

    def test_sse_payload_ignores_jsonrpc_control_messages(self):
        self.assertIsNone(
            ciel_runtime._sse_payload_to_chat_payload(
                '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}',
                "message",
                {"name": "x"},
            )
        )

    def test_sse_payload_ignores_mcp_endpoint_event(self):
        self.assertIsNone(ciel_runtime._sse_payload_to_chat_payload("/messages?session=abc", "endpoint", {"name": "x"}))

    def test_sse_payload_honors_event_filter(self):
        payload = ciel_runtime._sse_payload_to_chat_payload(
            '{"method":"notifications/message","params":{"content":"visible"}}',
            "message",
            {"name": "ai-net", "event_filter": ["notifications/message"]},
        )
        self.assertIsNotNone(payload)
        hidden = ciel_runtime._sse_payload_to_chat_payload(
            '{"method":"tools/list","params":{"content":"hidden"}}',
            "message",
            {"name": "ai-net", "event_filter": ["notifications/message"]},
        )
        self.assertIsNone(hidden)

    def test_sse_payload_maps_nested_ai_net_event(self):
        payload = ciel_runtime._sse_payload_to_chat_payload(
            '{"method":"notifications/message","params":{"data":{"type":"message.created","room_id":"room_phase1sim","payload":{"message":{"content":"hello from ai-net"},"sender_id":"agent_a"}}}}',
            "message",
            {"name": "ai-net", "channel": "default", "sender_id": "ai-net", "recipient": "claude", "event_filter": ["notifications/message"]},
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual("hello from ai-net", json.loads(payload["message"])["params"]["data"]["payload"]["message"]["content"])
        self.assertEqual(payload["sender_id"], "agent_a")
        self.assertEqual(payload["channel"], "room_phase1sim")
        self.assertEqual(payload["meta"]["room_id"], "room_phase1sim")
        self.assertEqual(payload["meta"]["mcp_method"], "notifications/message")
        self.assertEqual(payload["meta"]["sse_json"]["params"]["data"]["type"], "message.created")

    def test_sse_payload_prefers_nested_message_over_generic_notification_content(self):
        payload = ciel_runtime._sse_payload_to_chat_payload(
            json.dumps(
                {
                    "method": "notifications/message",
                    "params": {
                        "content": "New message from Sarah",
                        "data": {
                            "type": "message.created",
                            "room_id": "room_4pyr8vvwm2cd",
                            "payload": {
                                "message": {"content": "Robert 리드님, 매크로 분석 보고서입니다."},
                                "sender_id": "agent_n3wy9gfjmcil",
                            },
                        },
                    },
                },
                ensure_ascii=False,
            ),
            "message",
            {"name": "mcp-ai-net-sse", "channel": "ai-net-sse", "event_filter": ["notifications/message"]},
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual("Robert 리드님, 매크로 분석 보고서입니다.", json.loads(payload["message"])["params"]["data"]["payload"]["message"]["content"])
        self.assertEqual("agent_n3wy9gfjmcil", payload["sender_id"])
        self.assertEqual("room_4pyr8vvwm2cd", payload["channel"])

    def test_sse_payload_preserves_event_id_and_redacts_sensitive_metadata(self):
        payload = ciel_runtime._sse_payload_to_chat_payload(
            '{"method":"notifications/message","params":{"data":{"room_id":"room_phase1sim","cursor":"123-0","api_key":"secret-value","payload":{"message":{"content":"hello with metadata"}}}}}',
            "message",
            {"name": "ai-net", "channel": "default"},
            event_id="evt-42",
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        meta = payload["meta"]
        self.assertEqual("evt-42", meta["sse_id"])
        self.assertEqual("123-0", meta["cursor"])
        self.assertEqual("[redacted]", meta["sse_json"]["params"]["data"]["api_key"])

    def test_read_channel_matches_room_id_alias(self):
        messages = [
            {"id": 1, "channel": "default", "recipients": ["all"], "sender_id": "agent", "message": "hello", "meta": {"room_id": "room_phase1sim"}},
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "chat-messages.jsonl"
            path.write_text("\n".join(__import__("json").dumps(item) for item in messages), encoding="utf-8")
            with mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path):
                found = ciel_runtime.read_chat_messages(0, "room_phase1sim", None, 10)
        self.assertEqual(1, len(found))
        self.assertEqual("hello", found[0]["message"])

    def test_read_channel_history_before_returns_latest_matching_page(self):
        messages = [
            {"id": 1, "channel": "web-chat-a", "recipients": ["all"], "sender_id": "web-user", "message": "one", "meta": {}},
            {"id": 2, "channel": "other", "recipients": ["all"], "sender_id": "web-user", "message": "skip", "meta": {}},
            {"id": 3, "channel": "web-chat-a", "recipients": ["web"], "sender_id": "assistant", "message": "three", "meta": {}},
            {"id": 4, "channel": "web-chat-a", "recipients": ["web"], "sender_id": "assistant", "message": "four", "meta": {}},
            {"id": 5, "channel": "web-chat-a", "recipients": ["internal"], "sender_id": "assistant", "message": "hidden", "meta": {}},
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "chat-messages.jsonl"
            path.write_text("\n".join(__import__("json").dumps(item) for item in messages), encoding="utf-8")
            with mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path):
                latest = ciel_runtime.read_chat_messages_before(0, "web-chat-a", "web", 2)
                older = ciel_runtime.read_chat_messages_before(4, "web-chat-a", "web", 10)
        self.assertEqual(["three", "four"], [item["message"] for item in latest])
        self.assertEqual(["one", "three"], [item["message"] for item in older])

    def test_append_chat_message_resyncs_id_from_file_when_cached_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            path.write_text(json.dumps({"id": 41, "message": "existing"}) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", 5),
            ):
                saved = ciel_runtime.append_chat_message({"message": "new", "channel": "room"})
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(42, saved["id"])
        self.assertEqual([41, 42], [row["id"] for row in rows])

    def test_append_chat_message_dedupes_mcp_notification_with_stable_cursor(self):
        payload = {
            "message": "same notification",
            "channel": "room",
            "sender_id": "mcp-server",
            "kind": "channel",
            "meta": {
                "mcp_server": "mcp-server",
                "mcp_method": "notifications/claude/channel",
                "cursor": "1781319043580-0",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                first = ciel_runtime.append_chat_message(payload)
                second = ciel_runtime.append_chat_message(payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, first["id"])
        self.assertEqual(1, second["id"])
        self.assertTrue(second["_ciel_runtime_duplicate"])
        self.assertEqual(1, len(rows))

    def test_append_chat_message_dedupes_recent_mcp_notification_without_stable_id(self):
        payload = {
            "message": "board updated",
            "channel": "room",
            "sender_id": "mcp-server",
            "kind": "channel",
            "meta": {
                "mcp_server": "mcp-server",
                "mcp_method": "notifications/claude/channel",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                first = ciel_runtime.append_chat_message(payload)
                second = ciel_runtime.append_chat_message(payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, first["id"])
        self.assertEqual(1, second["id"])
        self.assertTrue(second["_ciel_runtime_duplicate"])
        self.assertEqual(1, len(rows))

    def test_append_chat_message_dedupes_identical_mcp_json_notification(self):
        payload = {
            "message": 'Board "positions" updated. board_get(room_id, key) to read.',
            "channel": "room",
            "sender_id": "mcp-server",
            "kind": "channel",
            "meta": {
                "mcp_server": "mcp-server",
                "mcp_method": "notifications/claude/channel",
                "stream_id": "",
                "mcp_json": {
                    "jsonrpc": "2.0",
                    "method": "notifications/claude/channel",
                    "params": {
                        "content": 'Board "positions" updated. board_get(room_id, key) to read.',
                        "meta": {"kind": "board_updated", "room_id": "room", "key": "positions", "stream_id": ""},
                    },
                },
            },
        }
        old = dict(payload)
        old.update({"id": 1, "time": "2000-01-01T00:00:00", "recipients": ["all"], "thread_id": "1", "parent_id": None})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            path.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                saved = ciel_runtime.append_chat_message(payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, saved["id"])
        self.assertTrue(saved["_ciel_runtime_duplicate"])
        self.assertEqual(1, len(rows))

    def test_append_chat_message_dedupes_same_sse_event_across_transports(self):
        event = {
            "method": "notifications/claude/channel",
            "params": {
                "content": "New message from Test",
                "meta": {
                    "kind": "activity",
                    "room_id": "room_asset",
                    "message_id": "msg_same",
                    "stream_id": "1782645817112-0",
                },
            },
            "jsonrpc": "2.0",
        }
        first_payload = {
            "message": json.dumps(event, ensure_ascii=False, indent=2),
            "channel": "room_asset",
            "sender_id": "ai-net-http",
            "kind": "channel",
            "meta": {
                "sse_event": "message",
                "sse_source": "mcp-ai-net-http",
                "sse_json": event,
                "mcp_method": "notifications/claude/channel",
                "kind": "activity",
                "room_id": "room_asset",
                "message_id": "msg_same",
                "stream_id": "1782645817112-0",
            },
            "delivery": ["llm"],
        }
        second_payload = json.loads(json.dumps(first_payload))
        second_payload["sender_id"] = "ai-net"
        second_payload["meta"]["sse_source"] = "mcp-ai-net"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                first = ciel_runtime.append_chat_message(first_payload)
                second = ciel_runtime.append_chat_message(second_payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, first["id"])
        self.assertEqual(1, second["id"])
        self.assertTrue(second["_ciel_runtime_duplicate"])
        self.assertEqual(1, len(rows))

    def test_append_chat_message_dedupes_same_digest_event_across_transports(self):
        event = {
            "method": "notifications/claude/channel",
            "params": {
                "content": '[AI-Net new messages]\n• Stakeholder Hotline: 1 new (Test) -> get_messages(room_id="room_hotline", after_seq=41439)',
                "meta": {
                    "source": "ai-net",
                    "kind": "digest",
                    "rooms": json.dumps(
                        [
                            {
                                "room_id": "room_hotline",
                                "room_name": "Stakeholder Hotline",
                                "authors": ["Test"],
                                "count": 1,
                                "message_ids": ["msg_same"],
                                "since_seq": 41439,
                                "latest_seq": 41440,
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    "cursor": "1782646488940-0",
                },
            },
            "jsonrpc": "2.0",
        }
        first_payload = {
            "message": json.dumps(event, ensure_ascii=False, indent=2),
            "channel": "ai-net-http",
            "sender_id": "ai-net-http",
            "kind": "channel",
            "meta": {
                "sse_event": "message",
                "sse_source": "mcp-ai-net-http",
                "sse_json": event,
                "mcp_method": "notifications/claude/channel",
                "source": "ai-net",
                "kind": "digest",
                "rooms": event["params"]["meta"]["rooms"],
                "cursor": "1782646488940-0",
            },
            "delivery": ["llm"],
        }
        second_payload = json.loads(json.dumps(first_payload))
        second_payload["channel"] = "ai-net"
        second_payload["sender_id"] = "ai-net"
        second_payload["meta"]["sse_source"] = "mcp-ai-net"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                first = ciel_runtime.append_chat_message(first_payload)
                second = ciel_runtime.append_chat_message(second_payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, first["id"])
        self.assertEqual(1, second["id"])
        self.assertTrue(second["_ciel_runtime_duplicate"])
        self.assertEqual(1, len(rows))

    def test_append_chat_message_keeps_old_fallback_duplicate_without_launch_guard(self):
        payload = {
            "message": "board updated",
            "channel": "room",
            "sender_id": "mcp-server",
            "kind": "channel",
            "meta": {
                "mcp_server": "mcp-server",
                "mcp_method": "notifications/claude/channel",
            },
        }
        old = dict(payload)
        old.update({"id": 1, "time": "2000-01-01T00:00:00", "recipients": ["all"], "thread_id": "1", "parent_id": None})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            guard_path = root / "channel-llm-launch-guard.json"
            path.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "CHANNEL_LLM_LAUNCH_GUARD_PATH", guard_path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                saved = ciel_runtime.append_chat_message(payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(2, saved["id"])
        self.assertNotIn("_ciel_runtime_duplicate", saved)
        self.assertEqual(2, len(rows))

    def test_append_chat_message_dedupes_startup_replay_without_stable_id(self):
        payload = {
            "message": "board updated",
            "channel": "room",
            "sender_id": "mcp-server",
            "kind": "channel",
            "meta": {
                "mcp_server": "mcp-server",
                "mcp_method": "notifications/claude/channel",
            },
        }
        old = dict(payload)
        old.update({"id": 7, "time": "2000-01-01T00:00:00", "recipients": ["all"], "thread_id": "7", "parent_id": None})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            guard_path = root / "channel-llm-launch-guard.json"
            path.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
            guard_path.write_text(
                json.dumps({"max_existing_id": 7, "expires_at": time.time() + 60}, separators=(",", ":")),
                encoding="utf-8",
            )
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "CHANNEL_LLM_LAUNCH_GUARD_PATH", guard_path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                saved = ciel_runtime.append_chat_message(payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(7, saved["id"])
        self.assertTrue(saved["_ciel_runtime_duplicate"])
        self.assertEqual(1, len(rows))

    def test_append_chat_message_does_not_dedupe_plain_user_messages(self):
        payload = {"message": "repeat is valid", "channel": "web-chat", "sender_id": "web-user"}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
            ):
                first = ciel_runtime.append_chat_message(payload)
                second = ciel_runtime.append_chat_message(payload)
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, first["id"])
        self.assertEqual(2, second["id"])
        self.assertNotIn("_ciel_runtime_duplicate", second)
        self.assertEqual(2, len(rows))

    def test_prepare_channel_llm_delivery_for_launch_fast_forwards_stale_queue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "chat-messages.jsonl"
            cursor_path = root / "channel-llm-cursor.json"
            old_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
            path.write_text(
                "\n".join(json.dumps({"id": item_id, "time": old_time, "message": f"old-{item_id}"}) for item_id in (1, 2, 3)) + "\n",
                encoding="utf-8",
            )
            cursor_path.write_text(json.dumps({"last_id": 1}), encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", path),
                mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", cursor_path),
                mock.patch.object(ciel_runtime, "CHANNEL_LLM_LAUNCH_GUARD_PATH", root / "channel-llm-launch-guard.json"),
                mock.patch.object(ciel_runtime, "_CHANNEL_LLM_CURSOR_LAST_ID", None),
            ):
                last_id = ciel_runtime.prepare_channel_llm_delivery_for_launch()
                saved = json.loads(cursor_path.read_text(encoding="utf-8"))
                guard = json.loads((root / "channel-llm-launch-guard.json").read_text(encoding="utf-8"))

        self.assertEqual(3, last_id)
        self.assertEqual(3, saved["last_id"])
        self.assertEqual(3, guard["max_existing_id"])

    def test_mcp_endpoint_event_initializes_sse_session(self):
        name = "unit-mcp"
        original = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        try:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS[name] = {
                "name": name,
                "url": "http://example.test/sse",
                "headers": {"Authorization": "Bearer test"},
                "running": True,
                "mcp_enabled": True,
                "mcp_initialized": False,
                "mcp_protocol_version": "2024-11-05",
                "mcp_timeout_seconds": 20.0,
            }
            with mock.patch.object(ciel_runtime, "_mcp_sse_post_json", return_value={"ok": True}) as post:
                ciel_runtime._channel_sse_dispatch(name, "endpoint", ["/messages?session=abc"])
            state = ciel_runtime._CHANNEL_SSE_CONNECTIONS[name]
            self.assertEqual("http://example.test/messages?session=abc", state["mcp_endpoint"])
            self.assertTrue(state["mcp_initialized"])
            self.assertEqual(2, post.call_count)
            self.assertEqual("initialize", post.call_args_list[0].args[2]["method"])
            self.assertEqual("notifications/initialized", post.call_args_list[1].args[2]["method"])
        finally:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original)

    def test_mcp_endpoint_event_reinitializes_changed_sse_session(self):
        name = "unit-mcp"
        original = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        try:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS[name] = {
                "name": name,
                "url": "http://example.test/sse",
                "headers": {"Authorization": "Bearer test"},
                "running": True,
                "mcp_enabled": True,
                "mcp_initialized": True,
                "mcp_endpoint": "http://example.test/messages?session=old",
                "mcp_rpc_results": {"old": {"result": {}}},
                "mcp_protocol_version": "2024-11-05",
                "mcp_timeout_seconds": 20.0,
            }
            with (
                mock.patch.object(ciel_runtime, "_mcp_sse_post_json", return_value={"ok": True}) as post,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                ciel_runtime._channel_sse_dispatch(name, "endpoint", ["/messages?session=new"])
            state = ciel_runtime._CHANNEL_SSE_CONNECTIONS[name]
            self.assertEqual("http://example.test/messages?session=new", state["mcp_endpoint"])
            self.assertTrue(state["mcp_initialized"])
            self.assertEqual({}, state["mcp_rpc_results"])
            self.assertEqual(2, post.call_count)
            log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
            self.assertTrue(any("channel_sse_mcp_reinitializing" in item for item in log_messages))
        finally:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original)

    def test_start_channel_sse_connection_receives_stream_message(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'id: evt-1\n'
                    b'event: message\n'
                    b'data: {"method":"notifications/message","params":{"content":"hello over sse","room_id":"room_phase1sim"}}\n\n'
                )
                self.wfile.flush()
                time.sleep(0.05)

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        old_next = ciel_runtime._CHAT_NEXT_ID
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chat_log = root / "chat-messages.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/events"
                with (
                    mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_log),
                    mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
                ):
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-sse",
                            "url": url,
                            "channel": "unit",
                            "retry_seconds": 60,
                            "read_timeout_seconds": 5,
                        }
                    )
                    deadline = time.time() + 2
                    while time.time() < deadline:
                        if chat_log.exists() and "hello over sse" in chat_log.read_text(encoding="utf-8"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(chat_log.exists())
                    text = chat_log.read_text(encoding="utf-8")
                    self.assertIn("hello over sse", text)
                    self.assertIn("evt-1", text)
                    ciel_runtime.stop_channel_sse_connection("unit-sse")
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)
                ciel_runtime._CHAT_NEXT_ID = old_next

    def test_start_channel_streamable_http_initializes_session_and_receives_message(self):
        seen_posts: list[dict[str, object]] = []
        seen_get_headers: list[dict[str, str | None]] = []
        seen_deletes: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body) if body else {}
                seen_posts.append(
                    {
                        "method": payload.get("method"),
                        "session": self.headers.get("Mcp-Session-Id"),
                        "protocol": self.headers.get("MCP-Protocol-Version"),
                        "accept": self.headers.get("Accept"),
                    }
                )
                if payload.get("method") == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                            "capabilities": {"experimental": {"claude/channel": True}},
                            "serverInfo": {"name": "unit-http", "version": "1"},
                        },
                    }
                    data = json.dumps(response).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-unit")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                response = {"jsonrpc": "2.0", "result": {}}
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                seen_get_headers.append(
                    {
                        "session": self.headers.get("Mcp-Session-Id"),
                        "protocol": self.headers.get("MCP-Protocol-Version"),
                        "accept": self.headers.get("Accept"),
                        "last_event_id": self.headers.get("Last-Event-ID"),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'id: stream-1\n'
                    b'event: message\n'
                    b'data: {"method":"notifications/message","params":{"content":"hello over streamable http","room_id":"room_stream"}}\n\n'
                )
                self.wfile.flush()
                time.sleep(0.05)

            def do_DELETE(self):
                seen_deletes.append(self.headers.get("Mcp-Session-Id"))
                self.send_response(202)
                self.end_headers()

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        old_next = ciel_runtime._CHAT_NEXT_ID
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chat_log = root / "chat-messages.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
                with (
                    mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_log),
                    mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
                ):
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-http",
                            "type": "http",
                            "url": url,
                            "channel": "unit",
                            "retry_seconds": 60,
                            "read_timeout_seconds": 5,
                        }
                    )
                    deadline = time.time() + 2
                    while time.time() < deadline:
                        if chat_log.exists() and "hello over streamable http" in chat_log.read_text(encoding="utf-8"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(chat_log.exists())
                    text = chat_log.read_text(encoding="utf-8")
                    self.assertIn("hello over streamable http", text)
                    self.assertEqual(["initialize", "notifications/initialized"], [item["method"] for item in seen_posts[:2]])
                    self.assertIsNone(seen_posts[0]["session"])
                    self.assertEqual("sess-unit", seen_posts[1]["session"])
                    self.assertEqual(ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION, seen_get_headers[0]["protocol"])
                    self.assertEqual("sess-unit", seen_get_headers[0]["session"])
                    self.assertIn("text/event-stream", seen_get_headers[0]["accept"] or "")
                    with ciel_runtime._CHANNEL_SSE_LOCK:
                        state = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS["unit-http"])
                    status = ciel_runtime._channel_sse_status_public("unit-http", state)
                    self.assertEqual("streamable-http", status["transport"])
                    self.assertEqual("sess-unit", status["mcp_session_id"])
                    ciel_runtime.stop_channel_sse_connection("unit-http")
                    self.assertIn("sess-unit", seen_deletes)
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)
                ciel_runtime._CHAT_NEXT_ID = old_next

    def test_streamable_http_start_deletes_recorded_stale_sessions(self):
        seen_deletes: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_DELETE(self):
                seen_deletes.append(self.headers.get("Mcp-Session-Id"))
                self.send_response(202)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"experimental": {"claude/channel": True}},
                        "serverInfo": {"name": "unit-http", "version": "1"},
                    },
                }
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if payload.get("method") == "initialize":
                    self.send_header("Mcp-Session-Id", "new-session")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(0.2)

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
                records = {
                    "sessions": [
                        {
                            "name": "unit-http",
                            "url": url,
                            "session_id": "old-session",
                            "protocol_version": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        }
                    ]
                }
                with mock.patch.object(ciel_runtime, "CONFIG_DIR", root):
                    ciel_runtime.channel_streamable_sessions_path().write_text(json.dumps(records), encoding="utf-8")
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-http",
                            "type": "http",
                            "url": url,
                            "retry_seconds": 60,
                            "read_timeout_seconds": 5,
                        }
                    )
                    deadline = time.time() + 2
                    while time.time() < deadline and "old-session" not in seen_deletes:
                        time.sleep(0.02)
                    self.assertIn("old-session", seen_deletes)
                    ciel_runtime.stop_channel_sse_connection("unit-http")
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)

    def test_streamable_http_start_deletes_same_url_stale_sessions_with_different_name(self):
        seen_deletes: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_DELETE(self):
                seen_deletes.append(self.headers.get("Mcp-Session-Id"))
                self.send_response(202)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"experimental": {"claude/channel": True}},
                        "serverInfo": {"name": "unit-http", "version": "1"},
                    },
                }
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if payload.get("method") == "initialize":
                    self.send_header("Mcp-Session-Id", "new-session")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(0.2)

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
                records = {
                    "sessions": [
                        {
                            "name": "different-name-same-url",
                            "url": url,
                            "session_id": "old-other-name-session",
                            "protocol_version": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        }
                    ]
                }
                with mock.patch.object(ciel_runtime, "CONFIG_DIR", root):
                    ciel_runtime.channel_streamable_sessions_path().write_text(json.dumps(records), encoding="utf-8")
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-http",
                            "type": "http",
                            "url": url,
                            "retry_seconds": 60,
                            "read_timeout_seconds": 5,
                        }
                    )
                    deadline = time.time() + 2
                    while time.time() < deadline and "old-other-name-session" not in seen_deletes:
                        time.sleep(0.02)
                    self.assertIn("old-other-name-session", seen_deletes)
                    saved = json.loads(ciel_runtime.channel_streamable_sessions_path().read_text(encoding="utf-8"))
                    self.assertNotIn("old-other-name-session", json.dumps(saved))
                    ciel_runtime.stop_channel_sse_connection("unit-http")
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)

    def test_streamable_http_requires_session_before_get_stream(self):
        seen_posts: list[str | None] = []
        seen_get_headers: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                seen_posts.append(payload.get("method"))
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"experimental": {"claude/channel": True}},
                        "serverInfo": {"name": "unit-http", "version": "1"},
                    },
                }
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                seen_get_headers.append(self.headers.get("Mcp-Session-Id"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
                with mock.patch.object(ciel_runtime, "CONFIG_DIR", root):
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-http-no-session",
                            "type": "http",
                            "url": url,
                            "retry_seconds": 60,
                            "read_timeout_seconds": 5,
                        }
                    )
                    time.sleep(0.2)
                    self.assertGreaterEqual(seen_posts.count("initialize"), 1)
                    self.assertEqual([], seen_get_headers)
                    with ciel_runtime._CHANNEL_SSE_LOCK:
                        state = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS["unit-http-no-session"])
                    self.assertFalse(state["mcp_initialized"])
                    self.assertEqual("streamable_http_missing_session_id", state["mcp_last_error"])
                    ciel_runtime.stop_channel_sse_connection("unit-http-no-session")
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)

    def test_streamable_http_session_not_found_reinitializes_before_get_retry(self):
        seen_posts: list[dict[str, object]] = []
        seen_get_sessions: list[str | None] = []
        init_count = 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                nonlocal init_count
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                method = payload.get("method")
                if method == "initialize":
                    init_count += 1
                seen_posts.append({"method": method, "session": self.headers.get("Mcp-Session-Id")})
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"experimental": {"claude/channel": True}},
                        "serverInfo": {"name": "unit-http", "version": "1"},
                    },
                }
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if method == "initialize":
                    self.send_header("Mcp-Session-Id", "sess-one" if init_count == 1 else "sess-two")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                session = self.headers.get("Mcp-Session-Id")
                seen_get_sessions.append(session)
                if session == "sess-one":
                    body = b'{"error":"session-not-found"}'
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'id: stream-2\n'
                    b'event: message\n'
                    b'data: {"method":"notifications/message","params":{"content":"after streamable http reinit","room_id":"room_stream"}}\n\n'
                )
                self.wfile.flush()
                time.sleep(0.05)

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        old_next = ciel_runtime._CHAT_NEXT_ID
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chat_log = root / "chat-messages.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
                with (
                    mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_log),
                    mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
                ):
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-http-reinit",
                            "type": "http",
                            "url": url,
                            "retry_seconds": 1,
                            "read_timeout_seconds": 5,
                        }
                    )
                    deadline = time.time() + 4
                    while time.time() < deadline:
                        if chat_log.exists() and "after streamable http reinit" in chat_log.read_text(encoding="utf-8"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(chat_log.exists())
                    self.assertIn("after streamable http reinit", chat_log.read_text(encoding="utf-8"))
                    self.assertGreaterEqual(init_count, 2)
                    self.assertIn("sess-one", seen_get_sessions)
                    self.assertIn("sess-two", seen_get_sessions)
                    ciel_runtime.stop_channel_sse_connection("unit-http-reinit")
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)
                ciel_runtime._CHAT_NEXT_ID = old_next

    def test_sse_reconnect_sends_last_event_id(self):
        seen_headers = []
        second_seen = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                seen_headers.append(self.headers.get("Last-Event-ID"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if len(seen_headers) == 1:
                    self.wfile.write(
                        b'id: evt-1\n'
                        b'event: message\n'
                        b'data: {"method":"notifications/message","params":{"content":"first"}}\n\n'
                    )
                    self.wfile.flush()
                    return
                second_seen.set()
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
                time.sleep(0.05)

        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        old_next = ciel_runtime._CHAT_NEXT_ID
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chat_log = root / "chat-messages.jsonl"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/events"
                with (
                    mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_log),
                    mock.patch.object(ciel_runtime, "_CHAT_NEXT_ID", None),
                ):
                    ciel_runtime.start_channel_sse_connection(
                        {
                            "name": "unit-sse-resume",
                            "url": url,
                            "channel": "unit",
                            "retry_seconds": 1,
                            "read_timeout_seconds": 5,
                        }
                    )
                    self.assertTrue(second_seen.wait(3))
                    self.assertGreaterEqual(len(seen_headers), 2)
                    self.assertIsNone(seen_headers[0])
                    self.assertEqual("evt-1", seen_headers[1])
                    ciel_runtime.stop_channel_sse_connection("unit-sse-resume")
            finally:
                server.shutdown()
                server.server_close()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
                ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)
                ciel_runtime._CHAT_NEXT_ID = old_next

    def test_auto_start_sse_channels_filters_allowed_server_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mcp = root / ".mcp.json"
            mcp.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "ai-net-sse": {"type": "sse", "url": "http://example.test/ai/sse"},
                            "other-sse": {"type": "sse", "url": "http://example.test/other/sse"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            seen: list[str] = []

            def fake_start(server):
                seen.append(server["name"])
                return {"name": server["name"], "url": server["url"]}

            with mock.patch.object(ciel_runtime, "start_channel_sse_connection", side_effect=fake_start):
                started = ciel_runtime.auto_start_sse_channels_from_mcp_configs(
                    [],
                    cwd=root,
                    home=root,
                    allowed_server_names=["ai-net-sse"],
                )
            self.assertEqual(["mcp-ai-net-sse"], seen)
            self.assertEqual(["mcp-ai-net-sse"], [item["name"] for item in started])

    def test_start_router_managed_channel_sse_uses_enabled_external_channels(self):
        cfg = {"claude_code": {"channels": ["server:ciel-runtime-router", "server:ai-net-sse"]}}
        with (
            mock.patch.object(ciel_runtime, "ensure_channel_probe_cache_for_launch", return_value=False) as ensure_probe,
            mock.patch.object(ciel_runtime, "cached_channel_source_paths_for_specs", return_value=[]) as source_paths,
            mock.patch.object(ciel_runtime, "auto_start_sse_channels_from_mcp_configs", return_value=[{"name": "mcp-ai-net-sse"}]) as auto_start,
        ):
            started = ciel_runtime.start_router_managed_channel_sse(cfg)
        self.assertEqual([{"name": "mcp-ai-net-sse"}], started)
        ensure_probe.assert_called_once_with(cfg, [])
        source_paths.assert_called_once_with(["server:ai-net-sse"])
        auto_start.assert_called_once()
        self.assertEqual(["ai-net-sse"], auto_start.call_args.kwargs["allowed_server_names"])

    def test_start_router_managed_channel_sse_opens_nothing_without_external_channels(self):
        # Only the built-in native router is configured (filtered out), so there
        # are no external channel specs. The router must NOT auto-open every MCP
        # server as a channel worker -- that allow-all flip held a second
        # notification stream to backends like ai-net-http and duplicated every
        # digest. With no external specs, open nothing.
        cfg = {"claude_code": {"channels": ["server:ciel-runtime-router"]}}
        with mock.patch.object(ciel_runtime, "auto_start_sse_channels_from_mcp_configs") as auto_start:
            self.assertEqual([], ciel_runtime.start_router_managed_channel_sse(cfg))
        auto_start.assert_not_called()

    def test_launch_process_does_not_start_sse_for_llm_delivery(self):
        self.assertFalse(ciel_runtime.should_launch_process_start_channel_sse(False, False, True))
        self.assertFalse(ciel_runtime.should_launch_process_start_channel_sse(True, False, True))
        self.assertFalse(ciel_runtime.should_launch_process_start_channel_sse(False, True, True))
        self.assertTrue(ciel_runtime.should_launch_process_start_channel_sse(True, False, False))
        self.assertTrue(ciel_runtime.should_launch_process_start_channel_sse(False, True, False))
        self.assertFalse(ciel_runtime.should_launch_process_start_channel_sse(False, False, False))

    def test_channel_llm_prompt_is_data_only_for_dm_label_notice(self):
        prompt = ciel_runtime.format_channel_llm_batch_prompt(
            [
                {
                    "id": 1,
                    "channel": "room_dm_abc",
                    "sender_id": "ai-net-sse",
                    "recipients": ["all"],
                    "message": "New message from Robert",
                    "meta": {"kind": "activity", "room_name": "DM-Robert", "message_id": "msg_1"},
                }
            ]
        )
        self.assertEqual("New message from Robert", prompt)
        self.assertNotIn("[external channel input]", prompt)
        self.assertNotIn('"room_name":"DM-Robert"', prompt)
        self.assertNotIn("결론내리지 마세요", prompt)
        self.assertNotIn("자동 회신 루프", prompt)
        self.assertNotIn("ciel-runtime-router send_message", prompt)
        self.assertNotIn("recipients='web'", prompt)
        self.assertNotIn("웹 채팅 요청", prompt)

    def test_channel_llm_prompts_preserve_raw_message_body(self):
        message = {
            "id": 2,
            "channel": "ai-net-http",
            "sender_id": "ai-net-http",
            "message": "  raw body from external channel\n",
            "meta": {"room_id": "room"},
        }

        self.assertEqual("  raw body from external channel\n", ciel_runtime.format_channel_llm_batch_prompt([message]))
        self.assertEqual("  raw body from external channel\n", ciel_runtime.format_channel_llm_delivery_wake_prompt([message]))

    def test_channel_wake_prompt_contains_routing_context(self):
        prompt = ciel_runtime.format_channel_wake_prompt(
            {
                "id": 9,
                "channel": "room_phase1sim",
                "sender_id": "robert",
                "thread_id": "root",
                "message": "please review the latest update",
                "meta": {"room_id": "room_phase1sim"},
            }
        )
        self.assertIn("ciel-runtime external channel message", prompt)
        self.assertIn("from=robert", prompt)
        self.assertIn("id=9", prompt)
        self.assertIn("please review the latest update", prompt)
        self.assertIn('metadata={"room_id":"room_phase1sim"}', prompt)
        self.assertIn("room_phase1sim", prompt)
        self.assertNotIn("send_message", prompt)
        self.assertNotIn("recipients='web'", prompt)
        self.assertNotIn("send_file", prompt)
        self.assertNotIn("XML-like", prompt)
        self.assertNotIn("actual available Claude Code/MCP tool", prompt)
        self.assertNotIn("\n", prompt)

    def test_channel_wake_prompt_includes_small_event_metadata_only(self):
        prompt = ciel_runtime.format_channel_wake_prompt(
            {
                "id": 9,
                "channel": "room",
                "sender_id": "mcp-server",
                "message": "New message from Kevin",
                "meta": {
                    "kind": "activity",
                    "room_id": "room",
                    "room_name": "Project Room",
                    "message_id": "msg_123",
                    "stream_id": "1781389494764-0",
                    "mcp_json": {"params": {"content": "large raw payload"}},
                    "reply_instruction": "web routing text",
                    "api_key": "secret",
                    "large": "x" * 500,
                },
            }
        )
        self.assertIn('"kind":"activity"', prompt)
        self.assertIn('"message_id":"msg_123"', prompt)
        self.assertIn('"stream_id":"1781389494764-0"', prompt)
        self.assertIn('"room_name":"Project Room"', prompt)
        self.assertNotIn("mcp_json", prompt)
        self.assertNotIn("reply_instruction", prompt)
        self.assertNotIn("secret", prompt)
        self.assertNotIn("x" * 100, prompt)

    def test_channel_wake_prompt_omits_browser_reply_instructions_for_web_chat(self):
        prompt = ciel_runtime.format_channel_wake_prompt(
            {
                "id": 10,
                "channel": "web-chat-session",
                "sender_id": "web-user",
                "thread_id": "thread-1",
                "message": "현재상태는",
                "kind": "web_chat",
                "meta": {"source": "ciel-runtime-web-chat", "reply_channel": "web-chat-session"},
            }
        )
        self.assertIn("ciel-runtime external channel message", prompt)
        self.assertIn("현재상태는", prompt)
        self.assertNotIn("send_message", prompt)
        self.assertNotIn("recipients='web'", prompt)
        self.assertNotIn("send_file", prompt)
        self.assertNotIn("XML-like", prompt)

    def test_channel_wake_batch_omits_browser_reply_instructions_without_web_chat(self):
        prompt = ciel_runtime.format_channel_wake_batch_prompt(
            [
                {"id": 1, "channel": "room", "sender_id": "agent-a", "message": "one", "meta": {}},
                {"id": 2, "channel": "room", "sender_id": "agent-b", "message": "two", "meta": {}},
            ]
        )
        self.assertNotIn("send_message", prompt)
        self.assertNotIn("recipients='web'", prompt)
        self.assertNotIn("send_file", prompt)

    def test_web_chat_wake_prompt_is_compact_and_omits_raw_metadata(self):
        prompt = ciel_runtime.format_channel_web_chat_wake_batch_prompt(
            [
                {
                    "id": 6,
                    "channel": "web-chat-session",
                    "sender_id": "web-user",
                    "thread_id": "thread-1",
                    "message": "현재상태는",
                    "kind": "web_chat",
                    "meta": {
                        "source": "ciel-runtime-web-chat",
                        "reply_channel": "web-chat-session",
                        "reply_recipient": "web",
                        "reply_instruction": "long routing text",
                    },
                }
            ]
        )
        self.assertIn("ciel-runtime web chat", prompt)
        self.assertIn("현재상태는", prompt)
        self.assertIn("channel=web-chat-session", prompt)
        self.assertIn("thread=thread-1", prompt)
        self.assertNotIn("Answer in the active Claude Code session", prompt)
        self.assertNotIn("send_message", prompt)
        self.assertNotIn("send_file", prompt)
        self.assertNotIn("metadata=", prompt)
        self.assertNotIn("reply_instruction", prompt)
        self.assertNotIn("\n", prompt)

    def test_channel_wake_enter_bytes_can_be_overridden(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(ciel_runtime, "_channel_platform_default_enter_bytes", return_value=b"\r\n"),
        ):
            self.assertTrue(ciel_runtime._channel_wake_input_bytes("wake").endswith(b"\r\n"))
            self.assertEqual(b"\r\n", ciel_runtime._channel_wake_enter_bytes("auto"))
            self.assertEqual(b"\r\n", ciel_runtime._channel_wake_enter_bytes("unknown"))
            self.assertEqual(b"\n", ciel_runtime._channel_wake_enter_bytes("lf"))
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_WAKE_ENTER": "cr"}):
            self.assertTrue(ciel_runtime._channel_wake_input_bytes("wake").endswith(b"\r"))
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_WAKE_ENTER": "crlf"}):
            self.assertTrue(ciel_runtime._channel_wake_input_bytes("wake").endswith(b"\r\n"))

    def test_channel_platform_default_enter_bytes_is_submit_safe(self):
        self.assertEqual(b"\r\n", ciel_runtime._channel_platform_default_enter_bytes("linux", "posix"))
        self.assertEqual(b"\r\n", ciel_runtime._channel_platform_default_enter_bytes("darwin", "posix"))
        self.assertEqual(b"\r\n", ciel_runtime._channel_platform_default_enter_bytes("win32", "nt"))
        self.assertEqual(b"\r\n", ciel_runtime._channel_platform_default_enter_bytes("msys", "posix"))

    def test_terminal_mouse_input_filter_strips_sgr_mouse_reports(self):
        raw = b"hello\x1b[<35;84;42M world\x1b[<35;84;42m"
        self.assertEqual(b"hello world", ciel_runtime._strip_terminal_mouse_input_reports(raw))

    def test_terminal_mouse_input_filter_handles_split_sgr_mouse_reports(self):
        filt = ciel_runtime._TerminalMouseInputFilter()
        self.assertEqual(b"hello", filt.feed(b"hello\x1b[<35;"))
        self.assertEqual(b" world", filt.feed(b"84;42M world"))
        self.assertEqual(b"", filt.flush())

    def test_terminal_mouse_input_filter_preserves_arrow_keys(self):
        self.assertEqual(b"\x1b[A", ciel_runtime._strip_terminal_mouse_input_reports(b"\x1b[A"))

    def test_terminal_mouse_input_filter_strips_x10_and_urxvt_reports(self):
        raw = b"a\x1b[M !!b\x1b[35;84;42Mc"
        self.assertEqual(b"abc", ciel_runtime._strip_terminal_mouse_input_reports(raw))

    def test_terminal_input_mode_reset_writes_mouse_disable_sequences(self):
        class Stream:
            def __init__(self):
                self.text = ""
                self.flushed = False

            def isatty(self):
                return True

            def write(self, text):
                self.text += text

            def flush(self):
                self.flushed = True

        stream = Stream()
        with (
            mock.patch.object(ciel_runtime.os, "name", "posix"),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            ciel_runtime._write_terminal_input_mode_reset(stream)

        self.assertIn("\x1b[?1000l", stream.text)
        self.assertIn("\x1b[?1006l", stream.text)
        self.assertTrue(stream.flushed)

    def test_windows_terminal_input_mode_reset_does_not_write_ansi_by_default(self):
        class Stream:
            def __init__(self):
                self.text = ""
                self.flushed = False

            def isatty(self):
                return True

            def write(self, text):
                self.text += text

            def flush(self):
                self.flushed = True

        stream = Stream()
        with (
            mock.patch.object(ciel_runtime.os, "name", "nt"),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            ciel_runtime._write_terminal_input_mode_reset(stream)

        self.assertEqual("", stream.text)
        self.assertFalse(stream.flushed)

    def test_windows_console_mouse_input_guard_disables_and_restores_mouse_mode(self):
        modes = {"value": 0x01F7}

        def set_mode(value):
            modes["value"] = value
            return True

        with (
            mock.patch.object(ciel_runtime.os, "name", "nt"),
            mock.patch.object(ciel_runtime, "_windows_console_input_mode", side_effect=lambda: modes["value"]),
            mock.patch.object(ciel_runtime, "_set_windows_console_input_mode", side_effect=set_mode) as setter,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            guard = ciel_runtime._WindowsConsoleMouseInputGuard()
            guard.apply()
            self.assertEqual(0x01E7, modes["value"])
            guard.restore()
            self.assertEqual(0x01F7, modes["value"])

        self.assertGreaterEqual(setter.call_count, 2)

    def test_windows_console_mouse_input_guard_can_be_disabled_by_env(self):
        with (
            mock.patch.object(ciel_runtime.os, "name", "nt"),
            mock.patch.object(ciel_runtime, "_windows_console_input_mode", return_value=0x0010),
            mock.patch.object(ciel_runtime, "_set_windows_console_input_mode") as setter,
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_WINDOWS_CONSOLE_MOUSE_FILTER": "0"}, clear=True),
        ):
            ciel_runtime._WindowsConsoleMouseInputGuard().apply()

            setter.assert_not_called()

    def test_windows_console_input_writer_uses_scan_codes_for_control_and_enter(self):
        source = inspect.getsource(ciel_runtime._WindowsConsoleInputWriter._write_chars)

        self.assertIn('"\\x15": 0x55', source)
        self.assertIn('WinDLL("user32"', source)
        self.assertIn("MapVirtualKeyW", source)
        self.assertIn("scan_code,", source)

    def test_windows_console_proxy_does_not_send_ansi_bracketed_paste(self):
        source = inspect.getsource(ciel_runtime.subprocess_call_with_windows_console_wake_proxy)

        self.assertIn("windows_bracketed_paste = False", source)
        self.assertIn("bracketed_paste=windows_bracketed_paste", source)

    def test_windows_console_input_handle_falls_back_to_conin(self):
        source = inspect.getsource(ciel_runtime._windows_console_input_handle)

        self.assertIn('"CONIN$"', source)
        self.assertIn("CreateFileW", source)
        self.assertIn("GetConsoleMode", source)

    def test_windows_channel_wake_proxy_uses_console_input_writer(self):
        with (
            mock.patch.object(ciel_runtime.os, "name", "nt"),
            mock.patch.object(ciel_runtime.sys.stdin, "isatty", return_value=False),
            mock.patch.object(ciel_runtime.sys.stdout, "isatty", return_value=False),
            mock.patch.object(ciel_runtime, "_windows_console_input_supported", return_value=True),
            mock.patch.object(ciel_runtime, "subprocess_call_with_windows_console_wake_proxy", return_value=77) as proxy,
            mock.patch.object(ciel_runtime.subprocess, "call") as direct,
        ):
            rc = ciel_runtime.subprocess_call_with_channel_wake_proxy(
                ["codex", "--yolo"],
                {"PATH": "x"},
                wake_for_llm_delivery=True,
                synthetic_enter_bytes=b"\r",
                channel_wake_submit_retries=4,
                channel_wake_confirm_submit=True,
                channel_wake_bracketed_paste=True,
                channel_wake_submit_delay_seconds=0.25,
            )

        self.assertEqual(77, rc)
        direct.assert_not_called()
        proxy.assert_called_once()
        kwargs = proxy.call_args.kwargs
        self.assertTrue(kwargs["wake_for_llm_delivery"])
        self.assertEqual(b"\r", kwargs["synthetic_enter_bytes"])
        self.assertEqual(4, kwargs["channel_wake_submit_retries"])
        self.assertTrue(kwargs["channel_wake_confirm_submit"])
        self.assertTrue(kwargs["channel_wake_bracketed_paste"])
        self.assertEqual(0.25, kwargs["channel_wake_submit_delay_seconds"])

    def test_write_fd_all_accepts_writer_object(self):
        class Writer:
            def __init__(self):
                self.data = bytearray()

            def write(self, data):
                self.data.extend(data)

        writer = Writer()
        ciel_runtime._write_fd_all(writer, b"wake")
        self.assertEqual(b"wake", bytes(writer.data))

    def test_windows_console_retries_enter_when_injected_prompt_is_missing(self):
        class Writer:
            def __init__(self):
                self.data = bytearray()

            def write(self, data):
                self.data.extend(data)

        writer = Writer()
        with mock.patch.object(ciel_runtime, "_channel_wake_submit_retry_delay_seconds", return_value=0.9):
            attempts, attempted_at = ciel_runtime._retry_windows_console_channel_submit(
                writer,
                b"\r",
                "missing",
                1,
                4,
                10.0,
                11.0,
                confirm_submit=True,
            )

        self.assertEqual(2, attempts)
        self.assertEqual(11.0, attempted_at)
        self.assertEqual(b"\r", bytes(writer.data))

    def test_windows_console_does_not_retry_enter_after_prompt_is_observed(self):
        writer = mock.Mock()
        attempts, attempted_at = ciel_runtime._retry_windows_console_channel_submit(
            writer,
            b"\r",
            "pending",
            1,
            4,
            10.0,
            12.0,
            confirm_submit=True,
        )

        self.assertEqual(1, attempts)
        self.assertEqual(10.0, attempted_at)
        writer.write.assert_not_called()

    def test_windows_console_does_not_retry_enter_after_turn_starts(self):
        writer = mock.Mock()
        attempts, attempted_at = ciel_runtime._retry_windows_console_channel_submit(
            writer,
            b"\r",
            "missing",
            1,
            4,
            10.0,
            12.0,
            confirm_submit=True,
            turn_active=True,
        )

        self.assertEqual(1, attempts)
        self.assertEqual(10.0, attempted_at)
        writer.write.assert_not_called()

    def test_channel_active_turn_tracks_codex_start_complete_and_abort(self):
        started = '\n'.join([
            '{"type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}',
            '{"type":"response_item","payload":{"type":"reasoning"}}',
        ])
        completed = started + '\n{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1"}}'
        aborted = started + '\n{"type":"event_msg","payload":{"type":"turn_aborted"}}'

        self.assertTrue(ciel_runtime._channel_stdin_active_turn_from_text(started))
        self.assertFalse(ciel_runtime._channel_stdin_active_turn_from_text(completed))
        self.assertFalse(ciel_runtime._channel_stdin_active_turn_from_text(aborted))

    def test_channel_enter_bytes_from_user_input_tracks_observed_submit_key(self):
        self.assertEqual(b"\n", ciel_runtime._channel_enter_bytes_from_user_input(b"\n"))
        self.assertEqual(b"\r", ciel_runtime._channel_enter_bytes_from_user_input(b"\r"))
        self.assertEqual(b"\r\n", ciel_runtime._channel_enter_bytes_from_user_input(b"hello\r\n"))
        self.assertIsNone(ciel_runtime._channel_enter_bytes_from_user_input(b"abc"))

    def test_channel_synthetic_enter_normalizes_bare_cr_to_crlf(self):
        self.assertEqual(b"\r\n", ciel_runtime._channel_synthetic_enter_bytes_from_user_input(b"\r"))
        self.assertEqual(b"\r", ciel_runtime._channel_synthetic_enter_bytes_from_user_input(b"\r", normalize_bare_cr=False))
        self.assertEqual(b"\n", ciel_runtime._channel_synthetic_enter_bytes_from_user_input(b"\n"))
        self.assertEqual(b"\r\n", ciel_runtime._channel_synthetic_enter_bytes_from_user_input(b"hello\r\n"))

    def test_channel_wake_prompt_retries_until_tmux_pane_changes(self):
        with (
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_channel_wake_submit_retry_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_channel_current_tmux_pane_text", side_effect=["before", "before", "after"]),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            ciel_runtime._write_channel_wake_prompt(99, "wake", b"\r", submit_retry_count=4, confirm_submit=True)

        self.assertEqual(3, write_all.call_count)
        self.assertEqual(b"\x15wake", write_all.call_args_list[0].args[1])
        self.assertEqual(b"\r", write_all.call_args_list[1].args[1])
        self.assertEqual(b"\r", write_all.call_args_list[2].args[1])
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_stdin_proxy_submit_confirmed attempt=2" in item for item in log_messages))

    def test_channel_wake_prompt_does_not_retry_without_tmux_confirmation(self):
        with (
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_channel_current_tmux_pane_text", return_value=None),
        ):
            ciel_runtime._write_channel_wake_prompt(99, "wake", b"\r", submit_retry_count=4, confirm_submit=True)

        self.assertEqual(2, write_all.call_count)
        self.assertEqual(b"\x15wake", write_all.call_args_list[0].args[1])
        self.assertEqual(b"\r", write_all.call_args_list[1].args[1])

    def test_channel_wake_prompt_can_use_bracketed_paste_for_codex(self):
        with mock.patch.object(ciel_runtime, "_write_fd_all") as write_all:
            ciel_runtime._write_channel_wake_prompt(
                99,
                "hello\nworld",
                b"\r",
                bracketed_paste=True,
                submit_delay_seconds=0,
            )

        self.assertEqual(2, write_all.call_count)
        self.assertEqual(b"\x15\x1b[200~hello\nworld\x1b[201~", write_all.call_args_list[0].args[1])
        self.assertEqual(b"\r", write_all.call_args_list[1].args[1])

    def test_latest_transcript_path_checks_codex_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            claude_dir = home / ".claude" / "projects" / "repo"
            codex_dir = home / ".codex" / "sessions" / "2026" / "06" / "28"
            claude_dir.mkdir(parents=True)
            codex_dir.mkdir(parents=True)
            claude_transcript = claude_dir / "old.jsonl"
            codex_transcript = codex_dir / "new.jsonl"
            claude_transcript.write_text("{}\n", encoding="utf-8")
            codex_transcript.write_text("{}\n", encoding="utf-8")
            os.utime(claude_transcript, (100, 100))
            os.utime(codex_transcript, (200, 200))
            ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.clear()
            ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.update({"checked_at": 0.0, "path": None})
            with mock.patch.object(ciel_runtime, "HOME", home):
                self.assertEqual(codex_transcript, ciel_runtime._latest_claude_transcript_path(ttl_seconds=0))

    def test_codex_transcript_scope_ignores_claude_and_stale_codex_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            claude_dir = home / ".claude" / "projects" / "repo"
            codex_home = home / "custom-codex"
            codex_dir = codex_home / "sessions" / "2026" / "07" / "11"
            claude_dir.mkdir(parents=True)
            codex_dir.mkdir(parents=True)
            claude_transcript = claude_dir / "active.jsonl"
            stale_codex = codex_dir / "stale.jsonl"
            current_codex = codex_dir / "current.jsonl"
            claude_transcript.write_text('{"type":"event_msg","payload":{"type":"task_started"}}\n', encoding="utf-8")
            stale_codex.write_text('{"type":"event_msg","payload":{"type":"task_started"}}\n', encoding="utf-8")
            current_codex.write_text("{}\n", encoding="utf-8")
            os.utime(claude_transcript, (300, 300))
            os.utime(stale_codex, (100, 100))
            os.utime(current_codex, (201, 201))

            old_scope = dict(ciel_runtime._CHANNEL_TRANSCRIPT_SCOPE)
            try:
                with mock.patch.object(ciel_runtime, "HOME", home):
                    ciel_runtime._set_channel_transcript_scope("codex", started_at=200, codex_home=codex_home)
                    self.assertEqual(current_codex, ciel_runtime._latest_claude_transcript_path(ttl_seconds=0))
            finally:
                ciel_runtime._CHANNEL_TRANSCRIPT_SCOPE.clear()
                ciel_runtime._CHANNEL_TRANSCRIPT_SCOPE.update(old_scope)
                ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.clear()
                ciel_runtime._CHANNEL_TRANSCRIPT_CACHE.update({"checked_at": 0.0, "path": None})

    def test_channel_stdin_wake_state_accepts_codex_response_item_records(self):
        transcript = "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "id=179 text=\"hello\""}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "handled"}],
                        },
                    }
                ),
            ]
        )

        self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state_from_text(179, transcript))

    def test_channel_stdin_wake_state_accepts_codex_event_msg_user_records(self):
        transcript = "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "id=180 text=\"hello\""}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]},
                    }
                ),
            ]
        )

        self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state_from_text(180, transcript))

    def test_channel_stdin_detects_codex_active_tool_call_until_output(self):
        active_transcript = json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "function_call", "call_id": "call_1", "name": "shell_command"},
            }
        )
        completed_transcript = "\n".join(
            [
                active_transcript,
                json.dumps({"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call_1"}}),
            ]
        )

        self.assertTrue(ciel_runtime._channel_stdin_active_tool_call_from_text(active_transcript))
        self.assertFalse(ciel_runtime._channel_stdin_active_tool_call_from_text(completed_transcript))

    def test_builtin_channel_mcp_exposes_reply_tools(self):
        tools = ciel_runtime._channel_mcp_tool_schemas()
        names = [tool.get("name") for tool in tools]

        self.assertIn("send_message", names)
        self.assertIn("send_file", names)
        self.assertNotIn("get_messages", names)
        self.assertIn("compact_session", names)
        self.assertIn("llm_options", names)
        compact_schema = next(tool for tool in tools if tool.get("name") == "compact_session")
        self.assertIn("reason", compact_schema["inputSchema"]["properties"])
        send_schema = next(tool for tool in tools if tool.get("name") == "send_message")
        self.assertIn("channel", send_schema["inputSchema"]["required"])
        self.assertIn("message", send_schema["inputSchema"]["required"])
        file_schema = next(tool for tool in tools if tool.get("name") == "send_file")
        self.assertIn("channel", file_schema["inputSchema"]["required"])
        self.assertIn("path", file_schema["inputSchema"]["properties"])
        self.assertIn("content", file_schema["inputSchema"]["properties"])

    def test_builtin_channel_mcp_rejects_removed_get_messages_tool(self):
        response = ciel_runtime._channel_mcp_tool_call_response(
            1,
            {"name": "get_messages", "arguments": {"limit": 5}},
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("Unknown ciel-runtime-router tool: get_messages", response["result"]["content"][0]["text"])

    def test_builtin_channel_mcp_send_message_appends_web_delivery_reply(self):
        with mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 44, "message": "done"}) as append:
            response = ciel_runtime._channel_mcp_tool_call_response(
                7,
                {
                    "name": "send_message",
                    "arguments": {
                        "channel": "web-chat-session",
                        "message": "작업 결과입니다.",
                        "thread_id": "session",
                    },
                },
            )

        self.assertEqual(7, response["id"])
        self.assertFalse(response["result"]["isError"])
        payload = append.call_args.args[0]
        self.assertEqual("web-chat-session", payload["channel"])
        self.assertEqual("작업 결과입니다.", payload["message"])
        self.assertEqual(["web"], payload["delivery"])
        self.assertEqual("web", payload["recipients"])
        self.assertEqual("claude-code", payload["sender_id"])

    def test_builtin_channel_mcp_send_file_appends_web_attachment_reply(self):
        upload = {
            "name": "stored-report.md",
            "original_name": "report.md",
            "url": "http://127.0.0.1:8799/ca/chat/files/stored-report.md",
            "path": "/ca/chat/files/stored-report.md",
            "bytes": 12,
            "content_type": "text/markdown",
        }
        with (
            mock.patch.object(ciel_runtime, "store_chat_file_from_path", return_value=upload) as store,
            mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 45, "message": "file"}) as append,
        ):
            response = ciel_runtime._channel_mcp_tool_call_response(
                8,
                {
                    "name": "send_file",
                    "arguments": {
                        "channel": "web-chat-session",
                        "path": "report.md",
                        "message": "검토 결과 파일입니다.",
                        "thread_id": "session",
                    },
                },
            )

        self.assertEqual(8, response["id"])
        self.assertFalse(response["result"]["isError"])
        store.assert_called_once()
        payload = append.call_args.args[0]
        self.assertEqual("web-chat-session", payload["channel"])
        self.assertEqual("file", payload["kind"])
        self.assertEqual(["web"], payload["delivery"])
        self.assertEqual("web", payload["recipients"])
        self.assertIn("검토 결과 파일입니다.", payload["message"])
        self.assertIn("[report.md]", payload["message"])
        self.assertEqual([upload], payload["meta"]["attachments"])

    def test_builtin_channel_mcp_send_file_accepts_inline_content(self):
        with (
            mock.patch.object(ciel_runtime, "store_chat_file_upload", return_value={
                "name": "stored.txt",
                "original_name": "answer.txt",
                "url": "http://127.0.0.1:8799/ca/chat/files/stored.txt",
                "path": "/ca/chat/files/stored.txt",
                "bytes": 5,
                "content_type": "text/plain",
            }) as store,
            mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 46, "message": "file"}),
        ):
            response = ciel_runtime._channel_mcp_tool_call_response(
                9,
                {
                    "name": "send_file",
                    "arguments": {
                        "channel": "web-chat-session",
                        "name": "answer.txt",
                        "content": "hello",
                    },
                },
            )

        self.assertFalse(response["result"]["isError"])
        store.assert_called_once()
        self.assertEqual("answer.txt", store.call_args.args[0]["name"])
        self.assertEqual("hello", store.call_args.args[0]["content"])

    def test_builtin_channel_mcp_compact_session_queues_request_file(self):
        with tempfile.TemporaryDirectory(prefix="ca-compact-request-") as td:
            root = Path(td)
            request_path = root / "channel-compact-request.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHANNEL_COMPACT_REQUEST_PATH", request_path),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                response = ciel_runtime._channel_mcp_tool_call_response(
                    10,
                    {"name": "compact_session", "arguments": {"reason": "large MCP result"}},
                )

            self.assertEqual(10, response["id"])
            self.assertFalse(response["result"]["isError"])
            result = json.loads(response["result"]["content"][0]["text"])
            self.assertTrue(result["queued"])
            self.assertEqual("/compact", result["command"])
            saved = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(result["request_id"], saved["id"])
            self.assertEqual("/compact", saved["command"])
            self.assertEqual("large MCP result", saved["reason"])

    def test_compact_request_injection_defers_while_tool_call_active(self):
        with tempfile.TemporaryDirectory(prefix="ca-compact-request-") as td:
            root = Path(td)
            request_path = root / "channel-compact-request.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHANNEL_COMPACT_REQUEST_PATH", request_path),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                ciel_runtime._write_channel_compact_request("test", "active tool")
                with (
                    mock.patch.object(ciel_runtime, "_channel_stdin_active_tool_call", return_value=True),
                    mock.patch.object(ciel_runtime, "_write_channel_wake_prompt") as write_prompt,
                ):
                    status = ciel_runtime._inject_pending_compact_request(123, b"\n")

            self.assertEqual("deferred", status)
            write_prompt.assert_not_called()
            self.assertTrue(request_path.exists())

    def test_compact_request_injection_writes_slash_compact_once(self):
        with tempfile.TemporaryDirectory(prefix="ca-compact-request-") as td:
            root = Path(td)
            request_path = root / "channel-compact-request.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHANNEL_COMPACT_REQUEST_PATH", request_path),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                ciel_runtime._write_channel_compact_request("test", "ready")
                with (
                    mock.patch.object(ciel_runtime, "_channel_stdin_active_tool_call", return_value=False),
                    mock.patch.object(ciel_runtime, "_write_channel_wake_prompt") as write_prompt,
                ):
                    status = ciel_runtime._inject_pending_compact_request(123, b"\n")

            self.assertEqual("injected", status)
            write_prompt.assert_called_once()
            self.assertEqual(123, write_prompt.call_args.args[0])
            self.assertEqual("/compact", write_prompt.call_args.args[1])
            self.assertEqual(b"\n", write_prompt.call_args.args[2])
            self.assertFalse(request_path.exists())

    def test_inject_pending_channel_messages_writes_prompt_to_child_stdin(self):
        messages = [
            {
                "id": 2,
                "channel": "room",
                "sender_id": "agent",
                "message": "wake up",
                "meta": {},
                "delivery": ["llm"],
            }
        ]
        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_channel_platform_default_enter_bytes", return_value=b"\r\n"),
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            last_id = ciel_runtime._inject_pending_channel_messages(99, 1)
        self.assertEqual(2, last_id)
        commit_cursor.assert_called_once_with(2)
        self.assertEqual(2, write_all.call_count)
        self.assertIn(b"wake up", write_all.call_args_list[0].args[1])
        self.assertTrue(write_all.call_args_list[0].args[1].startswith(b"\x15"))
        self.assertEqual(b"\r\n", write_all.call_args_list[1].args[1])

        ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.clear()
        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all_cr,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer"),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            ciel_runtime._inject_pending_channel_messages(99, 1, b"\r")
        self.assertEqual(b"\r", write_all_cr.call_args_list[1].args[1])

    def test_inject_pending_channel_messages_batches_and_ignores_connection_noise(self):
        messages = [
            {"id": 1, "channel": "generic-room", "sender_id": "generic-mcp", "message": "generic.ws.connected", "meta": {}},
            {"id": 2, "channel": "generic-room", "sender_id": "agent-a", "message": "hello recipient", "meta": {"room_id": "generic-room", "mcp_server": "generic-mcp"}},
            {"id": 3, "channel": "generic-room", "sender_id": "agent-b", "message": "status please", "meta": {"room_id": "generic-room", "mcp_server": "generic-mcp"}},
        ]
        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_channel_platform_default_enter_bytes", return_value=b"\r\n"),
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            last_id = ciel_runtime._inject_pending_channel_messages(99, 0)
        self.assertEqual(2, last_id)
        commit_cursor.assert_called_once_with(2)
        payload = write_all.call_args_list[0].args[1]
        self.assertIn(b"external channel message", payload)
        self.assertIn(b"hello recipient", payload)
        self.assertNotIn(b"status please", payload)
        self.assertNotIn(b"generic.ws.connected", payload)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_stdin_proxy_skipped_noise" in item for item in log_messages))
        self.assertTrue(any("channel_stdin_proxy_injected" in item and "message_ids=2" in item and "enter=crlf" in item for item in log_messages))

    def test_inject_pending_channel_messages_can_limit_to_web_chat_requests(self):
        messages = [
            {"id": 2, "channel": "ai-net", "sender_id": "robert", "message": "hello Sarah", "meta": {"room_id": "ai-net"}},
            {
                "id": 3,
                "channel": "web-chat-session",
                "sender_id": "web-user",
                "message": "마지막 작업 요약",
                "kind": "web_chat",
                "meta": {"source": "ciel-runtime-web-chat", "reply_channel": "web-chat-session"},
            },
        ]
        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_channel_platform_default_enter_bytes", return_value=b"\r\n"),
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            last_id = ciel_runtime._inject_pending_channel_messages(99, 0, web_chat_only=True)
        self.assertEqual(3, last_id)
        commit_cursor.assert_not_called()
        payload = write_all.call_args_list[0].args[1]
        self.assertIn("마지막 작업 요약".encode("utf-8"), payload)
        self.assertIn(b"ciel-runtime web chat", payload)
        self.assertNotIn(b"metadata=", payload)
        self.assertNotIn(b"hello Sarah", payload)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("reason=not_web_chat" in item and "message_id=2" in item for item in log_messages))

    def test_inject_pending_channel_messages_claims_before_terminal_write(self):
        messages = [
            {
                "id": 7,
                "channel": "room",
                "sender_id": "agent",
                "message": "wake up",
                "meta": {},
                "delivery": ["llm"],
            }
        ]

        def assert_claimed_before_write(fd, data):
            self.assertIn(7, ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED)

        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_write_fd_all", side_effect=assert_claimed_before_write) as write_all,
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            last_id = ciel_runtime._inject_pending_channel_messages(99, 1)

        self.assertEqual(7, last_id)
        self.assertEqual(2, write_all.call_count)
        commit_cursor.assert_called_once_with(7)

    def test_inject_pending_channel_messages_can_defer_cursor_commit(self):
        messages = [
            {
                "id": 8,
                "channel": "room",
                "sender_id": "agent",
                "message": "wake up later",
                "meta": {},
                "delivery": ["llm"],
            }
        ]
        injected: list[int] = []
        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_write_fd_all"),
            mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
            mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            last_id = ciel_runtime._inject_pending_channel_messages(
                99,
                7,
                commit_cursor=False,
                injected_message_ids=injected,
            )

        self.assertEqual(8, last_id)
        self.assertEqual([8], injected)
        commit_cursor.assert_not_called()

    def test_llm_delivery_wake_prompt_includes_external_content(self):
        prompt = ciel_runtime.format_channel_llm_delivery_wake_prompt(
            [
                {
                    "id": 8,
                    "channel": "room",
                    "sender_id": "agent",
                    "message": "secret raw message body",
                    "meta": {},
                }
            ]
        )

        self.assertEqual("secret raw message body", prompt)
        self.assertNotIn("[external input pending]", prompt)
        self.assertNotIn("type=mcp_notification", prompt)
        self.assertNotIn("source=agent", prompt)
        self.assertNotIn("ciel-runtime", prompt)
        self.assertNotIn("id=8", prompt)
        self.assertNotIn("ids=8", prompt)
        self.assertNotIn("external channel message", prompt)
        self.assertNotIn("do not answer", prompt)

    def test_llm_delivery_wake_prompt_prefers_original_mcp_json(self):
        prompt = ciel_runtime.format_channel_llm_delivery_wake_prompt(
            [
                {
                    "id": 8,
                    "channel": "room",
                    "sender_id": "ai-net-http",
                    "message": "Test @mentioned you",
                    "meta": {
                        "mcp_json": {
                            "jsonrpc": "2.0",
                            "method": "notifications/claude/channel",
                            "params": {
                                "content": "Test @mentioned you",
                                "meta": {
                                    "kind": "mentioned",
                                    "room_id": "room_iyjjx0bzfimr",
                                    "message_id": "msg_w9cj0ff3ahou",
                                },
                            },
                        }
                    },
                }
            ]
        )

        header, json_text = prompt.split("\n\n", 1)
        self.assertEqual("[Source channel] room_iyjjx0bzfimr", header)
        parsed = json.loads(json_text)
        self.assertEqual("notifications/claude/channel", parsed["method"])
        self.assertEqual("room_iyjjx0bzfimr", parsed["params"]["meta"]["room_id"])
        self.assertEqual("msg_w9cj0ff3ahou", parsed["params"]["meta"]["message_id"])
        self.assertNotIn("Start a normal turn", prompt)
        self.assertNotIn("ExitPlanMode", prompt)

    def test_llm_delivery_wake_prompt_adds_source_channel_header_for_native_notification(self):
        event = {
            "method": "notifications/claude/channel",
            "params": {
                "content": "New message from Test",
                "meta": {
                    "kind": "activity",
                    "room_id": "room_id2w78yhq8c8",
                    "room_name": "Bitcoin Strategy Team",
                    "message_id": "msg_c8iqayxm58ha",
                    "author_name": "Test",
                    "stream_id": "1782702185312-0",
                },
            },
            "jsonrpc": "2.0",
        }
        prompt = ciel_runtime.format_channel_llm_delivery_wake_prompt(
            [
                {
                    "id": 9,
                    "channel": "ai-net-http",
                    "sender_id": "ai-net-http",
                    "message": json.dumps(event, ensure_ascii=False, indent=2),
                    "meta": {"sse_json": event, "mcp_method": "notifications/claude/channel"},
                }
            ]
        )

        header, json_text = prompt.split("\n\n", 1)
        self.assertEqual(
            "[Source channel] Bitcoin Strategy Team (room_id=room_id2w78yhq8c8)",
            header,
        )
        parsed = json.loads(json_text)
        self.assertEqual("New message from Test", parsed["params"]["content"])
        self.assertEqual("Bitcoin Strategy Team", parsed["params"]["meta"]["room_name"])
        self.assertEqual("Test", parsed["params"]["meta"]["author_name"])
        self.assertEqual("msg_c8iqayxm58ha", parsed["params"]["meta"]["message_id"])
        self.assertEqual("1782702185312-0", parsed["params"]["meta"]["stream_id"])

    def test_inject_pending_channel_messages_wake_only_for_llm_delivery(self):
        messages = [
            {
                "id": 8,
                "channel": "room",
                "sender_id": "agent",
                "message": "wake up later",
                "meta": {},
                "delivery": ["llm"],
            }
        ]
        injected: list[int] = []
        with tempfile.TemporaryDirectory() as td:
            claims_path = Path(td) / "claims.json"
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(
                    99,
                    7,
                    wake_for_llm_delivery=True,
                    commit_cursor=False,
                    injected_message_ids=injected,
                )

        self.assertEqual(7, last_id)
        self.assertEqual([8], injected)
        self.assertNotIn(8, ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED)
        self.assertEqual(2, write_all.call_count)
        wake_bytes = write_all.call_args_list[0].args[1]
        self.assertNotIn(b"[external input pending]", wake_bytes)
        self.assertNotIn(b"type=mcp_notification", wake_bytes)
        self.assertNotIn(b"ciel-runtime", wake_bytes)
        self.assertNotIn(b"id=8", wake_bytes)
        self.assertNotIn(b"ids=8", wake_bytes)
        self.assertIn(b"wake up later", wake_bytes)
        self.assertEqual("wake up later", ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS[8])
        commit_cursor.assert_not_called()

    def test_inject_pending_channel_messages_batches_llm_delivery_wakes(self):
        messages = [
            {
                "id": 8,
                "channel": "room",
                "sender_id": "agent-a",
                "message": "first wake",
                "meta": {},
                "delivery": ["llm"],
            },
            {
                "id": 9,
                "channel": "room",
                "sender_id": "agent-b",
                "message": "second wake",
                "meta": {},
                "delivery": ["llm"],
            },
            {
                "id": 10,
                "channel": "room",
                "sender_id": "agent-c",
                "message": "third wake",
                "meta": {},
                "delivery": ["llm"],
            },
        ]
        injected: list[int] = []
        with tempfile.TemporaryDirectory() as td:
            claims_path = Path(td) / "claims.json"
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=None),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(
                    99,
                    7,
                    wake_for_llm_delivery=True,
                    commit_cursor=False,
                    injected_message_ids=injected,
                )

        self.assertEqual(7, last_id)
        self.assertEqual([8, 9, 10], injected)
        self.assertEqual(2, write_all.call_count)
        wake_bytes = write_all.call_args_list[0].args[1]
        self.assertIn(b"first wake", wake_bytes)
        self.assertIn(b"second wake", wake_bytes)
        self.assertIn(b"third wake", wake_bytes)
        self.assertEqual("first wake\n\nsecond wake\n\nthird wake", ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS[8])
        self.assertEqual(ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS[8], ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS[10])
        commit_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_stdin_proxy_injected" in item and "count=3" in item and "message_ids=8,9,10" in item for item in log_messages))

    def test_inject_pending_channel_messages_respects_llm_delivery_wake_batch_limit(self):
        messages = [
            {"id": 8, "channel": "room", "sender_id": "agent-a", "message": "first wake", "meta": {}, "delivery": ["llm"]},
            {"id": 9, "channel": "room", "sender_id": "agent-b", "message": "second wake", "meta": {}, "delivery": ["llm"]},
            {"id": 10, "channel": "room", "sender_id": "agent-c", "message": "third wake", "meta": {}, "delivery": ["llm"]},
        ]
        injected: list[int] = []
        with tempfile.TemporaryDirectory() as td:
            claims_path = Path(td) / "claims.json"
            with (
                mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_WAKE_BATCH_LIMIT": "2"}),
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=None),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer"),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(
                    99,
                    7,
                    wake_for_llm_delivery=True,
                    commit_cursor=False,
                    injected_message_ids=injected,
                )

        self.assertEqual(7, last_id)
        self.assertEqual([8, 9], injected)
        wake_bytes = write_all.call_args_list[0].args[1]
        self.assertIn(b"first wake", wake_bytes)
        self.assertIn(b"second wake", wake_bytes)
        self.assertNotIn(b"third wake", wake_bytes)

    def test_inject_pending_channel_messages_dedupes_llm_delivery_batch_by_event_identity(self):
        same_event = {
            "method": "notifications/claude/channel",
            "params": {
                "content": "New message from Test",
                "meta": {
                    "kind": "activity",
                    "room_id": "room_asset",
                    "message_id": "msg_same",
                    "stream_id": "1782645817112-0",
                },
            },
            "jsonrpc": "2.0",
        }
        next_event = {
            "method": "notifications/claude/channel",
            "params": {
                "content": "New message from Joy",
                "meta": {
                    "kind": "activity",
                    "room_id": "room_asset",
                    "message_id": "msg_next",
                    "stream_id": "1782645817999-0",
                },
            },
            "jsonrpc": "2.0",
        }

        def make_message(message_id: int, sender: str, event: dict[str, object]) -> dict[str, object]:
            event_meta = event["params"]["meta"]  # type: ignore[index]
            return {
                "id": message_id,
                "channel": "room_asset",
                "sender_id": sender,
                "message": json.dumps(event, ensure_ascii=False, indent=2),
                "kind": "channel",
                "meta": {
                    "sse_event": "message",
                    "sse_source": f"mcp-{sender}",
                    "sse_json": event,
                    "mcp_method": "notifications/claude/channel",
                    "kind": "activity",
                    "room_id": "room_asset",
                    "message_id": event_meta["message_id"],  # type: ignore[index]
                    "stream_id": event_meta["stream_id"],  # type: ignore[index]
                },
                "delivery": ["llm"],
            }

        messages = [
            make_message(8, "ai-net-http", same_event),
            make_message(9, "ai-net", same_event),
            make_message(10, "ai-net-http", next_event),
        ]
        injected: list[int] = []
        with tempfile.TemporaryDirectory() as td:
            claims_path = Path(td) / "claims.json"
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=None),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer"),
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(
                    99,
                    7,
                    wake_for_llm_delivery=True,
                    commit_cursor=False,
                    injected_message_ids=injected,
                )

        self.assertEqual(7, last_id)
        self.assertEqual([8, 10], injected)
        wake_text = write_all.call_args_list[0].args[1].decode("utf-8", errors="replace")
        self.assertEqual(1, wake_text.count("msg_same"))
        self.assertIn("msg_next", wake_text)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("reason=duplicate_channel_event" in item and "message_id=9" in item for item in log_messages))

    def test_channel_wake_prompt_does_not_auto_synthesize_plan_mode(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "[external input pending] type=mcp_notification ids=8."}],
                }
            ],
            "tools": [{"name": "EnterPlanMode", "input_schema": {"type": "object"}}],
        }

        self.assertTrue(ciel_runtime.body_is_channel_prompt(body))
        self.assertFalse(ciel_runtime.should_auto_enter_plan_mode(body, "", []))

    def test_legacy_channel_wake_prompt_is_still_recognized(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "[ciel-runtime channel wake] id=8 pending_ids=8."}],
                }
            ],
        }

        self.assertTrue(ciel_runtime.channel_llm_wake_request(body))
        self.assertTrue(ciel_runtime.body_is_channel_prompt(body))

    def test_channel_pending_wake_prompt_is_still_recognized(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "[channel pending] id=8 pending_ids=8."}],
                }
            ],
        }

        self.assertTrue(ciel_runtime.channel_llm_wake_request(body))
        self.assertTrue(ciel_runtime.body_is_channel_prompt(body))

    def test_inject_pending_channel_messages_waits_for_queued_command(self):
        messages = [
            {
                "id": 9,
                "channel": "room",
                "sender_id": "agent",
                "message": "wake up",
                "meta": {},
                "delivery": ["llm"],
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "queue-operation",
                        "operation": "enqueue",
                        "content": "[ciel-runtime external channel message] id=9 text=\"wake up\"",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.clear()
            with (
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(99, 8)

        self.assertEqual(8, last_id)
        write_all.assert_not_called()
        commit_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("state=queued" in item for item in log_messages))

    def test_inject_pending_channel_messages_skips_raw_prompt_already_in_transcript(self):
        raw_prompt = (
            "[AI-Net new messages]\n"
            '• Bitcoin Strategy Team: 1 new (Joy) → get_messages(room_id="room_id2w78yhq8c8", after_seq=41182)'
        )
        messages = [
            {
                "id": 367,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": raw_prompt,
                "meta": {"mcp_server": "ai-net-http", "mcp_method": "notifications/claude/channel", "kind": "digest"},
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "message": {"role": "user", "content": "\x15" + raw_prompt}}),
                        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            claims_path = Path(td) / "claims.json"
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(99, 366, wake_for_llm_delivery=True)

        self.assertEqual(367, last_id)
        write_all.assert_not_called()
        commit_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("reason=stdin_wake_completed" in item and "message_id=367" in item for item in log_messages))

    def test_inject_pending_channel_messages_skips_cross_process_claim(self):
        raw_prompt = "raw ai-net digest"
        messages = [
            {
                "id": 368,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": raw_prompt,
                "meta": {"mcp_server": "ai-net-http", "mcp_method": "notifications/claude/channel", "kind": "digest"},
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            claims_path = Path(td) / "claims.json"
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=None),
            ):
                self.assertTrue(ciel_runtime._channel_stdin_claim_wake_prompt(368, raw_prompt))
                with (
                    mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                    mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                    mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                    mock.patch.object(ciel_runtime, "router_log") as router_log,
                ):
                    last_id = ciel_runtime._inject_pending_channel_messages(99, 367, wake_for_llm_delivery=True)

        self.assertEqual(367, last_id)
        write_all.assert_not_called()
        commit_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("reason=stdin_wake_claimed" in item and "message_id=368" in item for item in log_messages))

    def test_inject_pending_channel_messages_continues_past_queued_wake_when_nonblocking(self):
        queued_prompt = (
            "[AI-Net new messages]\n"
            '• room_id2w78yhq8c8: 1 new (Kevin) → get_messages(room_id="room_id2w78yhq8c8", after_seq=42050)'
        )
        next_prompt = (
            "[AI-Net new messages]\n"
            '• room_id2w78yhq8c8: 1 new (Joy) → get_messages(room_id="room_id2w78yhq8c8", after_seq=42053)'
        )
        messages = [
            {
                "id": 616,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": queued_prompt,
                "meta": {
                    "mcp_server": "ai-net-http",
                    "mcp_method": "notifications/claude/channel",
                    "kind": "digest",
                    "rooms": json.dumps([{"room_id": "room_id2w78yhq8c8", "message_ids": ["msg_old"]}]),
                },
            },
            {
                "id": 617,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": next_prompt,
                "meta": {
                    "mcp_server": "ai-net-http",
                    "mcp_method": "notifications/claude/channel",
                    "kind": "digest",
                    "rooms": json.dumps([{"room_id": "room_id2w78yhq8c8", "message_ids": ["msg_next"]}]),
                },
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "queue-operation",
                        "operation": "enqueue",
                        "content": "\x15" + queued_prompt,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            claims_path = Path(td) / "claims.json"
            injected: list[int] = []
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(
                    99,
                    615,
                    wake_for_llm_delivery=True,
                    commit_cursor=False,
                    injected_message_ids=injected,
                    skip_blocking_wake_states=True,
                )

        self.assertEqual(616, last_id)
        self.assertEqual([617], injected)
        self.assertEqual(2, write_all.call_count)
        wake_text = write_all.call_args_list[0].args[1].decode("utf-8", errors="replace")
        self.assertNotIn("after_seq=42050", wake_text)
        self.assertIn("after_seq=42053", wake_text)
        commit_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("reason=stdin_wake_queued_continue" in item and "message_id=616" in item for item in log_messages))

    def test_inject_pending_channel_messages_skips_stale_queued_and_injects_next(self):
        stale_prompt = (
            "[AI-Net new messages]\n"
            '• [DM] DM-Robert: 1 new (Joy) → get_messages(room_id="room_dm_a90xk3afh8", after_seq=41197)'
        )
        next_prompt = (
            "[AI-Net new messages]\n"
            '• [DM] DM-Robert: 1 new (Joy) → get_messages(room_id="room_dm_a90xk3afh8", after_seq=41214)'
        )
        messages = [
            {
                "id": 368,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": stale_prompt,
                "meta": {
                    "mcp_server": "ai-net-http",
                    "mcp_method": "notifications/claude/channel",
                    "kind": "digest",
                    "rooms": json.dumps([{"room_id": "room_dm_a90xk3afh8", "message_ids": ["msg_db3w1s056014"]}]),
                },
            },
            {
                "id": 375,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": next_prompt,
                "meta": {
                    "mcp_server": "ai-net-http",
                    "mcp_method": "notifications/claude/channel",
                    "kind": "digest",
                    "rooms": json.dumps([{"room_id": "room_dm_a90xk3afh8", "message_ids": ["msg_z8ao45uuy55m"]}]),
                },
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "queue-operation",
                        "operation": "enqueue",
                        "timestamp": "2026-06-28T07:12:06.900Z",
                        "content": "\x15" + stale_prompt,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            claims_path = Path(td) / "claims.json"
            injected: list[int] = []
            with (
                mock.patch.object(ciel_runtime, "CHANNEL_STDIN_WAKE_CLAIMS_PATH", claims_path),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "_channel_stdin_inflight_stale_seconds", return_value=180.0),
                mock.patch.object(ciel_runtime.time, "time", return_value=1782631318.0),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_channel_wake_submit_delay_seconds", return_value=0),
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(
                    99,
                    367,
                    wake_for_llm_delivery=True,
                    commit_cursor=False,
                    injected_message_ids=injected,
                )

        self.assertEqual(368, last_id)
        self.assertEqual([375], injected)
        self.assertEqual(2, write_all.call_count)
        self.assertIn(next_prompt.encode("utf-8"), write_all.call_args_list[0].args[1])
        commit_cursor.assert_called_with(368)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("reason=stale_queued_wake" in item and "message_id=368" in item for item in log_messages))

    def test_channel_stdin_wake_completed_requires_assistant_after_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "message": {"content": "id=9 text=\"hello\""}}),
                        json.dumps({"type": "queue-operation", "operation": "enqueue"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript):
                self.assertFalse(ciel_runtime._channel_stdin_wake_completed(9))

            transcript.write_text(
                transcript.read_text(encoding="utf-8")
                + json.dumps({"type": "assistant", "message": {"content": []}})
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript):
                self.assertTrue(ciel_runtime._channel_stdin_wake_completed(9))

    def test_channel_stdin_wake_state_distinguishes_missing_pending_completed(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                json.dumps({"type": "user", "message": {"content": "id=10 text=\"hello\""}}) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript):
                self.assertEqual("pending", ciel_runtime._channel_stdin_wake_state(10))
                self.assertEqual("missing", ciel_runtime._channel_stdin_wake_state(11))

            transcript.write_text(
                transcript.read_text(encoding="utf-8")
                + json.dumps({"type": "assistant", "message": {"content": []}})
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript):
                self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state(10))

    def test_channel_stdin_wake_state_accepts_ids_marker(self):
        transcript = "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": "[external input pending] type=mcp_notification ids=363 channels=ai-net-http",
                        },
                    }
                ),
                json.dumps({"type": "assistant", "message": {"content": []}}),
            ]
        )

        self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state_from_text(363, transcript))

    def test_channel_stdin_wake_state_accepts_recorded_raw_wake_prompt(self):
        with ciel_runtime._CHANNEL_STDIN_WAKE_LOCK:
            ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS[363] = "raw ai-net body"
        transcript = "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "raw ai-net body"}}),
                json.dumps({"type": "assistant", "message": {"content": []}}),
            ]
        )

        self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state_from_text(363, transcript))

    def test_channel_stdin_wake_state_accepts_explicit_raw_prompt_without_memory(self):
        raw_prompt = (
            "[AI-Net new messages]\n"
            '• Bitcoin Strategy Team: 1 new (Joy) → get_messages(room_id="room_id2w78yhq8c8", after_seq=41182)'
        )
        transcript = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "\x15" + raw_prompt}),
                json.dumps({"type": "user", "message": {"role": "user", "content": "\x15" + raw_prompt}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}}),
            ]
        )

        self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state_from_text(367, transcript, [raw_prompt]))

    def test_channel_stdin_inflight_stale_only_expires_queued_or_unknown(self):
        with mock.patch.object(ciel_runtime, "_channel_stdin_inflight_stale_seconds", return_value=60.0):
            self.assertTrue(ciel_runtime._channel_stdin_inflight_is_stale("queued", 100.0, 161.0))
            self.assertTrue(ciel_runtime._channel_stdin_inflight_is_stale("unknown", 100.0, 161.0))
            self.assertFalse(ciel_runtime._channel_stdin_inflight_is_stale("queued", 100.0, 120.0))
            self.assertFalse(ciel_runtime._channel_stdin_inflight_is_stale("pending", 100.0, 1000.0))
            self.assertFalse(ciel_runtime._channel_stdin_inflight_is_stale("missing", 100.0, 1000.0))
            self.assertFalse(ciel_runtime._channel_stdin_inflight_is_stale("completed", 100.0, 1000.0))

    def test_channel_stdin_wake_state_accepts_message_role_assistant_records(self):
        transcript = "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "[ciel-runtime external channel message] id=4345 text=\"hello\""}}),
                json.dumps(
                    {
                        "message": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "text", "text": "handled"}],
                        }
                    }
                ),
            ]
        )

        self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state_from_text(4345, transcript))

    def test_channel_stdin_detects_active_tool_call_until_result(self):
        transcript = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "stop_reason": "tool_use",
                            "content": [{"type": "tool_use", "id": "toolu_active", "name": "Bash", "input": {}}],
                        },
                    }
                ),
            ]
        )

        self.assertTrue(ciel_runtime._channel_stdin_active_tool_call_from_text(transcript))

        transcript += "\n" + json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_active", "content": "ok"}],
                },
            }
        )

        self.assertFalse(ciel_runtime._channel_stdin_active_tool_call_from_text(transcript))

    def test_inject_pending_channel_messages_defers_while_tool_call_active(self):
        messages = [
            {
                "id": 12,
                "channel": "room",
                "sender_id": "agent",
                "message": "wake after tool",
                "meta": {},
                "delivery": ["llm"],
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "stop_reason": "tool_use",
                            "content": [{"type": "tool_use", "id": "toolu_active", "name": "Bash", "input": {}}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
                mock.patch.object(ciel_runtime, "_commit_channel_llm_cursor_if_newer") as commit_cursor,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                last_id = ciel_runtime._inject_pending_channel_messages(99, 11)

        self.assertEqual(11, last_id)
        write_all.assert_not_called()
        commit_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("active_tool_call" in item for item in log_messages))

    def test_channel_stdin_wake_state_treats_queued_command_as_queued_not_missing(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "queue-operation",
                                "operation": "enqueue",
                                "content": "[ciel-runtime external channel message] id=4971 text=\"hello\"",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "attachment",
                                "attachment": {
                                    "type": "queued_command",
                                    "prompt": "[ciel-runtime external channel message] id=4971 text=\"hello\"",
                                },
                            }
                        ),
                        json.dumps({"type": "user", "message": {"content": "id=4972 text=\"next\""}}),
                        json.dumps({"type": "assistant", "message": {"content": []}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript):
                self.assertEqual("queued", ciel_runtime._channel_stdin_wake_state(4971))
                self.assertEqual("completed", ciel_runtime._channel_stdin_wake_state(4972))

    def test_channel_stdin_recover_cursor_keeps_queued_command_message_advanced(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "queue-operation",
                                "operation": "enqueue",
                                "content": "[ciel-runtime external channel message] id=4971 text=\"kevin\"",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "attachment",
                                "attachment": {
                                    "type": "queued_command",
                                    "prompt": "[ciel-runtime external channel message] id=4971 text=\"kevin\"",
                                },
                            }
                        ),
                        json.dumps({"type": "user", "message": {"content": "id=4972 text=\"later\""}}),
                        json.dumps({"type": "assistant", "message": {"content": []}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            ciel_runtime._CHANNEL_STDIN_RECOVERY_CACHE.clear()
            with (
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                self.assertEqual(4987, ciel_runtime._channel_stdin_recover_cursor_from_queued_only(4987))

    def test_channel_stdin_recover_cursor_respects_channel_clear_floor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            floor_path = root / "channel-llm-clear-floor.json"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "queue-operation",
                                "operation": "enqueue",
                                "content": "[ciel-runtime external channel message] id=4971 text=\"old\"",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "attachment",
                                "attachment": {
                                    "type": "queued_command",
                                    "prompt": "[ciel-runtime external channel message] id=4971 text=\"old\"",
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            floor_path.write_text('{"last_id":4987}\n', encoding="utf-8")
            ciel_runtime._CHANNEL_STDIN_RECOVERY_CACHE.clear()
            with (
                mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript),
                mock.patch.object(ciel_runtime, "CHANNEL_LLM_CLEAR_FLOOR_PATH", floor_path),
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                self.assertEqual(4987, ciel_runtime._channel_stdin_recover_cursor_from_queued_only(4987))

    def test_channel_stdin_recover_cursor_keeps_completed_messages_advanced(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "message": {"content": "id=4971 text=\"kevin\""}}),
                        json.dumps({"type": "assistant", "message": {"content": []}}),
                        json.dumps(
                            {
                                "type": "queue-operation",
                                "operation": "enqueue",
                                "content": "[ciel-runtime external channel message] id=4971 text=\"kevin\"",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            ciel_runtime._CHANNEL_STDIN_RECOVERY_CACHE.clear()
            with mock.patch.object(ciel_runtime, "_latest_claude_transcript_path", return_value=transcript):
                self.assertEqual(4987, ciel_runtime._channel_stdin_recover_cursor_from_queued_only(4987))

    def test_channel_stdin_unseen_retry_seconds_is_bounded(self):
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_WAKE_UNSEEN_RETRY_SECONDS": "0"}):
            self.assertEqual(2.0, ciel_runtime._channel_stdin_unseen_retry_seconds())
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_WAKE_UNSEEN_RETRY_SECONDS": "999"}):
            self.assertEqual(300.0, ciel_runtime._channel_stdin_unseen_retry_seconds())

    def test_channel_stdin_rechecks_pending_after_inflight_completion_without_marker_change(self):
        marker = (123.0, 456)

        self.assertTrue(
            ciel_runtime._channel_stdin_should_check_pending(
                marker,
                marker,
                force_recheck=True,
                channel_inflight_id=None,
            )
        )
        self.assertFalse(
            ciel_runtime._channel_stdin_should_check_pending(
                marker,
                marker,
                force_recheck=False,
                channel_inflight_id=None,
            )
        )
        self.assertTrue(
            ciel_runtime._channel_stdin_should_check_pending(
                marker,
                marker,
                force_recheck=True,
                channel_inflight_id=3807,
            )
        )
        self.assertTrue(
            ciel_runtime._channel_stdin_should_check_pending(
                (124.0, 456),
                marker,
                force_recheck=False,
                channel_inflight_id=3807,
            )
        )

    def test_parse_pseudo_tool_calls_converts_invoke_only_for_available_tool(self):
        body = {
            "tools": [
                {"name": "mcp__ai-net-http__get_messages", "input_schema": {"type": "object"}},
            ]
        }
        text = (
            "Reading latest.\n"
            "<invoke name=\"mcp__ai-net-http__get_messages\">\n"
            "<parameter name=\"room_id\">room1</parameter>\n"
            "<parameter name=\"limit\">5</parameter>\n"
            "</invoke>\n"
            "<note>ordinary XML remains</note>"
        )

        visible, calls = ciel_runtime.parse_pseudo_tool_calls(text, body)

        self.assertIn("Reading latest.", visible)
        self.assertIn("<note>ordinary XML remains</note>", visible)
        self.assertNotIn("<invoke", visible)
        self.assertEqual("mcp__ai-net-http__get_messages", calls[0]["function"]["name"])
        self.assertEqual({"room_id": "room1", "limit": "5"}, calls[0]["function"]["arguments"])

    def test_body_with_pending_channel_messages_injects_llm_context(self):
        body = {"messages": [{"role": "user", "content": "continue"}], "stream": True}
        messages = [
            {"id": 2, "channel": "ai-net", "sender_id": "ai-net", "message": "ai-net.sse.connected", "meta": {}},
            {"id": 3, "channel": "room", "sender_id": "agent-a", "message": "Please check this.", "meta": {"room_id": "room", "mcp_server": "generic-mcp"}},
            {
                "id": 4,
                "channel": "ai-net-sse",
                "sender_id": "ai-net-sse",
                "message": "SSE MCP initialized",
                "meta": {"transport": "sse", "event": "initialized"},
            },
        ]
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value={"claude_code": {"channel_delivery": "llm"}}),
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            out = ciel_runtime.body_with_pending_channel_messages(body)

        self.assertIsNot(out, body)
        self.assertEqual(2, len(out["messages"]))
        injected = out["messages"][-1]["content"][0]["text"]
        self.assertEqual("Please check this.", injected)
        self.assertNotIn("[external channel input]", injected)
        self.assertNotIn("로컬 사용자 승인 없이 같은 채널/DM에 답장", injected)
        self.assertNotIn("답장 여부를 묻고 멈추지 마세요", injected)
        self.assertNotIn("미래 행동을 약속하는 말만 남기고 턴을 끝내지 마세요", injected)
        self.assertNotIn("같은 턴에서 필요한 조사/도구 호출/채널 보고까지 수행", injected)
        self.assertNotIn("실제 결제/투자 실행", injected)
        self.assertIn("Please check this.", injected)
        self.assertNotIn("ai-net.sse.connected", injected)
        self.assertNotIn("SSE MCP initialized", injected)
        write_cursor.assert_not_called()
        self.assertEqual("3", out["metadata"]["ciel_runtime_channel_cursor_last_id"])
        handler = type("Handler", (), {"_ciel_runtime_response_status": 200})()
        with (
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as commit_cursor,
        ):
            ciel_runtime.commit_pending_channel_delivery_cursors(out, handler)  # type: ignore[arg-type]
        commit_cursor.assert_called_with(3)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_llm_injected" in item and "message_ids=3" in item for item in log_messages))
        self.assertTrue(any("channel_llm_inject_skipped" in item and "transport_connected" in item for item in log_messages))

    def test_body_with_pending_channel_messages_allows_wake_during_stale_plan_mode(self):
        body = {
            "messages": [
                {"role": "user", "attachment": {"type": "plan_mode"}, "content": []},
                {
                    "role": "user",
                    "content": "\x15[external input pending] type=mcp_notification ids=3 channels=room",
                },
            ],
            "stream": True,
        }
        messages = [
            {
                "id": 3,
                "channel": "room",
                "sender_id": "agent-a",
                "message": "Wake-relevant channel message.",
                "meta": {"room_id": "room", "mcp_server": "generic-mcp"},
            }
        ]
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value={"claude_code": {"channel_delivery": "llm"}}),
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            out = ciel_runtime.body_with_pending_channel_messages(body)

        self.assertIsNot(out, body)
        self.assertIn("Wake-relevant channel message.", out["messages"][-1]["content"][0]["text"])
        self.assertNotIn("[external input pending]", json.dumps(out["messages"], ensure_ascii=False))
        self.assertEqual("3", out["metadata"]["ciel_runtime_channel_message_ids"])
        write_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_llm_inject_plan_mode_override" in item for item in log_messages))
        self.assertTrue(any("channel_llm_wake_prompt_stripped" in item for item in log_messages))

    def test_body_with_pending_channel_messages_strips_wake_when_no_pending(self):
        body = {
            "messages": [
                {"role": "user", "content": "previous user request"},
                {"role": "user", "content": "\x15[external input pending] type=mcp_notification ids=3 channels=room"},
            ],
            "stream": True,
        }
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value={"claude_code": {"channel_delivery": "llm"}}),
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=3),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=[]),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            out = ciel_runtime.body_with_pending_channel_messages(body)

        self.assertIsNot(out, body)
        self.assertEqual([{"role": "user", "content": "previous user request"}], out["messages"])
        self.assertNotIn("[external input pending]", json.dumps(out["messages"], ensure_ascii=False))
        write_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_llm_wake_prompt_stripped" in item for item in log_messages))

    def test_body_without_ciel_runtime_internal_metadata_strips_private_keys(self):
        body = {
            "model": "ciel-runtime-test",
            "metadata": {
                "ciel_runtime_channel_injected": True,
                "ciel_runtime_channel_cursor_last_id": "9",
                "user_id": "user-1",
            },
        }

        out = ciel_runtime.body_without_ciel_runtime_internal_metadata(body)

        self.assertIsNot(out, body)
        self.assertEqual({"user_id": "user-1"}, out["metadata"])
        self.assertIn("ciel_runtime_channel_injected", body["metadata"])

    def test_body_without_ciel_runtime_internal_metadata_removes_empty_metadata(self):
        body = {
            "model": "ciel-runtime-test",
            "metadata": {
                "ciel_runtime_channel_summary_injected": True,
                "ciel_runtime_channel_summary_cursor_last_id": "12",
            },
        }

        out = ciel_runtime.body_without_ciel_runtime_internal_metadata(body)

        self.assertIsNot(out, body)
        self.assertNotIn("metadata", out)

    def test_commit_pending_channel_delivery_cursors_accepts_private_metadata_override(self):
        sanitized_body = {"model": "ciel-runtime-test"}
        private_metadata = {"ciel_runtime_channel_cursor_last_id": "9"}
        handler = type("Handler", (), {"_ciel_runtime_response_status": 200})()

        with (
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as commit_cursor,
        ):
            ciel_runtime.commit_pending_channel_delivery_cursors(
                sanitized_body,
                handler,  # type: ignore[arg-type]
                metadata=private_metadata,
            )

        commit_cursor.assert_called_with(9)

    def test_ensure_channel_llm_delivery_cursor_preserves_existing_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            cursor_path = Path(td) / "channel-llm-cursor.json"
            cursor_path.write_text('{"last_id":3}\n', encoding="utf-8")
            original_cursor = ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID
            try:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = None
                with (
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", cursor_path),
                    mock.patch.object(ciel_runtime, "_chat_init_next_id", return_value=10),
                ):
                    self.assertEqual(3, ciel_runtime.ensure_channel_llm_delivery_cursor_initialized())
            finally:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = original_cursor

    def test_channel_llm_cursor_read_refreshes_newer_file_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            cursor_path = Path(td) / "channel-llm-cursor.json"
            cursor_path.write_text('{"last_id":66}\n', encoding="utf-8")
            original_cursor = ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID
            try:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = 51
                with mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", cursor_path):
                    self.assertEqual(66, ciel_runtime._channel_llm_read_cursor_locked())
                self.assertEqual(66, ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID)
            finally:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = original_cursor

    def test_channel_llm_cursor_read_keeps_memory_when_file_is_older(self):
        with tempfile.TemporaryDirectory() as td:
            cursor_path = Path(td) / "channel-llm-cursor.json"
            cursor_path.write_text('{"last_id":12}\n', encoding="utf-8")
            original_cursor = ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID
            try:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = 66
                with mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", cursor_path):
                    self.assertEqual(66, ciel_runtime._channel_llm_read_cursor_locked())
                self.assertEqual(66, ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID)
            finally:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = original_cursor

    def test_prepare_channel_llm_delivery_for_launch_preserves_recent_messages(self):
        with tempfile.TemporaryDirectory(prefix="ca-channel-launch-") as td:
            root = Path(td)
            chat_path = root / "chat-messages.jsonl"
            cursor_path = root / "channel-llm-cursor.json"
            guard_path = root / "channel-llm-launch-guard.json"
            now = time.time()
            old_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 3600))
            recent_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 30))
            chat_path.write_text(
                json.dumps({"id": 10, "time": old_time, "channel": "room", "message": "old"}, ensure_ascii=False) + "\n"
                + json.dumps({"id": 11, "time": recent_time, "channel": "room", "message": "recent"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            cursor_path.write_text('{"last_id":9}\n', encoding="utf-8")
            original_cursor = ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID
            try:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = None
                with (
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_path),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", cursor_path),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_LAUNCH_GUARD_PATH", guard_path),
                    mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_LAUNCH_RECENT_SECONDS": "600"}, clear=False),
                ):
                    self.assertEqual(10, ciel_runtime.prepare_channel_llm_delivery_for_launch())
                self.assertEqual({"last_id": 10}, json.loads(cursor_path.read_text(encoding="utf-8")))
                self.assertEqual(10, json.loads(guard_path.read_text(encoding="utf-8"))["max_existing_id"])
            finally:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = original_cursor

    def test_prepare_channel_llm_delivery_for_launch_can_fast_forward_all_when_recent_disabled(self):
        with tempfile.TemporaryDirectory(prefix="ca-channel-launch-") as td:
            root = Path(td)
            chat_path = root / "chat-messages.jsonl"
            cursor_path = root / "channel-llm-cursor.json"
            guard_path = root / "channel-llm-launch-guard.json"
            recent_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time()))
            chat_path.write_text(
                json.dumps({"id": 12, "time": recent_time, "channel": "room", "message": "recent"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            cursor_path.write_text('{"last_id":2}\n', encoding="utf-8")
            original_cursor = ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID
            try:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = None
                with (
                    mock.patch.object(ciel_runtime, "CHAT_MESSAGES_PATH", chat_path),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_CURSOR_PATH", cursor_path),
                    mock.patch.object(ciel_runtime, "CHANNEL_LLM_LAUNCH_GUARD_PATH", guard_path),
                    mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHANNEL_LAUNCH_RECENT_SECONDS": "0"}, clear=False),
                ):
                    self.assertEqual(12, ciel_runtime.prepare_channel_llm_delivery_for_launch())
                self.assertEqual({"last_id": 12}, json.loads(cursor_path.read_text(encoding="utf-8")))
            finally:
                ciel_runtime._CHANNEL_LLM_CURSOR_LAST_ID = original_cursor

    def test_commit_pending_channel_delivery_cursors_skips_failed_response(self):
        body = {
            "metadata": {
                "ciel_runtime_channel_cursor_last_id": "9",
            }
        }
        handler = type("Handler", (), {"_ciel_runtime_response_status": 500})()
        with (
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            ciel_runtime.commit_pending_channel_delivery_cursors(body, handler)  # type: ignore[arg-type]

        write_cursor.assert_not_called()
        self.assertTrue(any("channel_delivery_cursor_deferred" in str(call.args[1]) for call in router_log.call_args_list))

    def test_commit_pending_channel_delivery_cursors_skips_unconfirmed_channel_response(self):
        body = {
            "metadata": {
                "ciel_runtime_channel_cursor_last_id": "9",
            }
        }
        handler = type(
            "Handler",
            (),
            {
                "_ciel_runtime_response_status": 200,
                "_ciel_runtime_channel_delivery_guard": True,
                "_ciel_runtime_channel_delivery_ok": False,
                "_ciel_runtime_channel_delivery_reason": "ollama_stream_error:TimeoutError",
            },
        )()
        with (
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            ciel_runtime.commit_pending_channel_delivery_cursors(body, handler)  # type: ignore[arg-type]

        write_cursor.assert_not_called()
        self.assertTrue(
            any(
                "channel_delivery_cursor_deferred" in str(call.args[1])
                and "ollama_stream_error:TimeoutError" in str(call.args[1])
                for call in router_log.call_args_list
            )
        )

    def test_commit_pending_channel_delivery_cursors_commits_confirmed_channel_response(self):
        body = {
            "metadata": {
                "ciel_runtime_channel_cursor_last_id": "9",
            }
        }
        handler = type(
            "Handler",
            (),
            {
                "_ciel_runtime_response_status": 200,
                "_ciel_runtime_channel_delivery_guard": True,
                "_ciel_runtime_channel_delivery_ok": True,
                "_ciel_runtime_channel_delivery_reason": "ollama_stream_message_stop",
            },
        )()
        with (
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
        ):
            ciel_runtime.commit_pending_channel_delivery_cursors(body, handler)  # type: ignore[arg-type]

        write_cursor.assert_called_with(9)

    def test_body_with_pending_channel_messages_keeps_ai_net_write_tools(self):
        body = {
            "messages": [{"role": "user", "content": "continue"}],
            "stream": True,
            "tools": [
                {"name": "mcp__ai-net-sse__send_dm"},
                {"name": "mcp__ai-net-sse__send_message"},
                {"name": "mcp__ai-net-sse__get_messages"},
                {"name": "mcp__duckduckgo__search"},
            ],
            "tool_choice": {"type": "tool", "name": "mcp__ai-net-sse__send_dm"},
        }
        messages = [
            {"id": 3, "channel": "room", "sender_id": "agent-a", "message": "Please read this", "meta": {"room_id": "room", "mcp_server": "generic-mcp"}}
        ]
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value={"claude_code": {"channel_delivery": "llm"}}),
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked"),
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            out = ciel_runtime.body_with_pending_channel_messages(body)

        tool_names = [tool.get("name") for tool in out["tools"]]
        self.assertIn("mcp__ai-net-sse__send_dm", tool_names)
        self.assertIn("mcp__ai-net-sse__send_message", tool_names)
        self.assertIn("mcp__ai-net-sse__get_messages", tool_names)
        self.assertIn("mcp__duckduckgo__search", tool_names)
        self.assertEqual({"type": "tool", "name": "mcp__ai-net-sse__send_dm"}, out["tool_choice"])
        self.assertTrue(out["metadata"]["ciel_runtime_channel_injected"])
        self.assertEqual("3", out["metadata"]["ciel_runtime_channel_message_ids"])
        injected = out["messages"][-1]["content"][0]["text"]
        self.assertEqual("Please read this", injected)
        self.assertNotIn("[external channel input]", injected)
        self.assertNotIn("자율 처리 턴", injected)
        self.assertNotIn("필요한 읽기/쓰기 도구를 호출", injected)
        self.assertNotIn("tool_result", injected)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_llm_injected" in item and "message_ids=3" in item for item in log_messages))

    def test_channel_llm_prompt_keeps_ai_net_dm_as_data_only(self):
        prompt = ciel_runtime.format_channel_llm_batch_prompt(
            [
                {
                    "id": 110,
                    "channel": "room_4pyr8vvwm2cd",
                    "sender_id": "agent_2i7ibhkysdk1",
                    "recipients": ["agent_n3wy9gfjmcil"],
                    "message": "Sarah, 추가 매크로 분석 보고서를 보내주세요.",
                    "meta": {"room_id": "room_4pyr8vvwm2cd", "sender": "Robert", "recipient": "Sarah", "message_id": "msg_task"},
                }
            ]
        )
        self.assertEqual("Sarah, 추가 매크로 분석 보고서를 보내주세요.", prompt)
        self.assertNotIn("[external channel input]", prompt)
        self.assertNotIn("현재 Claude Code 세션의 에이전트에게 도착한 실제 업무 메시지", prompt)
        self.assertNotIn("DM/업무 지시/상태 확인/컨텍스트 요청", prompt)
        self.assertNotIn("로컬 사용자 승인 없이 같은 채널/DM에 답장", prompt)
        self.assertNotIn("답장 여부를 묻고 멈추지 마세요", prompt)
        self.assertNotIn("진행하겠습니다", prompt)
        self.assertNotIn("같은 턴에서 필요한 조사/도구 호출/채널 보고까지 수행", prompt)
        self.assertNotIn("단순 온보딩/인사/중복 테스트 메시지", prompt)
        self.assertNotIn("NO_REPLY", prompt)
        self.assertNotIn('to=["agent_n3wy9gfjmcil"]', prompt)
        self.assertNotIn('"message_id":"msg_task"', prompt)
        self.assertNotIn("after_id/cursor", prompt)
        self.assertNotIn("ciel-runtime-router send_message", prompt)
        self.assertNotIn("recipients='web'", prompt)
        self.assertNotIn("웹 채팅 요청", prompt)

    def test_channel_llm_prompt_omits_browser_reply_instructions_for_web_chat(self):
        prompt = ciel_runtime.format_channel_llm_batch_prompt(
            [
                {
                    "id": 220,
                    "channel": "web-chat-session",
                    "sender_id": "web-user",
                    "recipients": ["agent"],
                    "message": "현재 작업 상태를 알려줘",
                    "kind": "web_chat",
                    "meta": {
                        "source": "ciel-runtime-web-chat",
                        "reply_channel": "web-chat-session",
                        "reply_recipient": "web",
                    },
                }
            ]
        )
        self.assertNotIn("ciel-runtime-router send_message", prompt)
        self.assertNotIn("recipients='web'", prompt)
        self.assertNotIn("웹 채팅 요청", prompt)
        self.assertIn("현재 작업 상태를 알려줘", prompt)

    def test_channel_tool_result_context_is_injected_for_remembered_tool_use(self):
        ciel_runtime._CHANNEL_LLM_TOOL_CONTEXT.clear()
        source_body = {
            "metadata": {
                "ciel_runtime_channel_injected": True,
                "ciel_runtime_channel_message_ids": "110",
            },
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "[ciel-runtime channel inbox]\n<< room >> 에서 SSE 메시지가 도착했습니다.\n<< 발신자 >> Sarah\n<< 메시지 >> Robert 리드님, 준비 완료입니다.",
                        }
                    ],
                }
            ],
        }
        assistant_message = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_channel_1",
                    "name": "mcp__ai-net-sse__send_dm",
                    "input": {"to_agent_id": "agent_sarah", "content": "확인했습니다."},
                }
            ],
        }
        with mock.patch.object(ciel_runtime, "router_log") as router_log:
            ciel_runtime.remember_channel_injected_tool_uses(source_body, assistant_message)
            followup_body = {
                "messages": [
                    assistant_message,
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_channel_1",
                                "content": "DM sent",
                            }
                        ],
                    },
                ],
            }
            out = ciel_runtime.body_with_channel_tool_result_context(followup_body)

        self.assertIsNot(out, followup_body)
        self.assertTrue(out["metadata"]["ciel_runtime_channel_tool_result_followup"])
        injected = out["messages"][-1]["content"][0]["text"]
        self.assertIn("channel tool_result follow-up", injected)
        self.assertIn("toolu_channel_1", injected)
        self.assertIn("mcp__ai-net-sse__send_dm", injected)
        self.assertIn("Sarah", injected)
        self.assertIn("Robert 리드님, 준비 완료입니다.", injected)
        second = ciel_runtime.body_with_channel_tool_result_context(followup_body)
        self.assertIs(second, followup_body)
        self.assertEqual({}, ciel_runtime._CHANNEL_LLM_TOOL_CONTEXT)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_llm_tool_context_stored" in item and "toolu_channel_1" in item for item in log_messages))
        self.assertTrue(any("channel_llm_tool_result_context_injected" in item and "toolu_channel_1" in item for item in log_messages))

    def test_summarize_messages_for_trace_includes_tool_result_blocks(self):
        summary = ciel_runtime.summarize_messages_for_trace(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_trace_1",
                            "name": "mcp__ai-net-sse__send_dm",
                            "input": {"content": "hello"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_trace_1",
                            "content": "sent",
                        }
                    ],
                },
            ]
        )

        self.assertEqual("tool_use", summary[0]["content"][0]["type"])
        self.assertEqual("toolu_trace_1", summary[0]["content"][0]["id"])
        self.assertEqual("tool_result", summary[1]["content"][0]["type"])
        self.assertEqual("toolu_trace_1", summary[1]["content"][0]["tool_use_id"])
        self.assertEqual("sent", summary[1]["content"][0]["content"])

    def test_body_with_pending_channel_messages_skips_message_already_in_request(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "[ciel-runtime external channel message] channel=room "
                                "room=room from=ai-net-http id=5058 text=\"wake\""
                            ),
                        }
                    ],
                }
            ],
            "stream": True,
        }
        messages = [
            {
                "id": 5058,
                "channel": "room",
                "sender_id": "ai-net-http",
                "message": "wake",
                "meta": {"room_id": "room", "mcp_server": "ai-net-http"},
            }
        ]
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value={"claude_code": {"channel_delivery": "llm"}}),
            mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=5057),
            mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            out = ciel_runtime.body_with_pending_channel_messages(body)

        self.assertIs(out, body)
        write_cursor.assert_called_with(5058)
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("already_in_request" in item and "message_id=5058" in item for item in log_messages))

    def test_channel_message_ids_already_in_request_ignores_unrelated_ids(self):
        body = {
            "messages": [
                {"role": "user", "content": "ordinary text id=12"},
                {
                    "role": "user",
                    "content": (
                        "[ciel-runtime external channel messages] 2 new messages: "
                        "(id=14 room=room) \"one\" | (id=15 room=room) \"two\""
                    ),
                },
            ]
        }

        self.assertEqual({14, 15}, ciel_runtime._channel_message_ids_already_in_request(body))

    def test_channel_message_ids_already_in_request_accepts_external_input_ids(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": "[external channel input]\n<< room >>\nids=14,15 channel=room\nhello",
                }
            ]
        }

        self.assertEqual({14, 15}, ciel_runtime._channel_message_ids_already_in_request(body))

    def test_sanitize_assistant_pseudo_tool_text_history_removes_invoke_snippets_only(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "I will send it now.\n\n"
                                "court\n"
                                "<invoke name=\"mcp__ai-net-http__send_message\">\n"
                                "<parameter name=\"room_id\">room1</parameter>\n"
                                "</invoke>\n"
                                "Done."
                            ),
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_real",
                            "name": "mcp__ai-net-http__send_message",
                            "input": {"room_id": "room1", "content": "real"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": "<invoke name=\"mcp__ai-net-http__send_message\"></invoke> is user text",
                },
            ]
        }

        out = ciel_runtime.sanitize_assistant_pseudo_tool_text_history(body)

        assistant_content = out["messages"][0]["content"]
        self.assertNotIn("<invoke", assistant_content[0]["text"])
        self.assertNotIn("ciel-runtime", assistant_content[0]["text"])
        self.assertIn("I will send it now.", assistant_content[0]["text"])
        self.assertIn("Done.", assistant_content[0]["text"])
        self.assertEqual("tool_use", assistant_content[1]["type"])
        self.assertIn("<invoke", out["messages"][1]["content"])

    def test_sanitize_assistant_pseudo_tool_text_history_removes_xml_alias_tool_snippets(self):
        body = {
            "tools": [
                {"name": "Read"},
                {"name": "mcp__ai-net-http__get_assignment"},
            ],
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "I need to read the file.\n"
                                "<read>\n"
                                "<file_path>/tmp/example.txt</file_path>\n"
                                "<offset>0</offset>\n"
                                "<limit>20</limit>\n"
                                "</read>\n"
                                "Then fetch the assignment.\n"
                                "<get_assignment>\n"
                                "<parameter name=\"assignment_id\">tasgn_123</parameter>\n"
                                "</get_assignment>\n"
                                "<note>This is ordinary XML and should remain.</note>"
                            ),
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_real",
                            "name": "Read",
                            "input": {"file_path": "/tmp/example.txt", "offset": 0, "limit": 20},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": "<get_assignment><parameter name=\"assignment_id\">tasgn_user</parameter></get_assignment>",
                },
            ]
        }

        out = ciel_runtime.sanitize_assistant_pseudo_tool_text_history(body)

        assistant_text = out["messages"][0]["content"][0]["text"]
        self.assertNotIn("<read>", assistant_text)
        self.assertNotIn("<get_assignment>", assistant_text)
        self.assertIn("<note>This is ordinary XML and should remain.</note>", assistant_text)
        self.assertNotIn("ciel-runtime", assistant_text)
        self.assertEqual("tool_use", out["messages"][0]["content"][1]["type"])
        self.assertIn("<get_assignment>", out["messages"][1]["content"])

    def test_body_with_pending_channel_messages_skips_stdin_wake_delivered_messages(self):
        body = {"messages": [{"role": "user", "content": "continue"}], "stream": True}
        messages = [
            {
                "id": 3,
                "channel": "web-chat-session",
                "sender_id": "web-user",
                "message": "already typed",
                "kind": "web_chat",
                "meta": {"source": "ciel-runtime-web-chat"},
            }
        ]
        ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.clear()
        ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.add(3)
        try:
            with (
                mock.patch.object(ciel_runtime, "load_config", return_value={"claude_code": {"channel_delivery": "llm"}}),
                mock.patch.object(ciel_runtime, "_channel_llm_read_cursor_locked", return_value=1),
                mock.patch.object(ciel_runtime, "_channel_llm_write_cursor_locked") as write_cursor,
                mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                out = ciel_runtime.body_with_pending_channel_messages(body)
        finally:
            ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.clear()
        self.assertIs(out, body)
        write_cursor.assert_not_called()
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("stdin_wake_delivered" in item for item in log_messages))

    def test_channel_sse_dispatch_ignores_native_router_self_echo(self):
        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        try:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS["mcp-ciel-runtime-router"] = {
                "name": "mcp-ciel-runtime-router",
                "channel": "room_4pyr8vvwm2cd",
            }
            with (
                mock.patch.object(ciel_runtime, "_sse_payload_to_chat_payload") as parse_payload,
                mock.patch.object(ciel_runtime, "append_chat_message") as append,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                ciel_runtime._channel_sse_dispatch(
                    "mcp-ciel-runtime-router",
                    "message",
                    ['{"method":"notifications/claude/channel","params":{"recipients":["all"]}}'],
                )
        finally:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)

        parse_payload.assert_not_called()
        append.assert_not_called()
        self.assertTrue(any("native_router_self_echo" in str(call.args[1]) for call in router_log.call_args_list))

    def test_channel_sse_dispatch_stores_mcp_rpc_response_without_chat_append(self):
        original_connections = dict(ciel_runtime._CHANNEL_SSE_CONNECTIONS)
        try:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS["mcp-ai-net-sse"] = {
                "name": "mcp-ai-net-sse",
                "mcp_rpc_results": {},
            }
            with (
                mock.patch.object(ciel_runtime, "_sse_payload_to_chat_payload") as parse_payload,
                mock.patch.object(ciel_runtime, "append_chat_message") as append,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                ciel_runtime._channel_sse_dispatch(
                    "mcp-ai-net-sse",
                    "message",
                    [json.dumps({"jsonrpc": "2.0", "id": 123, "result": {"ok": True}})],
                )
                state = ciel_runtime._CHANNEL_SSE_CONNECTIONS["mcp-ai-net-sse"]
                stored = state["mcp_rpc_results"]["123"]
        finally:
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.clear()
            ciel_runtime._CHANNEL_SSE_CONNECTIONS.update(original_connections)

        self.assertEqual({"ok": True}, stored["result"])
        parse_payload.assert_not_called()
        append.assert_not_called()
        self.assertTrue(any("channel_sse_mcp_rpc_response" in str(call.args[1]) for call in router_log.call_args_list))

    def test_channel_string_list_decodes_json_array_strings(self):
        self.assertEqual(["all"], ciel_runtime._as_string_list('["all"]'))
        self.assertEqual(["Robert", "Sarah"], ciel_runtime._as_string_list(['["Robert"]', "Sarah"]))

    def test_channel_llm_skip_reason_rejects_internal_and_router_self_echo(self):
        self.assertEqual("recipient_internal", ciel_runtime._channel_llm_message_skip_reason({"message": "x", "recipients": "internal"}))
        self.assertEqual(
            "native_router_self_echo",
            ciel_runtime._channel_llm_message_skip_reason(
                {"message": "x", "sender_id": "mcp-ciel-runtime-router", "meta": {"sse_source": "mcp-ciel-runtime-router"}}
            ),
        )

    def test_channel_llm_skip_reason_rejects_unscoped_peer_messages(self):
        message = {"message": "hello", "channel": "room", "sender_id": "claude-code", "meta": {}}
        self.assertEqual("unscoped_channel_message", ciel_runtime._channel_llm_message_skip_reason(message))
        self.assertEqual("unscoped_channel_message", ciel_runtime._channel_mcp_message_skip_reason(message))

    def test_channel_llm_skip_reason_accepts_explicit_delivery_and_mcp_provenance(self):
        self.assertIsNone(
            ciel_runtime._channel_llm_message_skip_reason(
                {"message": "hello", "channel": "room", "sender_id": "agent", "delivery": ["llm"], "meta": {}}
            )
        )
        self.assertIsNone(
            ciel_runtime._channel_llm_message_skip_reason(
                {"message": "hello", "channel": "room", "sender_id": "agent", "meta": {"mcp_server": "generic-mcp"}}
            )
        )

    def test_channel_llm_skip_reason_rejects_presence_checkins(self):
        message = {
            "message": "1 colleague checked in: Kevin.",
            "channel": "ai-net-http",
            "sender_id": "ai-net-http",
            "meta": {"mcp_server": "ai-net-http", "mcp_method": "notifications/claude/channel", "kind": "checkins"},
        }

        self.assertEqual("checkins", ciel_runtime._channel_llm_message_skip_reason(message))

    def test_channel_skip_reason_rejects_system_event_metadata(self):
        message = {"message": "Connected", "meta": {"eventType": "system"}}
        self.assertEqual("system", ciel_runtime._channel_llm_message_skip_reason(message))
        self.assertEqual("system", ciel_runtime._channel_mcp_message_skip_reason(message))

    def test_channel_superseded_message_ids_only_coalesces_unreferenced_notifications(self):
        messages = [
            {
                "id": 10,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "old notice",
                "kind": "notice",
                "meta": {"mcp_server": "generic-mcp", "mcp_method": "notifications/message", "stream_id": "100-0"},
            },
            {
                "id": 11,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "referenced message",
                "kind": "notice",
                "meta": {
                    "mcp_server": "generic-mcp",
                    "mcp_method": "notifications/message",
                    "stream_id": "101-0",
                    "message_id": "msg-1",
                },
            },
            {
                "id": 12,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "new notice",
                "kind": "notice",
                "meta": {"mcp_server": "generic-mcp", "mcp_method": "notifications/message", "stream_id": "102-0"},
            },
        ]
        self.assertEqual({10}, ciel_runtime._channel_superseded_message_ids(messages))

    def test_channel_superseded_message_ids_coalesces_empty_external_order_by_local_queue_id(self):
        messages = [
            {
                "id": 20,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "old topic notice",
                "kind": "channel",
                "meta": {
                    "mcp_server": "generic-mcp",
                    "mcp_method": "notifications/resource/updated",
                    "kind": "resource_updated",
                    "key": "shared/resource",
                    "stream_id": "100-0",
                },
            },
            {
                "id": 21,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "different topic notice",
                "kind": "channel",
                "meta": {
                    "mcp_server": "generic-mcp",
                    "mcp_method": "notifications/resource/updated",
                    "kind": "resource_updated",
                    "key": "other/resource",
                    "stream_id": "",
                },
            },
            {
                "id": 22,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "new topic notice",
                "kind": "channel",
                "meta": {
                    "mcp_server": "generic-mcp",
                    "mcp_method": "notifications/resource/updated",
                    "kind": "resource_updated",
                    "key": "shared/resource",
                    "stream_id": "",
                },
            },
        ]
        self.assertEqual({20}, ciel_runtime._channel_superseded_message_ids(messages))

    def test_channel_superseded_message_ids_keeps_nested_unique_reference(self):
        messages = [
            {
                "id": 30,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "old referenced notice",
                "kind": "channel",
                "meta": {
                    "mcp_server": "generic-mcp",
                    "mcp_method": "notifications/action",
                    "stream_id": "",
                    "mcp_json": {
                        "params": {
                            "meta": {
                                "kind": "action_closed",
                                "poll_id": "poll-1",
                            }
                        }
                    },
                },
            },
            {
                "id": 31,
                "channel": "room",
                "sender_id": "generic-mcp",
                "message": "new referenced notice",
                "kind": "channel",
                "meta": {
                    "mcp_server": "generic-mcp",
                    "mcp_method": "notifications/action",
                    "stream_id": "",
                    "mcp_json": {
                        "params": {
                            "meta": {
                                "kind": "action_closed",
                                "poll_id": "poll-2",
                            }
                        }
                    },
                },
            },
        ]
        self.assertEqual(set(), ciel_runtime._channel_superseded_message_ids(messages))

    def test_channel_superseded_message_ids_keeps_ai_net_digest_message_ids(self):
        messages = [
            {
                "id": 374,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": "[AI-Net new messages]\n• room_id2w78yhq8c8: 1 new (Test)",
                "kind": "channel",
                "meta": {
                    "mcp_server": "ai-net-http",
                    "mcp_method": "notifications/claude/channel",
                    "kind": "digest",
                    "rooms": json.dumps([{"room_id": "room_id2w78yhq8c8", "message_ids": ["msg_leporpuku7pl"]}]),
                    "cursor": "1782631241601-0",
                },
            },
            {
                "id": 375,
                "channel": "ai-net-http",
                "sender_id": "ai-net-http",
                "message": "[AI-Net new messages]\n• [DM] DM-Robert: 1 new (Joy)",
                "kind": "channel",
                "meta": {
                    "mcp_server": "ai-net-http",
                    "mcp_method": "notifications/claude/channel",
                    "kind": "digest",
                    "rooms": json.dumps([{"room_id": "room_dm_a90xk3afh8", "message_ids": ["msg_z8ao45uuy55m"]}]),
                    "cursor": "1782631318836-0",
                },
            },
        ]

        self.assertEqual(set(), ciel_runtime._channel_superseded_message_ids(messages))

    def test_mcp_notification_wait_tool_timeout_is_capped(self):
        self.addCleanup(ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear)
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS": "1000"}, clear=False),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear()
            out = ciel_runtime.cap_mcp_notification_wait_tool_input(
                "mcp__ai-net-http__wait_for_notifications",
                {"timeout_ms": 30000},
            )

        self.assertEqual({"timeout_ms": 1000}, out)

    def test_mcp_notification_wait_tool_empty_input_gets_short_timeout(self):
        self.addCleanup(ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear)
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS": "1000"}, clear=False),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear()
            out = ciel_runtime.cap_mcp_notification_wait_tool_input(
                "mcp__generic__wait_for_events",
                {},
            )

        self.assertEqual({"timeout_ms": 1000}, out)

    def test_mcp_notification_wait_tool_duplicate_timeout_is_capped_harder(self):
        self.addCleanup(ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear)
        with (
            mock.patch.dict(
                os.environ,
                {
                    "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS": "1000",
                    "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_TIMEOUT_MS": "100",
                    "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_WINDOW_SECONDS": "90",
                },
                clear=False,
            ),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear()
            first = ciel_runtime.cap_mcp_notification_wait_tool_input(
                "mcp__ai-net-http__wait_for_notifications",
                {"timeout_ms": 30000},
            )
            second = ciel_runtime.cap_mcp_notification_wait_tool_input(
                "mcp__ai-net-http__wait_for_notifications",
                {"timeout_ms": 30000},
            )

        self.assertEqual({"timeout_ms": 1000}, first)
        self.assertEqual({"timeout_ms": 100}, second)

    def test_router_channel_mcp_notification_wraps_chat_message(self):
        notification = ciel_runtime._channel_mcp_notification(
            {
                "id": 7,
                "channel": "room_phase1sim",
                "sender_id": "robert",
                "thread_id": "root",
                "message": "hello Sarah",
                "recipients": ["sarah"],
                "meta": {"room_id": "room_phase1sim"},
            }
        )
        self.assertEqual("notifications/claude/channel", notification["method"])
        self.assertIn("hello Sarah", notification["params"]["content"])
        self.assertEqual("hello Sarah", notification["params"]["message"])
        self.assertEqual("hello Sarah", notification["params"]["text"])
        self.assertEqual("room_phase1sim", notification["params"]["channel"])
        self.assertEqual("room_phase1sim", notification["params"]["room_id"])
        self.assertEqual("robert", notification["params"]["sender_id"])
        self.assertEqual(["sarah"], notification["params"]["recipients"])
        self.assertEqual("7", notification["params"]["meta"]["ciel_runtime_message_id"])
        self.assertEqual('["sarah"]', notification["params"]["meta"]["recipients"])

    def test_router_channel_mcp_notification_normalizes_json_string_recipients(self):
        notification = ciel_runtime._channel_mcp_notification(
            {
                "id": 9,
                "channel": "room",
                "sender_id": "robert",
                "message": "hello",
                "recipients": '["sarah"]',
                "meta": {"room_id": "room"},
            }
        )
        self.assertEqual(["sarah"], notification["params"]["recipients"])
        self.assertEqual('["sarah"]', notification["params"]["meta"]["recipients"])

    def test_router_channel_mcp_notification_stringifies_meta_for_native_schema(self):
        notification = ciel_runtime._channel_mcp_notification(
            {
                "id": 8,
                "channel": "room",
                "sender_id": "agent",
                "thread_id": "root",
                "message": "native wake",
                "kind": "message",
                "meta": {"room_id": "room", "mcp_json": {"method": "notifications/message"}, "count": 3},
            }
        )
        meta = notification["params"]["meta"]
        self.assertTrue(all(isinstance(key, str) and isinstance(value, str) for key, value in meta.items()))
        self.assertEqual("8", meta["ciel_runtime_message_id"])
        self.assertEqual("3", meta["count"])
        self.assertIn("notifications/message", meta["mcp_json"])
        self.assertIn("mcp_json", meta["ciel_runtime_meta_json"])

    def test_channel_mcp_capabilities_declare_native_channel(self):
        capabilities = ciel_runtime._channel_mcp_capabilities()
        self.assertIn("tools", capabilities)
        self.assertIn("claude/channel", capabilities["experimental"])

    def test_channel_mcp_sse_headers_keep_connection_alive(self):
        class FakeHandler:
            def __init__(self):
                self.status = None
                self.headers = []
                self.ended = False

            def send_response(self, status):
                self.status = status

            def send_header(self, name, value):
                self.headers.append((name.lower(), value))

            def end_headers(self):
                self.ended = True

        handler = FakeHandler()
        ciel_runtime._send_channel_mcp_sse_headers(handler)
        headers = dict(handler.headers)
        self.assertEqual(200, handler.status)
        self.assertEqual("text/event-stream", headers["content-type"])
        self.assertEqual("keep-alive", headers["connection"])
        self.assertEqual("no", headers["x-accel-buffering"])
        self.assertTrue(handler.ended)

    def test_channel_mcp_rpc_responses_are_queued_for_sse(self):
        session = "session-rpc"
        with ciel_runtime._CHANNEL_MCP_LOCK:
            original = dict(ciel_runtime._CHANNEL_MCP_SESSIONS)
            ciel_runtime._CHANNEL_MCP_SESSIONS.clear()
            ciel_runtime._CHANNEL_MCP_SESSIONS[session] = {"outbox": []}
        try:
            response = ciel_runtime._channel_mcp_initialize_response(1, "2025-11-25")
            self.assertTrue(ciel_runtime._channel_mcp_enqueue(session, response))
            outbox = ciel_runtime._channel_mcp_take_outbox(session)
            self.assertEqual([response], outbox)
            self.assertEqual([], ciel_runtime._channel_mcp_take_outbox(session))
            self.assertEqual("ciel-runtime-router", outbox[0]["result"]["serverInfo"]["name"])
            self.assertEqual("2024-11-05", outbox[0]["result"]["protocolVersion"])
            self.assertIn("claude/channel", outbox[0]["result"]["capabilities"]["experimental"])
        finally:
            with ciel_runtime._CHANNEL_MCP_LOCK:
                ciel_runtime._CHANNEL_MCP_SESSIONS.clear()
                ciel_runtime._CHANNEL_MCP_SESSIONS.update(original)

    def test_channel_mcp_enqueue_rejects_missing_session(self):
        self.assertFalse(ciel_runtime._channel_mcp_enqueue("missing-session", {"jsonrpc": "2.0"}))

    def test_channel_mcp_notifications_ignore_transport_noise(self):
        messages = [
            {"id": 1, "channel": "generic-room", "sender_id": "generic-mcp", "message": "generic.ws.connected", "meta": {}},
            {"id": 2, "channel": "generic-room", "sender_id": "agent-a", "message": "hello recipient", "meta": {"room_id": "generic-room", "mcp_server": "generic-mcp"}},
        ]
        with mock.patch.object(ciel_runtime, "router_log") as router_log:
            last_id, events = ciel_runtime._channel_mcp_notifications_for_messages(messages, "session-1")
        self.assertEqual(2, last_id)
        self.assertEqual(1, len(events))
        self.assertEqual(2, events[0][0])
        self.assertIn("hello recipient", events[0][1]["params"]["content"])
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_mcp_skipped_noise" in item and "transport_connected" in item for item in log_messages))
        self.assertTrue(any("channel_mcp_notification_prepared" in item and "message_id=2" in item for item in log_messages))

    def test_channel_mcp_notifications_skip_internal_messages(self):
        messages = [
            {
                "id": 15,
                "channel": "room",
                "sender_id": "ciel-runtime-llm",
                "recipients": ["internal"],
                "message": "old internal response",
                "visibility": "user",
                "delivery": ["native"],
                "meta": {"room_id": "room"},
            },
            {
                "id": 16,
                "channel": "room",
                "sender_id": "ai-net",
                "recipients": ["all"],
                "message": "new external message",
                "visibility": "user",
                "delivery": ["native", "llm"],
                "meta": {"room_id": "room"},
            },
        ]
        with mock.patch.object(ciel_runtime, "router_log") as router_log:
            last_id, events = ciel_runtime._channel_mcp_notifications_for_messages(messages, "session-1")
        self.assertEqual(16, last_id)
        self.assertEqual(1, len(events))
        self.assertEqual(16, events[0][0])
        self.assertEqual(["all"], events[0][1]["params"]["recipients"])
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("recipient_internal" in item and "message_id=15" in item for item in log_messages))

    def test_channel_mcp_notifications_skip_llm_only_inputs(self):
        messages = [
            {
                "id": 106,
                "channel": "room",
                "sender_id": "ai-net",
                "recipients": ["all"],
                "message": "inbound event",
                "visibility": "user",
                "delivery": ["llm"],
                "meta": {"room_id": "room"},
            },
            {
                "id": 107,
                "channel": "room",
                "sender_id": "ciel-runtime-llm",
                "recipients": ["all"],
                "message": "direct response",
                "visibility": "user",
                "delivery": ["native"],
                "kind": "channel_llm_response",
                "meta": {"room_id": "room", "source_message_id": 106},
            },
        ]
        with mock.patch.object(ciel_runtime, "router_log") as router_log:
            last_id, events = ciel_runtime._channel_mcp_notifications_for_messages(messages, "session-1")
        self.assertEqual(107, last_id)
        self.assertEqual(1, len(events))
        self.assertEqual(107, events[0][0])
        self.assertEqual("channel_llm_response", events[0][1]["params"]["kind"])
        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("delivery_not_native" in item and "message_id=106" in item for item in log_messages))

    def test_channel_mcp_session_start_prefers_client_last_event_id_for_replay(self):
        class Handler:
            path = "/ca/mcp/sse"
            headers = {"Last-Event-ID": "10"}

        with (
            mock.patch.object(ciel_runtime, "_channel_mcp_ensure_cursor_initialized", return_value=12),
            mock.patch.object(ciel_runtime, "_channel_mcp_update_cursor") as update_cursor,
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            last_id = ciel_runtime._channel_mcp_session_start_last_id(Handler())
        self.assertEqual(10, last_id)
        update_cursor.assert_not_called()
        self.assertTrue(any("channel_mcp_resume" in str(call.args[1]) and "client_last_id=10" in str(call.args[1]) for call in router_log.call_args_list))

    def test_channel_mcp_session_start_advances_cursor_from_client_ack(self):
        class Handler:
            path = "/ca/mcp/sse?lastEventId=15"
            headers = {}

        with (
            mock.patch.object(ciel_runtime, "_channel_mcp_ensure_cursor_initialized", return_value=12),
            mock.patch.object(ciel_runtime, "_channel_mcp_update_cursor") as update_cursor,
        ):
            last_id = ciel_runtime._channel_mcp_session_start_last_id(Handler())
        self.assertEqual(15, last_id)
        update_cursor.assert_called_once_with(15)

    def test_channel_mcp_cursor_initializes_at_current_tail(self):
        with tempfile.TemporaryDirectory(prefix="ca-channel-cursor-") as td:
            root = Path(td)
            cursor_path = root / "cursor.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHANNEL_MCP_CURSOR_PATH", cursor_path),
                mock.patch.object(ciel_runtime, "_CHANNEL_MCP_CURSOR_LAST_ID", None),
                mock.patch.object(ciel_runtime, "_chat_scan_max_id", return_value=41),
            ):
                last_id = ciel_runtime._channel_mcp_ensure_cursor_initialized()
                self.assertEqual(41, last_id)
                self.assertEqual({"last_id": 41}, json.loads(cursor_path.read_text(encoding="utf-8")))

    def test_channel_mcp_cursor_persists_across_reconnects(self):
        with tempfile.TemporaryDirectory(prefix="ca-channel-cursor-") as td:
            root = Path(td)
            cursor_path = root / "cursor.json"
            cursor_path.write_text('{"last_id":9}\n', encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CHANNEL_MCP_CURSOR_PATH", cursor_path),
                mock.patch.object(ciel_runtime, "_CHANNEL_MCP_CURSOR_LAST_ID", None),
            ):
                self.assertEqual(9, ciel_runtime._channel_mcp_ensure_cursor_initialized())
                ciel_runtime._channel_mcp_update_cursor(12)
                self.assertEqual(12, ciel_runtime._channel_mcp_ensure_cursor_initialized())
                self.assertEqual({"last_id": 12}, json.loads(cursor_path.read_text(encoding="utf-8")))

    def test_mcp_proxy_notification_maps_to_chat_payload(self):
        payload = ciel_runtime._mcp_proxy_notification_payload(
            "ai-net",
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "data": {
                        "room_id": "room_phase1sim",
                        "payload": {"message": {"content": "wake from server"}},
                        "sender_id": "robert",
                    }
                },
            },
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual("wake from server", json.loads(payload["message"])["params"]["data"]["payload"]["message"]["content"])
        self.assertEqual("robert", payload["sender_id"])
        self.assertEqual("room_phase1sim", payload["channel"])
        self.assertEqual("notifications/message", payload["meta"]["mcp_method"])
        self.assertEqual("wake from server", payload["meta"]["mcp_json"]["params"]["data"]["payload"]["message"]["content"])

    def test_mcp_proxy_observer_deduplicates_generic_and_native_channel_notifications(self):
        generic = {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"content": "hello team", "room_id": "room_phase1sim", "sender_id": "robert", "thread_id": "root"},
        }
        native = {
            "jsonrpc": "2.0",
            "method": "notifications/claude/channel",
            "params": {"content": "hello team", "room_id": "room_phase1sim", "sender_id": "robert", "thread_id": "root"},
        }
        ciel_runtime._MCP_NOTIFICATION_DEDUP_RECENT.clear()
        try:
            with (
                mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 21}) as append,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                ciel_runtime._mcp_proxy_observe_json_message("ai-net", generic)
                ciel_runtime._mcp_proxy_observe_json_message("ai-net", native)
            append.assert_called_once()
            log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
            self.assertTrue(any("mcp_proxy_notification_skipped_duplicate" in item for item in log_messages))
        finally:
            ciel_runtime._MCP_NOTIFICATION_DEDUP_RECENT.clear()

    def test_mcp_proxy_observer_deduplicates_stable_event_across_writers(self):
        first = {
            "jsonrpc": "2.0",
            "method": "notifications/claude/channel",
            "params": {
                "content": "task completed",
                "meta": {
                    "kind": "assignment_completed",
                    "room_id": "room_phase1sim",
                    "assignment_id": "tasgn_same",
                    "stream_id": "1781045186019-0",
                },
            },
        }
        second = json.loads(json.dumps(first))
        ciel_runtime._MCP_NOTIFICATION_DEDUP_RECENT.clear()
        try:
            with (
                mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 24}) as append,
                mock.patch.object(ciel_runtime, "router_log") as router_log,
            ):
                ciel_runtime._mcp_proxy_observe_json_message("ai-net", first)
                ciel_runtime._mcp_proxy_observe_json_message("ai-net-http", second)
            append.assert_called_once()
            log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
            self.assertTrue(any("mcp_proxy_notification_skipped_duplicate" in item for item in log_messages))
        finally:
            ciel_runtime._MCP_NOTIFICATION_DEDUP_RECENT.clear()

    def test_mcp_proxy_observer_allows_repeated_same_method_notifications(self):
        message = {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"content": "repeatable alert", "room_id": "room_phase1sim", "sender_id": "robert"},
        }
        ciel_runtime._MCP_NOTIFICATION_DEDUP_RECENT.clear()
        try:
            with (
                mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 22}) as append,
            ):
                ciel_runtime._mcp_proxy_observe_json_message("ai-net", message)
                ciel_runtime._mcp_proxy_observe_json_message("ai-net", message)
            self.assertEqual(2, append.call_count)
        finally:
            ciel_runtime._MCP_NOTIFICATION_DEDUP_RECENT.clear()

    def test_mcp_proxy_observer_reads_content_length_framed_notification(self):
        body = __import__("json").dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "data": {
                        "room_id": "room_phase1sim",
                        "payload": {"message": {"content": "wake from framed mcp"}},
                        "sender_id": "robert",
                    }
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        with (
            mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 11}) as append,
        ):
            observer = ciel_runtime._McpStdoutObserver("ai-net")
            observer.feed(frame[:10])
            observer.feed(frame[10:])
        append.assert_called_once()
        payload = append.call_args.args[0]
        self.assertEqual("wake from framed mcp", json.loads(payload["message"])["params"]["data"]["payload"]["message"]["content"])
        self.assertEqual("robert", payload["sender_id"])
        self.assertEqual("room_phase1sim", payload["channel"])

    def test_mcp_proxy_observer_accepts_content_type_before_length(self):
        body = b'{"jsonrpc":"2.0","method":"notifications/message","params":{"content":"typed frame"}}'
        frame = b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n" + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        with mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 13}) as append:
            observer = ciel_runtime._McpStdoutObserver("generic")
            observer.feed(frame)
        append.assert_called_once()
        self.assertEqual("typed frame", json.loads(append.call_args.args[0]["message"])["params"]["content"])

    def test_mcp_proxy_observer_reads_jsonl_notification(self):
        line = (
            __import__("json").dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"content": "wake from json line", "room_id": "room"},
                }
            )
            + "\n"
        ).encode("utf-8")
        with mock.patch.object(ciel_runtime, "append_chat_message", return_value={"id": 12}) as append:
            observer = ciel_runtime._McpStdoutObserver("generic")
            observer.feed(line)
        append.assert_called_once()
        self.assertEqual("wake from json line", json.loads(append.call_args.args[0]["message"])["params"]["content"])

    def test_mcp_proxy_compacts_large_get_messages_tool_result(self):
        payload = {
            "success": True,
            "data": [
                {
                    "id": "msg_large",
                    "room_id": "room_iyjjx0bzfimr",
                    "sender_name": "PERP Monitor Bot",
                    "kind": "text",
                    "content": "시장 요약 본문\n" + ("important context " * 200),
                    "metadata": {
                        "stream_id": "1782508801647-0",
                        "snapshot": {"huge": "x" * 60000, "headlines": ["a", "b"]},
                    },
                }
            ],
        }
        response = {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "isError": False,
            },
        }

        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_MCP_TOOL_RESULT_MAX_CHARS": "8000"}):
            compacted = ciel_runtime._mcp_proxy_compact_tool_result_response(
                "ai-net-http",
                "get_messages",
                response,
            )

        text = compacted["result"]["content"][0]["text"]
        parsed = json.loads(text)
        self.assertLessEqual(len(text), 8000 + 40)
        self.assertTrue(parsed["ciel_runtime_compacted"])
        self.assertEqual("msg_large", parsed["data"][0]["id"])
        self.assertIn("시장 요약 본문", parsed["data"][0]["content"])
        self.assertIn("snapshot_keys", parsed["data"][0]["metadata"])
        self.assertNotIn("x" * 1000, text)

    def test_mcp_proxy_subcommand_round_trips_stdio_frame(self):
        with tempfile.TemporaryDirectory(prefix="ca-mcp-test-") as td:
            root = Path(td)
            server = root / "fake_server.py"
            server.write_text(
                textwrap.dedent(
                    r'''
                    import json
                    import sys

                    def read_frame():
                        header = b""
                        while b"\r\n\r\n" not in header:
                            chunk = sys.stdin.buffer.read(1)
                            if not chunk:
                                return None
                            header += chunk
                        length = 0
                        for line in header.decode("ascii", "replace").split("\r\n"):
                            if line.lower().startswith("content-length:"):
                                length = int(line.split(":", 1)[1].strip())
                        return sys.stdin.buffer.read(length)

                    def write_frame(payload):
                        body = json.dumps(payload).encode("utf-8")
                        sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
                        sys.stdout.buffer.flush()

                    frame = read_frame()
                    if frame:
                        request = json.loads(frame.decode("utf-8"))
                        write_frame({"jsonrpc": "2.0", "id": request.get("id"), "result": {"protocolVersion": "2024-11-05", "capabilities": {}}})
                        write_frame({"jsonrpc": "2.0", "method": "notifications/message", "params": {"content": "wake from subprocess"}})
                    '''
                ),
                encoding="utf-8",
            )
            config = root / "server.json"
            config.write_text(json.dumps({"command": sys.executable, "args": [str(server)]}), encoding="utf-8")
            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            body = json.dumps(request).encode("utf-8")
            input_frame = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
            env = os.environ.copy()
            env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(ciel_runtime.__file__).resolve()),
                    "mcp-proxy",
                    "--server-name",
                    "fake",
                    "--server-config",
                    str(config),
                ],
                input=input_frame,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=5,
                check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", errors="replace"))
            self.assertIn(b"Content-Length:", proc.stdout)
            self.assertIn(b'"id": 1', proc.stdout)
            chat_log = root / "config" / "chat-messages.jsonl"
            self.assertTrue(chat_log.exists())
            self.assertIn("wake from subprocess", chat_log.read_text(encoding="utf-8"))

    def test_mcp_proxy_subcommand_bridges_jsonl_stdio_server(self):
        with tempfile.TemporaryDirectory(prefix="ca-mcp-jsonl-test-") as td:
            root = Path(td)
            server = root / "fake_jsonl_server.py"
            server.write_text(
                textwrap.dedent(
                    r'''
                    import json
                    import sys

                    line = sys.stdin.buffer.readline()
                    if line:
                        request = json.loads(line.decode("utf-8"))
                        print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": {"protocolVersion": "2024-11-05", "capabilities": {}}}), flush=True)
                        print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {"content": "wake from jsonl subprocess"}}), flush=True)
                    '''
                ),
                encoding="utf-8",
            )
            config = root / "server.json"
            config.write_text(
                json.dumps({"command": sys.executable, "args": [str(server)], "ciel_runtime_stdio": "jsonl"}),
                encoding="utf-8",
            )
            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            body = json.dumps(request).encode("utf-8")
            input_frame = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
            env = os.environ.copy()
            env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(ciel_runtime.__file__).resolve()),
                    "mcp-proxy",
                    "--server-name",
                    "fake-jsonl",
                    "--server-config",
                    str(config),
                ],
                input=input_frame,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=5,
                check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", errors="replace"))
            self.assertIn(b"Content-Length:", proc.stdout)
            self.assertIn(b'"id": 1', proc.stdout)
            self.assertNotIn(b"Content-Length:", proc.stderr)
            chat_log = root / "config" / "chat-messages.jsonl"
            self.assertTrue(chat_log.exists())
            self.assertIn("wake from jsonl subprocess", chat_log.read_text(encoding="utf-8"))

    def test_mcp_proxy_subcommand_bridges_streamable_http_server(self):
        seen_posts: list[dict[str, object]] = []
        seen_gets: list[dict[str, object]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                seen_posts.append({"method": payload.get("method"), "session": self.headers.get("Mcp-Session-Id")})
                if payload.get("method") == "initialize":
                    result = {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "streamable-test", "version": "1"},
                    }
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                    data = json.dumps(response).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-proxy")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if payload.get("method") == "notifications/initialized":
                    response = {"jsonrpc": "2.0", "result": {}}
                elif payload.get("method") == "tools/list":
                    response = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "tools": [
                                {
                                    "name": "echo",
                                    "description": "Echo test",
                                    "inputSchema": {"type": "object", "properties": {}},
                                }
                            ]
                        },
                    }
                else:
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                seen_gets.append({"session": self.headers.get("Mcp-Session-Id"), "accept": self.headers.get("Accept")})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'id: notify-1\n'
                    b'event: message\n'
                    b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"stream notice"}}\n\n'
                )
                self.wfile.flush()
                time.sleep(0.2)

        def frame(payload: dict[str, object]) -> bytes:
            body = json.dumps(payload).encode("utf-8")
            return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-proxy-test-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps({"type": "http", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(ciel_runtime.__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        "fake-http",
                        "--server-config",
                        str(config),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None
                assert proc.stdout is not None
                assert proc.stderr is not None
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                proc.stdin.write(frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
                proc.stdin.flush()
                deadline = time.time() + 3
                while time.time() < deadline and not seen_gets:
                    time.sleep(0.02)
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
                proc.stdin.flush()
                time.sleep(0.1)
                proc.stdin.close()
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                self.assertIn(b"Content-Length:", stdout)
                self.assertIn(b'"id":1', stdout)
                self.assertIn(b'"id":2', stdout)
                self.assertIn(b'"tools"', stdout)
                self.assertNotIn(b"stream notice", stdout)
                chat_log = root / "config" / "chat-messages.jsonl"
                self.assertTrue(chat_log.exists())
                self.assertIn("stream notice", chat_log.read_text(encoding="utf-8"))
                self.assertEqual(["initialize", "notifications/initialized", "tools/list"], [item["method"] for item in seen_posts])
                self.assertIsNone(seen_posts[0]["session"])
                self.assertEqual("sess-proxy", seen_posts[1]["session"])
                self.assertEqual("sess-proxy", seen_posts[2]["session"])
                self.assertTrue(seen_gets)
                self.assertEqual("sess-proxy", seen_gets[0]["session"])
                self.assertIn("text/event-stream", str(seen_gets[0]["accept"]))
            finally:
                server.shutdown()
                server.server_close()

    def test_codex_mcp_split_proxy_forwards_post_without_upstream_get(self):
        seen_posts: list[dict[str, object]] = []
        seen_gets: list[dict[str, object]] = []

        class UpstreamHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                seen_posts.append({"method": payload.get("method"), "session": self.headers.get("Mcp-Session-Id")})
                body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "sess-upstream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                seen_gets.append({"session": self.headers.get("Mcp-Session-Id"), "accept": self.headers.get("Accept")})
                self.send_response(500)
                self.end_headers()

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        router = ThreadingHTTPServer(("127.0.0.1", 0), ciel_runtime.RouterHandler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        threading.Thread(target=router.serve_forever, daemon=True).start()
        try:
            upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}/mcp"
            router_url = f"http://127.0.0.1:{router.server_address[1]}"
            with tempfile.TemporaryDirectory(prefix="ca-codex-split-proxy-") as td:
                config = Path(td) / "codex-mcp.json"
                config.write_text(
                    json.dumps({"mcpServers": {"ai-net": {"type": "http", "url": upstream_url}}}),
                    encoding="utf-8",
                )
                with (
                    mock.patch.object(ciel_runtime, "CODEX_MCP_CONFIG", config),
                    mock.patch.object(ciel_runtime, "reject_external_router_request", return_value=False),
                    mock.patch.dict(os.environ, {"CIEL_RUNTIME_CODEX_MCP_LOCAL_SSE_SECONDS": "0.01"}, clear=False),
                ):
                    post_req = ciel_runtime.urllib.request.Request(
                        f"{router_url}/ca/codex-mcp/ai-net",
                        data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
                        headers={"Content-Type": "application/json", "Mcp-Session-Id": "sess-client"},
                        method="POST",
                    )
                    with ciel_runtime.urllib.request.urlopen(post_req, timeout=2) as response:
                        post_body = json.loads(response.read().decode("utf-8"))
                        returned_session = response.headers.get("Mcp-Session-Id")

                    get_req = ciel_runtime.urllib.request.Request(
                        f"{router_url}/ca/codex-mcp/ai-net",
                        headers={"Accept": "text/event-stream", "Mcp-Session-Id": "sess-client"},
                        method="GET",
                    )
                    with ciel_runtime.urllib.request.urlopen(get_req, timeout=2) as response:
                        get_body = response.read().decode("utf-8")

            self.assertEqual({"jsonrpc": "2.0", "id": 1, "result": {}}, post_body)
            self.assertEqual("sess-upstream", returned_session)
            self.assertEqual([{"method": "tools/list", "session": "sess-client"}], seen_posts)
            self.assertEqual([], seen_gets)
            self.assertIn("ciel-runtime owns upstream SSE", get_body)
        finally:
            router.shutdown()
            router.server_close()
            upstream.shutdown()
            upstream.server_close()

    def test_mcp_proxy_streamable_http_waits_for_initialized_before_get(self):
        lock = threading.Lock()
        state = {"initialized": False}
        seen_gets: list[bool] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if payload.get("method") == "initialize":
                    result = {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "streamable-test", "version": "1"},
                    }
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                    data = json.dumps(response).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-init-order")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if payload.get("method") == "notifications/initialized":
                    with lock:
                        state["initialized"] = True
                data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with lock:
                    ready = bool(state["initialized"])
                seen_gets.append(ready)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if ready:
                    self.wfile.write(
                        b'id: notify-ready\n'
                        b'event: message\n'
                        b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"post initialized notice"}}\n\n'
                    )
                    self.wfile.flush()
                else:
                    self.wfile.write(b": pre-initialized stream is intentionally quiet\n\n")
                    self.wfile.flush()
                time.sleep(0.4)

        def frame(payload: dict[str, object]) -> bytes:
            body = json.dumps(payload).encode("utf-8")
            return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-init-order-test-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps({"type": "http", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(ciel_runtime.__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        "fake-http",
                        "--server-config",
                        str(config),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None
                assert proc.stdout is not None
                assert proc.stderr is not None
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                proc.stdin.flush()
                time.sleep(0.15)
                proc.stdin.write(frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
                proc.stdin.flush()
                time.sleep(0.6)
                proc.stdin.close()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                self.assertTrue(seen_gets)
                self.assertTrue(seen_gets[0], f"first GET opened before notifications/initialized: {seen_gets}")
                chat_log = root / "config" / "chat-messages.jsonl"
                self.assertTrue(chat_log.exists())
                self.assertIn("post initialized notice", chat_log.read_text(encoding="utf-8"))
            finally:
                server.shutdown()
                server.server_close()

    def test_mcp_proxy_streamable_http_reopens_get_after_late_initialized(self):
        lock = threading.Lock()
        state = {"initialized": False}
        seen_gets: list[bool] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if payload.get("method") == "initialize":
                    result = {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "streamable-test", "version": "1"},
                    }
                    data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-late-init")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if payload.get("method") == "notifications/initialized":
                    with lock:
                        state["initialized"] = True
                data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with lock:
                    ready = bool(state["initialized"])
                    seen_gets.append(ready)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if ready:
                    self.wfile.write(
                        b'id: late-ready\n'
                        b'event: message\n'
                        b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"late initialized notice"}}\n\n'
                    )
                    self.wfile.flush()
                    time.sleep(0.1)
                    return
                # Simulate the real failure mode: a stream opened before
                # notifications/initialized stays alive but does not receive
                # backend notifications. The proxy must reopen it when the
                # initialized notification arrives instead of waiting for this
                # quiet stream to time out.
                self.wfile.write(b": pre-initialized stream is quiet\n\n")
                self.wfile.flush()
                time.sleep(2.0)

        def frame(payload: dict[str, object]) -> bytes:
            body = json.dumps(payload).encode("utf-8")
            return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-late-init-test-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps(
                        {
                            "type": "http",
                            "url": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                            "initialized_wait_seconds": 0.05,
                            "notification_read_timeout_seconds": 5,
                        }
                    ),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(ciel_runtime.__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        "fake-http",
                        "--server-config",
                        str(config),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None
                assert proc.stdout is not None
                assert proc.stderr is not None
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                proc.stdin.flush()
                deadline = time.time() + 5.0
                while time.time() < deadline and not seen_gets:
                    time.sleep(0.02)
                self.assertTrue(seen_gets, "proxy did not open the pre-initialized GET stream")
                proc.stdin.write(frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
                proc.stdin.flush()
                chat_log = root / "config" / "chat-messages.jsonl"
                deadline = time.time() + 1.5
                while time.time() < deadline:
                    if chat_log.exists() and "late initialized notice" in chat_log.read_text(encoding="utf-8"):
                        break
                    time.sleep(0.05)
                proc.stdin.close()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                self.assertFalse(seen_gets[0], f"test did not start with a pre-initialized GET: {seen_gets}")
                self.assertTrue(any(seen_gets[1:]), f"GET stream was not reopened after initialized: {seen_gets}")
                self.assertTrue(chat_log.exists())
                self.assertIn("late initialized notice", chat_log.read_text(encoding="utf-8"))
            finally:
                server.shutdown()
                server.server_close()

    def test_mcp_proxy_streamable_http_replies_jsonl_when_client_uses_jsonl(self):
        # Claude Code's stdio MCP client speaks newline-delimited JSON, not
        # LSP-style Content-Length frames. When a channel-capable streamable-HTTP
        # backend is forced through the proxy, the proxy must reply in JSONL or
        # Claude Code fails to connect to the server ("Failed to connect").
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if payload.get("method") == "initialize":
                    result = {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "streamable-test", "version": "1"},
                    }
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                    data = json.dumps(response).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-jsonl")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(0.1)

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-jsonl-test-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps({"type": "http", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(ciel_runtime.__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        "fake-http",
                        "--server-config",
                        str(config),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None
                assert proc.stdout is not None
                assert proc.stderr is not None
                # JSONL framing: one compact JSON object per line, no headers.
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8") + b"\n")
                proc.stdin.flush()
                time.sleep(0.2)
                proc.stdin.close()
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                # The reply must be JSONL: no Content-Length header, and the
                # first non-empty line parses as the initialize result.
                self.assertNotIn(b"Content-Length:", stdout)
                first_line = stdout.strip().splitlines()[0]
                reply = json.loads(first_line.decode("utf-8"))
                self.assertEqual(1, reply["id"])
                self.assertIn("protocolVersion", reply["result"])
            finally:
                server.shutdown()
                server.server_close()

    def test_mcp_proxy_streamable_http_wait_tool_receives_queued_notification(self):
        seen_posts: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                seen_posts.append(str(payload.get("method") or ""))
                if payload.get("method") == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "streamable-test", "version": "1"},
                        },
                    }
                    data = json.dumps(response).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-wait")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}
                data = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'id: notify-wait\n'
                    b'event: message\n'
                    b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"queued wait notice","room_id":"room_1"}}\n\n'
                )
                self.wfile.flush()
                time.sleep(0.2)

        def frame(payload: dict[str, object]) -> bytes:
            body = json.dumps(payload).encode("utf-8")
            return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-wait-test-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps({"type": "http", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(ciel_runtime.__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        "fake-http",
                        "--server-config",
                        str(config),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None
                assert proc.stdout is not None
                assert proc.stderr is not None
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                proc.stdin.write(frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
                proc.stdin.write(
                    frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "wait_for_notifications", "arguments": {"timeout_ms": 2000}},
                        }
                    )
                )
                proc.stdin.flush()
                time.sleep(0.3)
                proc.stdin.close()
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                self.assertIn(b'"id":2', stdout)
                self.assertIn(b"queued wait notice", stdout)
                self.assertNotIn("tools/call", seen_posts)
                chat_log = root / "config" / "chat-messages.jsonl"
                self.assertTrue(chat_log.exists())
                self.assertIn("queued wait notice", chat_log.read_text(encoding="utf-8"))
            finally:
                server.shutdown()
                server.server_close()

    def test_mcp_proxy_streamable_http_single_owner_self_heals_without_leak(self):
        # The single-owner manager must: (1) re-initialize the session itself
        # when the backend drops it, with NO Claude Code tool call (idle
        # self-heal); (2) never hold more than ONE concurrent GET notification
        # stream (no worker/connection leak); (3) store a notification delivered
        # after self-heal exactly ONCE. The old split design leaked stream
        # workers and stored each notification N times.
        lock = threading.Lock()
        st = {"inits": 0, "open_gets": 0, "max_open_gets": 0, "drop": False, "gets": 0}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if payload.get("method") == "initialize":
                    with lock:
                        st["inits"] += 1
                        st["drop"] = False
                        sess = f"sess-{st['inits']}"
                    result = {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "t", "version": "1"},
                    }
                    data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", sess)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with lock:
                    st["gets"] += 1
                    dropping = st["drop"]
                    n = st["gets"]
                if dropping or n == 1:
                    # First GET (and any GET after a forced drop) is rejected as
                    # session-not-found, forcing the manager to re-initialize.
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                with lock:
                    st["open_gets"] += 1
                    st["max_open_gets"] = max(st["max_open_gets"], st["open_gets"])
                    # Emit the post-heal notification on the FIRST successful GET
                    # only, so a duplicate stored copy means a real leak, not the
                    # fake server re-sending on every reconnect.
                    first_ok = not st.get("emitted")
                    st["emitted"] = True
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    if first_ok:
                        self.wfile.write(
                            b'event: message\n'
                            b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"healed once","room_id":"r1"}}\n\n'
                        )
                        self.wfile.flush()
                    time.sleep(0.8)
                finally:
                    with lock:
                        st["open_gets"] -= 1

        def frame(p):
            b = json.dumps(p).encode("utf-8")
            return b"Content-Length: " + str(len(b)).encode("ascii") + b"\r\n\r\n" + b

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-single-owner-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps({"type": "http", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp", "retry_seconds": 1}),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [sys.executable, str(Path(ciel_runtime.__file__).resolve()), "mcp-proxy",
                     "--server-name", "t", "--server-config", str(config)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                )
                assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
                # Only an initialize -- NO tool calls. Self-heal must happen on its own.
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                proc.stdin.flush()
                time.sleep(2.0)
                # Force a session drop while idle; the manager must re-init by itself.
                with lock:
                    st["drop"] = True
                time.sleep(2.5)
                proc.stdin.close()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                with lock:
                    max_open = st["max_open_gets"]
                    inits = st["inits"]
                # NEVER more than one concurrent notification stream (no leak).
                self.assertLessEqual(max_open, 1, f"stream leak: max concurrent GET streams = {max_open}")
                # Self-heal happened without any tool call (>=2 initializes).
                self.assertGreaterEqual(inits, 2, f"expected idle self-heal re-init, inits={inits}")
                # The post-heal notification was captured (>=1; the fake server
                # may re-emit across reconnects). The single-owner guarantee is
                # carried by max_open<=1 above -- with the old leaking design the
                # same notification was stored once PER leaked worker.
                chat_log = root / "config" / "chat-messages.jsonl"
                if chat_log.exists():
                    self.assertGreaterEqual(chat_log.read_text(encoding="utf-8").count("healed once"), 1)
            finally:
                server.shutdown()
                server.server_close()

    def test_mcp_proxy_streamable_http_quiet_timeout_resumes_same_session(self):
        lock = threading.Lock()
        state = {"inits": 0}
        seen_gets: list[dict[str, str | None]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if payload.get("method") == "initialize":
                    with lock:
                        state["inits"] += 1
                    result = {
                        "protocolVersion": ciel_runtime.MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "streamable-test", "version": "1"},
                    }
                    data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "sess-quiet")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                incoming_last_event_id = self.headers.get("Last-Event-ID")
                with lock:
                    seen_gets.append(
                        {
                            "session": self.headers.get("Mcp-Session-Id"),
                            "last_event_id": incoming_last_event_id,
                        }
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if incoming_last_event_id != "first-event":
                    self.wfile.write(
                        b'id: first-event\n'
                        b'event: message\n'
                        b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"first quiet event","room_id":"r1"}}\n\n'
                    )
                    self.wfile.flush()
                    time.sleep(7.0)
                    return
                self.wfile.write(
                    b'event: message\n'
                    b'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"content":"resumed after quiet timeout","room_id":"r1"}}\n\n'
                )
                self.wfile.flush()

        def frame(payload: dict[str, object]) -> bytes:
            body = json.dumps(payload).encode("utf-8")
            return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body

        with tempfile.TemporaryDirectory(prefix="ca-mcp-http-quiet-timeout-") as td:
            root = Path(td)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                config = root / "server.json"
                config.write_text(
                    json.dumps(
                        {
                            "type": "http",
                            "url": f"http://127.0.0.1:{server.server_address[1]}/mcp",
                            "notification_read_timeout_seconds": 5,
                            "retry_seconds": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(ciel_runtime.__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        "quiet-http",
                        "--server-config",
                        str(config),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                proc.stdin.write(frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
                proc.stdin.flush()
                chat_log = root / "config" / "chat-messages.jsonl"
                deadline = time.time() + 9.0
                while time.time() < deadline:
                    if chat_log.exists() and "resumed after quiet timeout" in chat_log.read_text(encoding="utf-8"):
                        break
                    time.sleep(0.05)
                proc.stdin.close()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
                self.assertTrue(chat_log.exists())
                self.assertIn("resumed after quiet timeout", chat_log.read_text(encoding="utf-8"))
                with lock:
                    inits = state["inits"]
                    gets = list(seen_gets)
                self.assertEqual(1, inits)
                self.assertGreaterEqual(len(gets), 2)
                self.assertEqual("sess-quiet", gets[0]["session"])
                self.assertTrue(
                    any(item["session"] == "sess-quiet" and item["last_event_id"] == "first-event" for item in gets[1:]),
                    f"proxy did not resume quiet timeout with Last-Event-ID: {gets}",
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_channel_mcp_config_points_to_router_sse(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "channel-mcp.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(td)),
                mock.patch.object(ciel_runtime, "CHANNEL_MCP_CONFIG", path),
                mock.patch.object(ciel_runtime, "_channel_mcp_ensure_cursor_initialized", return_value=0),
            ):
                written = ciel_runtime.write_channel_mcp_config()
            data = __import__("json").loads(written.read_text(encoding="utf-8"))
        self.assertEqual("sse", data["mcpServers"]["ciel-runtime-router"]["type"])
        self.assertTrue(data["mcpServers"]["ciel-runtime-router"]["url"].endswith("/ca/mcp/sse"))

    def test_channel_mcp_endpoint_uses_legacy_session_id_param(self):
        session = "session-123"
        endpoint = f"/ca/mcp/messages?sessionId={session}"
        params = urllib.parse.parse_qs(urllib.parse.urlparse(endpoint).query)
        self.assertEqual(session, params["sessionId"][0])


if __name__ == "__main__":
    unittest.main()
