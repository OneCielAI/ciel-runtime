import unittest
from unittest import mock

from ciel_runtime_support.synthetic_tool_policy import (
    ForcedPlanModeController,
    ForcedPlanModePorts,
    SyntheticTasklistPolicy,
    SyntheticTasklistPorts,
)


class SyntheticToolPolicyTests(unittest.TestCase):
    def test_tasklist_policy_appends_local_tool_use_without_mutating_message(self):
        log = mock.Mock()
        policy = SyntheticTasklistPolicy(
            SyntheticTasklistPorts(
                lambda _provider: True,
                lambda content: "question" if content else "",
                lambda *_args: True,
                lambda: 1234,
                log,
            )
        )
        message = {"content": [{"type": "text", "text": "choose"}], "stop_reason": "end_turn"}
        projected = policy.append(message, "model", {}, "choice", "ollama")

        self.assertEqual("end_turn", message["stop_reason"])
        self.assertEqual("tool_use", projected["stop_reason"])
        self.assertEqual("TaskList", projected["content"][-1]["name"])
        self.assertIn("1234", projected["content"][-1]["id"])
        log.assert_called_once()

    def test_tasklist_policy_preserves_identity_when_provider_is_disabled(self):
        policy = SyntheticTasklistPolicy(
            SyntheticTasklistPorts(lambda _provider: False, str, lambda *_args: True, lambda: 1, mock.Mock())
        )
        message = {"content": []}
        self.assertIs(message, policy.append(message, "model", {}, "choice", "anthropic"))

    def plan_controller(self, *, defer=False, active=False):
        write_json = mock.Mock()
        controller = ForcedPlanModeController(
            ForcedPlanModePorts(
                lambda _body: "EnterPlanMode",
                lambda *_args: defer,
                lambda _body: {"EnterPlanMode"},
                lambda _body: active,
                lambda model, name, payload: {"model": model, "name": name, "input": payload},
                write_json,
                mock.Mock(),
            )
        )
        return controller, write_json

    def test_plan_mode_controller_writes_synthetic_tool_response(self):
        controller, write_json = self.plan_controller()
        handler = object()
        self.assertTrue(controller.handle(handler, "ollama", {}, {"model": "m"}))
        write_json.assert_called_once_with(
            handler,
            {"model": "m", "name": "EnterPlanMode", "input": {}},
        )

    def test_plan_mode_controller_defers_thinking_and_native_anthropic(self):
        controller, write_json = self.plan_controller(defer=True)
        self.assertFalse(controller.handle(object(), "ollama", {}, {}))
        self.assertFalse(controller.handle(object(), "anthropic", {}, {}))
        write_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
