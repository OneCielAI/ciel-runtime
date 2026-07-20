import io
import unittest
from unittest import mock
import urllib.error

from ciel_runtime_support.architecture import ProviderRequestPolicy
from ciel_runtime_support.compatibility_probe import (
    CompatibilityApiKeyProbeBuilder,
    CompatibilityApiKeyProbeError,
    CompatibilityApiKeyProbeRunner,
    CompatibilityApiKeyProbeRunnerPorts,
    CompatibilityProbeAnthropicPorts,
    CompatibilityProbeProjectionPorts,
    CompatibilityProbeRoutingPorts,
)


class CompatibilityApiKeyProbeBuilderTests(unittest.TestCase):
    def builder(
        self,
        *,
        strategy="anthropic",
        model_alias="identity",
        router=False,
        endpoint_kind="anthropic-messages",
    ):
        policy = ProviderRequestPolicy(
            chat_path="/chat",
            models_path="/models",
            model_alias_strategy=model_alias,
            probe_strategy=strategy,
        )
        self.ollama_request = mock.Mock(return_value={"kind": "ollama"})
        self.openai_request = mock.Mock(return_value={"kind": "openai"})
        return CompatibilityApiKeyProbeBuilder(
            CompatibilityProbeProjectionPorts(
                normalize_thinking=lambda _p, _c, body: dict(body),
                normalize_tool_choice=lambda _p, _c, body: dict(body),
                resolve_model=lambda _p, _c, model: f"resolved/{model}",
                headers=lambda _p, _c: {"authorization": "secret"},
                request_policy=lambda _p, _c: policy,
            ),
            CompatibilityProbeRoutingPorts(
                ollama_request=self.ollama_request,
                openai_request=self.openai_request,
                endpoint=lambda _p, _c, operation: f"https://route/{operation}",
                opencode_endpoint_kind=lambda _p, _m, _c: endpoint_kind,
                openai_router_enabled=lambda _p, _c: router,
                request_base=lambda _p, _c: "https://upstream",
                join_url=lambda base, path: base.rstrip("/") + path,
                ncp_model_id=lambda model: f"ncp/{model}",
            ),
            CompatibilityProbeAnthropicPorts(
                cap_body=lambda _p, _c, body: dict(body),
                apply_options=lambda _p, _c, body: dict(body),
                resolve_tool_models=lambda _p, _c, body: dict(body),
                native_compat_enabled=lambda _p, _c: False,
                native_base_url=lambda _p, _c: "https://native",
            ),
        )

    def test_ollama_strategy_uses_ollama_request_family(self):
        builder = self.builder(strategy="ollama")

        url, body, headers = builder.build("provider", {}, "model", {"x": 1})

        self.assertEqual("https://route/ollama_chat", url)
        self.assertEqual({"kind": "ollama"}, body)
        self.assertEqual({"authorization": "secret"}, headers)
        self.ollama_request.assert_called_once()

    def test_opencode_rejects_unsupported_endpoint_family(self):
        builder = self.builder(strategy="opencode", endpoint_kind="responses")

        with self.assertRaises(CompatibilityApiKeyProbeError):
            builder.build("provider", {}, "model", {})

    def test_openai_router_applies_adapter_model_alias_strategy(self):
        builder = self.builder(router=True, model_alias="ncp")

        url, body, _headers = builder.build("provider", {}, "model", {})

        self.assertEqual("https://route/openai_chat", url)
        self.assertEqual({"kind": "openai"}, body)
        self.assertEqual(
            "ncp/resolved/model",
            self.openai_request.call_args.args[1],
        )

    def test_anthropic_fallback_projects_resolved_model(self):
        builder = self.builder()

        url, body, _headers = builder.build("provider", {}, "model", {"x": 1})

        self.assertEqual("https://upstream/v1/messages", url)
        self.assertEqual("resolved/model", body["model"])


class CompatibilityApiKeyProbeRunnerTests(unittest.TestCase):
    def runner(self, *, post=None):
        self.post = post or mock.Mock()
        return CompatibilityApiKeyProbeRunner(
            CompatibilityApiKeyProbeRunnerPorts(
                api_keys=lambda _p, _c: ["key-a", "key-b"],
                mask_secret=lambda key: f"masked-{key[-1]}",
                build_request=lambda _p, _c, _m, _b: (
                    "https://probe",
                    {"probe": True},
                    {"authorization": "secret"},
                ),
                post=self.post,
                http_error_message=lambda exc: str(exc.reason),
                failure_diagnosis=lambda _p, _code, _msg: "diagnosis",
            )
        )

    def test_runs_each_key_with_isolated_configuration(self):
        runner = self.runner()

        lines = runner.run("provider", {"api_keys": ["original"]}, "model", {}, 3)

        self.assertEqual(2, self.post.call_count)
        self.assertEqual("key-a", self.post.call_args_list[0].kwargs["pcfg"]["api_key"])
        self.assertEqual([], self.post.call_args_list[0].kwargs["pcfg"]["api_keys"])
        self.assertEqual("API key 2/2 (masked-b): OK", lines[-1])

    def test_maps_http_error_to_probe_error(self):
        error = urllib.error.HTTPError(
            "https://probe",
            401,
            "unauthorized",
            {},
            io.BytesIO(b""),
        )
        runner = self.runner(post=mock.Mock(side_effect=error))

        with self.assertRaises(CompatibilityApiKeyProbeError) as raised:
            runner.run("provider", {}, "model", {}, 3)

        self.assertEqual(401, raised.exception.code)
        self.assertEqual("diagnosis", raised.exception.diagnosis)


if __name__ == "__main__":
    unittest.main()
