import unittest

from ciel_runtime_support.protocols.anthropic_thinking_policy import message_content_blocks
from ciel_runtime_support.protocols.conversation_turn_policy import ConversationTurnPolicy, ConversationTurnPorts

# Claude Code 2.1.310 reminder texts (attachment builders in the shipped binary).
ACTIVE = ("Plan mode is active. The user indicated that they do not want you to execute yet -- you MUST NOT make "
          "any edits (with the exception of the plan file mentioned below), run any non-readonly tools")
SPARSE = ("Plan mode still active (see full instructions earlier in conversation). Read-only except plan file "
          "(C:/plans/p.md). Follow 5-phase workflow.")
EXITED = ("## Exited Plan Mode\n\nYou have exited plan mode. You can now make edits, run tools, and take actions. "
          "The plan file is located at C:/plans/p.md if you need to reference it.")
REENTRY = ("## Re-entering Plan Mode\n\nYou are returning to plan mode after having previously exited it. "
           "A plan file exists at C:/plans/p.md from your previous planning session.")


def policy():
    return ConversationTurnPolicy(ConversationTurnPorts(
        content_blocks=message_content_blocks,
        lookup_tool_schema=lambda _name: None,
        tool_schema=lambda _body, _name: None,
        log=lambda _level, _text: None,
        has_tool=lambda _body, _name: True,
        ultracode_preferred=lambda _body: False,
        content_to_text=lambda content: str(content),
    ))


def system(text):
    return {"role": "system", "content": [{"type": "text", "text": text}]}


def reminder(text):
    return {"role": "user", "content": [{"type": "text", "text": f"<system-reminder>\n{text}\n</system-reminder>"}]}


def body(*messages):
    return {"messages": [{"role": "user", "content": "This session is being continued from a previous conversation."},
                         *messages]}


class PlanModeReminderTextTests(unittest.TestCase):
    def test_claude_code_system_message_reminder_keeps_plan_mode_after_compaction(self):
        # Shape captured from Claude Code 2.1.310: other context first, then the reminder, in a
        # system-role message; no EnterPlanMode call survives the compaction.
        request = body(system("# Environment\n - Platform: win32\n- init: Initialize a new CLAUDE.md file\n\n" + ACTIVE),
                       {"role": "assistant", "content": [{"type": "text", "text": "Plan written."}]},
                       {"role": "user", "content": "계속"})
        self.assertTrue(policy().plan_mode_active(request))
        self.assertEqual("ExitPlanMode", policy().plan_mode_tool_name_for_emit(request, "ExitPlanMode", {"plan": "p"})[0])

    def test_sparse_reminder_and_older_user_reminder_shape(self):
        self.assertTrue(policy().plan_mode_active(body(system(SPARSE))))
        self.assertTrue(policy().plan_mode_active(body(reminder(ACTIVE))))
        self.assertTrue(policy().plan_mode_active(body(reminder(SPARSE))))

    def test_exit_and_reentry_follow_message_order(self):
        self.assertFalse(policy().plan_mode_active(body(system(ACTIVE), system(EXITED))))
        self.assertTrue(policy().plan_mode_active(body(system(ACTIVE), system(EXITED), system(REENTRY))))
        self.assertFalse(policy().plan_mode_active(body(reminder(ACTIVE), reminder(EXITED))))
        # Both texts inside one message: the later one wins.
        self.assertFalse(policy().plan_mode_active(body(system(ACTIVE + "\n\n" + EXITED))))
        self.assertTrue(policy().plan_mode_active(body(system(EXITED + "\n\n" + REENTRY))))

    def test_exit_plan_mode_result_still_ends_plan_mode(self):
        request = body(system(ACTIVE),
                       {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "ExitPlanMode", "input": {}}]},
                       {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "approved"}]})
        self.assertFalse(policy().plan_mode_active(request))
        self.assertIsNone(policy().plan_mode_tool_name_for_emit(request, "ExitPlanMode", {"plan": "p"})[0])

    def test_text_that_is_not_a_claude_code_reminder_does_not_enter_plan_mode(self):
        cases = [
            # A file read through a tool.
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r", "content": ACTIVE}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r",
                                          "content": [{"type": "text", "text": ACTIVE}]}]},
            # The user typed or pasted it.
            {"role": "user", "content": ACTIVE},
            {"role": "user", "content": [{"type": "text", "text": ACTIVE}]},
            # A file restored after compaction keeps its line-number prefixes.
            reminder("Called the Read tool with the following input: {}\nResult of calling the Read tool:\n12\t" + ACTIVE),
            # Mentioned inside a sentence, not as the reminder itself.
            system("The journal says that the line 'Plan mode is active. The user indicated that they do not want "
                   "you to execute yet' was missing."),
            # The assistant quoting it.
            {"role": "assistant", "content": [{"type": "text", "text": ACTIVE}]},
        ]
        for message in cases:
            with self.subTest(message=str(message)[:80]):
                request = body(message)
                self.assertFalse(policy().plan_mode_active(request))
                self.assertIsNone(policy().plan_mode_tool_name_for_emit(request, "ExitPlanMode", {"plan": "p"})[0])


if __name__ == "__main__":
    unittest.main()
