import unittest
from unittest import mock

import claude_any


ANTHROPIC_MODELS = [
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]


def _resolve(requested, current_model="claude-sonnet-4-6", provider="anthropic", models=None):
    pcfg = {
        "base_url": "https://api.anthropic.com",
        "api_key": "",
        "current_model": current_model,
        "route_through_router": True,
    }
    with mock.patch.object(
        claude_any, "read_model_list_cache", return_value=models or ANTHROPIC_MODELS
    ):
        return claude_any.resolve_requested_model(provider, pcfg, requested)


class AnthropicModelSwitchTests(unittest.TestCase):
    def test_native_id_resolves_to_itself_not_current_model(self):
        # /model opus while current_model is sonnet: must reach opus, not collapse.
        self.assertEqual("claude-opus-4-8", _resolve("claude-opus-4-8"))

    def test_native_fable_resolves_to_itself(self):
        self.assertEqual("claude-fable-5", _resolve("claude-fable-5"))

    def test_native_id_equal_to_current_model(self):
        self.assertEqual("claude-sonnet-4-6", _resolve("claude-sonnet-4-6"))

    def test_slug_alias_still_resolves(self):
        self.assertEqual(
            "claude-opus-4-8",
            _resolve("claude-any-anthropic-claude-opus-4-8"),
        )

    def test_unknown_claude_id_still_collapses_to_current_model(self):
        self.assertEqual("claude-sonnet-4-6", _resolve("claude-bogus-9"))

    def test_stale_other_provider_alias_still_collapses(self):
        self.assertEqual("claude-sonnet-4-6", _resolve("claude-any-deepseek-v4-flash"))

    def test_non_anthropic_provider_still_collapses_native_id(self):
        # On a non-anthropic provider a native Claude id is not a real upstream
        # model, so it must still collapse to that provider's current model.
        result = _resolve(
            "claude-opus-4-8",
            current_model="deepseek-v4-flash",
            provider="deepseek",
            models=["deepseek-v4-flash", "deepseek-v4-pro"],
        )
        self.assertEqual("deepseek-v4-flash", result)

    def test_provider_advertised_bare_claude_id_resolves_for_third_party(self):
        # Some third-party providers expose Claude-named upstream models. If the
        # router advertised that bare id in /v1/models, Claude Code may send it
        # back from /model and it must not collapse to the launch model.
        result = _resolve(
            "claude-opus-4-8",
            current_model="deepseek-v4-flash-free",
            provider="opencode",
            models=["deepseek-v4-flash-free", "claude-opus-4-8"],
        )
        self.assertEqual("claude-opus-4-8", result)

    def test_default_family_aliases_use_cached_models_not_current_model(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "current_model": "claude-opus-4-8",
            "route_through_router": True,
        }
        with mock.patch.object(claude_any, "read_model_list_cache", return_value=ANTHROPIC_MODELS):
            aliases = claude_any.claude_code_default_model_aliases(
                "anthropic",
                pcfg,
                "claude-any-anthropic-claude-opus-4-8",
            )

        self.assertEqual(
            "claude-any-anthropic-claude-opus-4-8",
            aliases["ANTHROPIC_DEFAULT_OPUS_MODEL"],
        )
        self.assertEqual(
            "claude-any-anthropic-claude-sonnet-4-6",
            aliases["ANTHROPIC_DEFAULT_SONNET_MODEL"],
        )
        self.assertEqual(
            "claude-any-anthropic-claude-haiku-4-5",
            aliases["ANTHROPIC_DEFAULT_HAIKU_MODEL"],
        )

    def test_launch_model_cache_refreshes_when_no_cache_or_registry_exists(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "current_model": "claude-opus-4-8",
            "route_through_router": True,
        }
        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "read_model_registry_models", return_value=None),
            mock.patch.object(claude_any, "upstream_model_ids", return_value=ANTHROPIC_MODELS) as upstream,
        ):
            claude_any.ensure_model_cache_for_launch("anthropic", pcfg)

        upstream.assert_called_once_with("anthropic", pcfg)

    def test_launch_model_cache_uses_existing_registry_without_network(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "current_model": "claude-opus-4-8",
            "route_through_router": True,
        }
        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "read_model_registry_models", return_value=ANTHROPIC_MODELS),
            mock.patch.object(claude_any, "upstream_model_ids") as upstream,
        ):
            claude_any.ensure_model_cache_for_launch("anthropic", pcfg)

        upstream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
