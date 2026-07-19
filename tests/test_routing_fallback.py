import unittest

from ciel_runtime_support.routing_fallback import build_fallback_plan


class RoutingFallbackTests(unittest.TestCase):
    def test_reason_filters_and_deduplicates_candidates(self):
        plan = build_fallback_plan(
            "primary",
            "model-a",
            (
                {"provider": "backup", "model": "model-b", "reasons": ["rate_limit"]},
                {"provider": "BACKUP", "model": "model-b", "reasons": ["rate_limit"]},
                {"provider": "third", "model": "model-c", "reasons": ["timeout"]},
            ),
        )
        self.assertEqual(
            (("primary", "model-a"), ("backup", "model-b")),
            tuple(target.identity for target in plan.candidates("rate_limit")),
        )

    def test_default_reasons_exclude_model_errors(self):
        plan = build_fallback_plan("primary", "model-a", ({"provider": "backup", "model": "model-b"},))
        self.assertEqual(1, len(plan.candidates("model_error")))
        self.assertEqual(2, len(plan.candidates("unavailable")))

    def test_invalid_and_empty_targets_are_skipped(self):
        plan = build_fallback_plan("primary", "model-a", ({"provider": "backup", "model": ""},))
        self.assertEqual(1, len(plan.candidates("timeout")))


if __name__ == "__main__":
    unittest.main()
