import unittest

from ciel_runtime_support.headless_config import (
    HeadlessChannelCommands,
    HeadlessConfigCommands,
    HeadlessConfigServices,
    apply_headless_config,
)


class HeadlessConfigTests(unittest.TestCase):
    def services(self, env, calls):
        def record(name):
            return lambda *args: calls.append((name, *args))

        return HeadlessConfigServices(
            environ=env,
            parse_bool=lambda value, default: default if value is None else value.lower() in {"1", "true", "on"},
            current_provider=lambda: "deepseek",
            commands=HeadlessConfigCommands(
                set_language=record("language"),
                set_web_fetch=record("web_fetch"),
                set_provider=record("provider"),
                set_api_keys=record("api_keys"),
                set_api_key=record("api_key"),
                set_base_url=record("base_url"),
                set_model=record("model"),
                set_advisor_model=record("advisor"),
                set_provider_options=record("provider_options"),
                set_ollama_options=record("ollama_options"),
            ),
            channels=HeadlessChannelCommands(
                add_channel=record("channel"),
                set_delivery=record("delivery"),
            ),
        )

    def test_applies_commands_in_environment_contract(self):
        calls = []
        env = {
            "CIEL_RUNTIME_LANGUAGE": "ko",
            "CIEL_RUNTIME_PROVIDER": "deepseek",
            "CIEL_RUNTIME_API_KEY": "secret",
            "CIEL_RUNTIME_BASE_URL": "https://example.test",
            "CIEL_RUNTIME_MODEL": "model-1",
            "CIEL_RUNTIME_MAX_OUTPUT_TOKENS": "8192",
            "CIEL_RUNTIME_CHANNELS": "web, slack",
            "CIEL_RUNTIME_CHANNEL_DELIVERY": "stdin",
        }
        result = apply_headless_config(self.services(env, calls))

        self.assertTrue(result.skip_menu)
        self.assertEqual(
            [
                ("language", "ko"),
                ("provider", "deepseek"),
                ("api_key", "deepseek", "secret"),
                ("base_url", "deepseek", "https://example.test"),
                ("model", "model-1"),
                ("provider_options", ["max_output_tokens=8192"]),
                ("channel", "web"),
                ("channel", "slack"),
                ("delivery", "stdin"),
            ],
            calls,
        )

    def test_api_keys_env_has_priority_and_requires_value(self):
        calls = []
        env = {
            "CIEL_RUNTIME_API_KEYS_ENV": "PROVIDER_KEYS",
            "PROVIDER_KEYS": "one,two",
            "CIEL_RUNTIME_API_KEY": "ignored",
        }
        apply_headless_config(self.services(env, calls))
        self.assertEqual([("api_keys", "deepseek", ["one,two"])], calls)

        with self.assertRaisesRegex(SystemExit, "MISSING is empty or not set"):
            apply_headless_config(self.services({"CIEL_RUNTIME_API_KEYS_ENV": "MISSING"}, []))

    def test_preserves_overrides_without_forcing_menu(self):
        result = apply_headless_config(
            self.services(
                {
                    "CIEL_RUNTIME_WEB_SEARCH": "off",
                    "CIEL_RUNTIME_UPDATE_CHECK": "on",
                    "CIEL_RUNTIME_SELF_UPDATE_CHECK": "off",
                    "CIEL_RUNTIME_FORCE_MENU": "on",
                },
                [],
            )
        )

        self.assertEqual((False, False, True, False, True), result.as_tuple())


if __name__ == "__main__":
    unittest.main()
