import unittest
from unittest.mock import patch

import ciel_runtime


class AdvisorFeedbackTests(unittest.TestCase):
    def test_internal_advisor_feedback_is_sent_back_to_main_model_body(self):
        body = {"messages": [{"role": "user", "content": "build the feature"}]}
        assistant_message = {
            "role": "assistant",
            "content": [{"type": "text", "text": "I will exit plan mode."}],
        }

        follow_body = ciel_runtime.body_with_internal_advisor_feedback(
            body,
            assistant_message,
            "Check the migration plan before approval.",
            "before ExitPlanMode plan approval",
        )

        feedback_text = ciel_runtime.anthropic_content_to_text(follow_body["messages"][-1]["content"])
        self.assertIn(ciel_runtime.ADVISOR_FEEDBACK_MARKER, feedback_text)
        self.assertIn("Check the migration plan before approval.", feedback_text)
        self.assertIn("Apply this advisor feedback now.", feedback_text)

    def test_refined_message_includes_visible_advisor_summary(self):
        body = {"messages": [{"role": "user", "content": "build the feature"}]}
        assistant_message = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_plan",
                    "name": "ExitPlanMode",
                    "input": {"plan": "ship it"},
                }
            ],
            "stop_reason": "tool_use",
        }
        refined = {
            "role": "assistant",
            "content": [{"type": "text", "text": "Updated plan is ready."}],
            "stop_reason": "end_turn",
        }

        with (
            patch("ciel_runtime.advisor_model_enabled", return_value="deepseek-v4-pro"),
            patch("ciel_runtime.advisor_provider_supported", return_value=True),
            patch("ciel_runtime.call_advisor_text", return_value="The plan needs a validation step.") as advisor_call,
            patch("ciel_runtime.call_provider_chat_once", return_value=refined) as main_call,
        ):
            out = ciel_runtime.refine_message_with_advisor(
                "ollama-cloud",
                {"advisor_model": "deepseek-v4-pro"},
                body,
                assistant_message,
                "main-model",
            )

        advisor_focus = advisor_call.call_args.kwargs["focus"]
        self.assertIn("ExitPlanMode plan before user approval", advisor_focus)
        self.assertIn("ship it", advisor_focus)
        self.assertTrue(main_call.called)
        sent_body = main_call.call_args.args[2]
        sent_text = ciel_runtime.anthropic_content_to_text(sent_body["messages"][-1]["content"])
        self.assertIn("The plan needs a validation step.", sent_text)
        assistant_summary = ciel_runtime.anthropic_content_to_text(sent_body["messages"][-2]["content"])
        self.assertIn("Pending Claude Code tool call: ExitPlanMode", assistant_summary)
        self.assertIn("ship it", assistant_summary)
        visible = ciel_runtime.anthropic_content_to_text(out["content"])
        self.assertIn("Advisor review (before ExitPlanMode plan approval):", visible)
        self.assertIn("The plan needs a validation step.", visible)
        self.assertIn("Updated plan is ready.", visible)

    def test_advisor_prompt_requires_actual_verdict(self):
        self.assertIn("Review now", ciel_runtime.ADVISOR_REVIEW_PROMPT)
        self.assertIn("Verdict:", ciel_runtime.ADVISOR_REVIEW_PROMPT)
        self.assertIn("Required next action:", ciel_runtime.ADVISOR_REVIEW_PROMPT)

    def test_advisor_visible_summary_is_bounded(self):
        text = ciel_runtime.advisor_visible_summary("x" * 1000, "trigger", limit=80)

        self.assertLessEqual(len(text), 120)
        self.assertIn("Advisor review (trigger):", text)
        self.assertIn("…", text)

    def test_autonomous_claude_code_advisor_server_tool_is_stripped(self):
        body = {
            "messages": [{"role": "user", "content": "continue"}],
            "tools": [
                {"name": "Bash", "description": "run", "input_schema": {"type": "object"}},
                {"type": "advisor_20260301", "name": "advisor", "model": "ciel-runtime-anthropic-claude-opus-4-8"},
            ],
        }

        out = ciel_runtime.strip_autonomous_advisor_server_tools("ollama", body)

        self.assertIsNot(out, body)
        self.assertEqual([tool["name"] for tool in out["tools"]], ["Bash"])
        self.assertEqual(2, len(body["tools"]))

    def test_anthropic_provider_keeps_native_advisor_server_tool(self):
        # Claude native / Anthropic routed sessions follow Claude Code's
        # built-in advisor flow; the server tool must pass through untouched.
        body = {
            "messages": [{"role": "user", "content": "continue"}],
            "tools": [
                {"name": "Bash", "description": "run", "input_schema": {"type": "object"}},
                {"type": "advisor_20260301", "name": "advisor", "model": "claude-sonnet-4-6"},
            ],
        }

        out = ciel_runtime.strip_autonomous_advisor_server_tools("anthropic", body)

        self.assertIs(out, body)
        self.assertEqual(["Bash", "advisor"], [tool["name"] for tool in out["tools"]])

    def test_plain_ciel_runtime_advisor_tool_schema_is_not_stripped(self):
        body = {
            "messages": [{"role": "user", "content": "review the plan"}],
            "tools": [
                ciel_runtime.advisor_tool_schema(),
                {"name": "Read", "input_schema": {"type": "object"}},
            ],
        }

        out = ciel_runtime.strip_autonomous_advisor_server_tools("ollama", body)

        self.assertIs(out, body)
        self.assertEqual(["advisor", "Read"], [tool["name"] for tool in out["tools"]])

    def test_explicit_advisor_request_keeps_server_tool_for_local_short_circuit(self):
        body = {
            "messages": [{"role": "user", "content": "CIEL_RUNTIME_ADVISOR_CALL\nFocus: plan"}],
            "tools": [
                {"type": "advisor_20260301", "name": "advisor", "model": "ciel-runtime-anthropic-claude-opus-4-8"},
                {"name": "Bash", "input_schema": {"type": "object"}},
            ],
        }

        out = ciel_runtime.strip_autonomous_advisor_server_tools("ollama", body)

        self.assertIs(out, body)
        self.assertEqual(["advisor", "Bash"], [tool["name"] for tool in out["tools"]])


if __name__ == "__main__":
    unittest.main()
