import unittest

from ciel_runtime_support.architecture import ProviderRequestPolicy
from ciel_runtime_support.provider_request_access import (
    ProviderRequestAccessEffects,
    ProviderRequestAccessPorts,
    ProviderRequestAccessService,
)


class ProviderRequestAccessServiceTests(unittest.TestCase):
    def service(self, *, credential_strategy="adapter", inbound=None):
        return ProviderRequestAccessService(
            ports=ProviderRequestAccessPorts(
                request_policy=lambda _provider, _config: ProviderRequestPolicy(
                    chat_path="/chat",
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
                user_agent_headers=lambda headers: {
                    **headers,
                    "user-agent": "ciel",
                },
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
