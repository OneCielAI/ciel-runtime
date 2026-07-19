import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.usage_events import JsonlUsageEventSink, UsageEvent, summarize_usage


class UsageEventTests(unittest.TestCase):
    def test_jsonl_sink_normalizes_and_persists_safe_usage_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            JsonlUsageEventSink(path, clock=lambda: 123.0).record(
                UsageEvent("openrouter", "model", input_tokens=-1, output_tokens=7)
            )
            row = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(0, row["input_tokens"])
            self.assertEqual(7, row["output_tokens"])
            self.assertEqual(123.0, row["timestamp"])
            self.assertNotIn("api_key", row)

    def test_disabled_sink_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            JsonlUsageEventSink(path, enabled=lambda: False).record(UsageEvent("p", "m"))
            self.assertFalse(path.exists())

    def test_summary_groups_provider_and_model(self):
        result = summarize_usage(
            [UsageEvent("p", "m", 2, 3), UsageEvent("p", "m", 5, 7), UsageEvent("p", "other", 1, 1)]
        )
        self.assertEqual({"requests": 2, "input_tokens": 7, "output_tokens": 10}, result[("p", "m")])


if __name__ == "__main__":
    unittest.main()
