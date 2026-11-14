"""Local-first request resolution answers exactly what the fetch-first resolver answered.

B4 condition (3), journal 2026-11-13 fixes/router-upstream-error-retry,
lead-rulings-b4-catalog-stall.md: whenever the catalog fetch succeeds, the new
local-first `resolve_requested` must return the same model as the 57dbfb3
fetch-first resolver for every alias form. `reference_resolve` below is a verbatim
copy of 57dbfb3 `ProviderModelSelection.resolve_requested`, run on the same real
ports. No network: catalog reads are served from fixtures.
"""
import contextlib
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support import provider_models
from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER


def reference_resolve(self, provider, config, requested):
    """57dbfb3 ciel_runtime_support/provider_model_selection.py resolve_requested, verbatim."""
    requested = self.identity.strip_context_suffix(requested)
    fallback = self.identity.normalize(provider, config.get("current_model") or "model")
    adapter = self.selection.adapter(provider, config)
    model_map = self.identity.model_map(
        provider,
        config,
        fetch=not bool(config.get(REMOTE_BRIDGE_CONFIG_MARKER)),
    )
    if not requested:
        return self.identity.api_model_id(provider, fallback)
    resolved = self.identity.unslug(provider, requested, model_map)
    if resolved:
        return self.identity.api_model_id(provider, resolved)
    if requested in set(model_map.values()):
        return self.identity.api_model_id(provider, requested)
    if adapter.preserves_claude_model_alias(requested):
        return self.identity.api_model_id(provider, requested)
    if requested.startswith(("claude-", "ciel-runtime-")):
        return self.identity.api_model_id(provider, fallback)
    return self.identity.api_model_id(
        provider, self.identity.normalize(provider, requested)
    )


CURRENT = "Team_Model-1.5"
OTHER = "other-model"
SLASHED = "vendor/Model X"
CLAUDE = "claude-opus-4-8"


def forms(provider):
    alias = ciel_runtime.alias_for
    values = [None, "", CURRENT, OTHER, SLASHED, CLAUDE, "claude-bogus-9", "plain-unknown",
              "ciel-runtime-otherprov-x", alias(provider, CURRENT), alias(provider, CURRENT) + "[1m]",
              alias(provider, OTHER), alias(provider, OTHER) + "[1m]", alias(provider, SLASHED),
              alias(provider, CLAUDE), f"ciel-runtime-{provider}-missing-model"]
    return list(dict.fromkeys(values))


@contextlib.contextmanager
def catalog(cache, served):
    """`cache`: the list-cache answer (None = no cache). `served`: what a successful fetch returns."""
    # Agent-capable entries, so catalog filters (CielAiRouter select_agent_models) keep them.
    response = {"object": "list", "data": [
        {"id": model_id, "object": "model", "owned_by": "fixture", "root": model_id,
         "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True}}
        for model_id in served]}
    patches = [
        mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None if cache is None else list(cache)),
        mock.patch.object(ciel_runtime, "read_model_registry_models", return_value=None),
        mock.patch.object(ciel_runtime, "write_model_list_cache"),
        mock.patch.object(ciel_runtime, "write_model_registry"),
        mock.patch.object(ciel_runtime, "http_json", return_value=response),
        mock.patch.object(ciel_runtime, "fetch_anthropic_api_model_ids", return_value=(list(served), "api:/v1/models")),
        mock.patch.object(ciel_runtime, "fetch_anthropic_public_model_ids", return_value=list(served)),
        mock.patch.object(ciel_runtime, "fetch_fireworks_model_ids", return_value=(list(served), {}, "fixture")),
        mock.patch.object(ciel_runtime, "provider_urlopen", side_effect=AssertionError("network")),
    ]
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        provider_models._FAILURE_COOLDOWN.clear()
        yield


SCENARIOS = {
    "cache_has_current": ([CURRENT, OTHER, SLASHED, CLAUDE], [CURRENT, OTHER, SLASHED, CLAUDE]),
    "cache_without_current": ([OTHER, SLASHED, CLAUDE], [OTHER, SLASHED, CLAUDE]),
    "no_cache_catalog_has_current": (None, [CURRENT, OTHER, SLASHED, CLAUDE]),
    "no_cache_catalog_without_current": (None, [OTHER, SLASHED, CLAUDE]),
}


class ResolutionEquivalenceTests(unittest.TestCase):
    def compare(self, provider, cache, served, extra=None):
        config = {"base_url": "http://catalog.invalid", "api_key": "fixture-key", "current_model": CURRENT,
                  **(extra or {})}
        selection = ciel_runtime.provider_model_selection()
        rows = []
        for requested in forms(provider):
            with catalog(cache, served):
                old = reference_resolve(selection, provider, dict(config), requested)
            with catalog(cache, served):
                new = selection.resolve_requested(provider, dict(config), requested)
            rows.append((requested, old, new))
        return rows

    def test_fixture_catalog_is_actually_served(self):
        # Guards against a vacuous comparison: the fetching path must see the ids.
        config = {"base_url": "http://catalog.invalid", "api_key": "fixture-key", "current_model": CURRENT}
        with catalog(None, [CURRENT, OTHER]):
            self.assertEqual({CURRENT, OTHER}, set(ciel_runtime.upstream_model_ids("cielairouter", dict(config))))

    def test_every_provider_alias_form_and_catalog_state_matches_the_fetching_resolver(self):
        compared = 0
        for provider in PROVIDER_ADAPTERS.names():
            for name, (cache, served) in SCENARIOS.items():
                for requested, old, new in self.compare(provider, cache, served):
                    compared += 1
                    with self.subTest(provider=provider, scenario=name, requested=requested):
                        self.assertEqual(old, new)
        self.assertGreater(compared, 300)

    def test_slug_collision_with_current_model_is_the_only_difference(self):
        # The only divergence found: with NO cached list, a different catalog id
        # whose alias equals current_model's alias. The fetch map keeps the
        # collider; the local-first resolver answers current_model without I/O.
        # With the collision in the cache, both answer the collider.
        # Accepted by lead-rulings-b4-catalog-stall.md, amendment 15:45Z (option L):
        # the alias encoding is lossy, so this input is ambiguous already, and
        # fetching here would bring back the B4 stall (condition C).
        colliding = "team_model/1.5"  # alias equals CURRENT's
        alias = ciel_runtime.alias_for("cielairouter", CURRENT)
        self.assertEqual(alias, ciel_runtime.alias_for("cielairouter", colliding))
        rows = self.compare("cielairouter", None, [CURRENT, colliding])
        different = {(requested, old, new) for requested, old, new in rows if old != new}
        self.assertEqual({(alias, colliding, CURRENT), (alias + "[1m]", colliding, CURRENT)}, different)
        cached = self.compare("cielairouter", [CURRENT, colliding], [CURRENT, colliding])
        self.assertEqual([], [(requested, old, new) for requested, old, new in cached if old != new])

if __name__ == "__main__":
    unittest.main()
