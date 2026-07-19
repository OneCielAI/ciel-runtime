import unittest
from unittest import mock

from ciel_runtime_support import prelaunch_terminal


class PrelaunchTerminalPrimitiveTests(unittest.TestCase):
    def test_cell_width_accounts_for_wide_and_combining_characters(self):
        self.assertEqual(4, prelaunch_terminal.cell_width("A한e\u0301"))

    def test_fit_cells_preserves_physical_terminal_width(self):
        self.assertEqual("한...", prelaunch_terminal.fit_cells("한글AB", 5))
        self.assertEqual(5, prelaunch_terminal.cell_width(prelaunch_terminal.pad_cells("한", 5)))

    def test_intro_panel_clamps_width_and_contains_brand(self):
        lines = prelaunch_terminal.intro_panel_lines(20, "Ciel Runtime", "Credits")
        self.assertTrue(all(len(line) == 48 for line in (lines[0], lines[-1])))
        self.assertIn("Ciel Runtime", "\n".join(lines))

    def test_animated_text_is_plain_when_output_is_not_tty(self):
        with mock.patch.object(prelaunch_terminal.sys.stdout, "isatty", return_value=False):
            self.assertEqual("Ciel", prelaunch_terminal.animated_ansi_text("Ciel", phase=0))


if __name__ == "__main__":
    unittest.main()
