"""Immutable runtime defaults shared by composition and application layers."""
from __future__ import annotations

from typing import Any


MODEL_CACHE_TTL_SECONDS = 300
OLLAMA_MODEL_CATALOG_URL = "https://ollama.com/api/tags"
OLLAMA_MODEL_CATALOG_TTL_SECONDS = 24 * 60 * 60
ANTHROPIC_MODEL_DOCS_URL = "https://docs.anthropic.com/en/docs/about-claude/models/overview"
ANTHROPIC_MODEL_DOCS_URLS = (
    ANTHROPIC_MODEL_DOCS_URL,
    "https://platform.claude.com/docs/en/about-claude/models/overview",
)
ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
)
ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS = ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS
ANTHROPIC_LIMITED_ACCESS_MODEL_IDS = ("claude-mythos-5", "claude-mythos-preview")
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go"
KIMI_CODING_BASE_URL = "https://api.kimi.com/coding"
KIMI_DEFAULT_MODEL = "kimi-for-coding"
KIMI_K3_MODEL = "k3"
KIMI_MODEL_FALLBACK_IDS = (KIMI_K3_MODEL, KIMI_DEFAULT_MODEL)
ZAI_ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic"
ZAI_DEFAULT_MODEL = "glm-5.2[1m]"
ZAI_MODEL_CONTEXT_HINTS = (
    ("glm-5.2", 1_000_000), ("glm-5-turbo", 200_000), ("glm-5.1", 200_000),
    ("glm-5", 200_000), ("glm-4.7", 200_000), ("glm-4.6", 200_000),
    ("glm-4.5", 128_000), ("glm-4-32b-0414-128k", 128_000),
)
ZAI_MANAGED_MCP_SERVERS = (
    ("web-search-prime", "https://api.z.ai/api/mcp/web_search_prime/mcp"),
    ("web-reader", "https://api.z.ai/api/mcp/web_reader/mcp"),
    ("zread", "https://api.z.ai/api/mcp/zread/mcp"),
)
FIREWORKS_INFERENCE_BASE_URL = "https://api.fireworks.ai/inference"
FIREWORKS_API_BASE_URL = "https://api.fireworks.ai"
FIREWORKS_DEFAULT_ACCOUNT_ID = "fireworks"
NCP_PYPI_PACKAGE = "nvd-claude-proxy"

PROVIDER_ALIASES = {
    "anthropic": "anthropic", "claude": "anthropic", "claude-native": "anthropic",
    "native": "anthropic", "claude-code": "anthropic", "agy": "agy",
    "antigravity": "agy", "google-antigravity": "agy", "agy-native": "agy",
    "native-agy": "agy", "codex": "codex", "codex-native": "codex",
    "native-codex": "codex", "openai-codex": "codex", "ollama": "ollama",
    "ollama-cloud": "ollama-cloud", "cloud-ollama": "ollama-cloud",
    "deepseek": "deepseek", "deepseek.com": "deepseek", "deepseek-com": "deepseek",
    "deepseek-api": "deepseek", "ds": "deepseek", "opencode": "opencode",
    "opencode.ai": "opencode", "opencode-ai": "opencode", "opencode-zen": "opencode",
    "zen": "opencode", "opencode-go": "opencode-go", "opencode.go": "opencode-go",
    "opencode_go": "opencode-go", "opencodego": "opencode-go", "kimi": "kimi",
    "kimi.com": "kimi", "kimi-code": "kimi", "kimi-coding": "kimi",
    "moonshot": "kimi", "moonshot-kimi": "kimi", "zai": "zai", "z.ai": "zai",
    "z-ai": "zai", "zhipu": "zai", "bigmodel": "zai", "glm": "zai",
    "vllm": "vllm", "vllm-local": "vllm", "lm-studio": "lm-studio",
    "lmstudio": "lm-studio", "lm": "lm-studio", "nvidia": "nvidia-hosted",
    "nvidia-hosted": "nvidia-hosted", "hosted-nvidia": "nvidia-hosted",
    "nim": "self-hosted-nim", "self-hosted-nim": "self-hosted-nim",
    "self-nim": "self-hosted-nim", "openrouter": "openrouter",
    "open-router": "openrouter", "openrouter.ai": "openrouter", "or": "openrouter",
    "fireworks": "fireworks", "fireworks.ai": "fireworks", "fireworks-ai": "fireworks",
    "fw": "fireworks",
}
OPENCODE_ENDPOINT_ALIASES = {
    "messages": "anthropic-messages", "anthropic": "anthropic-messages",
    "anthropic-messages": "anthropic-messages", "chat": "openai-chat",
    "openai-chat": "openai-chat", "chat-completions": "openai-chat",
    "responses": "openai-responses", "openai-responses": "openai-responses",
    "gemini": "google-generative", "google": "google-generative",
    "google-generative": "google-generative",
}
OFFICIAL_CHANNEL_PLUGINS = {
    "telegram": "plugin:telegram@claude-plugins-official",
    "discord": "plugin:discord@claude-plugins-official",
    "imessage": "plugin:imessage@claude-plugins-official",
    "fakechat": "plugin:fakechat@claude-plugins-official",
}

