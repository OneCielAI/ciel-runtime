import unittest

from ciel_runtime_support.protocols.tool_result_projection import (
    ToolResultProjectionServices,
    project_tool_result,
)


class ToolResultProjectionTests(unittest.TestCase):
    def services(self, unchanged=False):
        return ToolResultProjectionServices(
            is_read_unchanged=lambda _name, _result: unchanged,
            truncate=lambda text, limit: text[:limit],
            result_limit=5,
        )

    def test_error_and_success_results_have_distinct_next_step_guidance(self):
        error_text, error_summary = project_tool_result(
            "Read", "file.py", "missing", True, self.services()
        )
        success_text, success_summary = project_tool_result(
            "Read", "file.py", "content", False, self.services()
        )

        self.assertIn("failed", error_text)
        self.assertIn("different next step", error_summary)
        self.assertIn("completed successfully", success_text)
        self.assertIn("authoritative", success_summary)

    def test_unchanged_read_can_restore_prior_authoritative_result(self):
        tool_text, summary = project_tool_result(
            "Read",
            "file.py",
            "unchanged",
            False,
            self.services(unchanged=True),
            prior_success_text="abcdefgh",
            include_prior_success=True,
            in_plan_mode=True,
        )

        self.assertIn("abcde", tool_text)
        self.assertNotIn("abcdef", tool_text)
        self.assertIn("ExitPlanMode", summary)


if __name__ == "__main__":
    unittest.main()
