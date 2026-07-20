import json
import tempfile
import threading
import unittest
from pathlib import Path

from ciel_runtime_support.rate_limit_repository import RateLimitRepository
from ciel_runtime_support.router_rate_limit_service import (
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

    def test_provider_rate_key_is_global_and_legacy_key_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _ = self.service(Path(tmp))
            self.assertEqual("openrouter:__global__", service.key("openrouter", {}, "model"))
            self.assertEqual("openrouter:current", service.legacy_key("openrouter", {}, None))

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
