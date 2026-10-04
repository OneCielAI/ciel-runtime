import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support.codex_model_catalog import (
    CodexModelCatalogService,
    CodexModelCatalogSpec,
)
from ciel_runtime_support.config_repository import deep_merge
from ciel_runtime_support.model_cache_lifecycle import ModelCacheLifecyclePorts, ModelCacheLifecycleService
from ciel_runtime_support.provider_model_profile import reapply_launch_catalog_profile
from ciel_runtime_support.providers.cielairouter import (
    CielAiRouterProviderAdapter,
    lower_to_supported_effort,
    select_agent_models,
)


# Rows shaped like GET /v1/models?prefix=alias&configuredOnly=true&availableOnly=true
# from ciel-router-01 on 2026-10-02 (values trimmed to the fields used here).
ASTRA = {
    "id": "ASTRA", "owned_by": "combo", "root": "ASTRA", "parent": None,
    "context_length": 872000, "max_input_tokens": 872000, "max_output_tokens": 128000,
    "capabilities": {"tool_calling": True, "reasoning": True, "vision": True,
                     "thinking": True, "supportsThinking": True},
}
OPUS = {
    "id": "cc/claude-opus-5-5", "owned_by": "claude", "root": "claude-opus-5-5",
    "parent": None, "name": "Claude Opus 5.5", "context_length": 1000000,
    "max_output_tokens": 128000,
    "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True,
                     "supportsThinking": True,
                     "effort_tiers": ["none", "low", "medium", "high", "xhigh", "max"]},
}
SOL_HIGH = {
    "id": "cx/gpt-5.6-sol-high", "owned_by": "codex", "root": "gpt-5.6-sol-high",
    "parent": None, "context_length": 872000, "max_output_tokens": 128000,
    "api_format": "responses", "supported_endpoints": ["responses"],
    "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True,
                     "effort_tiers": ["low", "medium", "high", "xhigh", "max", "ultra"]},
}
IMAGE = {
    "id": "codex/gpt-5.6-sol", "owned_by": "codex", "type": "image",
    "capabilities": {"tool_calling": True, "thinking": True},
}
NO_TOOLS = {
    "id": "WebTokens", "owned_by": "combo", "root": "WebTokens",
    "capabilities": {"tool_calling": False, "thinking": True},
}
NO_THINKING = {
    "id": "no-think/cc/claude-opus-5-5", "owned_by": "claude",
    "capabilities": {"tool_calling": True, "reasoning": True},
}
EMBEDDING_ONLY = {
    "id": "x/embed", "owned_by": "x", "supported_endpoints": ["embeddings"],
    "capabilities": {"tool_calling": True, "thinking": True},
}
MIRROR = {
    "id": "claude/claude-opus-5-5", "owned_by": "claude", "parent": "cc/claude-opus-5-5",
    "capabilities": {"tool_calling": True, "thinking": True},
}


