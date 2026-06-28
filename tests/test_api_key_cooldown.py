import copy
import email.message
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime


def _headers(**fields):
    msg = email.message.Message()
    for k, v in fields.items():
        msg[k] = str(v)
    return msg


class RateLimitResetSecondsTests(unittest.TestCase):
    def test_millisecond_epoch(self):
        now = time.time()
        self.assertAlmostEqual(
            30.0, ciel_runtime.rate_limit_reset_seconds(str(int((now + 30) * 1000))), delta=2.0
        )

    def test_millisecond_epoch_near_term(self):
        now = time.time()
        self.assertAlmostEqual(
            5.0, ciel_runtime.rate_limit_reset_seconds(str(int((now + 5) * 1000))), delta=2.0
        )

    def test_seconds_epoch(self):
        now = time.time()
        self.assertAlmostEqual(
            90.0, ciel_runtime.rate_limit_reset_seconds(str(int(now + 90))), delta=2.0
        )

    def test_relative_seconds(self):
        self.assertEqual(45.0, ciel_runtime.rate_limit_reset_seconds("45"))

    def test_past_reset_is_zero(self):
        now = time.time()
        self.assertEqual(0.0, ciel_runtime.rate_limit_reset_seconds(str(int((now - 10) * 1000))))


class ApiKeyCooldownTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(ciel_runtime, "RATE_LIMIT_STATE_PATH", Path(self._tmp.name))
        self._patch.start()
        with ciel_runtime._API_KEY_ROTATION_LOCK:
            ciel_runtime._API_KEY_ROTATION_CURSOR.clear()

    def tearDown(self):
        self._patch.stop()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def pcfg(self, **overrides):
        p = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["openrouter"])
        p.update(overrides)
        return p

    def test_cooldown_from_x_ratelimit_reset_ms(self):
        now = time.time()
        rest = ciel_runtime.register_api_key_cooldown(
            "openrouter", self.pcfg(), "sk-k1",
            _headers(**{"X-RateLimit-Reset": str(int((now + 45) * 1000)), "X-RateLimit-Remaining": "0"}),
        )
        self.assertAlmostEqual(45.0, rest, delta=2.0)
        until = ciel_runtime.api_key_cooldown_until("openrouter", self.pcfg(), "sk-k1")
        self.assertAlmostEqual(45.0, until - now, delta=2.0)

    def test_cooldown_from_retry_after(self):
        rest = ciel_runtime.register_api_key_cooldown(
            "openrouter", self.pcfg(), "sk-k1", _headers(**{"Retry-After": "20"})
        )
        self.assertEqual(20.0, rest)

    def test_cooldown_default_when_no_headers(self):
        rest = ciel_runtime.register_api_key_cooldown("openrouter", self.pcfg(), "sk-k1", _headers())
        self.assertEqual(ciel_runtime.API_KEY_COOLDOWN_DEFAULT_SECONDS, rest)

    def test_cooldown_clamped_to_ceiling(self):
        rest = ciel_runtime.register_api_key_cooldown(
            "openrouter", self.pcfg(), "sk-k1", _headers(**{"Retry-After": "9999999"})
        )
        self.assertEqual(ciel_runtime.API_KEY_COOLDOWN_MAX_SECONDS, rest)

    def test_cooldown_covers_daily_quota_reset(self):
        # An RPD limit resets up to ~24h away; the ceiling must allow that.
        now = time.time()
        rest = ciel_runtime.register_api_key_cooldown(
            "openrouter", self.pcfg(), "sk-k1",
            _headers(**{"X-RateLimit-Reset": str(int((now + 80000) * 1000))}),
        )
        self.assertAlmostEqual(80000.0, rest, delta=5.0)

    def test_select_skips_cooled_key(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2", "sk-k3"])
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-k1", _headers(**{"Retry-After": "300"}))
        selected = {ciel_runtime.select_provider_api_key("openrouter", pcfg) for _ in range(6)}
        self.assertNotIn("sk-k1", selected)
        self.assertEqual({"sk-k2", "sk-k3"}, selected)

    def test_select_resumes_after_expiry(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2"])
        # cooldown in the past (already expired)
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-k1", _headers(**{"Retry-After": "1"}))
        time.sleep(1.1)
        selected = {ciel_runtime.select_provider_api_key("openrouter", pcfg) for _ in range(4)}
        self.assertIn("sk-k1", selected)

    def test_all_cooling_uses_soonest(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2", "sk-k3"])
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-k1", _headers(**{"Retry-After": "300"}))
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-k2", _headers(**{"Retry-After": "10"}))
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-k3", _headers(**{"Retry-After": "500"}))
        self.assertEqual("sk-k2", ciel_runtime.select_provider_api_key("openrouter", pcfg))

    def test_single_key_ignores_cooldown(self):
        pcfg = self.pcfg(api_key="sk-only")
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-only", _headers(**{"Retry-After": "300"}))
        self.assertEqual("sk-only", ciel_runtime.select_provider_api_key("openrouter", pcfg))

    def test_primary_api_key_unaffected_by_cooldown(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2"])
        ciel_runtime.register_api_key_cooldown("openrouter", pcfg, "sk-k1", _headers(**{"Retry-After": "300"}))
        self.assertEqual("sk-k1", ciel_runtime.provider_primary_api_key("openrouter", pcfg))

    def test_key_from_request_headers(self):
        self.assertEqual("sk-x", ciel_runtime.key_from_request_headers({"x-api-key": "sk-x"}))
        self.assertEqual("sk-y", ciel_runtime.key_from_request_headers({"authorization": "Bearer sk-y"}))

    def test_multi_key_rate_limit_headers_do_not_set_global_penalty(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2"])

        ciel_runtime.learn_router_rate_limit_headers(
            "openrouter",
            pcfg,
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            _headers(**{"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600"}),
        )

        state = json.loads(Path(self._tmp.name).read_text(encoding="utf-8"))
        entry = state["openrouter:__global__"]
        self.assertEqual(50, entry["server_rpm"])
        self.assertNotIn("penalty_until", entry)

    def test_rate_limit_headers_record_concurrency_and_queue_pressure(self):
        pcfg = self.pcfg(api_key="sk-k1", api_keys=[])

        ciel_runtime.learn_router_rate_limit_headers(
            "openrouter",
            pcfg,
            "model",
            _headers(
                **{
                    "X-RateLimit-Max-Concurrent": "10",
                    "X-RateLimit-Active": "9",
                    "X-RateLimit-Queue-Limit": "15",
                    "X-RateLimit-Queued": "14",
                }
            ),
        )

        state = json.loads(Path(self._tmp.name).read_text(encoding="utf-8"))
        entry = state["openrouter:__global__"]
        self.assertEqual(10, entry["server_max_concurrent"])
        self.assertEqual(9, entry["server_active"])
        self.assertEqual(15, entry["server_queue_limit"])
        self.assertEqual(14, entry["server_queued"])

    def test_multi_key_rate_limit_headers_clear_existing_global_penalty(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2"])
        Path(self._tmp.name).write_text(
            json.dumps({"openrouter:__global__": {"timestamps": [], "rpm": 50, "penalty_until": time.time() + 3600}}),
            encoding="utf-8",
        )

        ciel_runtime.learn_router_rate_limit_headers(
            "openrouter",
            pcfg,
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            _headers(**{"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600"}),
        )

        state = json.loads(Path(self._tmp.name).read_text(encoding="utf-8"))
        self.assertNotIn("penalty_until", state["openrouter:__global__"])

    def test_single_key_rate_limit_headers_keep_global_penalty(self):
        pcfg = self.pcfg(api_key="sk-k1", api_keys=[])

        ciel_runtime.learn_router_rate_limit_headers(
            "openrouter",
            pcfg,
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            _headers(**{"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600"}),
        )

        state = json.loads(Path(self._tmp.name).read_text(encoding="utf-8"))
        self.assertGreater(state["openrouter:__global__"]["penalty_until"], time.time())

    def test_disabled_rate_limit_still_honors_server_penalty(self):
        pcfg = self.pcfg(api_key="sk-k1", api_keys=[], rate_limit_rpm=0)
        Path(self._tmp.name).write_text(
            json.dumps({"openrouter:__global__": {"timestamps": [], "rpm": 0, "penalty_until": 105.0}}),
            encoding="utf-8",
        )

        with (
            mock.patch.object(ciel_runtime.time, "time", side_effect=[100.0, 105.1, 105.1, 105.1]),
            mock.patch.object(ciel_runtime.time, "sleep") as sleep,
        ):
            waited, used, rpm = ciel_runtime.apply_router_rate_limit("openrouter", pcfg, "model")

        self.assertEqual(5.0, waited)
        self.assertEqual(0, rpm)
        self.assertEqual(1, used)
        sleep.assert_called_once_with(5.0)

    def test_multi_key_apply_rate_limit_ignores_existing_global_penalty(self):
        pcfg = self.pcfg(api_key="", api_keys=["sk-k1", "sk-k2"], rate_limit_rpm=50)
        Path(self._tmp.name).write_text(
            json.dumps({"openrouter:__global__": {"timestamps": [], "rpm": 50, "penalty_until": time.time() + 3600}}),
            encoding="utf-8",
        )

        with mock.patch.object(ciel_runtime.time, "sleep") as sleep:
            waited, used, rpm = ciel_runtime.apply_router_rate_limit("openrouter", pcfg, "model")

        self.assertEqual(0.0, waited)
        self.assertEqual(50, rpm)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
