import unittest

from ciel_runtime_support.request_shortcuts import (
    ShortcutTextServices,
    import_session_args,
    live_api_keys_value,
    live_option_value,
    parse_channel_bridge_args,
)


SERVICES = ShortcutTextServices(latest_user_text=lambda body: str(body.get("text") or ""))


class RequestShortcutTests(unittest.TestCase):
    def test_channel_command_parses_loose_message(self):
        self.assertEqual(("send", {"message": "hello world"}), parse_channel_bridge_args("send hello world"))

    def test_live_option_ignores_template_placeholder(self):
        body = {"text": "MARKER\nValue: $ARGUMENTS\nArguments: coding"}
        self.assertEqual("coding", live_option_value(body, ("MARKER",), SERVICES))

    def test_api_keys_preserve_multiline_arguments(self):
        body = {"text": "MARKER\nArguments:\nsk-one\nsk-two"}
        self.assertEqual("sk-one\nsk-two", live_api_keys_value(body, ("MARKER",), SERVICES))

    def test_import_session_supports_quoted_path(self):
        body = {"text": 'MARKER\nArguments: Codex "C:/tmp/session file.jsonl"'}
        self.assertEqual(
            ("Codex", "C:/tmp/session file.jsonl"),
            import_session_args(body, ("MARKER",), SERVICES, posix=True),
        )


if __name__ == "__main__":
    unittest.main()
