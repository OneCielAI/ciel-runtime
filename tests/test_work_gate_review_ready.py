"""Gate notices must not ask main to review results that have no review pin."""
import unittest
from ciel_runtime_support.work_gate_check import reviewable_items


class ReviewableItemsTests(unittest.TestCase):
    def test_only_undecided_published_reviews_count(self):
        state = {"reviews": {
            "a": {"scope": "s", "item_id": "n1", "state": "pending", "decision": None},
            "b": {"scope": "s", "item_id": "n1", "state": "pending", "decision": None},
            "c": {"scope": "s", "item_id": "n2", "state": "deciding", "decision": "approve"},
            "d": {"scope": "other", "item_id": "n3", "state": "pending", "decision": None},
        }}
        self.assertEqual(["n1"], reviewable_items(state, "s"))

    def test_submitted_without_published_review_is_not_review_ready(self):
        self.assertEqual([], reviewable_items({"reviews": {}}, "s"))

    def test_stranded_no_report_is_not_an_approvable_result(self):
        self.assertEqual([], reviewable_items({"rows": {
            "a": {"scope": "s", "item_id": "n1", "state": "pending", "decision_id": None}
        }}, "s"))


if __name__ == "__main__":
    unittest.main()
