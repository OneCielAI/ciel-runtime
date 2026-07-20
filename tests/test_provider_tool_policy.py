import unittest
from types import SimpleNamespace

from ciel_runtime_support.provider_tool_policy import ProviderToolPolicy


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
