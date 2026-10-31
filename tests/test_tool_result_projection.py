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
            result_limit=12000,
        )

    def test_error_and_success_results_have_distinct_next_step_guidance(self):
        error_text, error_summary = project_tool_result(
            "Read", "file.py", "missing", True, self.services()
        )
        success_text, success_summary = project_tool_result(
            "Read", "file.py", "content", False, self.services()
        )

        self.assertIn("is_error=true", error_text)
        self.assertEqual("", error_summary)
        self.assertIn("is_error=false", success_text)
        self.assertEqual("", success_summary)

    def test_pending_running_and_failure_text_are_not_promoted_to_success(self):
        for value in ("pending", "running", "Process exited with code 2", '{"ok":false}'):
            with self.subTest(value=value):
                text, summary = project_tool_result("Bash", "health", value, False, self.services())
                self.assertIn(value, text)
                self.assertNotIn("completed successfully", text + summary)
                self.assertNotIn("Do not", text + summary)
                self.assertEqual("", summary)

    def test_long_output_keeps_head_tail_and_omission_count(self):
        from ciel_runtime_support.protocols.tool_result_projection import truncate_tool_result
        value = "BEGIN" + chr(10) + "x" * 24000 + chr(10) + "FAILED tests=2" + chr(10) + "Exit code: 2"
        result = truncate_tool_result(value, 12000)
        self.assertTrue(result.startswith("BEGIN" + chr(10)))
        self.assertTrue(result.endswith("FAILED tests=2" + chr(10) + "Exit code: 2"))
        self.assertIn("omitted", result)
        self.assertLessEqual(len(result), 12000)
        self.assertEqual("short", truncate_tool_result("short", 12000))

    def test_omission_marker_matches_missing_characters(self):
        import re
        from ciel_runtime_support.protocols.tool_result_projection import truncate_tool_result
        for size in (99, 100, 999, 1000, 9999, 10000, 99999):
            value = "x" * size
            for limit in (0, 4, 99, 100, 12000):
                result = truncate_tool_result(value, limit)
                self.assertLessEqual(len(result), limit)
                match = re.search(r"omitted: +(\d+) chars", result)
                if match:
                    self.assertEqual(int(match.group(1)), size - result.count("x"))

    def test_unchanged_read_can_restore_prior_authoritative_result(self):
        tool_text, summary = project_tool_result(
            "Read",
            "file.py",
            "unchanged",
            False,
            self.services(unchanged=True),
            prior_success_text="cached-head" + "x" * 24000 + "cached-tail",
            include_prior_success=True,
            in_plan_mode=True,
        )

        self.assertIn("cached-head", tool_text)
        self.assertIn("cached-tail", tool_text)
        self.assertIn("omitted", tool_text)
        self.assertIn("ExitPlanMode", summary)


if __name__ == "__main__":
    unittest.main()
