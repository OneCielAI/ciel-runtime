import unittest

from ciel_runtime_support.request_trace import RouterMessagePreviewPolicy


class RouterMessagePreviewPolicyTests(unittest.TestCase):
    def policy(self, environment=None) -> RouterMessagePreviewPolicy:
        return RouterMessagePreviewPolicy(
            environ=environment or {},
            load_config=lambda: {
                "router_debug_message_preview_chars": 8
            },
            positive_int=lambda value: (
                int(value) if value and int(value) > 0 else None
            ),
            latest_user_text=lambda body: str(body.get("text") or ""),
            redact_text=lambda text: text.replace("secret", "[redacted]"),
        )

    def test_projection_redacts_normalizes_and_truncates(self):
        result = self.policy().project(
            {"text": "hello\n secret"},
        )

        self.assertEqual("hello [r", result["message_preview"])
        self.assertTrue(result["message_preview_truncated"])

    def test_environment_limit_overrides_config(self):
        policy = self.policy(
            {"CIEL_RUNTIME_ROUTER_MESSAGE_PREVIEW_CHARS": "4"}
        )

        self.assertEqual(4, policy.configured_chars({}))


if __name__ == "__main__":
    unittest.main()
