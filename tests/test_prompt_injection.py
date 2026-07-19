import unittest

from ciel_runtime_support.prompt_injection import (
    PromptInjector,
    append_anthropic_system_texts,
    normalize_anthropic_system_role_messages,
)


class PromptInjectorTests(unittest.TestCase):
    def setUp(self):
        self.injector = PromptInjector()

    def test_anthropic_preserves_cached_identity_and_input(self):
        identity = {"type": "text", "text": "identity", "cache_control": {"type": "ephemeral"}}
        body = {"system": [identity], "messages": []}
        out = self.injector.inject(body, "anthropic_messages", ["runtime context"])
        self.assertEqual(identity, out["system"][0])
        self.assertEqual({"type": "text", "text": "runtime context"}, out["system"][1])
        out["system"][0]["text"] = "changed"
        self.assertEqual("identity", body["system"][0]["text"])

    def test_chat_inserts_after_existing_privileged_messages(self):
        body = {"messages": [{"role": "system", "content": "base"}, {"role": "user", "content": "hello"}]}
        out = self.injector.inject(body, "openai_chat", ["injected"])
        self.assertEqual(["system", "system", "user"], [item["role"] for item in out["messages"]])
        self.assertEqual("injected", out["messages"][1]["content"])

    def test_ollama_uses_chat_strategy(self):
        out = self.injector.inject({"messages": []}, "ollama_chat", ["context"])
        self.assertEqual([{"role": "system", "content": "context"}], out["messages"])

    def test_responses_appends_instructions(self):
        out = self.injector.inject({"instructions": "base", "input": "hello"}, "openai_responses", ["one", "two"])
        self.assertEqual("base\n\none\n\ntwo", out["instructions"])

    def test_google_preserves_key_style_and_parts(self):
        body = {"system_instruction": {"parts": [{"text": "base"}]}}
        out = self.injector.inject(body, "google_generative", ["context"])
        self.assertEqual([{"text": "base"}, {"text": "context"}], out["system_instruction"]["parts"])
        self.assertNotIn("systemInstruction", out)

    def test_empty_injection_returns_shallow_copy_without_format_changes(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        out = self.injector.inject(body, "openai_chat", ["", "  "])
        self.assertEqual(body, out)
        self.assertIsNot(body, out)


class AnthropicCompatibilityTests(unittest.TestCase):
    def test_append_string_system(self):
        self.assertEqual(
            [{"type": "text", "text": "base"}, {"type": "text", "text": "extra"}],
            append_anthropic_system_texts("base", ["extra"]),
        )

    def test_normalize_system_role_messages(self):
        body = {
            "system": [{"type": "text", "text": "base"}],
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "state"}]},
                {"role": "user", "content": "hello"},
            ],
        }
        out = normalize_anthropic_system_role_messages(
            body,
            lambda content: " ".join(str(item.get("text") or "") for item in content) if isinstance(content, list) else str(content),
        )
        self.assertEqual([{"role": "user", "content": "hello"}], out["messages"])
        self.assertEqual("state", out["system"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