class CielAiRouterProviderTests(unittest.TestCase):
    def pcfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["cielairouter"])
        pcfg.update(overrides)
        return pcfg

    def contract(self, **overrides):
        return ciel_runtime.provider_contract_config("cielairouter", self.pcfg(**overrides))

    def profile(self, model, info, **overrides):
        pcfg = self.pcfg(api_key="router-test", current_model=model, **overrides)
        with mock.patch.object(ciel_runtime, "cached_current_model_info", return_value=info):
            messages = ciel_runtime.apply_provider_model_profile("cielairouter", pcfg)
        return pcfg, messages

    def info(self, row):
        data = {"object": "list", "data": [row]}
        return ciel_runtime.model_info_from_response("cielairouter", data)[row["id"]]

    def test_provider_is_registered_with_aliases_and_astra_default(self):
        self.assertEqual("cielairouter", ciel_runtime.normalize_provider("ciel-ai-router"))
        self.assertEqual("cielairouter", ciel_runtime.normalize_provider("ciel-router"))
        self.assertEqual("CielAiRouter", ciel_runtime.PROVIDER_LABELS["cielairouter"])
        cfg = deep_merge(ciel_runtime.DEFAULT_CONFIG, {"providers": {}})
        defaults = cfg["providers"]["cielairouter"]
        self.assertEqual("ASTRA", defaults["current_model"])
        self.assertEqual(872000, defaults["context_window"])
        # A persisted default would be merged back on every load and pin
        # Claude Code's output limit for Claude models.
        self.assertNotIn("max_output_tokens", defaults)

    def test_each_runtime_keeps_its_native_protocol(self):
        pcfg = self.pcfg(api_key="router-test")
        for operation in ("anthropic_messages", "openai_responses", "openai_chat"):
            with self.subTest(operation=operation):
                self.assertEqual(
                    operation,
                    ciel_runtime.select_provider_protocol("cielairouter", pcfg, operation, "ASTRA"),
                )
        self.assertEqual(
            "https://ciel-router-01.ezonebot.com/v1/chat/completions",
            ciel_runtime.provider_endpoint("cielairouter", pcfg, "openai_chat"),
        )
        self.assertEqual(
            "https://ciel-router-01.ezonebot.com",
            ciel_runtime.native_anthropic_base_url("cielairouter", pcfg),
        )
        self.assertEqual(
            ("/v1/models?prefix=alias&configuredOnly=true&availableOnly=true",),
            ciel_runtime.provider_model_paths("cielairouter", pcfg),
        )

    def test_router_key_is_sent_as_bearer_only(self):
        headers = ciel_runtime.provider_headers("cielairouter", self.pcfg(api_key="router-test"))
        self.assertEqual("Bearer router-test", headers["Authorization"])
        self.assertNotIn("x-api-key", headers)

    def test_catalog_keeps_agent_ready_chat_models_like_the_vsix(self):
        rows = [ASTRA, OPUS, SOL_HIGH, IMAGE, NO_TOOLS, NO_THINKING, EMBEDDING_ONLY, MIRROR]
        self.assertEqual(
            ["ASTRA", "cc/claude-opus-5-5", "cx/gpt-5.6-sol-high"],
            [row["id"] for row in select_agent_models(rows)],
        )
        selected = ciel_runtime.select_provider_catalog_entries(
            "cielairouter", self.pcfg(), {"object": "list", "data": rows}
        )
        self.assertEqual(
            ["ASTRA", "cc/claude-opus-5-5", "cx/gpt-5.6-sol-high"],
            ciel_runtime.model_ids_from_response(selected),
        )

    def test_catalog_metadata_keeps_limits_and_capabilities(self):
        info = self.info(OPUS)
        self.assertEqual(1000000, info["max_model_len"])
        self.assertEqual(128000, info["max_output_tokens"])
        self.assertEqual(
            ["none", "low", "medium", "high", "xhigh", "max"],
            info["capabilities"]["effort_tiers"],
        )

    def test_claude_models_keep_claude_code_native_limits_and_capabilities(self):
        pcfg, messages = self.profile(
            "cc/claude-opus-5-5", self.info(OPUS),
            max_output_tokens=32000, claude_code_supported_capabilities=["thinking"],
        )
        self.assertEqual(1000000, pcfg["context_window"])
        self.assertEqual(1000000, pcfg["max_model_len"])
        self.assertNotIn("max_output_tokens", pcfg)
        self.assertNotIn("claude_code_supported_capabilities", pcfg)
        self.assertEqual(128000, pcfg["catalog_max_output_tokens"])
        self.assertEqual(
            "effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking",
            ciel_runtime.claude_code_capability_string("cielairouter", pcfg),
        )
        self.assertEqual(1, len(messages))

    def test_explicit_output_limit_survives_the_catalog_profile(self):
        pcfg, _ = self.profile(
            "cc/claude-opus-5-5", self.info(OPUS),
            max_output_tokens=32000, output_tokens_explicit=True,
        )
        self.assertEqual(32000, pcfg["max_output_tokens"])

    def test_other_models_take_limits_and_capabilities_from_the_catalog(self):
        pcfg, _ = self.profile("ASTRA", self.info(ASTRA))
        self.assertEqual(872000, pcfg["context_window"])
        self.assertEqual(128000, pcfg["max_output_tokens"])
        self.assertEqual(["thinking"], pcfg["claude_code_supported_capabilities"])
        self.assertNotIn("catalog_effort_tiers", pcfg)
        pcfg, _ = self.profile("cx/gpt-5.6-sol-high", self.info(SOL_HIGH))
        self.assertEqual(
            "effort,xhigh_effort,max_effort,thinking",
            ciel_runtime.claude_code_capability_string("cielairouter", pcfg),
        )
        self.assertEqual(
            {
                "ciel_template_slugs": ["gpt-5.6-sol-high", "gpt-5.6-sol"],
                "ciel_reasoning_efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
            },
            pcfg["codex_model_catalog"],
        )

    def test_requests_for_the_selected_model_stay_within_catalog_limits(self):
        pcfg, _ = self.profile("cc/claude-opus-5-5", self.info(OPUS))
        anthropic = ciel_runtime.apply_provider_adapter_request_policy(
            "cielairouter", pcfg,
            {"model": "cc/claude-opus-5-5", "max_tokens": 200000, "output_config": {"effort": "ultra"}},
            "anthropic_messages",
        )
        self.assertEqual(128000, anthropic["max_tokens"])
        self.assertEqual({"effort": "max"}, anthropic["output_config"])
        responses = ciel_runtime.apply_provider_adapter_request_policy(
            "cielairouter", pcfg,
            {"model": "cc/claude-opus-5-5", "max_output_tokens": 4096, "reasoning": {"effort": "high", "summary": "auto"}},
            "openai_responses",
        )
        self.assertEqual(4096, responses["max_output_tokens"])
        self.assertEqual({"effort": "high", "summary": "auto"}, responses["reasoning"])
        other = ciel_runtime.apply_provider_adapter_request_policy(
            "cielairouter", pcfg, {"model": "ASTRA", "max_tokens": 200000}, "openai_chat",
        )
        self.assertEqual(200000, other["max_tokens"])

    def test_unsupported_effort_lowers_to_the_nearest_listed_tier(self):
        tiers = ["low", "medium", "high", "xhigh", "max"]
        self.assertEqual("max", lower_to_supported_effort("ultra", tiers))
        self.assertEqual("high", lower_to_supported_effort("high", tiers))
        self.assertEqual("low", lower_to_supported_effort("none", tiers))
        self.assertEqual("custom", lower_to_supported_effort("custom", tiers))

    def test_model_cache_is_scoped_to_the_router_key(self):
        first = ciel_runtime.model_cache_key("cielairouter", self.pcfg(api_key="key-one"))
        second = ciel_runtime.model_cache_key("cielairouter", self.pcfg(api_key="key-two"))
        self.assertNotEqual(first, second)
        self.assertNotIn("key-one", first)
        tabitoken = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["tabitoken"])
        tabitoken["api_key"] = "key-one"
        self.assertNotIn("api_key_id", ciel_runtime.model_cache_key("tabitoken", tabitoken))

    def test_launch_reapplies_the_cached_catalog_profile(self):
        calls = []
        lifecycle = ModelCacheLifecycleService(ModelCacheLifecyclePorts(
            invalidate_config=lambda: None, artifact_paths=lambda: (),
            read_list_cache=lambda *_: ["ASTRA"], read_registry_models=lambda *_: None,
            upstream_model_ids=lambda *_: [], catalog_model_ids=lambda *_: [],
            normalize_model_id=lambda _provider, model: model, unique_model_ids=lambda _provider, ids: ids,
            sorted_model_ids=lambda ids: ids, log=lambda *_: None,
            launch_profile=lambda provider, config: calls.append(provider),
        ))
        lifecycle.ensure_for_launch("cielairouter", {})
        self.assertEqual(["cielairouter"], calls)

        pcfg = self.pcfg(api_key="router-test", current_model="cc/claude-opus-5-5")
        stored = {"providers": {"cielairouter": copy.deepcopy(pcfg)}}
        saved = []
        with mock.patch.object(ciel_runtime, "cached_current_model_info", return_value=self.info(OPUS)):
            reapply_launch_catalog_profile(
                "cielairouter", pcfg, True, ciel_runtime.apply_provider_model_profile,
                lambda: stored, saved.append, lambda *_: None,
            )
        self.assertEqual(1000000, pcfg["context_window"])
        self.assertEqual(128000, pcfg["catalog_max_output_tokens"])
        # The router and the status line read the saved configuration.
        self.assertEqual([stored], saved)
        self.assertEqual(1000000, stored["providers"]["cielairouter"]["context_window"])
        self.assertTrue(
            ciel_runtime.provider_model_catalog_policy("cielairouter", pcfg).reapply_catalog_profile_at_launch
        )
        tabitoken = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["tabitoken"])
        self.assertFalse(
            ciel_runtime.provider_model_catalog_policy("tabitoken", tabitoken).reapply_catalog_profile_at_launch
        )
        profile = mock.Mock(return_value=["changed"])
        reapply_launch_catalog_profile("tabitoken", tabitoken, False, profile, dict, saved.append, lambda *_: None)
        profile.assert_not_called()

    def test_catalog_selection_roundtrip_and_rejection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pcfg = self.pcfg(api_key="isolated-key", current_model="removed", custom_models=["removed"])
            config = {"provider": "cielairouter", "providers": {"cielairouter": pcfg}}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            def save(value):
                config_path.write_text(json.dumps(value), encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "MODEL_LIST_CACHE_PATH", root / "list.json"),
                mock.patch.object(ciel_runtime, "MODEL_REGISTRY_PATH", root / "registry.json"),
                mock.patch.object(ciel_runtime, "GATEWAY_CACHE_PATH", root / "gateway.json", create=True),
                mock.patch.object(ciel_runtime, "load_config", side_effect=lambda: json.loads(config_path.read_text(encoding="utf-8"))),
                mock.patch.object(ciel_runtime, "get_current_provider", side_effect=lambda cfg: ("cielairouter", cfg["providers"]["cielairouter"])),
                mock.patch.object(ciel_runtime, "save_config", side_effect=save) as saved,
                mock.patch.object(ciel_runtime, "clear_model_cache") as cleared,
                mock.patch.object(ciel_runtime, "http_json", return_value={"data": [ASTRA, OPUS]}) as http,
            ):
                self.assertEqual([], ciel_runtime.cached_or_configured_model_ids("cielairouter", pcfg))
                ids = ciel_runtime.upstream_model_ids("cielairouter", pcfg, force_refresh=True)
                self.assertEqual(["ASTRA", OPUS["id"]], ids)
                self.assertEqual(ids, ciel_runtime.cached_or_configured_model_ids("cielairouter", pcfg))
                before = {p.name: p.read_bytes() for p in root.iterdir()}
                messages = ciel_runtime.model_selection_controller().select("removed")
                self.assertIn("rejected", messages[0])
                self.assertIn("removed", messages[0])
                saved.assert_not_called()
                cleared.assert_not_called()
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
                alias = ciel_runtime.alias_for("cielairouter", OPUS["id"])
                messages = ciel_runtime.model_selection_controller().select(alias)
                self.assertIn("set to " + OPUS["id"], messages[0])
                restored = json.loads(config_path.read_text(encoding="utf-8"))["providers"]["cielairouter"]
                self.assertEqual(OPUS["id"], restored["current_model"])
                self.assertTrue(ciel_runtime.ensure_current_model_from_provider_list("cielairouter", restored)[0])
                http.return_value = {"data": [ASTRA]}
                ok, messages = ciel_runtime.ensure_current_model_from_provider_list("cielairouter", restored, force_refresh=True)
                self.assertFalse(ok)
                self.assertIn(OPUS["id"], messages[0])
                self.assertIn("reselect", messages[0])
                self.assertEqual(OPUS["id"], restored["current_model"])

    def test_successful_filtered_empty_catalog_is_cached_without_fallback(self):
        pcfg = self.pcfg(api_key="test", current_model="removed", custom_models=["custom"])
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", return_value={"data": [IMAGE, NO_TOOLS, NO_THINKING]}),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write,
        ):
            self.assertEqual([], ciel_runtime.upstream_model_ids("cielairouter", pcfg, force_refresh=True))
            self.assertEqual([], write.call_args.args[2])
            ok, messages = ciel_runtime.ensure_current_model_from_provider_list("cielairouter", pcfg)
            self.assertFalse(ok)
            self.assertIn("removed", messages[0])

    def test_adapter_contract_defaults(self):
        adapter = CielAiRouterProviderAdapter()
        policy = adapter.model_catalog_policy(self.contract())
        self.assertTrue(policy.authoritative_upstream_catalog)
        self.assertTrue(policy.per_key_catalog)
        self.assertTrue(policy.reapply_catalog_profile_at_launch)
        status = adapter.status_policy(self.contract())
        self.assertEqual(10.0, status.probe_timeout_seconds)
        self.assertEqual("/v1/models?prefix=alias&configuredOnly=true&availableOnly=true", status.catalog_path)
        self.assertEqual(("ASTRA",), policy.fallback_models)


