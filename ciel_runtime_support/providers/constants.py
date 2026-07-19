"""Provider-owned endpoint and catalog constants."""

PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "agy": "https://antigravity.google",
    "codex": "https://api.openai.com",
    "ollama": "http://127.0.0.1:11434",
    "ollama-cloud": "https://ollama.com",
    "deepseek": "https://api.deepseek.com/anthropic",
    "opencode": "https://opencode.ai/zen",
    "opencode-go": "https://opencode.ai/zen/go",
    "kimi": "https://api.kimi.com/coding",
    "zai": "https://api.z.ai/api/anthropic",
    "vllm": "http://127.0.0.1:8000",
    "lm-studio": "http://127.0.0.1:1234/v1",
    "nvidia-hosted": "https://integrate.api.nvidia.com/v1",
    "self-hosted-nim": "http://127.0.0.1:8000",
    "openrouter": "https://openrouter.ai/api/v1",
    "fireworks": "https://api.fireworks.ai/inference",
}

DEFAULT_REQUEST_TIMEOUT_MS = 300000

ZAI_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "glm-5.2[1m]",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "glm-5-turbo",
    "glm-4.7",
    "glm-4.7-flashx",
    "glm-4.7-flash",
    "glm-4.6",
    "glm-4.5",
    "glm-4.5-x",
    "glm-4.5-airx",
    "glm-4.5-air",
    "glm-4.5-flash",
    "glm-4-32b-0414-128k",
)

__all__ = [
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "PROVIDER_DEFAULT_BASE_URLS",
    "ZAI_MODEL_FALLBACK_IDS",
]
