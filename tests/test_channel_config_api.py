import unittest
from unittest import mock

from ciel_runtime_support.channel_config_service import (
    ChannelConfigApi,
    ChannelConfigService,
)


class ChannelConfigApiTests(unittest.TestCase):
    def test_explicit_api_preserves_public_contract(self):
        service = mock.create_autospec(ChannelConfigService, instance=True)
        service.parse_passthrough.return_value = ["server:test"]
        service.auto_import.return_value = ["server:test"]
        service.launch_specs.return_value = ["server:test"]
        service.is_tagged.return_value = True
        service.normalize_delivery.return_value = "llm"
        service.delivery_mode.return_value = "native"
        service.set_delivery.return_value = ["delivery"]
        service.add.return_value = ["added"]
        service.remove.return_value = ["removed"]
        service.clear.return_value = ["cleared"]
        api = ChannelConfigApi(lambda: service)
        config = {"claude_code": {}}

        self.assertEqual(
            ["server:test"],
            api.parse_passthrough_channel_specs(passthrough=["--channels"]),
        )
        self.assertEqual(
            ["server:test"],
            api.auto_import_passthrough_channels(passthrough=["--channels"]),
        )
        self.assertEqual(
            ["server:test"],
            api.channel_specs_for_launch(
                cfg=config,
                passthrough=["ignored-by-existing-contract"],
                extra_specs=["server:extra"],
            ),
        )
        self.assertTrue(api.is_channel_spec_tagged(spec="server:test"))
        self.assertEqual("llm", api.normalize_channel_delivery(value="llm"))
        self.assertEqual("native", api.channel_delivery_mode(cfg=config))
        self.assertEqual(["delivery"], api.set_channel_delivery_config(value="native"))
        self.assertEqual(
            ["added"], api.add_channel_spec(spec="server:test", development=True)
        )
        self.assertEqual(["removed"], api.remove_channel_spec(spec="server:test"))
        self.assertEqual(["cleared"], api.clear_channel_specs())
        service.launch_specs.assert_called_once_with(config, ["server:extra"])
        service.add.assert_called_once_with("server:test")


if __name__ == "__main__":
    unittest.main()
