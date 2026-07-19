import unittest
from pathlib import Path

from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS


def config(provider, model="model", **options):
    return ProviderConfig(
        name=provider,
        base_url=f"https://{provider}.invalid",
        model=model,
        options=options,
    )


class ProviderContractMatrixTests(unittest.TestCase):
    def test_primary_protocol_matrix(self):
        cases = {
            "anthropic": "anthropic_messages",
            "codex": "openai_responses",
            "agy": "openai_responses",
            "ollama": "ollama_chat",
            "ollama-cloud": "ollama_chat",
            "openrouter": "openai_chat",
            "vllm": "openai_chat",
            "lm-studio": "openai_chat",
            "nvidia-hosted": "openai_chat",
            "self-hosted-nim": "openai_chat",
            "deepseek": "anthropic_messages",
            "kimi": "anthropic_messages",
            "zai": "anthropic_messages",
            "fireworks": "openai_chat",
            "opencode": "anthropic_messages",
            "opencode-go": "anthropic_messages",
        }
        for provider, protocol in cases.items():
            with self.subTest(provider=provider):
                adapter = PROVIDER_ADAPTERS.create(provider)
                self.assertEqual(protocol, adapter.capabilities(config(provider)).upstream_protocol)

    def test_dual_protocol_provider_selection(self):
        for provider in ("kimi", "fireworks"):
            adapter = PROVIDER_ADAPTERS.create(provider)
            configured = config(provider)
            with self.subTest(provider=provider):
                self.assertEqual("anthropic_messages", adapter.select_protocol("anthropic_messages", configured))
                self.assertEqual("openai_chat", adapter.select_protocol("openai_responses", configured))

    def test_openai_compatible_native_mode_selection(self):
        for provider in ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "openrouter"):
            adapter = PROVIDER_ADAPTERS.create(provider)
            with self.subTest(provider=provider):
                self.assertEqual(
                    "openai_chat",
                    adapter.select_protocol("anthropic_messages", config(provider, native_compat=False)),
                )
                self.assertEqual(
                    "anthropic_messages",
                    adapter.select_protocol("anthropic_messages", config(provider, native_compat=True)),
                )

    def test_opencode_model_protocol_selection(self):
        adapter = PROVIDER_ADAPTERS.create("opencode")
        configured = config("opencode")
        cases = {
            "claude-sonnet-4-6": "anthropic_messages",
            "qwen3.5-coder": "anthropic_messages",
            "deepseek-v4": "openai_chat",
            "gpt-5.4": "openai_responses",
            "gemini-3-pro": "google_generative",
        }
        for model, protocol in cases.items():
            with self.subTest(model=model):
                self.assertEqual(protocol, adapter.select_protocol("anthropic_messages", configured, model))

    def test_provider_owned_tool_choice_policy(self):
        vllm = PROVIDER_ADAPTERS.create("vllm")
        deepseek = PROVIDER_ADAPTERS.create("deepseek")
        self.assertFalse(vllm.supports_tool_choice(config("vllm"), "model"))
        self.assertFalse(deepseek.supports_tool_choice(config("deepseek"), "deepseek-v4-pro"))
        self.assertTrue(deepseek.supports_tool_choice(config("deepseek"), "deepseek-chat"))
        self.assertTrue(
            deepseek.supports_tool_choice(config("deepseek", supports_tool_choice=True), "deepseek-v4-pro")
        )

    def test_model_catalog_strategy_matrix(self):
        cases = {
            "agy": "configured",
            "anthropic": "anthropic",
            "deepseek": "configured",
            "fireworks": "fireworks",
            "kimi": "openai",
            "lm-studio": "lm_studio",
            "nvidia-hosted": "nvidia",
            "ollama": "ollama",
            "ollama-cloud": "ollama",
            "opencode": "openai",
            "openrouter": "openai",
            "vllm": "openai",
            "zai": "openai",
        }
        for provider, kind in cases.items():
            with self.subTest(provider=provider):
                adapter = PROVIDER_ADAPTERS.create(provider)
                self.assertEqual(kind, adapter.model_catalog_policy(config(provider)).kind)

    def test_nvidia_forwarding_policy_is_adapter_owned(self):
        adapter = PROVIDER_ADAPTERS.create("nvidia-hosted")
        policy = adapter.request_policy(config("nvidia-hosted"))

        self.assertEqual("ncp", policy.model_alias_strategy)
        self.assertTrue(policy.stream_required)

        openrouter_policy = PROVIDER_ADAPTERS.create("openrouter").request_policy(config("openrouter"))
        self.assertEqual("identity", openrouter_policy.model_alias_strategy)
        self.assertFalse(openrouter_policy.stream_required)

    def test_model_catalog_service_has_no_provider_name_dispatch(self):
        support = Path(__file__).resolve().parents[1] / "ciel_runtime_support"
        for filename in (
            "provider_models.py",
            "provider_policy.py",
            "provider_config_mutations.py",
            "openai_forwarding.py",
            "response_collection.py",
        ):
            source = (support / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn('provider == "', source)
                self.assertNotIn("provider in (", source)

    def test_configuration_and_api_key_capability_matrix(self):
        required_api_key = {
            "deepseek",
            "fireworks",
            "kimi",
            "nvidia-hosted",
            "ollama-cloud",
            "opencode",
            "opencode-go",
            "openrouter",
            "zai",
        }
        for provider in PROVIDER_ADAPTERS.names():
            adapter = PROVIDER_ADAPTERS.create(provider)
            configured = config(provider)
            with self.subTest(provider=provider):
                self.assertEqual(provider in required_api_key, adapter.capabilities(configured).requires_api_key)
                self.assertIsNotNone(adapter.configuration_policy(configured))

    def test_status_projection_capability_matrix(self):
        ollama = PROVIDER_ADAPTERS.create("ollama").configuration_policy(config("ollama"))
        codex = PROVIDER_ADAPTERS.create("codex").configuration_policy(config("codex"))
        vllm = PROVIDER_ADAPTERS.create("vllm").configuration_policy(config("vllm"))
        nvidia = PROVIDER_ADAPTERS.create("nvidia-hosted").configuration_policy(config("nvidia-hosted"))

        self.assertTrue(ollama.uses_ollama_status)
        self.assertTrue(codex.runtime_owns_model)
        self.assertIn("context_reserve_tokens", vllm.status_fields)
        self.assertNotIn("context_reserve_tokens", nvidia.status_fields)

    def test_api_key_status_is_provider_owned(self):
        cases = {
            "anthropic": "Claude login",
            "agy": "native AGY",
            "codex": "native Codex",
            "deepseek": "DeepSeek required",
            "ollama": "not required for Ollama",
            "opencode": "OpenCode Zen required",
        }
        for provider, expected in cases.items():
            adapter = PROVIDER_ADAPTERS.create(provider)
            status = adapter.api_key_status(config(provider), key_count=0, primary_detail="")
            with self.subTest(provider=provider):
                self.assertIn(expected, status)

    def test_kimi_request_options_are_adapter_owned(self):
        adapter = PROVIDER_ADAPTERS.create("kimi")
        configured = config("kimi", model="k3")
        normalized = adapter.normalize_request_options(
            configured,
            {"model": "k3", "thinking": {"type": "enabled"}},
        )
        self.assertEqual("max", normalized["thinking"]["effort"])
        self.assertEqual(
            {"type": "auto"},
            adapter.normalize_tool_choice(configured, "k3", {"type": "any"}),
        )


if __name__ == "__main__":
    unittest.main()
