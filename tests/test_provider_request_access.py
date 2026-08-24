import unittest

from ciel_runtime_support.architecture import ProviderRequestPolicy
from ciel_runtime_support.provider_request_access import (
    ProviderRequestAccessEffects,
    ProviderRequestAccessPorts,
    ProviderRequestAccessService,
)


class ProviderRequestAccessServiceTests(unittest.TestCase):
    def service(
        self,
        *,
        credential_strategy="adapter",
        chat_path="/chat",
        inbound=None,
    ):
        return ProviderRequestAccessService(
            ports=ProviderRequestAccessPorts(
                request_policy=lambda _provider, _config: ProviderRequestPolicy(
                    chat_path=chat_path,
                    models_path="/models",
                    model_alias_strategy="ncp",
                    credential_strategy=credential_strategy,
                    stream_required=True,
                ),
                select_api_key=lambda _provider, _config: "secret",
                meaningful_key=lambda key: key != "not-used",
                adapter_headers=lambda _provider, _config, key: {
                    "authorization": f"Bearer {key}"
                },
                inbound_credentials=lambda _key, _headers: inbound,
            ),
            effects=ProviderRequestAccessEffects(
                user_agent_headers=lambda headers: (
                    dict(headers)
                    if any(
                        str(name).lower() == "user-agent"
                        for name in headers
                    )
                    else {**headers, "user-agent": "ciel"}
                ),
                ncp_model_id=lambda model: f"ncp:{model}",
                normalize_provider=lambda value: str(value).lower(),
            ),
        )

    def test_adapter_headers_are_used_for_standard_credentials(self):
        headers = self.service().headers("deepseek", {})
        self.assertEqual("Bearer secret", headers["authorization"])
        self.assertEqual("ciel", headers["user-agent"])

    def test_inbound_credentials_are_selected_by_adapter_policy(self):
        headers = self.service(
            credential_strategy="anthropic_inbound",
            inbound={"authorization": "Bearer oauth"},
        ).headers("anthropic", {})
        self.assertEqual("Bearer oauth", headers["authorization"])

    def test_anthropic_compatible_policy_preserves_inbound_client_headers(self):
        headers = self.service().headers(
            "kimi",
            {},
            {
                "content-type": "application/custom+json",
                "anthropic-version": "2099-01-01",
                "anthropic-beta": "prompt-caching-2024-07-31",
                "x-claude-code-session-id": "session-1",
                "user-agent": "claude-cli/2.1.181 (external, cli)",
                "host": "127.0.0.1:9464",
            },
            "anthropic_messages",
        )

        self.assertEqual(
            "prompt-caching-2024-07-31", headers["anthropic-beta"]
        )
        self.assertEqual("application/custom+json", headers["content-type"])
        self.assertEqual("2099-01-01", headers["anthropic-version"])
        self.assertEqual("session-1", headers["x-claude-code-session-id"])
        self.assertEqual(
            "claude-cli/2.1.181 (external, cli)", headers["user-agent"]
        )
        self.assertNotIn("host", headers)
        self.assertEqual("Bearer secret", headers["authorization"])

    def test_anthropic_target_adds_missing_protocol_headers(self):
        headers = self.service().headers(
            "kimi", {}, {}, "anthropic_messages"
        )

        self.assertEqual(
            {
                "authorization": "Bearer secret",
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "user-agent": "ciel",
            },
            headers,
        )

    def test_openai_target_does_not_add_anthropic_protocol_headers(self):
        headers = self.service().headers(
            "kimi", {}, {}, "openai_responses"
        )

        self.assertNotIn("content-type", headers)
        self.assertNotIn("anthropic-version", headers)

    def test_protocol_not_provider_default_controls_passthrough(self):
        inbound = {
            "x-client-request-id": "codex-request-1",
            "user-agent": "codex-cli/1.2.3",
        }

        responses = self.service(chat_path="/v1/messages").headers(
            "kimi", {}, inbound, "openai_responses"
        )
        internal = self.service(chat_path="/v1/messages").headers(
            "kimi", {}, inbound
        )

        self.assertEqual("codex-request-1", responses["x-client-request-id"])
        self.assertEqual("codex-cli/1.2.3", responses["user-agent"])
        self.assertNotIn("x-client-request-id", internal)
        self.assertEqual("ciel", internal["user-agent"])

    def test_model_alias_and_streaming_come_from_request_policy(self):
        service = self.service()
        self.assertEqual("ncp:model", service.upstream_model("nvidia", {}, "model"))
        self.assertTrue(service.requires_streaming("nvidia", {}))

    def test_request_key_is_recovered_without_provider_knowledge(self):
        self.assertEqual(
            "key",
            ProviderRequestAccessService.key_from_headers(
                {"Authorization": "Bearer key"}
            ),
        )


if __name__ == "__main__":
    unittest.main()
