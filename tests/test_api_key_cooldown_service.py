import unittest
from unittest import mock

from ciel_runtime_support.api_key_cooldown import (
    ApiKeyCooldownPorts,
    ApiKeyCooldownService,
)


class ApiKeyCooldownServiceTests(unittest.TestCase):
    def service(self, repository=None, keys=None, logs=None, now=100.0):
        repository = repository or mock.Mock()
        logs = logs if logs is not None else []
        return ApiKeyCooldownService(
            ApiKeyCooldownPorts(
                repository=repository,
                rotation_name=lambda provider, _config: f"{provider}:endpoint",
                config_keys=lambda _provider, _config: list(keys or []),
                meaningful_key=lambda key: bool(str(key).strip()),
                log=lambda level, message: logs.append((level, message)),
                now=lambda: now,
            )
        )

    def test_reset_policy_prefers_reset_header_and_clamps_value(self):
        self.assertEqual(
            90_000.0,
            ApiKeyCooldownService.reset_seconds({"x-ratelimit-reset": "999999"}),
        )
        self.assertEqual(
            12.0,
            ApiKeyCooldownService.reset_seconds({"Retry-After": "12"}),
        )

    def test_register_hashes_secret_before_persisting_or_logging(self):
        repository = mock.Mock()
        logs = []
        service = self.service(repository=repository, logs=logs)

        self.assertEqual(15.0, service.register("openrouter", {}, "secret-key", {"Retry-After": "15"}))

        state_key, seconds = repository.register_cooldown.call_args.args
        self.assertIn(":__key__:", state_key)
        self.assertNotIn("secret-key", state_key)
        self.assertEqual(15.0, seconds)
        self.assertNotIn("secret-key", logs[0][1])

    def test_live_key_count_excludes_cooling_credentials(self):
        repository = mock.Mock()
        repository.cooldown_until.side_effect = [0.0, 150.0, 99.0]
        service = self.service(repository=repository, keys=["a", "b", "c"])
        self.assertEqual(2, service.live_key_count("provider", {}))


if __name__ == "__main__":
    unittest.main()
