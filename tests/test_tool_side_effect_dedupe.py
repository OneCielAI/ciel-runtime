import threading
import unittest

from ciel_runtime_support.tool_side_effect_dedupe import (
    ToolSideEffectDedupePolicy,
    ToolSideEffectDedupePorts,
    ToolSideEffectDedupeRepository,
    ToolSideEffectDedupeService,
)


class ToolSideEffectDedupeServiceTest(unittest.TestCase):
    def service(self):
        recent = {}
        audits = []
        logs = []
        clock = iter((10.0, 11.0, 700.0))
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(frozenset({"send_message"}), ttl_seconds=600),
            ToolSideEffectDedupeRepository(recent, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: next(clock),
                audit=lambda event, payload: audits.append((event, payload)),
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        return service, recent, audits, logs

    def test_exact_repeat_is_dropped_and_audited(self):
        service, recent, audits, logs = self.service()
        tool_input = {"message": "hello", "room": "general"}

        self.assertFalse(service.should_drop("mcp__chat__send_message", tool_input))
        self.assertTrue(service.should_drop("mcp__chat__send_message", tool_input))

        self.assertEqual(1, len(recent))
        self.assertEqual("dropped_duplicate_side_effect_tool_call", audits[0][0])
        self.assertTrue(any("dropped duplicate" in message for _level, message in logs))

    def test_hash_is_stable_across_mapping_order(self):
        service, _recent, _audits, _logs = self.service()
        self.assertEqual(
            service.key("send_message", {"a": 1, "b": 2}),
            service.key("send_message", {"b": 2, "a": 1}),
        )

    def test_read_only_tool_is_not_deduplicated(self):
        service, recent, _audits, _logs = self.service()
        self.assertFalse(service.should_drop("mcp__chat__get_messages", {}))
        self.assertEqual({}, recent)


if __name__ == "__main__":
    unittest.main()
