import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.web_search_result_events import project_web_search_results
from ciel_runtime_support.transcript_delta_delivery import TranscriptDeltaDeliveryService, TranscriptDeliveryPorts


def claude_call():
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "s1", "name": "WebSearch", "input": {"query": "private query"}}]}}


def claude_result():
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "s1", "content": 'Web search results for query: "private query"\n\nLinks: [{"url":"https://example.org/a","title":"private title"},{"url":"https://example.org/a"},{"url":"javascript:bad"}]\n\nprivate summary https://not-a-source.test'}]}}


class WebSearchResultTests(unittest.TestCase):
    def test_claude_links_only_and_deduplication(self):
        state = {}
        self.assertEqual([], project_web_search_results(claude_call(), "claude", state))
        results = project_web_search_results(claude_result(), "claude", state)
        self.assertEqual(["https://example.org/a"], results[0]["urls"])
        self.assertNotIn("private", json.dumps(results))
        self.assertEqual([], project_web_search_results(claude_result(), "claude", state))

    def test_unrelated_tool_result_is_ignored(self):
        self.assertEqual([], project_web_search_results(claude_result(), "claude", {}))

    def test_anthropic_server_results_without_call_record(self):
        record = {"type": "assistant", "message": {"content": [{"type": "web_search_tool_result", "tool_use_id": "srv1", "content": [{"type": "web_search_result", "url": "https://example.org/b", "encrypted_content": "secret"}]}]}}
        self.assertEqual(["https://example.org/b"], project_web_search_results(record, "claude", {})[0]["urls"])

    def test_codex_structured_sources_not_query_or_action_url(self):
        item = {"type": "response_item", "payload": {"type": "web_search_call", "id": "ws1", "status": "completed", "action": {"type": "search", "query": "https://query.test", "sources": [{"url": "https://example.org/a"}]}}}
        self.assertEqual(["https://example.org/a"], project_web_search_results(item, "codex", {})[0]["urls"])
        item["payload"]["action"] = {"type": "open_page", "url": "https://input.test"}
        self.assertEqual([], project_web_search_results(item, "codex", {}))
        item["payload"]["status"] = "in_progress"
        item["payload"]["results"] = [{"url": "https://example.org/a"}]
        self.assertEqual([], project_web_search_results(item, "codex", {}))

    def test_codex_function_output_links(self):
        state = {}
        call = {"type": "response_item", "payload": {"type": "function_call", "name": "web.run", "call_id": "c1", "arguments": '{"search_query":[{"q":"test"}]}'}}
        project_web_search_results(call, "codex", state)
        result = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "Page (https://example.org/a_(b))\n[other](https://example.org/c)"}}
        self.assertEqual(["https://example.org/a_(b)", "https://example.org/c"], project_web_search_results(result, "codex", state)[0]["urls"])

    def test_codex_citations_only_not_message_text(self):
        record = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "id": "m1", "content": [{"type": "output_text", "text": "https://prose.test", "annotations": [{"type": "url_citation", "url": "https://example.org/a"}]}]}}
        self.assertEqual(["https://example.org/a"], project_web_search_results(record, "codex", {})[0]["urls"])

    def test_codex_non_search_web_call_does_not_emit(self):
        state = {}
        project_web_search_results({"type": "response_item", "payload": {"type": "function_call", "name": "web.run", "call_id": "c", "arguments": '{"open":[{"ref_id":"https://input.test"}]}'}}, "codex", state)
        self.assertEqual([], project_web_search_results({"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c", "output": "https://output.test"}}, "codex", state))

    def test_malformed_links_and_error_are_ignored(self):
        for content, error in (("Links: [broken", False), ('Links: [{"url":"https://example.org"}]', True)):
            state = {}
            project_web_search_results(claude_call(), "claude", state)
            result = claude_result()
            result["message"]["content"][0].update(content=content, is_error=error)
            self.assertEqual([], project_web_search_results(result, "claude", state))

    def test_persisted_call_pairing_after_restart_and_partial_record(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript = root / "session.jsonl"
            transcript.write_text(json.dumps(claude_call()) + "\n", encoding="utf-8")
            events = []
            ports = TranscriptDeliveryPorts(
                load_config=lambda: {"tool_call_events": {"start_mode": "beginning"}},
                latest_transcript=lambda: transcript,
                scope=lambda: {"runtime": "claude", "session_id": "session1"},
                log=lambda *_: None,
                event_publish=lambda **event: events.append(event),
            )
            service = TranscriptDeltaDeliveryService(root / "cursors.json", "w", ports)
            self.assertEqual(1, service.poll_tool_call_events())
            serialized = json.dumps(claude_result())
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(serialized[:20])
            self.assertEqual(0, service.poll_tool_call_events())
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(serialized[20:] + "\n")
            restarted = TranscriptDeltaDeliveryService(root / "cursors.json", "w", ports)
            self.assertEqual(1, restarted.poll_tool_call_events())
            self.assertEqual("tool.call", events[-1]["category"])
            self.assertEqual("result", events[-1]["data"]["phase"])
            self.assertEqual(["https://example.org/a"], events[-1]["data"]["urls"])
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(serialized + "\n")
            self.assertEqual(0, restarted.poll_tool_call_events())

    def test_state_is_bounded(self):
        state = {}
        for index in range(550):
            call = claude_call()
            call["message"]["content"][0]["id"] = str(index)
            project_web_search_results(call, "claude", state)
        self.assertEqual(512, len(state["pending"]))
