import unittest
from unittest import mock

from ciel_runtime_support.provider_model_selection import (
    ProviderModelSelection,
    ProviderModelSelectionApi,
)


class ProviderModelSelectionApiTests(unittest.TestCase):
    def test_explicit_api_preserves_keyword_only_refresh_contract(self):
        selection = mock.create_autospec(ProviderModelSelection, instance=True)
        selection.current_upstream_id.return_value = "model"
        selection.ensure_selected.return_value = (True, ["selected"])
        selection.list_objects_for_request.return_value = [{"id": "model"}]
        selection.selection = mock.Mock()
        selection.selection.placeholders.return_value = {"placeholder"}
        api = ProviderModelSelectionApi(lambda: selection)
        config = {"current_model": "model"}

        self.assertEqual(
            "model",
            api.current_upstream_model_id(provider="test", pcfg=config),
        )
        self.assertEqual(
            {"placeholder"}, api.provider_placeholder_model_ids(provider="test")
        )
        self.assertEqual(
            (True, ["selected"]),
            api.ensure_current_model_from_provider_list(
                provider="test", pcfg=config, force_refresh=True
            ),
        )
        self.assertEqual(
            [{"id": "model"}],
            api.list_model_objects_for_request(
                provider="test", pcfg=config, inbound_headers={"x-test": "1"}
            ),
        )
        selection.ensure_selected.assert_called_once_with(
            "test", config, force_refresh=True
        )


if __name__ == "__main__":
    unittest.main()
