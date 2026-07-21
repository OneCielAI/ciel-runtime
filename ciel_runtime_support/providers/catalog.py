"""Declarative adapters for standard OpenAI-compatible API providers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

from ..architecture import (
    MessageProtocol,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderRequestPolicy,
)
from ..runtime_constants import DEFAULT_REQUEST_TIMEOUT_MS
from .base import OpenAICompatibleProviderAdapter, provider_configuration


@dataclass(frozen=True, slots=True)
class CompatibleProviderSpec:
    """Immutable transport and discovery policy for one compatible provider."""

    name: str
    label: str
    base_url: str
    models: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    chat_path: str = "/v1/chat/completions"
    models_path: str = "/v1/models"
    authorization_header: str = "Authorization"
    include_x_api_key: bool = False
    requires_api_key: bool = True


class CatalogOpenAIProviderAdapter(OpenAICompatibleProviderAdapter):
    """Concrete Strategy assembled from a validated compatible-provider spec."""

    def __init__(
        self,
        spec: CompatibleProviderSpec,
        *,
        base_url: str = "",
    ) -> None:
        self.spec = spec
        default_model = spec.models[0] if spec.models else ""
        super().__init__(
            name=spec.name,
            base_url=base_url or spec.base_url,
            configuration_defaults_value=provider_configuration(
                default_model,
                custom_models=spec.models,
                native_compat=False,
                context_window=131072,
                max_output_tokens=8192,
                context_reserve_tokens=4096,
                request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
                stream_enabled=True,
                stream_word_chunking=False,
            ),
            authorization_header=spec.authorization_header,
            include_x_api_key=spec.include_x_api_key,
            require_api_key=spec.requires_api_key,
            api_key_display_name_value=spec.label,
            api_key_launch_error_value=(
                f"Launch blocked: {spec.label} requires an API key."
                if spec.requires_api_key
                else ""
            ),
            capabilities_value=ProviderCapabilities(
                upstream_protocol="openai_chat",
                requires_api_key=spec.requires_api_key,
            ),
            request_policy_value=ProviderRequestPolicy(
                chat_path=spec.chat_path,
                models_path=spec.models_path,
            ),
            model_catalog_policy_value=ProviderModelCatalogPolicy(
                kind="openai",
                fallback_models=spec.models,
                allow_configured_fallback=True,
            ),
        )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def router_native_anthropic_enabled(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> bool:
        del config, model
        return False

    def supported_protocols(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"openai_chat"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del operation, config, model
        return "openai_chat"


def catalog_provider_factory(
    spec: CompatibleProviderSpec,
) -> Callable[..., ProviderAdapter]:
    """Return a descriptor-compatible factory bound to one immutable spec."""

    return partial(CatalogOpenAIProviderAdapter, spec)


COMPATIBLE_PROVIDER_SPECS: tuple[CompatibleProviderSpec, ...] = (
    CompatibleProviderSpec(
        "alicode-intl", "Alibaba Coding International",
        "https://coding-intl.dashscope.aliyuncs.com/v1",
        ("qwen3.5-plus", "kimi-k2.5", "glm-5", "qwen3-coder-next"),
        ("alibaba-coding-intl",),
    ),
    CompatibleProviderSpec(
        "alicode", "Alibaba Coding", "https://coding.dashscope.aliyuncs.com/v1",
        ("qwen3.5-plus", "kimi-k2.5", "glm-5", "qwen3-coder-next"),
        ("alibaba-coding",),
    ),
    CompatibleProviderSpec(
        "alims-intl", "Alibaba Model Studio International",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ("qwen3.5-plus", "kimi-k2.5", "glm-5", "qwen3-coder-next"),
        ("dashscope-intl",),
    ),
    CompatibleProviderSpec(
        "blackbox", "Blackbox AI", "https://api.blackbox.ai/v1",
        ("claude-sonnet-4.6", "gpt-5.4", "gpt-5.3-codex", "deepseek-v4-flash"),
        ("bb",),
    ),
    CompatibleProviderSpec(
        "byteplus", "BytePlus ModelArk",
        "https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        ("seed-2-0-code-preview-260328", "seed-2-0-pro-260328", "glm-4-7-251222"),
        ("bpm",), chat_path="/chat/completions", models_path="/models",
    ),
    CompatibleProviderSpec(
        "cerebras", "Cerebras", "https://api.cerebras.ai/v1",
        ("gpt-oss-120b", "zai-glm-4.7", "llama-3.3-70b"),
    ),
    CompatibleProviderSpec(
        "chutes", "Chutes AI", "https://llm.chutes.ai/v1", aliases=("ch",),
    ),
    CompatibleProviderSpec(
        "cohere", "Cohere", "https://api.cohere.ai/v1",
        ("command-a-03-2025", "command-r-plus-08-2024", "command-r-08-2024"),
    ),
    CompatibleProviderSpec(
        "cloudflare-ai", "Cloudflare Workers AI", "",
        aliases=("workers-ai",), chat_path="/chat/completions",
        models_path="/models",
    ),
    CompatibleProviderSpec(
        "cline", "Cline", "https://api.cline.bot/api/v1",
        aliases=("cline-oauth",),
    ),
    CompatibleProviderSpec(
        "clinepass", "Cline Pass", "https://api.cline.bot/api/v1",
        aliases=("cline-pass",),
    ),
    CompatibleProviderSpec(
        "featherless", "Featherless", "https://api.featherless.ai/v1",
        ("deepseek-ai/DeepSeek-V4-Pro", "zai-org/GLM-5.2", "moonshotai/Kimi-K2.7-Code"),
        ("fl",),
    ),
    CompatibleProviderSpec(
        "glm-cn", "GLM China", "https://open.bigmodel.cn/api/coding/paas/v4",
        ("glm-5.2", "glm-5.1", "glm-5", "glm-4.7"),
        ("bigmodel-cn",), chat_path="/chat/completions", models_path="/models",
    ),
    CompatibleProviderSpec(
        "gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
        ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"),
        ("google-gemini",),
    ),
    CompatibleProviderSpec(
        "groq", "Groq", "https://api.groq.com/openai/v1",
        ("llama-3.3-70b-versatile", "qwen/qwen3-32b", "openai/gpt-oss-120b"),
    ),
    CompatibleProviderSpec(
        "github", "GitHub Copilot", "https://api.githubcopilot.com",
        aliases=("github-copilot", "copilot"),
        chat_path="/chat/completions", models_path="/models",
    ),
    CompatibleProviderSpec(
        "gitlab", "GitLab Duo", "https://gitlab.com/api/v4",
        aliases=("gitlab-duo",), chat_path="/chat/completions",
        models_path="/models",
    ),
    CompatibleProviderSpec(
        "hyperbolic", "Hyperbolic", "https://api.hyperbolic.xyz/v1",
        ("deepseek-ai/DeepSeek-R1", "deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-Coder-32B-Instruct"),
        ("hyp",),
    ),
    CompatibleProviderSpec(
        "huggingface", "Hugging Face Inference", "https://router.huggingface.co/v1",
        aliases=("hf", "hugging-face"),
    ),
    CompatibleProviderSpec(
        "iflow", "iFlow", "https://apis.iflow.cn/v1",
        aliases=("iflow-oauth",),
    ),
    CompatibleProviderSpec(
        "kilocode", "Kilo Code", "https://api.kilo.ai/api/openrouter",
        aliases=("kilo", "kilo-code"),
    ),
    CompatibleProviderSpec(
        "kimchi", "Kimchi", "https://llm.kimchi.dev/openai/v1",
        aliases=("kimchi-oauth",),
    ),
    CompatibleProviderSpec(
        "mistral", "Mistral", "https://api.mistral.ai/v1",
        ("codestral-latest", "mistral-large-latest", "mistral-medium-latest"),
    ),
    CompatibleProviderSpec(
        "mimo-free", "Xiaomi MiMo Free", "https://api.xiaomimimo.com/api/free-ai/openai",
        ("mimo-v2-flash",), aliases=("mimo-free-api",), requires_api_key=False,
    ),
    CompatibleProviderSpec(
        "mmf", "Xiaomi MiMo Free API", "https://api.xiaomimimo.com/api/free-ai/openai",
        ("mimo-v2-flash",), aliases=("xiaomi-mmf",),
    ),
    CompatibleProviderSpec(
        "nebius", "Nebius AI", "https://api.studio.nebius.ai/v1",
        ("meta-llama/Llama-3.3-70B-Instruct",),
    ),
    CompatibleProviderSpec(
        "openai", "OpenAI API", "https://api.openai.com/v1",
        ("gpt-5.4", "gpt-5.4-mini", "gpt-5.2", "gpt-5.1", "gpt-4.1", "o3", "o4-mini"),
        ("openai-api",),
    ),
    CompatibleProviderSpec(
        "perplexity", "Perplexity", "https://api.perplexity.ai",
        ("sonar-pro", "sonar"), ("pplx",),
        chat_path="/chat/completions", models_path="/models",
    ),
    CompatibleProviderSpec(
        "qwen", "Qwen Code", "https://portal.qwen.ai/v1",
        aliases=("qwen-oauth", "qwen-code"),
    ),
    CompatibleProviderSpec(
        "siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1",
        ("deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3.5-397B-A17B", "zai-org/GLM-5.1", "moonshotai/Kimi-K2.6"),
        ("silicon-flow",),
    ),
    CompatibleProviderSpec(
        "together", "Together AI", "https://api.together.xyz/v1",
        ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen3-235B-A22B"),
        ("together-ai",),
    ),
    CompatibleProviderSpec(
        "venice", "Venice AI", "https://api.venice.ai/api/v1",
        ("venice-uncensored-1-2", "zai-org-glm-5", "deepseek-v4-pro", "qwen3-coder-480b-a35b-instruct-turbo"),
        ("vn",),
    ),
    CompatibleProviderSpec(
        "vertex", "Google Vertex AI", "",
        aliases=("vertex-ai",), chat_path="/chat/completions",
        models_path="/models",
    ),
    CompatibleProviderSpec(
        "vertex-partner", "Google Vertex AI Partner Models", "",
        aliases=("vertex-maas",), chat_path="/chat/completions",
        models_path="/models",
    ),
    CompatibleProviderSpec(
        "vercel-ai-gateway", "Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1",
        aliases=("vercel", "vercel-ai"),
    ),
    CompatibleProviderSpec(
        "volcengine-ark", "Volcengine Ark",
        "https://ark.cn-beijing.volces.com/api/coding/v3",
        ("Doubao-Seed-2.0-Code", "DeepSeek-V4-Pro", "GLM-5.1", "Kimi-K2.6"),
        ("ark", "volcengine"), chat_path="/chat/completions", models_path="/models",
    ),
    CompatibleProviderSpec(
        "xai", "xAI", "https://api.x.ai/v1",
        ("grok-4", "grok-4-fast-reasoning", "grok-code-fast-1", "grok-3"),
        ("grok",),
    ),
    CompatibleProviderSpec(
        "xiaomi-mimo", "Xiaomi MiMo", "https://api.xiaomimimo.com/v1",
        ("mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-omni", "mimo-v2-flash"),
        ("mimo",),
    ),
    CompatibleProviderSpec(
        "xiaomi-tokenplan", "Xiaomi MiMo Token Plan",
        "https://token-plan-sgp.xiaomimimo.com/v1",
        ("mimo-v2.5-pro", "mimo-v2.5-pro-claude", "mimo-v2.5", "mimo-v2-pro"),
        ("xmtp", "xiaomi-token-plan"),
    ),
)

CATALOG_PROVIDER_BASE_URLS = {
    spec.name: spec.base_url for spec in COMPATIBLE_PROVIDER_SPECS
}


__all__ = [
    "CATALOG_PROVIDER_BASE_URLS",
    "COMPATIBLE_PROVIDER_SPECS",
    "CatalogOpenAIProviderAdapter",
    "CompatibleProviderSpec",
    "catalog_provider_factory",
]
