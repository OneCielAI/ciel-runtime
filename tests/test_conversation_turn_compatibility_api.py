import unittest

from ciel_runtime_support.protocols.conversation_turn_policy import (
    ConversationTurnCompatibilityApi,
)


class FakeConversationTurnPolicy:
    def __init__(self, marker):
        self.marker = marker

    def plan_mode_active(self, body):
        return body.get("mode") == self.marker

    def latest_user_text(self, body):
        return f"{self.marker}:{body['text']}"

    def should_auto_enter_plan_mode(self, body, response_text, tool_calls):
        return (body["mode"], response_text, tool_calls) == (
            self.marker,
            "plan",
            [],
        )

    def empty_end_turn_notice(self):
        return self.marker


class ConversationTurnCompatibilityApiTests(unittest.TestCase):
    def test_adapter_preserves_explicit_method_signatures(self):
        api = ConversationTurnCompatibilityApi(
            lambda: FakeConversationTurnPolicy("active")
        )
        self.assertTrue(api.plan_mode_active({"mode": "active"}))
        self.assertEqual("active:hello", api.latest_user_text({"text": "hello"}))
        self.assertTrue(
            api.should_auto_enter_plan_mode(
                {"mode": "active"}, "plan", []
            )
        )
        self.assertEqual("active", api.empty_end_turn_notice())

    def test_policy_is_resolved_per_call_for_runtime_composition(self):
        marker = ["first"]
        api = ConversationTurnCompatibilityApi(
            lambda: FakeConversationTurnPolicy(marker[0])
        )
        self.assertEqual("first", api.empty_end_turn_notice())
        marker[0] = "second"
        self.assertEqual("second", api.empty_end_turn_notice())


if __name__ == "__main__":
    unittest.main()
