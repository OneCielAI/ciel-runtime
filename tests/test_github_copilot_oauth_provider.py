import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from io import BytesIO
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
    GITHUB_COPILOT_PUBLIC_MODEL_IDS,
    GITHUB_COPILOT_RESPONSES_ONLY_MODELS,
)
from ciel_runtime_support.remote_bridge import (
    PUBLIC_MODEL_ID_METADATA_KEY,
    REMOTE_BRIDGE_CONFIG_MARKER,
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
        self.assertEqual(
            "/chat/completions",
            adapter.resolve_endpoint("openai_chat", contract),
        )
        self.assertEqual(
            "/responses",
            adapter.resolve_endpoint("openai_responses", contract),
        )
        self.assertEqual(
            "https://api.githubcopilot.com/responses",
            ciel_runtime.provider_endpoint(
                "github-copilot-oauth",
                config,
                "openai_responses",
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

    def test_copilot_endpoint_metadata_selects_the_supported_wire(self):
        config = self.provider_config()
        adapter = ciel_runtime.configured_provider_adapter(
            "github-copilot-oauth", config
        )
        contract = ciel_runtime.project_provider_contract_config(
            "github-copilot-oauth", config, ["copilot-token"]
        )

        responses_only = replace(
            contract,
            options={
                **contract.options,
                "_ciel_model_metadata": {
                    "supported_endpoints": ["/responses", "ws:/responses"]
                },
            },
        )
        dual = replace(
            contract,
            options={
                **contract.options,
                "_ciel_model_metadata": {
                    "supported_endpoints": [
                        "/responses",
                        "/chat/completions",
                    ]
                },
            },
        )
        anthropic_and_chat = replace(
            contract,
            options={
                **contract.options,
                "_ciel_model_metadata": {
                    "supported_endpoints": [
                        "/v1/messages",
                        "/chat/completions",
                    ]
                },
            },
        )

        self.assertEqual(
            "openai_responses",
            adapter.select_protocol(
                "openai_chat", responses_only, "gpt-5.6-luna"
            ),
        )
        self.assertEqual(
            "openai_chat",
            adapter.select_protocol("openai_chat", dual, "gpt-5.4"),
        )
        self.assertEqual(
            "openai_responses",
            adapter.select_protocol("openai_responses", dual, "gpt-5.4"),
        )
        self.assertEqual(
            "openai_chat",
            adapter.select_protocol(
                "openai_chat", anthropic_and_chat, "claude-sonnet-4.6"
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            adapter.select_protocol(
                "anthropic_messages",
                anthropic_and_chat,
                "claude-sonnet-4.6",
            ),
        )

    def test_copilot_responses_only_fallback_and_catalog_projection(self):
        config = self.provider_config()
        adapter = ciel_runtime.configured_provider_adapter(
            "github-copilot-oauth", config
        )
        contract = ciel_runtime.project_provider_contract_config(
            "github-copilot-oauth", config, ["copilot-token"]
        )

        for model in GITHUB_COPILOT_RESPONSES_ONLY_MODELS:
            with self.subTest(model=model):
                self.assertEqual(
                    "openai_responses",
                    adapter.select_protocol("openai_chat", contract, model),
                )
        raw_metadata = {
            "id": "gpt-5.6-sol",
            "name": "GPT-5.6 Sol",
            "vendor": "OpenAI",
            "version": "5.6",
            "preview": False,
            "model_picker_enabled": True,
            "model_picker_category": "powerful",
            "policy": {"terms": "copilot"},
            "supported_endpoints": [
                "/responses",
                "",
                "ws:/responses",
            ],
            "capabilities": {
                "supports": {
                    "reasoning_effort": [
                        "none",
                        "low",
                        "medium",
                        "high",
                        "xhigh",
                        "max",
                    ],
                    "tools": True,
                },
                "limits": {
                    "max_context_window_tokens": 400_000,
                    "max_prompt_tokens": 272_000,
                    "max_output_tokens": 128_000,
                },
            },
        }

        projected = adapter.project_model_metadata(raw_metadata)

        self.assertEqual(
            ["/responses", "ws:/responses"], projected["supported_endpoints"]
        )
        self.assertEqual("GPT-5.6 Sol", projected["name"])
        self.assertEqual("OpenAI", projected["vendor"])
        self.assertEqual("5.6", projected["version"])
        self.assertFalse(projected["preview"])
        self.assertTrue(projected["model_picker_enabled"])
        self.assertEqual("powerful", projected["model_picker_category"])
        self.assertEqual({"terms": "copilot"}, projected["policy"])
        self.assertEqual(raw_metadata["capabilities"], projected["capabilities"])
        self.assertEqual(400_000, projected["max_model_len"])
        self.assertEqual(128_000, projected["max_output_tokens"])

        raw_metadata["capabilities"]["limits"]["max_output_tokens"] = 1
        self.assertEqual(
            128_000,
            projected["capabilities"]["limits"]["max_output_tokens"],
        )

        mai_picker = adapter.project_model_metadata(
            {
                "id": "mai-code-1-flash-picker",
                "model_picker_enabled": True,
                "supported_endpoints": ["/responses"],
            }
        )
        mai_hidden_wire_alias = adapter.project_model_metadata(
            {
                "id": "mai-code-1-flash",
                "model_picker_enabled": False,
                "supported_endpoints": ["/responses"],
            }
        )
        self.assertEqual(
            {"mai-code-1-flash-picker": "mai-code-1-flash"},
            GITHUB_COPILOT_PUBLIC_MODEL_IDS,
        )
        self.assertEqual(
            "mai-code-1-flash",
            mai_picker[PUBLIC_MODEL_ID_METADATA_KEY],
        )
        self.assertNotIn(
            PUBLIC_MODEL_ID_METADATA_KEY,
            mai_hidden_wire_alias,
        )

    def test_openai_chat_passthrough_uses_copilot_contract_path(self):
        config = self.provider_config()
        captured = {}

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b""

        def urlopen(request, **_kwargs):
            captured["url"] = request.full_url
            return Response()

        handler = mock.Mock()
        handler.headers = {}
        handler.wfile = BytesIO()
        with (
            mock.patch.object(
                ciel_runtime,
                "github_copilot_oauth_token",
                return_value="copilot-token",
            ),
            mock.patch.object(ciel_runtime, "provider_urlopen", side_effect=urlopen),
            mock.patch.object(ciel_runtime, "_copy_upstream_response_headers"),
        ):
            ciel_runtime.forward_provider_chat(
                handler,
                "github-copilot-oauth",
                config,
                {"model": "gemini-3.1-pro-preview", "messages": []},
            )

        self.assertEqual(
            "https://api.githubcopilot.com/chat/completions",
            captured["url"],
        )

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

    def test_remote_upstream_401_refreshes_token_without_replaying_generation(self):
        error = urllib.error.HTTPError(
            "https://api.githubcopilot.com/chat/completions",
            401,
            "Unauthorized",
            {},
            None,
        )
        request = urllib.request.Request(
            "https://api.githubcopilot.com/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer stale"},
            method="POST",
        )
        open_request = mock.Mock(side_effect=error)
        runtime = GitHubCopilotOAuthRuntime(
            Path(tempfile.gettempdir()),
            GitHubCopilotOAuthRuntimePorts(
                clear_model_cache=mock.Mock(),
                log=mock.Mock(),
                provider_headers=mock.Mock(),
                network_open=open_request,
            ),
        )
        config = self.provider_config()
        config[REMOTE_BRIDGE_CONFIG_MARKER] = True

        with (
            mock.patch.object(
                GitHubCopilotOAuthRuntime,
                "force_refresh",
                return_value="fresh-token",
            ) as refresh,
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            runtime.open(
                request,
                30.0,
                "github-copilot-oauth",
                config,
            )

        self.assertEqual(401, caught.exception.code)
        self.assertEqual(1, open_request.call_count)
        refresh.assert_called_once_with()


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
