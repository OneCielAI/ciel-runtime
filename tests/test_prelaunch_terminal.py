import contextlib
import io
import unittest
from unittest import mock

from ciel_runtime_support import prelaunch_terminal
from ciel_runtime_support.prelaunch_terminal import (
    PrelaunchInputStyle,
    PrelaunchRenderBrand,
    PrelaunchRenderData,
    PrelaunchRenderServices,
    PrelaunchRenderText,
)


class PrelaunchTerminalTests(unittest.TestCase):
    def test_visible_rows_keeps_selected_row_and_scroll_markers(self):
        rows = [f"row-{index}" for index in range(20)]

        visible = prelaunch_terminal.visible_rows(rows, selected=10, limit=6)

        self.assertIn((10, "row-10"), visible)
        self.assertIsNone(visible[0][0])
        self.assertIsNone(visible[-1][0])

    def test_renderer_uses_injected_data_and_text_ports(self):
        cfg = {"language": "en"}
        provider_cfg = {
            "base_url": "https://provider.example",
            "current_model": "model-a",
        }
        services = PrelaunchRenderServices(
            brand=PrelaunchRenderBrand(
                animated_ansi_text=lambda value: value,
                credits="credits",
                version="test-version",
            ),
            data=PrelaunchRenderData(
                api_key_status_line=lambda provider, pcfg: "api-key: configured",
                get_current_provider=lambda value: ("provider-a", provider_cfg),
                llm_option_description_for_value=lambda *args: "description",
                llm_option_panel_rows=lambda *args: ([], []),
                load_config=lambda: cfg,
                main_menu_rows=lambda *args: ["Launch Claude", "Quit"],
                provider_mode_label=lambda *args: "router",
            ),
            text=PrelaunchRenderText(
                ansi=lambda value, code=None: value,
                cell_width=len,
                fit_cells=lambda value, width: value[:width],
                pad_cells=lambda value, width: value[:width].ljust(width),
                ui_text=lambda key, language: "Quit" if key == "quit" else key,
            ),
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            result = prelaunch_terminal.render_prelaunch_screen(
                0, None, 0, [], [], [], True, services=services
            )

        self.assertFalse(result)
        rendered = output.getvalue()
        self.assertIn("Ciel Runtime vtest-version", rendered)
        self.assertIn("provider: provider-a", rendered)
        self.assertIn("model: model-a", rendered)

    def test_raw_input_returns_none_for_non_tty(self):
        style = PrelaunchInputStyle(ansi=lambda value, code=None: value, log=lambda *args: None)

        with mock.patch.object(prelaunch_terminal.sys, "stdin", io.StringIO("value")):
            result = prelaunch_terminal._prompt_menu_value_raw("value: ", style=style)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
