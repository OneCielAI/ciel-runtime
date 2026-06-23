import unittest

import claude_any


CC_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


class AdvisorSystemBlockOrderTests(unittest.TestCase):
    """Anthropic rejects OAuth requests whose first system block is not the
    Claude Code identity (HTTP 429 rate_limit_error, message "Error"), so the
    advisor request must keep the inbound session's first system block first.
    """

    def test_list_system_keeps_first_block_first_and_verbatim(self):
        system = [
            {"type": "text", "text": CC_IDENTITY, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Long Claude Code instructions."},
        ]
        blocks = claude_any.anthropic_system_with_advisor(system)
        self.assertEqual(CC_IDENTITY, blocks[0]["text"])
        self.assertEqual({"type": "ephemeral"}, blocks[0].get("cache_control"))
        self.assertEqual(claude_any.ADVISOR_REVIEW_PROMPT, blocks[1]["text"])
        self.assertIn("Original session system context", blocks[2]["text"])
        self.assertIn("Long Claude Code instructions.", blocks[2]["text"])

    def test_first_block_copy_does_not_mutate_inbound_body(self):
        system = [{"type": "text", "text": CC_IDENTITY}]
        blocks = claude_any.anthropic_system_with_advisor(system)
        blocks[0]["text"] = "mutated"
        self.assertEqual(CC_IDENTITY, system[0]["text"])

    def test_string_system_becomes_first_block(self):
        blocks = claude_any.anthropic_system_with_advisor(CC_IDENTITY)
        self.assertEqual(CC_IDENTITY, blocks[0]["text"])
        self.assertEqual(claude_any.ADVISOR_REVIEW_PROMPT, blocks[1]["text"])
        self.assertEqual(2, len(blocks))

    def test_no_system_keeps_advisor_prompt_first(self):
        blocks = claude_any.anthropic_system_with_advisor(None)
        self.assertEqual(claude_any.ADVISOR_REVIEW_PROMPT, blocks[0]["text"])

    def test_non_text_first_block_falls_back_to_context_summary(self):
        system = [{"type": "image", "source": {}}, {"type": "text", "text": "rules"}]
        blocks = claude_any.anthropic_system_with_advisor(system)
        self.assertEqual(claude_any.ADVISOR_REVIEW_PROMPT, blocks[0]["text"])
        self.assertIn("rules", blocks[1]["text"])

    def test_extra_system_texts_appended_last(self):
        system = [{"type": "text", "text": CC_IDENTITY}]
        blocks = claude_any.anthropic_system_with_advisor(system, ["runtime state"])
        self.assertEqual(CC_IDENTITY, blocks[0]["text"])
        self.assertEqual(claude_any.ADVISOR_REVIEW_PROMPT, blocks[1]["text"])
        self.assertIn("runtime state", blocks[-1]["text"])

    def test_advisor_request_system_starts_with_session_identity(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "advisor_model": "claude-sonnet-4-6",
            "route_through_router": True,
        }
        body = {
            "model": "claude-haiku-4-5",
            "system": [
                {"type": "text", "text": CC_IDENTITY},
                {"type": "text", "text": "Long Claude Code instructions."},
            ],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "CLAUDE_ANY_ADVISOR_CALL\nFocus: plan"}]},
            ],
        }
        req = claude_any.advisor_request("anthropic", "claude-sonnet-4-6", body, pcfg)
        self.assertEqual(CC_IDENTITY, req["system"][0]["text"])
        self.assertEqual(claude_any.ADVISOR_REVIEW_PROMPT, req["system"][1]["text"])


class AdvisorEndpointQueryTests(unittest.TestCase):
    def test_anthropic_endpoint_without_force_query_has_no_query(self):
        pcfg = {"base_url": "https://api.anthropic.com"}
        self.assertEqual(
            "https://api.anthropic.com/v1/messages",
            claude_any.advisor_endpoint("anthropic", pcfg),
        )

    def test_anthropic_endpoint_honors_force_query_string(self):
        pcfg = {"base_url": "https://api.anthropic.com", "force_query_string": "beta=true"}
        self.assertEqual(
            "https://api.anthropic.com/v1/messages?beta=true",
            claude_any.advisor_endpoint("anthropic", pcfg),
        )


if __name__ == "__main__":
    unittest.main()
