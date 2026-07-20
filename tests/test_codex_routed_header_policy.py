import unittest

from ciel_runtime_support.router_http import CodexRoutedHeaderPolicy


class CodexRoutedHeaderPolicyTests(unittest.TestCase):
    def policy(self):
        return CodexRoutedHeaderPolicy(
            decorate=lambda headers: {**headers, "user-agent": "ciel"},
        )

    def test_projects_native_auth_and_removes_transport_headers(self):
        headers = self.policy().project(
            {
                "Authorization": "Bearer native",
                "ChatGPT-Account-ID": "account-1",
                "Host": "localhost",
                "Content-Length": "10",
                "Accept-Encoding": "gzip",
            }
        )

        self.assertEqual("Bearer native", headers["Authorization"])
        self.assertEqual(
            "account-1",
            headers["ChatGPT-Account-ID"],
        )
        self.assertEqual("identity", headers["accept-encoding"])
        self.assertEqual("application/json", headers["content-type"])
        self.assertEqual("ciel", headers["user-agent"])
        self.assertNotIn("Host", headers)
        self.assertNotIn("Content-Length", headers)

    def test_preserves_explicit_content_type_case_insensitively(self):
        headers = self.policy().project(
            {
                "authorization": "Bearer native",
                "Content-Type": "application/json; charset=utf-8",
            }
        )

        self.assertEqual(
            "application/json; charset=utf-8",
            headers["content-type"],
        )

    def test_missing_native_authorization_is_rejected(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "native Codex auth headers",
        ):
            self.policy().project({"Content-Type": "application/json"})


if __name__ == "__main__":
    unittest.main()