class CodexCatalogTemplateTests(unittest.TestCase):
    BUNDLED = {
        "models": [
            {"slug": "gpt-6-astra", "base_instructions": "astra prompt",
             "default_reasoning_level": "low", "context_window": 272000,
             "supported_reasoning_levels": [{"effort": "low", "description": "Astra low"}]},
            {"slug": "gpt-5.6-sol", "base_instructions": "sol prompt",
             "upgrade": {"model": "gpt-6-sol", "migration_markdown": "Meet GPT-6 Sol"},
             "availability_nux": {"message": "Try GPT-6 Sol"},
             "default_reasoning_level": "low", "context_window": 272000,
             "supported_reasoning_levels": [
                 {"effort": effort, "description": f"Sol {effort}"}
                 for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
             ]},
        ]
    }

    def write(self, metadata):
        def run(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.BUNDLED), stderr="")

        with TemporaryDirectory() as directory:
            service = CodexModelCatalogService(Path(directory), run, lambda *_: None)
            path = service.write(
                "codex",
                CodexModelCatalogSpec("routed", "CielAiRouter", 872000, metadata=metadata),
                {},
            )
            catalog = json.loads(Path(path).read_text(encoding="utf-8"))
        return next(item for item in catalog["models"] if item["slug"] == "routed")

    def test_matching_bundled_entry_is_the_template(self):
        routed = self.write({
            "ciel_template_slugs": ["gpt-5.6-sol-high", "gpt-5.6-sol"],
            "ciel_reasoning_efforts": ["low", "medium", "high"],
        })
        self.assertEqual("sol prompt", routed["base_instructions"])
        self.assertEqual(["low", "medium", "high"], [level["effort"] for level in routed["supported_reasoning_levels"]])
        self.assertEqual("Sol medium", routed["supported_reasoning_levels"][1]["description"])
        self.assertEqual("low", routed["default_reasoning_level"])
        self.assertEqual(872000, routed["context_window"])
        self.assertFalse(any(key.startswith("ciel_") for key in routed))
        # Codex would otherwise migrate the routed alias to gpt-6-sol.
        self.assertIsNone(routed["upgrade"])
        self.assertIsNone(routed["availability_nux"])

    def test_unknown_template_falls_back_to_the_first_bundled_entry(self):
        routed = self.write({"ciel_template_slugs": ["ASTRA"], "ciel_reasoning_efforts": ["medium", "max"]})
        self.assertEqual("astra prompt", routed["base_instructions"])
        self.assertEqual("medium", routed["default_reasoning_level"])
        self.assertEqual("Max reasoning effort", routed["supported_reasoning_levels"][1]["description"])


if __name__ == "__main__":
    unittest.main()
