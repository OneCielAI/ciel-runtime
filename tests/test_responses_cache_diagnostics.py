import unittest

from ciel_runtime_support.responses_cache_diagnostics import (
    cache_trace,
    request_cache_profile,
    usage_with_cache_profile,
)


class ResponsesCacheDiagnosticsTests(unittest.TestCase):
    def test_profiles_cache_relevant_request_shape_without_raw_content(self):
        body = {
            "instructions": "private instructions",
            "input": [{"role": "user", "content": "private input"}],
            "tools": [{"type": "function", "name": "read"}],
            "prompt_cache_key": "private-thread-key",
            "prompt_cache_retention": "24h",
        }

        profile = request_cache_profile(body, 1234)

        self.assertEqual("24h", profile["prompt_cache_retention"])
        self.assertEqual(1, profile["request_input_items"])
        self.assertEqual(1, profile["request_tools"])
        self.assertEqual(1234, profile["request_bytes"])
        self.assertNotIn("private", str(profile))

    def test_usage_calculates_hit_rate_and_low_hit_trace(self):
        profile = request_cache_profile(
            {"input": "hello", "prompt_cache_key": "thread"}, 100
        )
        observation = usage_with_cache_profile(
            {
                "input_tokens": 1000,
                "cache_read_tokens": 800,
                "uncached_input_tokens": 200,
            },
            profile,
        )

        self.assertEqual(80.0, observation["cache_hit_percent"])
        level, message = cache_trace("meta", "muse", observation)
        self.assertEqual("WARN", level)
        self.assertIn("cache_hit_percent=80.0", message)


if __name__ == "__main__":
    unittest.main()
