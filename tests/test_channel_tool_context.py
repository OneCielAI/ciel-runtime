import threading
import unittest

from ciel_runtime_support.channel_tool_context import (
    ChannelToolContextPolicy,
    ChannelToolContextPorts,
    ChannelToolContextRepository,
    ChannelToolContextService,
)


class ChannelToolContextServiceTest(unittest.TestCase):
    def service(self, *, limit=200, max_inject=8):
        logs = []
        contexts = {}
        service = ChannelToolContextService(
            repository=ChannelToolContextRepository(contexts, threading.Lock(), limit),
            policy=ChannelToolContextPolicy(max_inject=max_inject, prompt_limit=40),
            ports=ChannelToolContextPorts(
                content_to_text=lambda content: "".join(
                    str(block.get("text") or "") for block in content if isinstance(block, dict)
                ),
                truncate=lambda text, limit: text[:limit],
                now=lambda: float(len(contexts) + 1),
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        return service, contexts, logs

    def test_remember_and_inject_followup_consumes_context(self):
        service, contexts, logs = self.service()
        source = {
            "metadata": {
                "ciel_runtime_channel_injected": True,
                "ciel_runtime_channel_message_ids": "42",
            },
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "[ciel-runtime channel inbox] hello"}]}
            ],
        }
        service.remember_message(
            source,
            {
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "send", "input": {"message": "ok"}}
                ]
            },
        )

        projected = service.inject_followup(
            {
                "messages": [
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1"}]}
                ]
            }
        )

        self.assertEqual({}, contexts)
        self.assertTrue(projected["metadata"]["ciel_runtime_channel_tool_result_followup"])
        injected = projected["messages"][-1]["content"][0]["text"]
        self.assertIn("tool-1", injected)
        self.assertIn("channel_message_ids=42", injected)
        self.assertTrue(any("context_stored" in message for _level, message in logs))
        self.assertTrue(any("context_injected" in message for _level, message in logs))

    def test_repository_evicts_oldest_context(self):
        service, contexts, _logs = self.service(limit=2)
        source = {"metadata": {"ciel_runtime_channel_injected": True}, "messages": []}

        service.remember(source, "old", "tool", {})
        service.remember(source, "middle", "tool", {})
        service.remember(source, "new", "tool", {})

        self.assertEqual({"middle", "new"}, set(contexts))

    def test_non_channel_tool_use_is_not_stored(self):
        service, contexts, logs = self.service()

        service.remember({"metadata": {}, "messages": []}, "tool-1", "send", {})

        self.assertEqual({}, contexts)
        self.assertEqual([], logs)


if __name__ == "__main__":
    unittest.main()
