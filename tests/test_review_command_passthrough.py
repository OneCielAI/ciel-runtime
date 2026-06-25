import unittest

import ciel_runtime


class ReviewCommandPassthroughTests(unittest.TestCase):
    def test_review_and_slash_command_tools_are_not_blocked(self):
        blocked = ciel_runtime.resolve_blocked_tools("ollama-cloud", {})

        self.assertNotIn("SlashCommand", blocked)
        self.assertNotIn("review", blocked)
        self.assertNotIn("Review", blocked)

    def test_filter_preserves_slash_command_tool_for_non_anthropic_provider(self):
        body = {
            "tools": [
                {
                    "name": "SlashCommand",
                    "description": "Run a custom slash command",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                },
                {
                    "name": "Read",
                    "description": "Read a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"file_path": {"type": "string"}},
                    },
                },
                {
                    "name": "EnterWorktree",
                    "description": "Internal Claude Code worktree tool",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ]
        }

        filtered = ciel_runtime.filter_blocked_tools("ollama-cloud", {}, body)
        names = [tool["name"] for tool in filtered["tools"]]

        self.assertIn("SlashCommand", names)
        self.assertIn("Read", names)
        self.assertNotIn("EnterWorktree", names)

    def test_filter_hides_server_side_web_search_for_non_anthropic_provider(self):
        body = {
            "tools": [
                {
                    "name": "WebSearch",
                    "description": "Search the web",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": "web_search",
                    "type": "web_search_20250305",
                    "max_uses": 8,
                },
                {
                    "name": "WebFetch",
                    "description": "Fetch a URL",
                    "input_schema": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
                {
                    "name": "web_fetch",
                    "type": "web_fetch_20250305",
                    "max_uses": 8,
                },
                {
                    "name": "mcp__duckduckgo__search",
                    "description": "Search with DuckDuckGo",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            ]
        }

        filtered = ciel_runtime.filter_blocked_tools("ollama", {}, body)
        names = [tool["name"] for tool in filtered["tools"]]

        self.assertNotIn("WebSearch", names)
        self.assertNotIn("web_search", names)
        self.assertNotIn("WebFetch", names)
        self.assertNotIn("web_fetch", names)
        self.assertIn("mcp__duckduckgo__search", names)

    def test_filter_keeps_server_side_web_search_for_anthropic_provider(self):
        body = {
            "tools": [
                {"name": "WebSearch", "input_schema": {"type": "object", "properties": {}}},
                {"name": "web_search", "type": "web_search_20250305", "max_uses": 8},
                {"name": "WebFetch", "input_schema": {"type": "object", "properties": {}}},
                {"name": "web_fetch", "type": "web_fetch_20250305", "max_uses": 8},
            ]
        }

        filtered = ciel_runtime.filter_blocked_tools("anthropic", {}, body)
        names = [tool["name"] for tool in filtered["tools"]]

        self.assertIn("WebSearch", names)
        self.assertIn("web_search", names)
        self.assertIn("WebFetch", names)
        self.assertIn("web_fetch", names)

    def test_slash_command_tool_schema_is_forwarded_to_ollama(self):
        tools = [
            {
                "name": "SlashCommand",
                "description": "Run a custom slash command",
                "input_schema": {
                    "type": "object",
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string"},
                        "arguments": {"type": "string"},
                    },
                },
            }
        ]

        converted = ciel_runtime.anthropic_tools_to_ollama(tools)

        self.assertEqual("SlashCommand", converted[0]["function"]["name"])
        self.assertEqual(["command"], converted[0]["function"]["parameters"]["required"])


if __name__ == "__main__":
    unittest.main()
