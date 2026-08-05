import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.github_copilot_oauth import (
    COPILOT_CHAT_VERSION,
    COPILOT_VSCODE_VERSION,
    GITHUB_ACCESS_TOKEN_URL,
    GITHUB_COPILOT_TOKEN_URL,
    GitHubCopilotOAuthClient,
    GitHubCopilotOAuthCredentials,
    GitHubCopilotOAuthRepository,
    GitHubCopilotOAuthService,
)
from ciel_runtime_support.github_copilot_oauth_runtime import (
    GitHubCopilotOAuthRuntime,
    GitHubCopilotOAuthRuntimePorts,
)
from ciel_runtime_support.providers.github_copilot_oauth import (
    GITHUB_COPILOT_MODELS,
)


class GitHubCopilotOAuthProviderTests(unittest.TestCase):
    def provider_config(self):
        return dict(
            ciel_runtime.DEFAULT_CONFIG["providers"]["github-copilot-oauth"]
        )

    def test_oauth_provider_is_separate_from_api_key_provider(self):
        self.assertIn("github", ciel_runtime.DEFAULT_CONFIG["providers"])
        self.assertIn(
            "github-copilot-oauth",
            ciel_runtime.DEFAULT_CONFIG["providers"],
        )
        self.assertEqual(
            "github-copilot-oauth",
            ciel_runtime.PROVIDER_ALIASES["copilot-oauth"],
        )
        self.assertEqual(
            "GitHub Copilot OAuth",
            ciel_runtime.PROVIDER_LABELS["github-copilot-oauth"],
        )

    def test_provider_uses_copilot_headers_and_model_specific_protocols(self):
        config = self.provider_config()
        adapter = ciel_runtime.configured_provider_adapter(
            "github-copilot-oauth", config
        )
        contract = ciel_runtime.project_provider_contract_config(
            "github-copilot-oauth", config, ["copilot-token"]
        )

        headers = adapter.build_headers(contract, "copilot-token")

        self.assertEqual(
            "Bearer copilot-token", headers["authorization"]
        )
        self.assertEqual("vscode-chat", headers["copilot-integration-id"])
        self.assertIn("vscode/", headers["editor-version"])
        self.assertIn("copilot-chat/", headers["editor-plugin-version"])
        self.assertEqual("1.128.0", COPILOT_VSCODE_VERSION)
        self.assertEqual("0.43.0", COPILOT_CHAT_VERSION)
        self.assertNotIn("x-api-key", headers)
        self.assertEqual(
            "openai_responses",
            adapter.select_protocol(
                "openai_responses", contract, "gpt-5.4"
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            adapter.select_protocol(
                "openai_responses", contract, "claude-sonnet-4.6"
            ),
        )
        self.assertEqual(
            "openai_chat",
            adapter.select_protocol(
                "openai_responses", contract, "gemini-3.1-pro-preview"
            ),
        )

    def test_oauth_provider_ignores_regular_api_key_field(self):
        config = self.provider_config()
        config["api_key"] = "must-not-be-used"

        with mock.patch.object(
            ciel_runtime,
            "github_copilot_oauth_token",
            return_value="oauth-copilot-token",
        ):
            keys = ciel_runtime.provider_config_api_keys(
                "github-copilot-oauth", config
            )

        self.assertEqual(["oauth-copilot-token"], keys)

    def test_current_fallback_catalog_excludes_retired_models(self):
        self.assertEqual("gpt-5.6-sol", GITHUB_COPILOT_MODELS[0])
        self.assertIn("claude-opus-5", GITHUB_COPILOT_MODELS)
        self.assertIn("claude-opus-4.8", GITHUB_COPILOT_MODELS)
        self.assertIn("kimi-k2.7-code", GITHUB_COPILOT_MODELS)
        self.assertNotIn("gpt-5.2-codex", GITHUB_COPILOT_MODELS)
        self.assertNotIn("gemini-2.5-pro", GITHUB_COPILOT_MODELS)

    def test_account_catalog_does_not_merge_stale_configured_models(self):
        config = self.provider_config()
        config["current_model"] = "gpt-5.2-codex"
        config["custom_models"] = ["gpt-5.2-codex"]
        response = {
            "data": [
                {"id": "gpt-5.6-sol"},
                {"id": "claude-opus-4.8"},
            ]
        }

        with (
            mock.patch.object(
                ciel_runtime, "read_model_list_cache", return_value=None
            ),
            mock.patch.object(
                ciel_runtime,
                "github_copilot_oauth_token",
                return_value="copilot-token",
            ),
            mock.patch.object(
                ciel_runtime, "http_json", return_value=response
            ),
            mock.patch.object(ciel_runtime, "write_model_list_cache"),
        ):
            models = ciel_runtime.upstream_model_ids(
                "github-copilot-oauth", config, force_refresh=True
            )

        self.assertEqual(
            {"gpt-5.6-sol", "claude-opus-4.8"}, set(models)
        )

    def test_cli_parser_exposes_login_status_and_logout(self):
        for action in ("login", "status", "logout"):
            args = ciel_runtime.build_parser().parse_args(
                ["copilot-oauth", action]
            )
            self.assertEqual(action, args.action)
            self.assertIs(ciel_runtime.cmd_copilot_oauth, args.func)

    def test_prelaunch_credential_panel_exposes_only_oauth_actions(self):
        runtime = mock.Mock()
        runtime.panel_rows.return_value = (
            ["OAuth status: Connected as octocat"],
            [
                "oauth-status",
                "oauth-login",
                "oauth-status",
                "oauth-logout",
                "back",
            ],
        )

        with mock.patch.object(
            ciel_runtime,
            "github_copilot_oauth_runtime",
            return_value=runtime,
        ):
            rows, values = ciel_runtime.api_key_panel_rows(
                "github-copilot-oauth", self.provider_config()
            )

        self.assertIn("Connected as octocat", rows[0])
        self.assertEqual(
            [
                "oauth-status",
                "oauth-login",
                "oauth-status",
                "oauth-logout",
                "back",
            ],
            values,
        )
        self.assertNotIn("input", values)

    def test_launch_readiness_requires_oauth_instead_of_config_api_key(self):
        config = {
            "current_provider": "github-copilot-oauth",
            "providers": {
                "github-copilot-oauth": self.provider_config(),
            },
        }

        with mock.patch.object(
            ciel_runtime,
            "github_copilot_oauth_token",
            return_value="",
        ):
            errors = ciel_runtime.launch_readiness_errors(config)

        self.assertTrue(
            any(
                "ciel-runtimectl copilot-oauth login" in error
                for error in errors
            )
        )

    def test_upstream_401_forces_token_refresh_and_retries_once(self):
        error = urllib.error.HTTPError(
            "https://api.githubcopilot.com/chat/completions",
            401,
            "Unauthorized",
            {},
            None,
        )
        response = mock.Mock()
        request = urllib.request.Request(
            "https://api.githubcopilot.com/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer stale"},
            method="POST",
        )

        open_request = mock.Mock(side_effect=[error, response])
        runtime = GitHubCopilotOAuthRuntime(
            Path(tempfile.gettempdir()),
            GitHubCopilotOAuthRuntimePorts(
                clear_model_cache=mock.Mock(),
                log=mock.Mock(),
                provider_headers=lambda _provider, _config: {
                    "Authorization": "Bearer fresh-token"
                },
                network_open=open_request,
            ),
        )
        with mock.patch.object(
            GitHubCopilotOAuthRuntime,
            "force_refresh",
            return_value="fresh-token",
        ):
            result = runtime.open(
                request,
                30.0,
                "github-copilot-oauth",
                self.provider_config(),
            )

        self.assertIs(response, result)
        self.assertEqual(2, open_request.call_count)
        retry = open_request.call_args_list[1].args[0]
        self.assertEqual(
            "Bearer fresh-token", retry.get_header("Authorization")
        )


