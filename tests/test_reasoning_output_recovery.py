import unittest

import ciel_runtime
from ciel_runtime_support import codex_turn_recovery
from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.provider_adapters import (
    AlibabaModelStudioProviderAdapter,
    AnthropicProviderAdapter,
    DeepSeekProviderAdapter,
    KimiProviderAdapter,
    OllamaProviderAdapter,
    OpenRouterProviderAdapter,
    XaiProviderAdapter,
)


def work_body():
    return {
        "model": "qwen3.8:27b",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "파일을 만들고 검증해"}],
            }
        ],
    }


def exhausted_message(*, stop_reason="max_tokens", notice=True):
    content = [{"type": "thinking", "thinking": "long private reasoning"}]
    if notice:
        content.append(
            {
                "type": "text",
                "text": (
                    "[ciel-runtime] Upstream model exhausted its output budget during "
                    "reasoning before producing text or a tool call."
                ),
            }
        )
    return {
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
    }


def tool_message():
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_recovered",
                "name": "Read",
                "input": {"file_path": "README.md"},
            }
        ],
    }


class ReasoningOutputEvidenceTests(unittest.TestCase):
    def test_runtime_notice_is_exact_output_budget_evidence(self):
        self.assertTrue(
            codex_turn_recovery.message_exhausted_reasoning_output_budget(
                exhausted_message()
            )
        )

    def test_raw_max_tokens_reasoning_only_is_output_budget_evidence(self):
        self.assertTrue(
            codex_turn_recovery.message_exhausted_reasoning_output_budget(
                exhausted_message(notice=False)
            )
        )

    def test_plain_reasoning_only_turn_is_not_misclassified(self):
        self.assertFalse(
            codex_turn_recovery.message_exhausted_reasoning_output_budget(
                exhausted_message(stop_reason="end_turn", notice=False)
            )
        )


class ProviderRecoveryContractTests(unittest.TestCase):
    def test_ollama_boolean_thinking_model_disables_thinking(self):
        adapter = OllamaProviderAdapter()
        config = ProviderConfig(
            name="ollama",
            base_url=adapter.default_base_url(),
            model="qwen3.8:27b",
            options={
                "ollama_model_metadata_model": "qwen3.8:27b",
                "ollama_model_capabilities": ["completion", "thinking"],
            },
        )

        self.assertEqual(
            ("disable", None),
            adapter.reasoning_output_recovery(
                config, config.model, "ollama_chat", work_body()
            ),
        )

    def test_ollama_non_disableable_architecture_uses_native_minimum(self):
        adapter = OllamaProviderAdapter()
        config = ProviderConfig(
            name="ollama",
            base_url=adapter.default_base_url(),
            model="gpt-oss:20b",
            options={
                "ollama_model_metadata_model": "gpt-oss:20b",
                "ollama_model_architecture": "gptoss",
                "ollama_model_capabilities": ["completion", "thinking"],
            },
        )

        self.assertEqual(
            ("minimum", "low"),
            adapter.reasoning_output_recovery(
                config, config.model, "ollama_chat", work_body()
            ),
        )

    def test_non_thinking_ollama_model_is_not_given_guessed_controls(self):
        adapter = OllamaProviderAdapter()
        config = ProviderConfig(
            name="ollama",
            base_url=adapter.default_base_url(),
            model="llama3.2",
            options={"ollama_model_capabilities": ["completion"]},
        )

        self.assertEqual(
            ("none", None),
            adapter.reasoning_output_recovery(
                config, config.model, "ollama_chat", work_body()
            ),
        )

    def test_native_anthropic_contract_omits_extended_thinking_on_retry(self):
        adapter = AnthropicProviderAdapter()
        config = ProviderConfig(
            name="anthropic",
            base_url=adapter.default_base_url(),
            model="claude-sonnet-4-6",
        )

        self.assertEqual(
            ("omit", None),
            adapter.reasoning_output_recovery(
                config, config.model, "anthropic_messages", work_body()
            ),
        )

    def test_undeclared_openai_compatible_provider_stays_prompt_only(self):
        adapter = OpenRouterProviderAdapter()
        config = ProviderConfig(
            name="openrouter",
            base_url=adapter.default_base_url(),
            model="vendor/unknown-model",
        )

        self.assertEqual(
            ("none", None),
            adapter.reasoning_output_recovery(
                config, config.model, "openai_chat", work_body()
            ),
        )

    def test_deepseek_native_contract_uses_explicit_disable(self):
        adapter = DeepSeekProviderAdapter()
        config = ProviderConfig(
            name="deepseek",
            base_url=adapter.default_base_url(),
            model="deepseek-v4-pro[1m]",
        )

        self.assertEqual(
            ("disable", None),
            adapter.reasoning_output_recovery(
                config, config.model, "anthropic_messages", work_body()
            ),
        )

    def test_kimi_declares_minimum_only_for_supported_k3_chat_model(self):
        adapter = KimiProviderAdapter()
        config = ProviderConfig(
            name="kimi",
            base_url=adapter.default_base_url(),
            model="k3",
        )

        self.assertEqual(
            ("minimum", "low"),
            adapter.reasoning_output_recovery(
                config, config.model, "openai_chat", work_body()
            ),
        )
        self.assertEqual(
            ("none", None),
            adapter.reasoning_output_recovery(
                config, "unknown-kimi-model", "openai_chat", work_body()
            ),
        )

    def test_alibaba_qwen38_declares_openai_disable(self):
        adapter = AlibabaModelStudioProviderAdapter()
        config = ProviderConfig(
            name="alibaba-model-studio",
            base_url=adapter.default_base_url(),
            model="qwen3.8-max",
        )

        self.assertEqual(
            ("disable", None),
            adapter.reasoning_output_recovery(
                config, config.model, "openai_chat", work_body()
            ),
        )

    def test_xai_declares_low_as_its_minimum_recovery_effort(self):
        adapter = XaiProviderAdapter()
        config = ProviderConfig(
            name="xai",
            base_url=adapter.default_base_url(),
            model="grok-4.6",
        )

        self.assertEqual(
            ("minimum", "low"),
            adapter.reasoning_output_recovery(
                config, config.model, "openai_responses", work_body()
            ),
        )


