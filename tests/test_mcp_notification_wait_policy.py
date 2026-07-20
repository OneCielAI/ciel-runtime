import threading
import unittest

from ciel_runtime_support.mcp_notification_wait_policy import (
    McpNotificationWaitPolicy,
    McpNotificationWaitPorts,
    McpNotificationWaitRepository,
    McpNotificationWaitService,
)


class McpNotificationWaitPolicyTest(unittest.TestCase):
    def service(self, env=None, schema=None):
        values = env or {}
        recent = {}
        logs = []
        clock = iter((10.0, 11.0, 200.0))
        service = McpNotificationWaitService(
            McpNotificationWaitPolicy(values.get),
            McpNotificationWaitRepository(recent, threading.Lock()),
            McpNotificationWaitPorts(
                lookup_schema=lambda name: schema,
                now=lambda: next(clock),
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        return service, recent, logs

    def test_wait_tool_duplicate_uses_stricter_cap(self):
        service, recent, logs = self.service(
            {
                "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS": "1000",
                "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_TIMEOUT_MS": "100",
                "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_WINDOW_SECONDS": "90",
            }
        )

        first = service.cap_input("mcp__chat__wait_for_messages", {"timeout_ms": 30_000})
        second = service.cap_input("mcp__chat__wait_for_messages", {"timeout_ms": 30_000})

        self.assertEqual(1000, first["timeout_ms"])
        self.assertEqual(100, second["timeout_ms"])
        self.assertEqual(1, len(recent))
        self.assertTrue(any("duplicate=true" in message for _level, message in logs))

    def test_seconds_schema_receives_seconds_cap(self):
        service, _recent, _logs = self.service(schema={"properties": {"timeout": {"type": "number"}}})

        projected = service.cap_input("mcp__events__wait_for_event", {})

        self.assertEqual({"timeout": 1}, projected)

    def test_non_mcp_tool_is_unchanged(self):
        service, _recent, _logs = self.service()
        value = {"timeout_ms": 30_000}
        self.assertIs(value, service.cap_input("wait_for_event", value))


if __name__ == "__main__":
    unittest.main()
