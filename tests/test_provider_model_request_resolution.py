"""Request model resolution is local-first; a configured alias never blocks on catalog I/O.

B4, 2026-11-13: every `resolve_requested_model` built a model map with
`fetch=True`, so an already configured CielAiRouter alias still paid a 41 s catalog stall.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.provider_model_selection import (
    ModelCatalogPorts,
    ModelIdentityPorts,
    ModelSelectionPorts,
    ProviderModelSelection,
)


class RequestResolutionTests(unittest.TestCase):
    def selection(self, maps):
        model_map = mock.Mock(side_effect=lambda _p, _c, fetch=True: dict(maps["fetch" if fetch else "local"]))
        identity = ModelIdentityPorts(
            normalize=lambda _p, value: str(value or "").strip(),
            model_map=model_map,
            unslug=lambda _p, alias, mapping: mapping.get(alias),
            api_model_id=lambda _p, value: value,
            strip_context_suffix=lambda value: str(value or ""),
            alias=lambda provider, model: f"ciel-runtime-{provider}-{model}",
        )
        adapter = SimpleNamespace(preserves_claude_model_alias=lambda _value: False)
        ports = ModelSelectionPorts(
            adapter=lambda *_: adapter, contract=mock.Mock(), placeholders=lambda _: set(),
            upstream_ids=mock.Mock(), unique_ids=mock.Mock(), apply_specs=mock.Mock(), apply_timeout=mock.Mock())
        catalog = ModelCatalogPorts(model_object=mock.Mock(), headers=mock.Mock(), fetch_anthropic=mock.Mock(),
                                    sorted_ids=mock.Mock(), routed_anthropic=mock.Mock(), log=mock.Mock())
        return ProviderModelSelection(identity, ports, catalog), model_map

    def test_configured_alias_never_fetches(self):
        alias = "ciel-runtime-cielairouter-_walkie_sol61-opus55-fast"
        selection, model_map = self.selection({"local": {alias: "_Walkie_Sol61-Opus55-Fast"},
                                               "fetch": {alias: "_Walkie_Sol61-Opus55-Fast"}})
        self.assertEqual("_Walkie_Sol61-Opus55-Fast", selection.resolve_requested("cielairouter", {}, alias))
        model_map.assert_called_once_with("cielairouter", {}, fetch=False)

    def test_unresolved_alias_fetches_once_then_resolves(self):
        alias = "ciel-runtime-cielairouter-new"
        selection, model_map = self.selection({"local": {}, "fetch": {alias: "new-model"}})
        self.assertEqual("new-model", selection.resolve_requested("cielairouter", {}, alias))
        self.assertEqual([mock.call("cielairouter", {}, fetch=False), mock.call("cielairouter", {}, fetch=True)],
                         model_map.call_args_list)

    def test_plain_model_without_local_alias_fetches_for_authoritative_resolution(self):
        selection, model_map = self.selection({"local": {}, "fetch": {"alias": "wire"}})
        self.assertEqual("plain-model", selection.resolve_requested("cielairouter", {}, "plain-model"))
        self.assertEqual(2, model_map.call_count)

    def test_configured_current_model_alias_resolves_without_catalog_or_cache(self):
        # An authoritative catalog with no cached list still names the configured
        # model; the B4 reproduction stalled 41 s here before the first open.
        alias = "ciel-runtime-cielairouter-owned-send"
        selection, model_map = self.selection({"local": {}, "fetch": {}})
        self.assertEqual("owned-send", selection.resolve_requested("cielairouter", {"current_model": "owned-send"}, alias))
        model_map.assert_called_once_with("cielairouter", {"current_model": "owned-send"}, fetch=False)

    def test_missing_requested_model_never_fetches(self):
        selection, model_map = self.selection({"local": {}, "fetch": {}})
        self.assertEqual("configured", selection.resolve_requested("cielairouter", {"current_model": "configured"}, None))
        model_map.assert_called_once_with("cielairouter", {"current_model": "configured"}, fetch=False)


if __name__ == "__main__":
    unittest.main()
