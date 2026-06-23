import unittest

import claude_any


class CronToolCompatibilityTests(unittest.TestCase):
    def test_cron_tools_are_not_blocked_for_non_anthropic_provider(self):
        blocked = claude_any.resolve_blocked_tools("ollama-cloud", {})

        self.assertNotIn("CronCreate", blocked)
        self.assertNotIn("CronDelete", blocked)
        self.assertNotIn("CronList", blocked)
        self.assertIn("ScheduleWakeup", blocked)
        self.assertIn("WaitForMcpServers", blocked)

    def test_filter_preserves_cron_tools_for_non_anthropic_provider(self):
        body = {
            "tools": [
                {"name": "CronCreate", "input_schema": {"type": "object", "properties": {}}},
                {"name": "CronDelete", "input_schema": {"type": "object", "properties": {}}},
                {"name": "CronList", "input_schema": {"type": "object", "properties": {}}},
                {"name": "ScheduleWakeup", "input_schema": {"type": "object", "properties": {}}},
                {"name": "WaitForMcpServers", "input_schema": {"type": "object", "properties": {}}},
            ]
        }

        filtered = claude_any.filter_blocked_tools("ollama-cloud", {}, body)
        names = [tool["name"] for tool in filtered["tools"]]

        self.assertIn("CronCreate", names)
        self.assertIn("CronDelete", names)
        self.assertIn("CronList", names)
        self.assertNotIn("ScheduleWakeup", names)
        self.assertNotIn("WaitForMcpServers", names)

    def test_filter_hides_enter_plan_mode_when_ultracode_workflow_is_active(self):
        body = {
            "system": "Ultracode is on: use the Workflow tool for every substantive task.",
            "messages": [{"role": "user", "content": "implement a feature"}],
            "tools": [
                {"name": "Workflow", "input_schema": {"type": "object", "properties": {}}},
                {"name": "EnterPlanMode", "input_schema": {"type": "object", "properties": {}}},
                {"name": "Read", "input_schema": {"type": "object", "properties": {}}},
            ],
        }

        filtered = claude_any.filter_blocked_tools("opencode", {}, body)
        names = [tool["name"] for tool in filtered["tools"]]

        self.assertIn("Workflow", names)
        self.assertIn("Read", names)
        self.assertNotIn("EnterPlanMode", names)

    def test_filter_keeps_enter_plan_mode_for_anthropic_ultracode(self):
        body = {
            "system": "Ultracode is on: use the Workflow tool for every substantive task.",
            "messages": [{"role": "user", "content": "implement a feature"}],
            "tools": [
                {"name": "Workflow", "input_schema": {"type": "object", "properties": {}}},
                {"name": "EnterPlanMode", "input_schema": {"type": "object", "properties": {}}},
            ],
        }

        filtered = claude_any.filter_blocked_tools("anthropic", {}, body)
        names = [tool["name"] for tool in filtered["tools"]]

        self.assertIn("EnterPlanMode", names)

    def test_cron_create_aliases_are_normalized(self):
        fixed = claude_any._validate_and_fix_tool_input(
            "CronCreate",
            {
                "schedule": "*/1 * * * *",
                "message": "Check build status",
                "recurring": "true",
                "durable": "false",
            },
        )

        self.assertEqual(
            fixed,
            {
                "cron": "*/1 * * * *",
                "prompt": "Check build status",
                "recurring": True,
                "durable": False,
            },
        )

    def test_cron_delete_aliases_are_normalized(self):
        fixed = claude_any._validate_and_fix_tool_input("CronDelete", {"jobId": "cron-123"})

        self.assertEqual(fixed, {"id": "cron-123"})

    def test_cron_list_drops_spurious_input(self):
        fixed = claude_any._validate_and_fix_tool_input("CronList", {"anything": "ignored"})

        self.assertEqual(fixed, {})


if __name__ == "__main__":
    unittest.main()
