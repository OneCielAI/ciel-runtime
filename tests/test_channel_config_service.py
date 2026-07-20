import unittest

from ciel_runtime_support.channel_config_service import (
    ChannelConfigPorts,
    ChannelConfigService,
)


class ChannelConfigServiceTests(unittest.TestCase):
    def service(self) -> ChannelConfigService:
        def dedupe(values):
            return list(dict.fromkeys(value for value in values if value))

        return ChannelConfigService(
            "server:ciel-runtime-router",
            ChannelConfigPorts(
                load=lambda: {},
                save=lambda _config: None,
                invalidate=lambda: None,
                dedupe=dedupe,
                log=lambda _level, _message: None,
                environment={},
            ),
        )

    def test_configured_specs_normalizes_scalar_and_preserves_builtin_first(self):
        service = self.service()

        self.assertEqual(
            ["server:ciel-runtime-router", "server:external"],
            service.configured_specs(
                {"claude_code": {"channels": "server:external"}}
            ),
        )

    def test_configured_specs_deduplicates_and_ignores_invalid_shape(self):
        service = self.service()

        self.assertEqual(
            ["server:ciel-runtime-router", "plugin:one"],
            service.configured_specs(
                {
                    "claude_code": {
                        "channels": [
                            "plugin:one",
                            "plugin:one",
                            "",
                        ]
                    }
                }
            ),
        )
        self.assertEqual(
            ["server:ciel-runtime-router"],
            service.configured_specs({"claude_code": {"channels": 7}}),
        )


if __name__ == "__main__":
    unittest.main()
