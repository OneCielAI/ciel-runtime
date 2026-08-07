import unittest

import ciel_runtime
from ciel_runtime_support.tool_schema import (
    _missing_required_tool_fields,
    _validate_and_fix_tool_input,
)


# Captured from a stalled routed session (ollama-cloud/deepseek-v4-flash:0731).
# The model deleted an import line, which Edit expresses as new_string: "".
DELETE_LINE_EDIT = {
    "replace_all": False,
    "file_path": "/home/ca-samuel/perp-dashboard-frontend/components/views/chart-view.tsx",
    "old_string": 'import { TickerEnvPanel } from "@/components/terminal/ticker-env-panel"',
    "new_string": "",
}


class MissingRequiredToolFieldTests(unittest.TestCase):
    def test_supplied_empty_string_satisfies_required_field(self):
        self.assertEqual([], _missing_required_tool_fields("Edit", DELETE_LINE_EDIT))

    def test_absent_required_field_is_reported(self):
        omitted = {"file_path": "/tmp/a.txt", "old_string": "abc"}

        self.assertEqual(["new_string"], _missing_required_tool_fields("Edit", omitted))

    def test_write_accepts_empty_content(self):
        self.assertEqual(
            [],
            _missing_required_tool_fields(
                "Write", {"file_path": "/tmp/empty.txt", "content": ""}
            ),
        )

    def test_repair_injection_does_not_hide_an_omitted_field(self):
        omitted = {"file_path": "/tmp/a.txt", "old_string": "abc"}
        repaired = _validate_and_fix_tool_input("Edit", dict(omitted))

        # The repair layer invents a placeholder, so completeness has to be
        # judged against what the model itself emitted.
        self.assertEqual("", repaired["new_string"])
        self.assertEqual([], _missing_required_tool_fields("Edit", repaired))
        self.assertEqual(["new_string"], _missing_required_tool_fields("Edit", omitted))


class DropEmittedToolCallTests(unittest.TestCase):
    def test_keeps_delete_edit_that_supplies_an_empty_new_string(self):
        repaired = _validate_and_fix_tool_input("Edit", dict(DELETE_LINE_EDIT))

        self.assertFalse(
            ciel_runtime.should_drop_emitted_tool_call(
                "Edit", repaired, "Edit", None, DELETE_LINE_EDIT
            )
        )

    def test_drops_edit_that_never_supplied_new_string(self):
        omitted = {"file_path": "/tmp/a.txt", "old_string": "abc"}
        repaired = _validate_and_fix_tool_input("Edit", dict(omitted))

        self.assertTrue(
            ciel_runtime.should_drop_emitted_tool_call(
                "Edit", repaired, "Edit", None, omitted
            )
        )

    def test_without_supplied_input_repaired_call_is_still_kept(self):
        repaired = _validate_and_fix_tool_input("Edit", dict(DELETE_LINE_EDIT))

        self.assertFalse(
            ciel_runtime.should_drop_emitted_tool_call("Edit", repaired, "Edit", None)
        )


if __name__ == "__main__":
    unittest.main()
