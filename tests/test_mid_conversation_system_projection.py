import ciel_runtime
import unittest


MID_TURN = (
    "The user sent a new message while you were working:\n"
    "새로 생성된 티커별 파라미터에 대해서 support document에 업데이트되었어?"
)


def ollama_messages(messages, system=None):
    body = {"messages": messages}
    if system is not None:
        body["system"] = system
    return ciel_runtime.anthropic_messages_to_ollama(body)


def closing(messages):
    return ollama_messages(messages)[-1]


class ClosingSystemMessageProjectionTests(unittest.TestCase):
    """A conversation that ends on a system message carries live user input.

    Anthropic's mid-conversation system message is how Claude Code hands over
    what the user typed while a turn was still running. The chat wire has no
    equivalent envelope, so the projection has to pick one the wire honours.
    """

    def test_closing_system_message_is_projected_as_user_input(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "sleep 40 실행해줘"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "sleep 40"}}
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "waited"}]},
            {"role": "system", "content": [{"type": "text", "text": MID_TURN}]},
        ]

        last = closing(messages)

        self.assertEqual("user", last.get("role"))
        self.assertIn("support document", str(last.get("content")))

    def test_every_message_of_a_closing_system_run_is_projected(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "작업 시작"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "진행합니다."}]},
            {"role": "system", "content": [{"type": "text", "text": "first queued message"}]},
            {"role": "system", "content": [{"type": "text", "text": "second queued message"}]},
        ]

        out = ollama_messages(messages)

        trailing = out[-2:]
        self.assertEqual(["user", "user"], [message.get("role") for message in trailing])
        self.assertIn("first queued message", str(trailing[0].get("content")))
        self.assertIn("second queued message", str(trailing[1].get("content")))

    def test_system_message_followed_by_conversation_stays_system(self):
        """The client also states static context this way, e.g. the agent catalog.

        Anything the model has already had a turn to act on is background, not
        an instruction waiting for an answer, so its envelope is left alone.
        """

        catalog = "Available agent types for the Agent tool:\n- Explore"
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "작업 시작"}]},
            {"role": "system", "content": [{"type": "text", "text": catalog}]},
            {"role": "assistant", "content": [{"type": "text", "text": "진행합니다."}]},
            {"role": "user", "content": [{"type": "text", "text": "계속"}]},
        ]

        out = ollama_messages(messages)

        catalog_messages = [
            message for message in out if catalog.splitlines()[0] in str(message.get("content"))
        ]
        self.assertEqual(1, len(catalog_messages))
        self.assertEqual("system", catalog_messages[0].get("role"))

    def test_top_level_system_prompt_is_untouched(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "작업 시작"}]},
            {"role": "system", "content": [{"type": "text", "text": MID_TURN}]},
        ]

        out = ollama_messages(messages, system=[{"type": "text", "text": "You are Claude Code."}])

        self.assertEqual("system", out[0].get("role"))
        self.assertIn("You are Claude Code.", str(out[0].get("content")))
        self.assertEqual("user", out[-1].get("role"))

    def test_conversation_without_a_closing_system_message_is_unchanged(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "작업 시작"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "진행합니다."}]},
        ]

        roles = [message.get("role") for message in ollama_messages(messages)]

        self.assertEqual("assistant", roles[-1])
        self.assertNotIn("system", roles[2:])


if __name__ == "__main__":
    unittest.main()
