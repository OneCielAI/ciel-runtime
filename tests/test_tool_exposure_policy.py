import unittest
from unittest import mock

from ciel_runtime_support.tool_exposure_policy import ToolExposurePolicy, ToolExposurePorts


class ToolExposurePolicyTests(unittest.TestCase):
    def policy(self, *, blocked=(), workflow=False, plan=False):
        log = mock.Mock()
        return (
            ToolExposurePolicy(
                ToolExposurePorts(
                    lambda _provider, _config: set(blocked),
                    lambda _body: workflow,
                    lambda _body: plan,
                    log,
                )
            ),
            log,
        )

    def test_filters_blocked_tool_and_matching_tool_choice_without_mutation(self):
        policy, log = self.policy(blocked={"CronCreate"})
        body = {
            "tools": [{"name": "CronCreate"}, {"name": "Read"}],
            "tool_choice": {"type": "tool", "name": "CronCreate"},
        }
        filtered = policy.filter("ollama", {}, body)

        self.assertEqual([{"name": "Read"}], filtered["tools"])
        self.assertNotIn("tool_choice", filtered)
        self.assertEqual(2, len(body["tools"]))
        self.assertEqual(2, log.call_count)

    def test_dynamic_plan_tool_is_hidden_only_for_non_anthropic_workflow(self):
        policy, _log = self.policy(workflow=True)
        body = {"tools": [{"name": "EnterPlanMode"}, {"name": "Read"}]}

        self.assertEqual([{"name": "Read"}], policy.filter("openrouter", {}, body)["tools"])
        self.assertIs(body, policy.filter("anthropic", {}, body))

    def test_matching_choice_is_removed_even_without_tools(self):
        policy, _log = self.policy(blocked={"CronCreate"})
        body = {"tool_choice": {"type": "tool", "name": "CronCreate"}}
        self.assertEqual({}, policy.filter("ollama", {}, body))


if __name__ == "__main__":
    unittest.main()
