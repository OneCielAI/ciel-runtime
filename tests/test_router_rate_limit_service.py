import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.rate_limit_repository import RateLimitRepository
from ciel_runtime_support.remote_bridge import REQUEST_API_KEY_MARKER
from ciel_runtime_support.router_rate_limit_service import (
    RouterRateLimitApi,
    RouterRateLimitPaths,
    RouterRateLimitPorts,
    RouterRateLimitService,
)


class RouterRateLimitServiceTests(unittest.TestCase):
    def service(self, root, *, key_count=1, now=lambda: 100.0, sleep=lambda _value: None):
        state_path = root / "rate-limit.json"
        lock = threading.RLock()
        logs = []
        repository = RateLimitRepository(root, state_path, lock, lambda *entry: logs.append(entry))
        service = RouterRateLimitService(
            paths=RouterRateLimitPaths(root, state_path, lock),
            repository=repository,
            ports=RouterRateLimitPorts(
                current_model_id=lambda _provider, _config: "current",
                api_key_count=lambda _provider, _config: key_count,
                positive_int=lambda value: int(value) if int(value) > 0 else None,
                log=lambda *entry: logs.append(entry),
                now=now,
                sleep=sleep,
            ),
        )
        return service, state_path, logs

    def test_explicit_api_delegates_with_public_keyword_names(self):
        service = mock.create_autospec(RouterRateLimitService, instance=True)
        service.key.return_value = "provider:__global__"
        service.capacity.return_value = 9
        service.apply.return_value = (0.0, 1, 10)
        api = RouterRateLimitApi(lambda: service)

        self.assertEqual(
            "provider:__global__",
            api.key(provider="provider", pcfg={}, model="model"),
        )
        self.assertEqual(9, api.capacity(rpm=10))
        self.assertEqual(
            (0.0, 1, 10),
            api.apply(provider="provider", pcfg={}, model="model"),
        )

    def test_provider_rate_key_is_global_and_legacy_key_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _ = self.service(Path(tmp))
            self.assertEqual("openrouter:__global__", service.key("openrouter", {}, "model"))
            self.assertEqual("openrouter:current", service.legacy_key("openrouter", {}, None))

    def test_request_scoped_key_never_reuses_router_host_global_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _ = self.service(Path(tmp))
            request_config = {REQUEST_API_KEY_MARKER: True, "rpm": 1}

            self.assertEqual(
                "openrouter:__request_scoped__",
                service.key("openrouter", request_config, "model"),
            )
            self.assertNotEqual(
                service.key("openrouter", {}, "model"),
                service.key("openrouter", request_config, "model"),
            )
            self.assertIsNone(service.configured_rpm("openrouter", request_config))
            self.assertIsNone(service.effective_rpm("openrouter", request_config, "model"))
            self.assertEqual({}, service.state_entry("openrouter", request_config, "model"))

    def test_request_scoped_learning_and_usage_do_not_mutate_host_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, state_path, _ = self.service(Path(tmp))
            original = {"openrouter:__global__": {"server_rpm": 77, "timestamps": [99.0]}}
            state_path.write_text(json.dumps(original), encoding="utf-8")
            request_config = {REQUEST_API_KEY_MARKER: True}

            service.learn_headers(
                "openrouter",
                request_config,
                "model",
                {"x-ratelimit-limit-requests": "1"},
            )
            self.assertEqual((0, None), service.usage("openrouter", request_config, "model"))
            self.assertEqual(
                (0, None), service.record_usage("openrouter", request_config, "model", 1)
            )
            self.assertEqual((0.0, 0, None), service.apply("openrouter", request_config, "model"))

            self.assertEqual(original, json.loads(state_path.read_text(encoding="utf-8")))

    def test_request_scoped_backoff_honors_retry_after_without_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, state_path, logs = self.service(Path(tmp))
            original = {"openrouter:__global__": {"penalty_until": 999.0}}
            state_path.write_text(json.dumps(original), encoding="utf-8")
            request_config = {REQUEST_API_KEY_MARKER: True}

            self.assertEqual(
                12.5,
                service.register_backoff("openrouter", request_config, "model", "12.5"),
            )
            self.assertEqual(original, json.loads(state_path.read_text(encoding="utf-8")))
            self.assertTrue(any("request_scoped_no_persist=1" in entry[1] for entry in logs))

    def test_wait_for_penalty_uses_repository_state_and_injected_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = iter((100.0, 105.1))
            sleeps = []
            service, state_path, _ = self.service(
                Path(tmp), now=lambda: next(clock), sleep=sleeps.append
            )
            state_path.write_text(
                json.dumps({"openrouter:__global__": {"penalty_until": 105.0}}),
                encoding="utf-8",
            )
            self.assertEqual(5.0, service.wait_for_penalty("openrouter", {}, "model", 50))
            self.assertEqual([5.0], sleeps)

    def test_multiple_keys_ignore_provider_global_penalty(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, state_path, _ = self.service(Path(tmp), key_count=2)
            state_path.write_text(
                json.dumps({"openrouter:__global__": {"penalty_until": 999.0}}),
                encoding="utf-8",
            )
            self.assertEqual(0.0, service.wait_for_penalty("openrouter", {}, "model", 50))


if __name__ == "__main__":
    unittest.main()
