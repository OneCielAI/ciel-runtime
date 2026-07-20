import unittest
from unittest import mock

from ciel_runtime_support.architecture import (
    ProviderAdapter,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderRequestPolicy,
)
from ciel_runtime_support.provider_contract_projection import (
    ProviderContractProjectionApi,
)


class ProviderContractProjectionApiTests(unittest.TestCase):
    def test_explicit_api_projects_adapter_contracts(self):
        adapter = mock.create_autospec(ProviderAdapter, instance=True)
        contract = ProviderConfig("test", "https://example.test", "model")
        adapter.resolve_endpoint.return_value = "/v1/messages"
        adapter.request_policy.return_value = ProviderRequestPolicy(
            "/v1/messages", "/v1/models"
        )
        adapter.context_policy.return_value = ProviderContextPolicy(
            capacity_strategy="configured_first"
        )
        api = ProviderContractProjectionApi(
            adapter=lambda _provider, _pcfg: adapter,
            contract=lambda _provider, _pcfg: contract,
            request_base=lambda _provider, _pcfg: "https://example.test",
            join_url=lambda base, path: base + path,
        )

        self.assertEqual(
            "https://example.test/v1/messages",
            api.endpoint(provider="test", pcfg={}, operation="messages"),
        )
        self.assertEqual(
            "/v1/messages", api.request_policy(provider="test", pcfg={}).chat_path
        )
        self.assertEqual(
            "configured_first",
            api.context_policy(provider="test", pcfg={}).capacity_strategy,
        )
        adapter.resolve_endpoint.assert_called_once_with("messages", contract)


if __name__ == "__main__":
    unittest.main()
