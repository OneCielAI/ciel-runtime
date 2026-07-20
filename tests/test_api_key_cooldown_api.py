import unittest

from ciel_runtime_support.api_key_cooldown import (
    ApiKeyCooldownCompatibilityApi,
)


class FakeCooldownService:
    def __init__(self, value):
        self.value = value

    def state_key(self, _provider, _config, _key):
        return self.value


class ApiKeyCooldownCompatibilityApiTests(unittest.TestCase):
    def test_service_factory_is_resolved_for_each_call(self):
        services = iter(
            [FakeCooldownService("first"), FakeCooldownService("second")]
        )
        api = ApiKeyCooldownCompatibilityApi(lambda: next(services))

        self.assertEqual("first", api.state_key("provider", {}, "key"))
        self.assertEqual("second", api.state_key("provider", {}, "key"))

    def test_retry_after_is_compared_with_request_timeout_margin(self):
        self.assertEqual(
            (True, 10.0),
            ApiKeyCooldownCompatibilityApi.retry_after_exceeds_request_timeout(
                {"Retry-After": "10"}, 10.0
            ),
        )
        self.assertEqual(
            (False, None),
            ApiKeyCooldownCompatibilityApi.retry_after_exceeds_request_timeout(
                {}, 10.0
            ),
        )


if __name__ == "__main__":
    unittest.main()
