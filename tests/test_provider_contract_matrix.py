import ast
import unittest
from pathlib import Path

from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS, PROVIDER_LABELS


def config(provider, model="model", **options):
    return ProviderConfig(
        name=provider,
        base_url=f"https://{provider}.invalid",
        model=model,
        options=options,
    )


class ProviderContractMatrixTests(unittest.TestCase):
    def test_provider_labels_share_the_adapter_registry_definition(self):
        self.assertEqual(set(PROVIDER_ADAPTERS.names()), set(PROVIDER_LABELS))
        self.assertTrue(all(label.strip() for label in PROVIDER_LABELS.values()))

    def test_every_adapter_supplies_minimal_default_configuration(self):
        for provider in PROVIDER_ADAPTERS.names():
            with self.subTest(provider=provider):
                defaults = PROVIDER_ADAPTERS.create(provider).default_configuration()
                self.assertEqual(PROVIDER_ADAPTERS.create(provider).default_base_url(), defaults["base_url"])
                self.assertIn("current_model", defaults)
                self.assertIn("custom_models", defaults)

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

    def test_model_identity_matrix_is_adapter_owned(self):
        cases = (
            ("kimi", "kimi-code/k3", "k3", "kimi-code/k3"),
            ("kimi", "kimi-k2.7-code", "kimi-for-coding", "kimi-k2.7-code"),
            ("ollama-cloud", "qwen3:cloud", "qwen3", "qwen3:cloud"),
            ("zai", "glm-5.2[1m]", "glm-5.2[1m]", "glm-5.2"),
            ("deepseek", "deepseek-v4-pro[1m]", "deepseek-v4-pro[1m]", "deepseek-v4-pro[1m]"),
            ("openrouter", "model[1m]", "model", "model[1m]"),
        )
        for provider, raw, normalized, upstream in cases:
            adapter = PROVIDER_ADAPTERS.create(provider)
            with self.subTest(provider=provider, raw=raw):
                self.assertEqual(normalized, adapter.normalize_model_id(raw))
                self.assertEqual(upstream, adapter.upstream_api_model_id(raw))

        nvidia = PROVIDER_ADAPTERS.create("nvidia-hosted")
        self.assertTrue(nvidia.preserves_claude_model_alias("claude-nvidia-model"))
        self.assertFalse(nvidia.preserves_claude_model_alias("nvidia/model"))

    def test_model_configuration_profile_is_adapter_owned(self):
        kimi = PROVIDER_ADAPTERS.create("kimi")
        updates, notice = kimi.model_configuration_profile(config("kimi", model="k3"))
        self.assertEqual(1048576, updates["context_window"])
        self.assertEqual("max", updates["effort_level"])
        self.assertIn("Kimi K3", notice or "")

        generic = PROVIDER_ADAPTERS.create("openrouter")
        self.assertEqual(({}, None), generic.model_configuration_profile(config("openrouter")))

    def test_catalog_model_selection_policy_is_adapter_owned(self):
        expected = {
            "vllm": {"", "model", "my-model"},
            "lm-studio": {"", "model", "local-model"},
            "self-hosted-nim": {"", "model"},
        }
        for provider in PROVIDER_ADAPTERS.names():
            adapter = PROVIDER_ADAPTERS.create(provider)
            configured = config(provider)
            with self.subTest(provider=provider):
                self.assertEqual(provider in expected, adapter.requires_catalog_model_selection(configured))
                if provider in expected:
                    self.assertEqual(expected[provider], set(adapter.placeholder_model_ids()))

    def test_route_mode_projection_is_adapter_owned(self):
        cases = {
            "anthropic": ("Anthropic routing mode updated.", "mode: direct Claude Native"),
            "agy": ("AGY routing mode updated.", "mode: agy-native"),
            "codex": ("Codex routing mode updated.", "mode: codex-native"),
        }
        for provider, expected in cases.items():
            with self.subTest(provider=provider):
                self.assertEqual(expected, PROVIDER_ADAPTERS.create(provider).routing_mode_update(False))

    def test_inbound_beta_query_capability_is_adapter_owned(self):
        for provider in PROVIDER_ADAPTERS.names():
            adapter = PROVIDER_ADAPTERS.create(provider)
            with self.subTest(provider=provider):
                self.assertEqual(
                    provider == "anthropic",
                    adapter.propagates_inbound_beta_query(config(provider)),
                )

    def test_main_query_projection_delegates_without_provider_dispatch(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in ("upstream_messages_query", "upstream_query_string_status"):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertIn("propagates_inbound_beta_query", function_source)
                self.assertNotIn('provider == "', function_source)

    def test_option_presentation_matrix_is_adapter_owned(self):
        cases = {
            "anthropic": {"show_route"},
            "ollama": {"show_rate_limit", "show_tool_choice", "show_stream"},
            "lm-studio": {"show_rate_limit", "show_native", "show_tool_choice", "show_sampling", "show_stream"},
            "nvidia-hosted": {"show_rate_limit", "show_tool_choice", "show_sampling", "show_stream"},
            "opencode": {"show_native", "show_tool_choice", "show_stream", "show_ip_family"},
        }
        fields = (
            "show_rate_limit",
            "show_native",
            "show_route",
            "show_tool_choice",
            "show_sampling",
            "show_stream",
            "show_ip_family",
        )
        for provider, enabled in cases.items():
            policy = PROVIDER_ADAPTERS.create(provider).option_presentation_policy(config(provider))
            with self.subTest(provider=provider):
                self.assertEqual(enabled, {field for field in fields if getattr(policy, field)})

    def test_main_option_status_delegates_without_provider_dispatch(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in ("provider_options_status", "llm_options_status"):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertIn("option_presentation_policy", function_source)
                self.assertNotIn('provider == "', function_source)
                self.assertNotIn("provider in (", function_source)

    def test_main_option_panel_delegates_without_provider_dispatch(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in (
            "llm_option_current_bool",
            "llm_option_panel_rows",
            "llm_option_prompt_default",
        ):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertNotIn('provider == "', function_source)
                self.assertNotIn("provider in (", function_source)

    def test_llm_option_service_has_no_provider_name_dispatch(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "llm_option_config.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('provider == "', source)
        self.assertNotIn("provider in (", source)

    def test_main_model_identity_functions_delegate_without_provider_dispatch(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in ("normalize_model_id", "upstream_api_model_id", "alias_for"):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertIn("PROVIDER_ADAPTERS.create(provider)", function_source)
                self.assertNotIn('provider == "', function_source)
                self.assertNotIn("provider in (", function_source)

    def test_historical_tool_turn_normalization_policy_is_adapter_owned(self):
        anthropic = PROVIDER_ADAPTERS.create("anthropic").request_policy(config("anthropic"))
        openrouter = PROVIDER_ADAPTERS.create("openrouter").request_policy(config("openrouter"))

        self.assertFalse(anthropic.normalize_historical_tool_turns)
        self.assertTrue(openrouter.normalize_historical_tool_turns)

    def test_model_catalog_service_has_no_provider_name_dispatch(self):
        support = Path(__file__).resolve().parents[1] / "ciel_runtime_support"
        for filename in (
            "provider_models.py",
            "provider_policy.py",
            "provider_config_mutations.py",
            "openai_forwarding.py",
            "response_collection.py",
            "anthropic_tool_turns.py",
            "claude_router.py",
        ):
            source = (support / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn('provider == "', source)
                self.assertNotIn("provider in (", source)

    def test_response_collection_dispatch_uses_provider_protocol_contract(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "collect_provider_message_for_responses"
        )
        function_source = ast.get_source_segment(source, function) or ""

        self.assertIn("select_provider_protocol", function_source)
        self.assertNotIn('provider == "', function_source)
        self.assertNotIn("provider in (", function_source)
        self.assertNotIn("OPENCODE_PROVIDER_NAMES", function_source)

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

    def test_context_strategy_matrix(self):
        cases = {
            "anthropic": ("managed", "managed"),
            "ollama": ("ollama", "ollama"),
            "ollama-cloud": ("ollama", "ollama"),
            "vllm": ("remote_first", "standard"),
            "self-hosted-nim": ("remote_first", "standard"),
            "nvidia-hosted": ("nvidia", "standard"),
            "kimi": ("hint_first", "standard"),
            "zai": ("hint_first", "standard"),
            "deepseek": ("configured_first", "standard"),
            "openrouter": ("configured_first", "standard"),
            "lm-studio": ("hint_configured", "standard"),
        }
        for provider, expected in cases.items():
            adapter = PROVIDER_ADAPTERS.create(provider)
            policy = adapter.context_policy(config(provider))
            with self.subTest(provider=provider):
                self.assertEqual(expected, (policy.capacity_strategy, policy.settings_strategy))

    def test_context_status_strategy_matrix(self):
        cases = {
            "anthropic": "configured",
            "ollama": "ollama_budget",
            "ollama-cloud": "ollama_budget",
            "vllm": "openai_budget",
            "lm-studio": "openai_budget",
            "nvidia-hosted": "openai_budget",
            "self-hosted-nim": "openai_budget",
            "zai": "provider",
            "deepseek": "configured",
        }
        for provider, expected in cases.items():
            with self.subTest(provider=provider):
                policy = PROVIDER_ADAPTERS.create(provider).context_policy(config(provider))
                self.assertEqual(expected, policy.status_capacity_strategy)

    def test_provider_owned_compatibility_diagnosis(self):
        vllm = PROVIDER_ADAPTERS.create("vllm")
        self.assertIn(
            "--tool-call-parser",
            vllm.compatibility_failure_diagnosis(400, "tool parser failed") or "",
        )
        nvidia = PROVIDER_ADAPTERS.create("nvidia-hosted")
        self.assertIn("API Catalog", nvidia.compatibility_failure_diagnosis(404, "missing") or "")
        self.assertIn(
            "transient",
            nvidia.compatibility_failure_diagnosis(503, "unavailable") or "",
        )
        zai = PROVIDER_ADAPTERS.create("zai")
        self.assertIn(
            "tool-use probes time out",
            zai.known_compatibility_tool_use_blocker("glm-4.7-flash"),
        )
        self.assertEqual("", zai.known_compatibility_tool_use_blocker("glm-5.2"))

    def test_compatibility_runtime_metadata_matrix(self):
        enabled = {"lm-studio", "vllm", "self-hosted-nim"}
        for provider in PROVIDER_ADAPTERS.names():
            adapter = PROVIDER_ADAPTERS.create(provider)
            with self.subTest(provider=provider):
                self.assertEqual(
                    provider in enabled,
                    adapter.exposes_compatibility_runtime_info(config(provider)),
                )
        lm_studio = PROVIDER_ADAPTERS.create("lm-studio")
        self.assertEqual(
            (
                "Runtime loaded_context_length: 32768",
                "Runtime model state: loaded",
            ),
            lm_studio.compatibility_runtime_metadata_lines(
                config("lm-studio"),
                {"loaded_context_len": 32768, "state": "loaded"},
            ),
        )

    def test_model_launch_and_runtime_info_strategy_matrix(self):
        launch_cases = {
            "ollama": "ollama_unslug",
            "ollama-cloud": "alias",
            "nvidia-hosted": "alias",
        }
        for provider, expected in launch_cases.items():
            with self.subTest(provider=provider, policy="launch"):
                adapter = PROVIDER_ADAPTERS.create(provider)
                self.assertEqual(expected, adapter.launch_model_strategy(config(provider)))
        runtime_cases = {
            "lm-studio": "lm_studio",
            "vllm": "openai",
            "self-hosted-nim": "openai",
            "ollama": "",
        }
        for provider, expected in runtime_cases.items():
            with self.subTest(provider=provider, policy="runtime_info"):
                adapter = PROVIDER_ADAPTERS.create(provider)
                self.assertEqual(expected, adapter.runtime_model_info_strategy(config(provider)))

    def test_claude_launch_enrichment_policy_matrix(self):
        anthropic = PROVIDER_ADAPTERS.create("anthropic")
        self.assertFalse(anthropic.allows_auto_web_search(config("anthropic")))
        self.assertFalse(anthropic.requires_compat_prompt(config("anthropic")))
        zai = PROVIDER_ADAPTERS.create("zai")
        self.assertFalse(zai.allows_auto_web_search(config("zai", managed_mcp=True)))
        self.assertTrue(zai.allows_auto_web_search(config("zai", managed_mcp=False)))
        vllm = PROVIDER_ADAPTERS.create("vllm")
        self.assertTrue(vllm.allows_auto_web_search(config("vllm")))
        self.assertTrue(vllm.requires_compat_prompt(config("vllm")))

    def test_router_native_anthropic_capability_matrix(self):
        enabled = {
            "deepseek",
            "fireworks",
            "kimi",
            "lm-studio",
            "opencode",
            "opencode-go",
            "self-hosted-nim",
            "vllm",
            "zai",
        }
        for provider in PROVIDER_ADAPTERS.names():
            adapter = PROVIDER_ADAPTERS.create(provider)
            configured = config(provider, native_compat=True)
            with self.subTest(provider=provider):
                self.assertEqual(
                    provider in enabled,
                    adapter.router_native_anthropic_enabled(configured, "model"),
                )

        opencode = PROVIDER_ADAPTERS.create("opencode")
        self.assertTrue(
            opencode.router_native_anthropic_enabled(config("opencode", native_compat=True), "claude-sonnet")
        )
        self.assertFalse(
            opencode.router_native_anthropic_enabled(config("opencode", native_compat=True), "gpt-5")
        )

    def test_main_native_compatibility_delegates_to_adapter(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "provider_native_compat_enabled"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("router_native_anthropic_enabled", function_source)
        self.assertNotIn("vllm_native_compat_enabled", function_source)
        self.assertNotIn("opencode_native_compat_enabled", function_source)

    def test_context_workflows_delegate_without_provider_dispatch(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            "provider_model_context_capacity",
            "cap_context_settings_to_model_capacity",
            "cap_output_settings_to_context_ratio",
            "configured_context_window_for_timeout",
            "configured_output_tokens_for_timeout",
            "calculated_request_timeout_ms",
            "recommended_request_timeout_ms",
            "context_setup_panel_rows",
            "apply_context_setup_to_provider",
            "infer_preset_id_from_options",
            "model_option_family",
            "recommended_preset_id",
            "required_context_for_preset",
        }
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        }
        self.assertEqual(names, set(functions))
        for name, function_source in functions.items():
            with self.subTest(function=name):
                self.assertNotIn('provider == "', function_source)
                self.assertNotIn("provider in (", function_source)

    def test_base_url_status_strategy_matrix(self):
        cases = {
            "agy": "native_agy",
            "codex": "native_codex",
            "deepseek": "configured",
            "fireworks": "catalog",
            "kimi": "catalog",
            "nvidia-hosted": "nvidia",
            "ollama": "generic",
            "opencode": "catalog",
            "zai": "configured",
        }
        for provider, expected_kind in cases.items():
            adapter = PROVIDER_ADAPTERS.create(provider)
            with self.subTest(provider=provider):
                self.assertEqual(expected_kind, adapter.status_policy(config(provider)).kind)

    def test_unreachable_launch_guidance_is_provider_owned(self):
        cases = {
            "lm-studio": "LM Studio's Local Server",
            "ollama": "Start Ollama",
            "ollama-cloud": "Start Ollama",
            "self-hosted-nim": "Start NIM",
            "vllm": "vLLM must be reachable",
        }
        for provider, expected_text in cases.items():
            adapter = PROVIDER_ADAPTERS.create(provider)
            with self.subTest(provider=provider):
                self.assertIn(expected_text, adapter.status_policy(config(provider)).unreachable_hint)

    def test_runtime_readiness_validation_is_provider_owned(self):
        lm_studio = PROVIDER_ADAPTERS.create("lm-studio").status_policy(config("lm-studio"))
        vllm = PROVIDER_ADAPTERS.create("vllm").status_policy(config("vllm"))

        self.assertEqual("lm_studio", lm_studio.readiness_validation)
        self.assertEqual("none", vllm.readiness_validation)

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