APP_NAME = "Ciel Runtime"
VERSION = "0.1.1"
CREDITS = "Credits: One Ciel LLC"
PRELAUNCH_CANCEL = 10
PRELAUNCH_LAUNCH_CODEX = 11
PRELAUNCH_LAUNCH_CLAUDE = 12
PRELAUNCH_LAUNCH_AGY = 13
PRELAUNCH_LAUNCH_CODEX_APP_SERVER = 14
ROUTER_LOG_MAX_BYTES = 1_000_000
REQUEST_DUMP_MAX_BYTES = 5_000_000
RESPONSE_DUMP_MAX_BYTES = 5_000_000
RESPONSE_DUMP_TEXT_LIMIT = 16_000
SSE_TRACE_MAX_BYTES = 2 * 1024 * 1024
SSE_TRACE_EVENT_LIMIT = 240
SSE_TRACE_PAYLOAD_LIMIT = 4_000
CHAT_MESSAGES_MAX_BYTES = 20_000_000
CHAT_MESSAGE_DEDUPE_SCAN_LIMIT = 500
CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS = 30.0
CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT = 600.0
DEFAULT_REQUEST_TIMEOUT_MS = 300000
BUILTIN_CHANNEL_SPEC = "server:ciel-runtime-router"
CHANNEL_LLM_WAKE_PREFIX = "[external input pending]"
CHANNEL_LLM_WAKE_LEGACY_PREFIXES = ("[ciel-runtime channel wake]", "[channel pending]")
MCP_PROXY_TOOL_RESULT_MAX_CHARS_DEFAULT = 24000
MCP_PROXY_TOOL_RESULT_ITEM_TEXT_CHARS = 6000
ADVISOR_FEEDBACK_MARKER = "CIEL_RUNTIME_ADVISOR_FEEDBACK"
PLAN_GUARD_MARKER = "[ciel-runtime-plan-guard]"
PLAN_MODE_SELF_TOOLS = ("EnterPlanMode", "ExitPlanMode")
ANTHROPIC_THINKING_BLOCK_TYPES = ("thinking", "redacted_thinking")
DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC = (
    "EnterWorktree", "ExitWorktree", "TeamCreate", "TeamDelete", "TeammateTool",
    "SendMessage", "SendMessageTool", "ScheduleWakeup", "WaitForMcpServers",
    "WebSearch", "web_search", "WebFetch", "web_fetch", "RemoteTrigger", "PushNotification",
)
CLAUDE_SERVER_SIDE_WEB_TOOLS = ("WebSearch", "WebFetch")
OPENAI_COMPATIBLE_ROUTER_PROVIDERS = (
    "vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "openrouter"
)
CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS = (
    *OPENAI_COMPATIBLE_ROUTER_PROVIDERS,
    "kimi",
    "fireworks",
)
AUTO_DETECT_NATIVE_COMPAT_PROVIDERS = ("vllm", "lm-studio", "self-hosted-nim")
CLAUDE_ANTHROPIC_ENDPOINT_PROVIDERS = ("deepseek", "kimi", "zai", "fireworks")
ROUTED_COMPAT_PROMPT = (
    "You are running inside Claude Code through the ciel-runtime router. Do not stop after announcing what you plan to do. "
    "When the user asks you to create, edit, or run code, immediately use the available Claude Code tools such as Write, Edit, Read, and Bash as appropriate, "
    "except while Claude Code is in Plan Mode. In Plan Mode, first explore/read as needed, write or update the plan file named by the plan_mode attachment, "
    "and only then call ExitPlanMode to leave Plan Mode; when bypass permissions is active, ciel-runtime auto-approves that plan exit, so do not ask the user separately "
    "and do not call EnterPlanMode again. then report the concrete result. If the task has several reasonable implementation parts, do all in-scope parts; do not ask the user "
    "which part to start or whether to do all unless the user explicitly requested a choice. If you decide not to use tools, provide the complete requested code or answer in the same turn. "
    "Use skills only when the user's request clearly matches that skill; never invoke keybindings-help unless the user asks about keybindings. Keep final answers concise and do not expose hidden chain-of-thought. "
    "When calling Claude Code tools, use exactly the tool schema and do not invent extra fields. Bash: command (string), description (string), timeout (integer), run_in_background (boolean). "
    "Read: file_path (string), offset (integer), limit (integer). Write: file_path (string), content (string). Edit: file_path (string), old_string (string), new_string (string), replace_all (boolean). "
    "TaskList: no input. TaskUpdate: taskId (string), optional status enum exactly one of pending, in_progress, completed, deleted. CronCreate: cron (standard 5-field local-time cron string), "
    "prompt (string), optional recurring (boolean), optional durable (boolean). CronDelete: id (string returned by CronCreate). CronList: no input. Do not call WaitForMcpServers; it is a Claude Code lifecycle tool "
    "that may exist but is often not enabled in the current routed context. If an MCP server appears disconnected, use only tools present in the current tool list, retry ordinary MCP tools when available, "
    "or report the concrete connection state. Never write pseudo tool calls, partial JSON, or markdown code fences when a real Claude Code tool call is required."
)
NON_ANTHROPIC_COMPAT_PROMPT = ROUTED_COMPAT_PROMPT
LANGUAGES = {"en": "English", "ko": "한국어", "ja": "日本語", "zh": "中文"}
MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "glm-5.2": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 999424},
    "glm-5.2:cloud": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 999424},
    "glm-4.7": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "glm-5.1": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "glm-4.7:cloud": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "glm-5.1:cloud": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "qwen3-coder": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 65536},
    "qwen3-coder:30b": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 65536},
    "qwen3.6:27b": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 65536},
    "deepseek-r1": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "llama3.3:70b": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 131072},
}
LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT = 32768
LM_STUDIO_DEFAULT_CLAUDE_CODE_CONTEXT = 65536
__all__ = [name for name in globals() if name.isupper()]
