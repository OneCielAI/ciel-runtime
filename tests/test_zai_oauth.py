import copy
import unittest

import ciel_runtime
from ciel_runtime_support.zai_oauth import (
    ZaiOAuthClient,
    ZaiOAuthError,
    ZaiOAuthResult,
    ZaiOAuthRuntime,
    ZaiOAuthRuntimePorts,
    ZaiOAuthService,
)


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ZaiOAuthClientTests(unittest.TestCase):
    def test_init_poll_and_api_key_resolution_match_zcode_contract(self):
        http = FakeHttp([
            {"code": 0, "data": {"flow_id": "flow-1", "poll_token": "server-copy", "authorize_url": "https://chat.z.ai/oauth/flow-1", "expires_at": 2000, "poll_interval_sec": 2}},
            {"code": 0, "data": {"status": "pending"}},
            {"code": 0, "data": {"status": "ready", "token": "zcode-jwt", "zai": {"access_token": "oauth-access"}, "user": {"user_id": "user-1"}}},
            {"code": 0, "data": {"access_token": "business-token"}},
            {"code": 0, "data": {"organizations": [{"organizationId": "org-1", "organizationName": "默认机构", "projects": [{"projectId": "project-1", "projectName": "默认项目"}]}]}},
            {"code": 0, "data": []},
            {"code": 0, "data": {"apiKey": "key-id"}},
            {"code": 0, "data": {"secretKey": "key-secret"}},
        ])
        clock = iter([1000.0, 1000.0, 1000.0, 1002.0, 1002.0])
        sleeps = []
        urls = []
        result = ZaiOAuthService(
            ZaiOAuthClient(http),
            now=lambda: next(clock),
            sleep=sleeps.append,
            open_url=lambda url: urls.append(url) or True,
        ).login(on_authorize_url=lambda _url: None)

        self.assertEqual("key-id.key-secret", result.api_key)
        self.assertEqual("user-1", result.user_id)
        self.assertEqual(["https://chat.z.ai/oauth/flow-1"], urls)
        self.assertEqual([2.0], sleeps)
        self.assertEqual("Bearer business-token", http.calls[4][2]["headers"]["Authorization"])
        self.assertNotIn("oauth-access", repr(http.calls[-1]))

    def test_invalid_init_error_is_secret_free(self):
        client = ZaiOAuthClient(FakeHttp([{"code": 404, "msg": "secret-token"}]))
        with self.assertRaises(ZaiOAuthError) as raised:
            client.initialize("private-poll-token")
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertNotIn("private-poll-token", str(raised.exception))

    def test_authorization_code_callback_validates_state_and_exchanges_code(self):
        http = FakeHttp([{"code": 0, "data": {"token": "jwt", "zai": {"access_token": "access"}, "user": {"user_id": "user-1"}}}])
        client = ZaiOAuthClient(http)

        result = client.exchange_callback(
            "zcode://zai-auth/callback?code=auth-code&state=state-1", "state-1"
        )

        self.assertEqual("access", result["zai"]["access_token"])
        self.assertEqual("https://zcode.z.ai/api/v1/oauth/token", http.calls[0][1])
        self.assertEqual("auth-code", http.calls[0][2]["body"]["code"])

    def test_authorization_code_callback_rejects_wrong_state_before_network(self):
        http = FakeHttp([])
        with self.assertRaisesRegex(ZaiOAuthError, "state did not match"):
            ZaiOAuthClient(http).exchange_callback(
                "zcode://zai-auth/callback?code=auth-code&state=wrong", "expected"
            )
        self.assertEqual([], http.calls)


class ZaiOAuthRuntimeTests(unittest.TestCase):
    def config(self):
        return {"current_provider": "deepseek", "providers": {"zai": copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["zai"])}}

    def runtime(self, service, config, saved, output):
        return ZaiOAuthRuntime(service, ZaiOAuthRuntimePorts(
            load_config=lambda: config,
            save_config=lambda value: saved.append(copy.deepcopy(value)),
            clear_model_cache=lambda: None,
            mask=lambda value: f"masked:{value[-3:]}",
            fingerprint=lambda _value: "fp-test",
            output=lambda *values, **_kwargs: output.append(" ".join(map(str, values))),
        ))

    def test_login_commits_only_the_final_api_key(self):
        config, saved, output = self.config(), [], []

        class Service:
            def login(self, **kwargs):
                kwargs["on_authorize_url"]("https://chat.z.ai/authorize")
                return ZaiOAuthResult("key-id.key-secret", "user-1")

        messages = self.runtime(Service(), config, saved, output).action("login")
        provider = config["providers"]["zai"]
        self.assertEqual("zai", config["current_provider"])
        self.assertEqual("key-id.key-secret", provider["api_key"])
        self.assertEqual("zai-oauth", provider["credential_source"])
        self.assertEqual("user-1", provider["oauth_user_id"])
        self.assertEqual(1, len(saved))
        self.assertIn("completed", messages[0])
        self.assertNotIn("key-id.key-secret", "\n".join(output + messages))

    def test_failed_login_does_not_mutate_or_save_config(self):
        config, saved = self.config(), []
        before = copy.deepcopy(config)

        class Service:
            def login(self, **_kwargs):
                raise ZaiOAuthError("upstream unavailable")

        with self.assertRaisesRegex(ZaiOAuthError, "upstream unavailable"):
            self.runtime(Service(), config, saved, []).action("login")
        self.assertEqual(before, config)
        self.assertEqual([], saved)

    def test_logout_does_not_clear_a_manually_configured_key(self):
        config, saved = self.config(), []
        config["providers"]["zai"]["api_key"] = "manual-key"
        messages = self.runtime(None, config, saved, []).action("logout")
        self.assertEqual("manual-key", config["providers"]["zai"]["api_key"])
        self.assertEqual([], saved)
        self.assertIn("no OAuth-derived", messages[0])

    def test_init_404_falls_back_to_verified_authorization_code_contract(self):
        http = FakeHttp([
            ZaiOAuthError("init missing", http_status=404),
            {"code": 0, "data": {"token": "jwt", "zai": {"access_token": "oauth-access"}, "user": {"user_id": "user-1"}}},
            {"code": 0, "data": {"access_token": "business-token"}},
            {"code": 0, "data": {"organizations": [{"organizationId": "org-1", "projects": [{"projectId": "project-1"}]}]}},
            {"code": 0, "data": [{"name": "zcode-api-key", "apiKey": "key-id"}]},
            {"code": 0, "data": {"secretKey": "key-secret"}},
        ])
        urls = []
        service = ZaiOAuthService(
            ZaiOAuthClient(http),
            open_url=lambda _url: self.fail("--no-browser must not open a browser"),
            read_callback=lambda _prompt: "zcode://zai-auth/callback?code=auth-code&state=state-1",
            random_token=lambda: "state-1",
        )

        result = service.login(no_browser=True, on_authorize_url=urls.append)

        self.assertEqual("key-id.key-secret", result.api_key)
        self.assertIn("client_id=client_P8X5CMWmlaRO9gyO-KSqtg", urls[0])
        self.assertIn("state=state-1", urls[0])


if __name__ == "__main__":
    unittest.main()
