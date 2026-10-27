import unittest
from types import SimpleNamespace

from ciel_runtime_support.provider_tool_policy import (
    ProviderToolPolicy, configure_blocked_tools, blocked_tools_option_value,
)
from ciel_runtime_support.runtime_constants import DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC


class ProviderToolPolicyTests(unittest.TestCase):
    def policy(self, *, blocked=True, repair=False, normalize=False):
        capabilities = SimpleNamespace(
            blocks_default_tools=blocked,
            repairs_anthropic_tool_input=repair,
        )
        adapter = SimpleNamespace(
            capabilities=lambda _config: capabilities,
            normalizes_anthropic_tool_use=lambda _config: normalize,
            supports_tool_choice=lambda _config, model: model != "off",
            normalize_tool_choice=lambda _config, _model, choice: choice,
            supports_server_web_tools=lambda _config: False,
        )
        return ProviderToolPolicy(
            adapter_for=lambda _provider, _config: adapter,
            contract_for=lambda _provider, config: config,
            current_model=lambda _provider, config: str(
                config.get("current_model") or "model"
            ),
            strip_context_suffix=lambda value: value,
            resolve_emitted_name=lambda name, _body: name,
            default_blocked_tools=frozenset({"Task"}),
            repair_tools=frozenset({"AskUserQuestion"}),
            log=lambda _level, _message: None,
        )

    def test_default_and_explicit_blocked_tools(self):
        policy = self.policy()

        self.assertEqual({"Task"}, policy.blocked_tools("provider", {}))
        self.assertEqual(
            {"Read"},
            policy.blocked_tools(
                "provider",
                {"blocked_tools": ["Read"]},
            ),
        )
        self.assertEqual(
            set(),
            policy.blocked_tools("provider", {"blocked_tools": False}),
        )

    def test_only_verified_client_sendmessage_leaves_default_blocklist(self):
        blocked = set(DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC)
        self.assertNotIn("SendMessage", blocked)
        self.assertTrue({"SendMessageTool", "TeamCreate", "TeamDelete", "TeammateTool",
                         "WebSearch", "WebFetch", "ScheduleWakeup", "PushNotification"} <= blocked)

    def test_blocklist_editor_validates_replacement_and_reset(self):
        config = {}
        for raw, expected in [('false', False), ('[]', []),
                              ('[" SendMessage ", "WebSearch", "SendMessage"]', ["SendMessage", "WebSearch"])]:
            configure_blocked_tools(config, raw)
            self.assertEqual(expected, config["blocked_tools"])
            self.assertNotEqual("default", blocked_tools_option_value(config))
        for raw in ('true', 'SendMessage', '[1]', '[""]', '{"SendMessage": true}'):
            before = dict(config)
            with self.assertRaises(ValueError):
                configure_blocked_tools(config, raw)
            self.assertEqual(before, config)
        configure_blocked_tools(config, "default")
        self.assertNotIn("blocked_tools", config)
        self.assertEqual("default", blocked_tools_option_value(config))
        self.assertEqual({"SendMessage"}, self.policy().blocked_tools("provider", {"blocked_tools": ["SendMessage"]}))

    def test_runtime_filter_and_forced_choice_preserve_sendmessage_unless_explicitly_blocked(self):
        import ciel_runtime
        body = {"tools": [{"name": "SendMessage", "input_schema": {"type": "object"}},
                          {"name": "SendMessageTool", "input_schema": {"type": "object"}},
                          {"name": "TeamCreate", "input_schema": {"type": "object"}}],
                "tool_choice": {"type": "tool", "name": "SendMessage"}}
        for provider in ("cielairouter", "vllm", "ollama-cloud", "opencode"):
            filtered = ciel_runtime.filter_blocked_tools(provider, {}, body)
            self.assertEqual(["SendMessage"], [t["name"] for t in filtered["tools"]])
            self.assertEqual(body["tool_choice"], filtered["tool_choice"])
            explicit = ciel_runtime.filter_blocked_tools(provider, {"blocked_tools": ["SendMessage"]}, body)
            self.assertNotIn("SendMessage", [t["name"] for t in explicit["tools"]])
            self.assertNotIn("tool_choice", explicit)

    def test_menu_setter_persists_scoped_override_and_rejects_invalid_input(self):
        import copy
        from unittest import mock
        import ciel_runtime
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save):
            for provider in ("cielairouter", "anthropic", "ollama-cloud", "vllm"):
                with self.subTest(provider=provider):
                    before = copy.deepcopy(config["providers"])
                    lines = ciel_runtime.set_llm_option_config(provider, "blocked_tools", '["SendMessage"]')
                    self.assertIn("subsequent routed requests", " ".join(lines))
                    self.assertEqual(["SendMessage"], config["providers"][provider]["blocked_tools"])
                    self.assertIn("SendMessage", ciel_runtime.resolve_blocked_tools(provider, config["providers"][provider]))
                    for other, old in before.items():
                        if other != provider:
                            self.assertEqual(old, config["providers"][other])
                    count = save.call_count
                    ciel_runtime.set_llm_option_config(provider, "blocked_tools", 'true')
                    self.assertEqual(count, save.call_count)
                    ciel_runtime.set_llm_option_config(provider, "blocked_tools", 'default')
                    self.assertNotIn("blocked_tools", config["providers"][provider])

    def test_provider_options_cli_mutation_uses_same_validated_override(self):
        import ciel_runtime
        for provider in ("cielairouter", "anthropic", "ollama-cloud", "vllm"):
            config = {}
            ciel_runtime.apply_provider_option(provider, config, 'blocked_tools=["SendMessage"]')
            self.assertEqual(["SendMessage"], config["blocked_tools"])
            ciel_runtime.apply_provider_option(provider, config, 'unset:blocked_tools')
            self.assertEqual({}, config)
            with self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option(provider, config, 'blocked_tools=true')

    def test_capabilities_drive_normalization_and_repair(self):
        policy = self.policy(repair=True, normalize=True)

        self.assertTrue(
            policy.normalize_anthropic_stream_tool_use("provider", {})
        )
        self.assertTrue(
            policy.should_repair_passthrough_input(
                "provider",
                {},
                "AskUserQuestion",
                None,
            )
        )

    def test_tool_choice_status_uses_adapter_strategy(self):
        policy = self.policy()

        self.assertEqual(
            "auto (on)",
            policy.tool_choice_status(
                "provider",
                {"current_model": "model"},
            ),
        )

    def test_tool_choice_normalization_is_adapter_owned(self):
        policy = self.policy()
        body = {
            "model": "model",
            "tool_choice": {"type": "tool", "name": "Read"},
        }

        self.assertIs(body, policy.normalize_tool_choice("provider", {}, body))
        self.assertEqual(
            "off",
            policy.tool_choice_status(
                "provider",
                {"supports_tool_choice": False},
            ),
        )


if __name__ == "__main__":
    unittest.main()
