import re
import unittest

from ciel_runtime_support.provider_catalog_sources import (
    AnthropicCatalogPolicy,
    FireworksCatalogPolicy,
    ModelCatalogProjectionPorts,
    ProviderCatalogHttpPorts,
    ProviderCatalogPolicyPorts,
    ProviderCatalogSourceService,
)


class ProviderCatalogSourceServiceTests(unittest.TestCase):
    def service(self, http_json=lambda *_args, **_kwargs: {}):
        return ProviderCatalogSourceService(
            projection=ModelCatalogProjectionPorts(
                normalize_model_id=lambda _provider, value: str(value).strip(),
                model_context=lambda item: item.get("context_length"),
                positive_int=lambda value: int(value) if value else None,
                provider_metadata=lambda _provider: lambda _item: {},
            ),
            http=ProviderCatalogHttpPorts(
                http_json=http_json,
                join_url=lambda base, path: base.rstrip("/") + path,
                upstream_base=lambda _provider, _config: "https://api.example",
                request_headers=lambda: {},
                urlopen=lambda *_args, **_kwargs: None,
            ),
            policy=ProviderCatalogPolicyPorts(
                unique_model_ids=lambda _provider, values: list(dict.fromkeys(values)),
                log=lambda _level, _message: None,
            ),
            anthropic=AnthropicCatalogPolicy(
                docs_urls=(),
                default_ids=("claude-sonnet-4-6",),
                limited_ids=("claude-mythos-preview",),
                fallback_ids=("claude-sonnet-4-6",),
                public_id_pattern=re.compile(r"claude-[a-z]+-\d+-\d+"),
            ),
            fireworks=FireworksCatalogPolicy(
                default_account_id="fireworks",
                api_base_url="https://api.fireworks.ai",
                inference_base_url="https://api.fireworks.ai/inference",
            ),
        )

    def test_model_ids_accept_common_catalog_shapes(self):
        service = self.service()
        self.assertEqual(
            ["model-a", "model-b"],
            service.model_ids_from_response(
                {"data": [{"id": "model-a"}, {"name": "model-b"}]}
            ),
        )

    def test_fireworks_account_can_be_inferred_from_model_resource(self):
        service = self.service()
        self.assertEqual(
            "acme",
            service.fireworks_account_id(
                {"current_model": "accounts/acme/models/large"}
            ),
        )

    def test_anthropic_docs_projection_deduplicates_and_filters(self):
        service = self.service()
        ids = service.anthropic_model_ids_from_docs_text(
            "claude-sonnet-4-6 and claude-sonnet-4-6"
        )
        self.assertEqual(["claude-sonnet-4-6"], ids)
        self.assertEqual(ids, service.filter_anthropic_default_model_ids(ids))

    def test_anthropic_api_falls_back_to_second_endpoint(self):
        calls = []

        def http_json(url, **_kwargs):
            calls.append(url)
            if url.endswith("/v1/models"):
                raise OSError("unsupported")
            return {"data": [{"id": "claude-sonnet-4-6"}]}

        ids, source = self.service(http_json).fetch_anthropic_api_model_ids({}, {})
        self.assertEqual(["claude-sonnet-4-6"], ids)
        self.assertEqual("api:/models", source)
        self.assertEqual(2, len(calls))


if __name__ == "__main__":
    unittest.main()