class RoutedRecoveryIntegrationTests(unittest.TestCase):
    def test_ollama_budget_exhaustion_retries_once_with_thinking_disabled(self):
        calls = []
        logs = []
        config = {
            "base_url": "http://127.0.0.1:11434",
            "current_model": "qwen3.8:27b",
            "ollama_model_metadata_model": "qwen3.8:27b",
            "ollama_model_capabilities": ["completion", "thinking"],
            "think": True,
        }

        def collect(_handler, provider, retry_config, retry_body):
            calls.append((provider, retry_config, retry_body))
            return tool_message()

        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "ollama",
            config,
            work_body(),
            exhausted_message(),
            codex_turn_recovery.CodexTurnRecoveryServices(
                should_retry=ciel_runtime.should_retry_preamble_only_turn,
                collect_message=collect,
                log=lambda level, message: logs.append((level, message)),
                prepare_reasoning_budget_retry=(
                    ciel_runtime._codex_turn_recovery_services().prepare_reasoning_budget_retry
                ),
            ),
        )

        self.assertTrue(codex_turn_recovery.message_has_tool_use(recovered))
        self.assertEqual(1, len(calls), "recovery is bounded to one upstream retry")
        _, retry_config, retry_body = calls[0]
        self.assertFalse(retry_config["think"])
        self.assertTrue(retry_config["think_explicit"])
        self.assertEqual({"type": "disabled"}, retry_body["thinking"])
        self.assertTrue(
            any("strategy=disable" in message for _, message in logs), logs
        )

    def test_unsupported_provider_keeps_safe_prompt_only_retry(self):
        config, body, strategy = codex_turn_recovery.project_reasoning_output_budget_retry(
            {"effort_level": "high"}, work_body(), "none", None
        )

        self.assertEqual("prompt_only", strategy)
        self.assertEqual("high", config["effort_level"])
        self.assertNotIn("thinking", body)

    def test_projection_failure_falls_back_to_prompt_only_retry(self):
        calls = []

        def broken_projection(*_args):
            raise RuntimeError("adapter contract unavailable")

        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "unknown-provider",
            {},
            work_body(),
            exhausted_message(),
            codex_turn_recovery.CodexTurnRecoveryServices(
                should_retry=ciel_runtime.should_retry_preamble_only_turn,
                collect_message=lambda *_args: calls.append(_args) or tool_message(),
                log=lambda *_args: None,
                prepare_reasoning_budget_retry=broken_projection,
            ),
        )

        self.assertTrue(codex_turn_recovery.message_has_tool_use(recovered))
        self.assertEqual(1, len(calls))

    def test_minimum_projection_updates_common_and_internal_effort(self):
        config, body, strategy = codex_turn_recovery.project_reasoning_output_budget_retry(
            {}, work_body(), "minimum", "low"
        )

        self.assertEqual("minimum", strategy)
        self.assertEqual("low", config["effort_level"])
        self.assertEqual("low", body["thinking"]["effort"])
        self.assertEqual("low", body["reasoning_effort"])
        self.assertEqual(
            "low", body["metadata"]["ciel_runtime_reasoning_effort"]
        )

    def test_omit_projection_removes_existing_reasoning_request(self):
        original = {
            **work_body(),
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "reasoning_effort": "high",
            "metadata": {"ciel_runtime_reasoning_effort": "high"},
        }

        _, body, strategy = codex_turn_recovery.project_reasoning_output_budget_retry(
            {}, original, "omit", None
        )

        self.assertEqual("omit", strategy)
        self.assertNotIn("thinking", body)
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("metadata", body)


if __name__ == "__main__":
    unittest.main()
