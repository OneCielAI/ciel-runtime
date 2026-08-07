import unittest

from ciel_runtime_support.tool_schema import _validate_and_fix_tool_input


class ToolSchemaNumericTypeTests(unittest.TestCase):
    def test_shell_command_timeout_ms_integral_float_becomes_integer(self):
        fixed = _validate_and_fix_tool_input(
            "shell_command",
            {"command": "cargo check", "timeout_ms": 120000.0},
        )

        self.assertEqual(120000, fixed["timeout_ms"])
        self.assertIs(type(fixed["timeout_ms"]), int)

    def test_unknown_custom_tool_normalizes_nested_integral_floats(self):
        fixed = _validate_and_fix_tool_input(
            "custom_tool",
            {"count": 3.0, "nested": {"offset": 4.0}, "ratio": 0.25},
        )

        self.assertIs(type(fixed["count"]), int)
        self.assertIs(type(fixed["nested"]["offset"]), int)
        self.assertIs(type(fixed["ratio"]), float)

    def test_client_number_schema_cannot_reintroduce_integral_timeout_float(self):
        source_body = {
            "tools": [
                {
                    "name": "shell_command",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "timeout_ms": {"type": "number"},
                        },
                    },
                }
            ]
        }

        fixed = _validate_and_fix_tool_input(
            "shell_command",
            {"command": "cargo check", "timeout_ms": 120000},
            source_body,
        )

        self.assertEqual(120000, fixed["timeout_ms"])
        self.assertIs(type(fixed["timeout_ms"]), int)


if __name__ == "__main__":
    unittest.main()
