import unittest
from pathlib import Path

from ciel_runtime_support.architecture_budget import (
    FINAL_FILE_LINE_BUDGET,
    MAIN_FILE_LINE_BUDGET,
    architecture_budget_violations,
)


class ArchitectureBudgetTests(unittest.TestCase):
    def test_migration_budget_only_moves_toward_final_limit(self):
        self.assertGreater(MAIN_FILE_LINE_BUDGET, FINAL_FILE_LINE_BUDGET)
        self.assertEqual(4_999, FINAL_FILE_LINE_BUDGET)

    def test_no_file_exceeds_its_current_budget(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((), architecture_budget_violations(root))


if __name__ == "__main__":
    unittest.main()
