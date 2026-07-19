import unittest

from ciel_runtime_support.runtime_compatibility import (
    DEFAULT_RUNTIME_COMPATIBILITY,
    RuntimeCompatibilityPolicy,
)


class RuntimeCompatibilityPolicyTests(unittest.TestCase):
    def test_native_provider_is_restricted_to_its_runtime(self):
        self.assertTrue(DEFAULT_RUNTIME_COMPATIBILITY.supports("claude", "anthropic"))
        self.assertFalse(DEFAULT_RUNTIME_COMPATIBILITY.supports("codex", "anthropic"))
        self.assertTrue(DEFAULT_RUNTIME_COMPATIBILITY.supports("codex", "codex"))
        self.assertTrue(DEFAULT_RUNTIME_COMPATIBILITY.supports("agy", "agy"))

    def test_regular_upstream_provider_supports_routed_runtimes(self):
        self.assertTrue(DEFAULT_RUNTIME_COMPATIBILITY.supports("claude", "vllm"))
        self.assertTrue(DEFAULT_RUNTIME_COMPATIBILITY.supports("codex", "vllm"))
        self.assertFalse(DEFAULT_RUNTIME_COMPATIBILITY.supports("agy", "vllm"))

    def test_policy_is_configurable_without_provider_adapter_changes(self):
        policy = RuntimeCompatibilityPolicy(
            native_runtime_by_provider={"native-x": "runtime-x"},
            routed_runtimes=frozenset({"runtime-y"}),
        )
        self.assertTrue(policy.supports("runtime-x", "native-x"))
        self.assertTrue(policy.supports("runtime-y", "provider-y"))
        self.assertFalse(policy.supports("runtime-y", "native-x"))


if __name__ == "__main__":
    unittest.main()
