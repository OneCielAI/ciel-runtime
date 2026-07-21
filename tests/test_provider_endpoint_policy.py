import unittest

from ciel_runtime_support.provider_endpoint_policy import (
    ProviderEndpointPolicy,
    ProviderEndpointPorts,
    ProviderEndpointPresentation,
    build_default_provider_endpoint_policy,
)


class ProviderEndpointPolicyTests(unittest.TestCase):
    def policy(self, protocol="anthropic_messages"):
        return ProviderEndpointPolicy(
            ports=ProviderEndpointPorts(
                normalize_model_id=lambda _provider, model: model.removesuffix(":free"),
                strip_context_suffix=lambda model: model.removesuffix("[1m]"),
                alias_for=lambda _provider, model: f"alias:{model}",
                select_protocol=lambda _provider, _config, _model: protocol,
            ),
            presentation=ProviderEndpointPresentation(
                aliases={
                    "messages": "anthropic-messages",
                    "chat": "openai-chat",
                },
                labels={
                    "anthropic-messages": "messages",
                    "openai-chat": "chat",
                    "openai-responses": "responses",
                },
                routed_protocols=frozenset(
                    {"anthropic-messages", "openai-chat"}
                ),
            ),
        )

    def test_adapter_protocol_is_normalized_for_presentation(self):
        self.assertEqual(
            "anthropic-messages",
            self.policy().endpoint_kind("opencode", "model"),
        )

    def test_default_builder_owns_endpoint_presentation(self):
        policy = build_default_provider_endpoint_policy(self.policy().ports)
        self.assertEqual("openai-chat", policy.normalize_endpoint_kind("chat"))
        self.assertEqual("messages", policy.presentation.labels["anthropic-messages"])

    def test_override_matches_normalized_model_identity(self):
        config = {"model_endpoints": {"model": "chat"}}
        self.assertEqual(
            "openai-chat",
            self.policy().endpoint_kind("opencode", "model:free", config),
        )

    def test_unsupported_protocol_is_marked_in_display(self):
        self.assertEqual(
            "responses unsupported",
            self.policy("openai_responses").endpoint_display(
                "opencode", "gpt"
            ),
        )

    def test_override_is_marked_in_display(self):
        config = {"model_endpoints": {"model": "messages"}}
        self.assertEqual(
            "messages override",
            self.policy().endpoint_display("opencode", "model", config),
        )


if __name__ == "__main__":
    unittest.main()
