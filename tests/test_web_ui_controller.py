import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.web_ui_controller import (
    WebUiConstants,
    WebUiController,
    WebUiDisplayPorts,
    WebUiHttpPorts,
    WebUiProjectionPorts,
)


class WebUiControllerTests(unittest.TestCase):
    def controller(self) -> WebUiController:
        self.output = mock.Mock()
        return WebUiController(
            constants=WebUiConstants(
                "1.0",
                Path("activity.json"),
                Path("context.json"),
                120_000,
            ),
            projection=WebUiProjectionPorts(
                current_alias=lambda _config: "alias-model",
                read_json=lambda path: (
                    {"tokens": 50, "percent": 25}
                    if path.name == "context.json"
                    else {"event": "complete"}
                ),
                rate_limit_usage=lambda _provider, _config: (2, 10),
                positive_int=lambda value: (
                    int(value)
                    if value is not None and int(value) > 0
                    else None
                ),
                idle_timeout_ms=lambda timeout: timeout // 2,
                context_limit=lambda _provider, _config: 200,
            ),
            display=WebUiDisplayPorts(
                render_home=lambda **values: values,
                render_chat=lambda **values: values,
                provider_mode=lambda _provider, _config: "router",
                api_key_status=lambda _provider, _config: "configured",
            ),
            http=WebUiHttpPorts(
                load_config=lambda: {
                    "current_provider": "provider",
                    "providers": {"provider": {}},
                },
                current_provider=lambda config: (
                    config["current_provider"],
                    config["providers"][config["current_provider"]],
                ),
                write_text=self.output,
            ),
        )

    def test_home_projection_formats_runtime_metrics(self):
        result = self.controller().render_router_home(
            {},
            "provider",
            {"rate_limit_status": True},
        )

        self.assertEqual("50/200 tok (25%)", result["context_text"])
        self.assertEqual("2/10", result["rpm_text"])
        self.assertEqual("complete · provider", result["upstream_text"])

    def test_chat_get_is_scoped_and_writes_html(self):
        controller = self.controller()

        self.assertFalse(controller.handle_get(object(), "/other"))
        self.assertTrue(controller.handle_get(object(), "/ca/web/chat"))

        self.output.assert_called_once()
        self.assertEqual(
            "text/html; charset=utf-8",
            self.output.call_args.kwargs["content_type"],
        )


if __name__ == "__main__":
    unittest.main()
