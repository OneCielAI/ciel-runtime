import unittest

from ciel_runtime_support.protocols.conversation_policy import (
    ConversationPolicyServices,
    claude_code_state_messages,
    collect_tool_result_context,
    is_attachment_only_message,
    should_skip_upstream_message,
)


def content_blocks(message):
    content = message.get("content")
    return content if isinstance(content, list) else ([content] if content is not None else [])


def content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or item.get("content") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


def services(plan_mode=False):
    return ConversationPolicyServices(
        content_blocks=content_blocks,
        content_to_text=content_to_text,
        plan_mode_active=lambda _body: plan_mode,
        persisted_output=lambda text: "<persisted-output>" in text,
        transcript_event=lambda message: message.get("type") == "transcript",
        guard_feedback=lambda text: text.startswith("guard:"),
    )


class ConversationPolicyTests(unittest.TestCase):
    def test_attachment_without_content_is_not_forwarded(self):
        self.assertTrue(is_attachment_only_message({"attachment": {"type": "plan_mode"}}, services()))

    def test_tool_blocks_make_attachment_relevant(self):
        message = {"attachment": {"type": "plan_mode"}, "content": [{"type": "tool_use", "id": "1"}]}
        self.assertFalse(is_attachment_only_message(message, services()))

    def test_plan_state_reports_successful_plan_write(self):
        body = {
            "messages": [
                {"attachment": {"type": "plan_mode", "planFilePath": "plan.md", "planExists": True}},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "w", "name": "Write", "input": {"file_path": "plan.md"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w", "content": "ok"}]},
            ]
        }
        state = claude_code_state_messages(body, services(plan_mode=True))[0]["content"]
        self.assertIn("written or updated in this conversation: yes", state)

    def test_collect_context_keeps_success_and_current_unchanged_id(self):
        body = {"messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r1", "content": "contents"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "r2", "name": "Read", "input": {"file_path": "a"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r2", "content": "file unchanged since your last read"}]},
        ]}
        successful, noops = collect_tool_result_context(body, services())
        self.assertEqual({"r2"}, noops)
        self.assertEqual("contents", next(iter(successful.values())))

    def test_skip_policy_filters_internal_messages(self):
        self.assertTrue(should_skip_upstream_message({"type": "transcript"}, services()))
        self.assertTrue(should_skip_upstream_message({"role": "user", "content": "guard: denied"}, services()))


if __name__ == "__main__":
    unittest.main()
