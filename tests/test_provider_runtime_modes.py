import unittest

from ciel_runtime_support.provider_runtime_modes import (
    ProviderNativeCompatibilityPolicy,
    RuntimeModePolicy,
)


class ProviderRuntimeModePolicyTests(unittest.TestCase):
    def test_runtime_routing_uses_declarative_provider_mapping(self):
        policy = RuntimeModePolicy(
            parse_bool=lambda value, **_kwargs: bool(value),
            runtime_providers={"anthropic": "anthropic"},
        )
        self.assertTrue(policy.native_anthropic("anthropic"))
        self.assertTrue(
            policy.anthropic_routed(
                "anthropic", {"route_through_router": True}
            )
        )
        self.assertFalse(
            policy.direct_anthropic(
                "anthropic", {"route_through_router": True}
            )
        )

    def test_unrelated_provider_is_not_a_native_runtime(self):
        policy = RuntimeModePolicy(
            parse_bool=lambda value, **_kwargs: bool(value),
            runtime_providers={"codex": "codex"},
        )
        self.assertFalse(policy.native_codex("ollama"))
        self.assertFalse(
            policy.codex_routed(
                "ollama", {"route_through_router": True}
            )
        )

    def test_native_compatibility_combines_group_and_adapter_policy(self):
        policy = ProviderNativeCompatibilityPolicy(
            native_enabled=lambda _provider, config: bool(config.get("native")),
            compatibility_groups={"opencode": frozenset({"zen", "go"})},
        )
        self.assertTrue(policy.opencode("zen", {"native": True}))
        self.assertFalse(policy.opencode("other", {"native": True}))
        self.assertFalse(policy.opencode("go", {"native": False}))


if __name__ == "__main__":
    unittest.main()