class GitHubCopilotOAuthServiceTests(unittest.TestCase):
    def test_repository_round_trip_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = GitHubCopilotOAuthRepository(
                Path(directory) / "oauth.json"
            )
            credentials = GitHubCopilotOAuthCredentials(
                github_access_token="github-token",
                copilot_token="copilot-token",
                copilot_token_expires_at=1_900_000_000,
                github_login="octocat",
            )

            repository.save(credentials)

            self.assertEqual(credentials, repository.load())
            self.assertTrue(repository.clear())
            self.assertEqual(
                GitHubCopilotOAuthCredentials(), repository.load()
            )

    def test_device_flow_handles_pending_and_exchanges_copilot_token(self):
        http = mock.Mock()
        http.post_form.side_effect = [
            {
                "device_code": "device",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "interval": 1,
                "expires_in": 60,
            },
            {"error": "authorization_pending"},
            {"access_token": "github-token"},
        ]
        http.get_json.return_value = {
            "token": "copilot-token",
            "expires_at": 1_900_000_000,
        }
        pending = []
        now_values = iter((100.0, 100.0, 101.0))
        client = GitHubCopilotOAuthClient(
            http=http,
            sleep=lambda _seconds: None,
            now=lambda: next(now_values),
        )

        device = client.request_device_code()
        access_token = client.poll_access_token(
            device, on_pending=pending.append
        )
        token = client.exchange_copilot_token(access_token)

        self.assertEqual("github-token", access_token)
        self.assertEqual(("copilot-token", 1_900_000_000), token)
        self.assertEqual([1], pending)
        exchange_url, exchange_headers = http.get_json.call_args.args
        self.assertEqual(GITHUB_COPILOT_TOKEN_URL, exchange_url)
        self.assertEqual(
            "token github-token", exchange_headers["Authorization"]
        )
        poll_calls = [
            call
            for call in http.post_form.call_args_list
            if call.args[0] == GITHUB_ACCESS_TOKEN_URL
        ]
        self.assertEqual(2, len(poll_calls))

    def test_current_token_reuses_fresh_token_without_network(self):
        repository = mock.Mock()
        repository.load.return_value = GitHubCopilotOAuthCredentials(
            github_access_token="github-token",
            copilot_token="fresh-token",
            copilot_token_expires_at=2_000,
        )
        client = mock.Mock()
        service = GitHubCopilotOAuthService(
            repository=repository,
            client=client,
            now=lambda: 1_000,
        )

        self.assertEqual("fresh-token", service.current_token())
        client.exchange_copilot_token.assert_not_called()
        repository.save.assert_not_called()

    def test_current_token_refreshes_expiring_token_and_persists_it(self):
        repository = mock.Mock()
        repository.load.return_value = GitHubCopilotOAuthCredentials(
            github_access_token="github-token",
            copilot_token="old-token",
            copilot_token_expires_at=1_100,
            github_login="octocat",
        )
        client = mock.Mock()
        client.exchange_copilot_token.return_value = (
            "new-token",
            2_000,
        )
        service = GitHubCopilotOAuthService(
            repository=repository,
            client=client,
            now=lambda: 1_000,
        )

        self.assertEqual("new-token", service.current_token())
        client.exchange_copilot_token.assert_called_once_with(
            "github-token"
        )
        saved = repository.save.call_args.args[0]
        self.assertEqual("new-token", saved.copilot_token)
        self.assertEqual("octocat", saved.github_login)

    def test_force_refresh_ignores_still_valid_cached_copilot_token(self):
        repository = mock.Mock()
        repository.load.return_value = GitHubCopilotOAuthCredentials(
            github_access_token="github-token",
            copilot_token="cached-token",
            copilot_token_expires_at=9_999,
            github_login="octocat",
        )
        client = mock.Mock()
        client.exchange_copilot_token.return_value = (
            "replacement-token",
            20_000,
        )
        service = GitHubCopilotOAuthService(
            repository=repository,
            client=client,
            now=lambda: 1_000,
        )

        self.assertEqual("replacement-token", service.force_refresh())
        client.exchange_copilot_token.assert_called_once_with(
            "github-token"
        )
        self.assertEqual(
            "replacement-token",
            repository.save.call_args.args[0].copilot_token,
        )


if __name__ == "__main__":
    unittest.main()
