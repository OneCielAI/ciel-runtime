import unittest
from unittest import mock

from ciel_runtime_support.llm_config_http import (
    LlmConfigHttpController,
    LlmConfigHttpIO,
    LlmConfigIdentity,
    LlmConfigMutations,
    LlmConfigPanels,
)


class LlmConfigHttpControllerTests(unittest.TestCase):
    def controller(self):
        cfg = {"language": "en", "providers": {"openai": {"current_model": "gpt", "advisor_model": "mini"}}}
        identity = LlmConfigIdentity(
            lambda: cfg,
            lambda value: ("openai", value["providers"]["openai"]),
            lambda _value: "alias",
            lambda _provider, _config: "balanced",
            lambda _provider, _config: "32k",
            lambda _config, _language: "normal",
            {"openai": "OpenAI"},
        )
        panels = LlmConfigPanels(
            lambda *_args: (["Temperature", "Back"], ["temperature", "back"]),
            lambda *_args: "0.5",
            lambda *_args: (["Balanced"], ["balanced"]),
            lambda *_args: (["32k"], ["32k"]),
            lambda *_args: (["Normal"], ["normal"]),
        )
        mutations = LlmConfigMutations(*(mock.Mock(return_value=[name]) for name in (
            "model", "advisor", "preset", "context", "timeout", "option"
        )))
        io = LlmConfigHttpIO(mock.Mock(), mock.Mock(), mock.Mock())
        return LlmConfigHttpController(identity, panels, mutations, io), mutations, io

    def test_payload_projects_identity_and_filters_navigation_rows(self):
        controller, _mutations, _io = self.controller()
        payload = controller.payload(["saved"])

        self.assertEqual("OpenAI", payload["provider_label"])
        self.assertEqual("alias", payload["alias"])
        self.assertEqual([{"label": "Temperature", "key": "temperature", "value": "0.5"}], payload["options"])
        self.assertEqual(["saved"], payload["messages"])

    def test_post_dispatches_option_and_publishes_event(self):
        controller, mutations, io = self.controller()
        handler = object()

        self.assertTrue(controller.handle_post(handler, controller.PATH, {"key": "temperature", "value": "0.7"}))

        mutations.set_option.assert_called_once_with("openai", "temperature", "0.7")
        io.publish_event.assert_called_once()
        self.assertEqual(["option"], io.write_json.call_args.args[1]["messages"])

    def test_post_reports_missing_option_key_as_bad_request(self):
        controller, _mutations, io = self.controller()

        self.assertTrue(controller.handle_post(object(), controller.PATH, {"action": "option"}))

        self.assertEqual(400, io.write_json.call_args.args[2])
        self.assertEqual("Missing option key", io.write_json.call_args.args[1]["error"])


if __name__ == "__main__":
    unittest.main()
