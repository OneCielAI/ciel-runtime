import re
import unittest

from ciel_runtime_support.provider_catalog_sources import (
    AnthropicCatalogPolicy,
    FireworksCatalogPolicy,
    ModelCatalogProjectionPorts,
    ProviderCatalogHttpPorts,
    ProviderCatalogPolicyPorts,
    ProviderCatalogSourceService,
    build_default_provider_catalog_source_service,
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

    def test_default_builder_owns_immutable_provider_policies(self):
        custom = self.service()
        service = build_default_provider_catalog_source_service(
            custom.projection,
            custom.http,
            custom.policy,
        )
        self.assertTrue(service.anthropic.docs_urls)
        self.assertEqual("fireworks", service.fireworks.default_account_id)

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

    def test_anthropic_docs_lineup_ids_survive_an_allow_list_that_lags(self):
        # The overview page ships its current lineup as an escaped JSON
        # payload.  claude-opus-5-5 reached the page on 2026-09-22 while the
        # hand-maintained allow-list still ended at claude-opus-5, and the
        # intersection dropped it from every picker and registry entry.
        page = (
            'xx\\"models\\":[{\\"id\\":\\"claude-opus-5-5\\",\\"lifecycle\\":\\"active\\"},'
            '{\\"id\\":\\"claude-opus-3\\",\\"lifecycle\\":\\"retired\\",\\"legacy\\":true},'
            '{\\"id\\":\\"claude-sonnet-5\\",\\"lifecycle\\":\\"active\\"}]yy'
        )
        service = self.service()
        self.assertEqual(
            ["claude-opus-5-5", "claude-sonnet-5"],
            service.anthropic_lineup_model_ids_from_docs_text(page),
        )
        scanned = service.anthropic_model_ids_from_docs_text(
            "claude-opus-5-5 claude-sonnet-4-6"
        )
        kept = service.filter_anthropic_default_model_ids(
            scanned, lineup_ids=["claude-opus-5-5"]
        )
        self.assertIn("claude-opus-5-5", kept)
        self.assertIn("claude-sonnet-4-6", kept)

    def test_anthropic_docs_lineup_ids_skip_unparseable_payloads(self):
        service = self.service()
        self.assertEqual([], service.anthropic_lineup_model_ids_from_docs_text("no payload"))
        self.assertEqual(
            [],
            service.anthropic_lineup_model_ids_from_docs_text('\\"models\\":[{\\"id\\":'),
        )

    def test_anthropic_fetch_returns_a_lineup_model_outside_the_allow_list(self):
        page = (
            '<html>claude-opus-5-5 claude-sonnet-4-6'
            '\\"models\\":[{\\"id\\":\\"claude-opus-5-5\\",\\"lifecycle\\":\\"active\\"}]'
            "</html>"
        )

        class _Response:
            def read(self, _limit=0):
                return page.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        service = ProviderCatalogSourceService(
            projection=ModelCatalogProjectionPorts(
                normalize_model_id=lambda _provider, value: str(value).strip(),
                model_context=lambda item: item.get("context_length"),
                positive_int=lambda value: int(value) if value else None,
                provider_metadata=lambda _provider: lambda _item: {},
            ),
            http=ProviderCatalogHttpPorts(
                http_json=lambda *_args, **_kwargs: {},
                join_url=lambda base, path: base.rstrip("/") + path,
                upstream_base=lambda _provider, _config: "https://api.example",
                request_headers=lambda: {},
                urlopen=lambda *_args, **_kwargs: _Response(),
            ),
            policy=ProviderCatalogPolicyPorts(
                unique_model_ids=lambda _provider, values: list(dict.fromkeys(values)),
                log=lambda _level, _message: None,
            ),
            anthropic=AnthropicCatalogPolicy(
                docs_urls=("https://docs.example/models",),
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
        self.assertEqual(
            ["claude-opus-5-5", "claude-sonnet-4-6"],
            service.fetch_anthropic_public_model_ids(),
        )

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
