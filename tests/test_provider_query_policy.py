import unittest

from ciel_runtime_support.provider_query_policy import ProviderQueryPolicy


class ProviderQueryPolicyTests(unittest.TestCase):
    def policy(self) -> ProviderQueryPolicy:
        return ProviderQueryPolicy(
            normalize_provider=lambda value: value.strip().lower(),
            propagates_inbound_beta=lambda provider, _config: (
                provider == "anthropic"
            ),
        )

    def test_forced_query_wins_and_strips_question_mark(self):
        self.assertEqual(
            "trace=1",
            self.policy().upstream_query(
                {"force_query_string": " ?trace=1 "},
                "/v1/messages?beta=true",
                "ollama",
            ),
        )

    def test_beta_is_only_propagated_for_capable_provider(self):
        policy = self.policy()

        self.assertEqual(
            "beta=true",
            policy.upstream_query(
                {},
                "/v1/messages?beta=1",
                "anthropic",
            ),
        )
        self.assertEqual(
            "",
            policy.upstream_query(
                {},
                "/v1/messages?beta=true",
                "ollama",
            ),
        )

    def test_status_uses_capability(self):
        self.assertEqual(
            "auto (beta=true when routed)",
            self.policy().status("anthropic", {}),
        )
        self.assertEqual("empty", self.policy().status("ollama", {}))


if __name__ == "__main__":
    unittest.main()
