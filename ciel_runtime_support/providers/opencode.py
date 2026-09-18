"""OpenCode Zen provider adapter."""

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Mapping

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .base import (
    HttpBearerProviderAdapter,
    configuration_policy,
    provider_configuration,
)
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS
from .opencode_catalog import (
    OPENCODE_UNION_ALPHA_CONTEXT_WINDOW,
    OPENCODE_UNION_ALPHA_MAX_OUTPUT_TOKENS,
    OPENCODE_ZEN_MODEL_PROTOCOLS,
)


OPENCODE_ZEN_OX_ALPHA_FREE_MODEL = "x-preview-f-free"
OPENCODE_GO_OX_ALPHA_FREE_MODEL = "ox-alpha-free"
# Latest OpenCode CLI release (2026-09-17, GitHub tag v1.18.31) and the Bun
# runtime it embeds. Together with the bundled ai-sdk version these form the
# User-Agent the OpenCode client sends (captured 2026-09-18 from
# opencode 1.18.31), e.g.
#   opencode/1.18.31 ai-sdk/provider-utils/4.0.46 runtime/bun/1.3.14
OPENCODE_CLIENT_VERSION = "1.18.31"
OPENCODE_BUN_VERSION = "1.3.14"
# One @ai-sdk/provider-utils version per wire, from the ai-sdk packages the
# client bundles (bun.lock: @ai-sdk/anthropic 3.0.111, @ai-sdk/openai 3.0.88,
# @ai-sdk/openai-compatible 2.0.41); both live captures agree.
_OPENCODE_PROVIDER_UTILS_BY_PROTOCOL = {
    "anthropic_messages": "4.0.46",
    "openai_responses": "4.0.40",
    "openai_chat": "4.0.23",
}

# packages/schema/src/identifier.ts: 26 characters, six timestamp-derived bytes
# rendered as hex followed by fourteen characters of this alphabet. Sessions
# are created with descending() and messages with ascending(); the raw IDs
# recorded on this machine (ses_f4ed90377ffe..., msg_0b126fcdb001a...)
# reproduce exactly with the same millisecond timestamp and a per-millisecond
# counter starting at one.
_OPENCODE_ID_MASK = (1 << 48) - 1
_OPENCODE_ID_ALPHABET = (
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_OPENCODE_ID_LOCK = threading.Lock()
_OPENCODE_ID_STATE = {"timestamp": 0, "counter": 0}


def _new_opencode_id(prefix: str, *, descending: bool) -> str:
    timestamp = int(time.time() * 1000)
    with _OPENCODE_ID_LOCK:
        if timestamp != _OPENCODE_ID_STATE["timestamp"]:
            _OPENCODE_ID_STATE["timestamp"] = timestamp
            _OPENCODE_ID_STATE["counter"] = 0
        _OPENCODE_ID_STATE["counter"] += 1
        counter = _OPENCODE_ID_STATE["counter"]
    current = timestamp * 0x1000 + counter
    if descending:
        current = (~current) & _OPENCODE_ID_MASK
    else:
        current = current & _OPENCODE_ID_MASK
    random_part = "".join(
        _OPENCODE_ID_ALPHABET[byte % 62] for byte in secrets.token_bytes(14)
    )
    return f"{prefix}{current:012x}{random_part}"


def new_opencode_session_id() -> str:
    return _new_opencode_id("ses_", descending=True)


def new_opencode_message_id() -> str:
    return _new_opencode_id("msg_", descending=False)


# One router process serves one workspace conversation, so its own requests
# (advisor, compaction, probes) share one stable session for routing/caching.
_ROUTER_SESSION_ID = new_opencode_session_id()


@dataclass(frozen=True)
class OpenCodeProviderAdapter(HttpBearerProviderAdapter):
    name: str = "opencode"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "claude-sonnet-4-6",
            custom_models=(
                "claude-sonnet-4-6",
                OPENCODE_ZEN_OX_ALPHA_FREE_MODEL,
                *(model for model in OPENCODE_ZEN_MODEL_PROTOCOLS if model != "claude-sonnet-4-6"),
            ),
            native_compat=True,
            context_window=200000,
            max_output_tokens=8192,
            context_reserve_tokens=8192,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ip_family="ipv6-preferred",
            haiku_model="claude-haiku-4-5",
            subagent_model="claude-sonnet-4-6",
            model_endpoints={OPENCODE_ZEN_OX_ALPHA_FREE_MODEL: "openai-chat"},
        )
    )
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "OpenCode Zen"
    api_key_launch_error_value: str = (
        "Launch blocked: OpenCode Zen requires a OpenCode Zen API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/messages",
            models_path="/v1/models",
            probe_strategy="opencode",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            allow_configured_fallback=True,
            allow_public_without_auth=True,
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, object], str | None]:
        if self.normalize_model_id(config.model).casefold() != "union-alpha":
            return {}, None
        return (
            {
                "context_window": OPENCODE_UNION_ALPHA_CONTEXT_WINDOW,
                "max_model_len": OPENCODE_UNION_ALPHA_CONTEXT_WINDOW,
                "max_output_tokens": OPENCODE_UNION_ALPHA_MAX_OUTPUT_TOKENS,
                "supports_vision": True,
                "model_profile": "opencode-union-alpha-262k",
            },
            "OpenCode Union Alpha profile applied: 262,144-token context and "
            "131,072-token maximum output.",
        )

    # The zen gateway answers 403 FreeTierError unless the request declares both
    # a ``bash`` and a ``read`` tool (probed 2026-09-18: every declared set
    # without both is refused, every set containing both is served). Codex
    # declares shell/exec and Claude Code declares capitalised Bash/Read, so
    # neither passes on its own.
    _OPENCODE_GATE_TOOL_NAMES = ("bash", "read")

    @staticmethod
    def _opencode_gate_tool(name: str, protocol: MessageProtocol) -> Mapping[str, object]:
        description = (
            "Declared for OpenCode CLI compatibility. Never call this tool; "
            "use the client's own tools instead."
        )
        if str(protocol) == "anthropic_messages":
            return {
                "name": name,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
        if str(protocol) == "openai_responses":
            return {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "strict": True,
            }
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }

    def normalize_request_options_for_protocol(
        self,
        config: ProviderConfig,
        request: Mapping[str, object],
        protocol: MessageProtocol | None,
    ) -> Mapping[str, object]:
        normalized = dict(super().normalize_request_options_for_protocol(config, request, protocol))
        tools = normalized.get("tools")
        if tools is None:
            # Requests without a tools array (title generation, compaction
            # helpers) still have to declare the gate's two tools.
            tools = []
        elif not isinstance(tools, list):
            return normalized
        declared = set()
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            nested = tool.get("function")
            name = tool.get("name") or (
                nested.get("name") if isinstance(nested, Mapping) else None
            )
            if name:
                declared.add(str(name))
        missing = [name for name in self._OPENCODE_GATE_TOOL_NAMES if name not in declared]
        if not missing:
            return normalized
        wire = protocol or self.select_protocol("anthropic_messages", config)
        normalized["tools"] = [
            *tools,
            *(self._opencode_gate_tool(name, wire) for name in missing),
        ]
        return normalized

    def opencode_client_headers(
        self, config: ProviderConfig, *, session_id: str, request_id: str
    ) -> Mapping[str, str]:
        """Return the identity header set the OpenCode client sends.

        packages/opencode/src/session/llm/request.ts builds these for every
        provider whose id starts with opencode: User-Agent
        opencode/<version> decorated by the ai-sdk transport, the client flag
        (OPENCODE_CLIENT, default cli), the project id, a session id and a
        per-message request id.
        """

        protocol = self.select_protocol("anthropic_messages", config)
        provider_utils = _OPENCODE_PROVIDER_UTILS_BY_PROTOCOL.get(
            str(protocol), "4.0.46"
        )
        return {
            "x-opencode-session": session_id,
            "x-opencode-request": request_id,
            "x-opencode-client": "cli",
            # Non-repository workspaces get the literal project id global
            # (packages/core/src/project.ts); that is the shape the client
            # sends from a directory that is not a repository.
            "x-opencode-project": "global",
            "user-agent": (
                f"opencode/{OPENCODE_CLIENT_VERSION} "
                f"ai-sdk/provider-utils/{provider_utils} "
                f"runtime/bun/{OPENCODE_BUN_VERSION}"
            ),
        }

    def request_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
        *,
        router_originated: bool = False,
    ) -> Mapping[str, str]:
        # Every request to the opencode gateway carries the OpenCode client
        # identity, not only the router's own: the zen free tier answers 403
        # FreeTierError to a request without it (probed 2026-09-18), and the
        # tools rule below is enforced alongside it. Client headers are still
        # forwarded; only the identity set is replaced.
        del router_originated
        headers = dict(self.build_headers(config, api_key))
        headers.update(
            self.opencode_client_headers(
                config,
                session_id=_ROUTER_SESSION_ID,
                request_id=new_opencode_message_id(),
            )
        )
        return headers

    def session_headers(self, config: ProviderConfig) -> Mapping[str, str]:
        # Router-originated requests (advisor, compaction, probes) present the
        # OpenCode CLI identity with the router's own stable session. Without
        # the session header Go answers 400 MissingSessionID.
        return self.opencode_client_headers(
            config,
            session_id=_ROUTER_SESSION_ID,
            request_id=new_opencode_message_id(),
        )

    def compatibility_headers(self, config: ProviderConfig) -> Mapping[str, str]:
        # A compatibility probe is its own short conversation, so it gets a
        # fresh session rather than the router's; Zen and Go both route through
        # the same gateway and expect the same client identity.
        return self.opencode_client_headers(
            config,
            session_id=new_opencode_session_id(),
            request_id=new_opencode_message_id(),
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        return bool(config.options.get("native_compat", True)) and (
            self.select_protocol("anthropic_messages", config, model)
            == "anthropic_messages"
        )

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True,
            show_tool_choice=True,
            show_stream=True,
            show_ip_family=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del operation
        raw_model = str(model or config.model or "").strip()
        overrides = config.options.get("model_endpoints")
        if isinstance(overrides, Mapping):
            raw = overrides.get(raw_model)
            key = str(raw or "").strip().lower().replace("_", "-")
            mapped = {
                "anthropic": "anthropic_messages",
                "anthropic-messages": "anthropic_messages",
                "messages": "anthropic_messages",
                "openai": "openai_chat",
                "openai-chat": "openai_chat",
                "chat": "openai_chat",
                "openai-responses": "openai_responses",
                "responses": "openai_responses",
                "google-generative": "google_generative",
                "gemini": "google_generative",
            }.get(key)
            if mapped is not None:
                return mapped
        normalized = raw_model.split("[", 1)[0].lower()
        for prefix in ("ciel-runtime-opencode-go-", "ciel-runtime-opencode-"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break
        documented = self.documented_model_protocols().get(normalized)
        if documented is not None:
            return documented
        if self.name == "opencode-go":
            if normalized.startswith(("gpt-", "grok-", "muse-spark-")):
                return "openai_responses"
            if normalized == OPENCODE_GO_OX_ALPHA_FREE_MODEL:
                return "openai_chat"
            if normalized == "hy3":
                return "openai_chat"
            if normalized.startswith(("glm-", "kimi-", "deepseek-", "mimo-", "hy3-")):
                return "openai_chat"
            return "anthropic_messages"
        if normalized.startswith(("gpt-", "grok-", "muse-spark-")):
            return "openai_responses"
        if normalized.startswith("gemini-"):
            return "google_generative"
        if normalized == OPENCODE_ZEN_OX_ALPHA_FREE_MODEL:
            return "openai_chat"
        if normalized.startswith(
            (
                "minimax-",
                "glm-",
                "kimi-",
                "big-pickle",
                "deepseek-",
                "hy3-",
                "laguna-",
                "mimo-",
                "nemotron-",
                "north-",
            )
        ):
            return "openai_chat"
        return "anthropic_messages"

    def documented_model_protocols(self) -> Mapping[str, MessageProtocol]:
        return OPENCODE_ZEN_MODEL_PROTOCOLS

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        return frozenset({self.select_protocol("anthropic_messages", config, model)})

    def openai_reasoning_passback_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        requested = self.normalize_model_id(str(model or ""))
        prefix = f"ciel-runtime-{self.name}-"
        if requested.startswith(prefix):
            requested = requested[len(prefix) :]
        elif requested.startswith("ciel-runtime-"):
            requested = config.model
        model_id = self.normalize_model_id(requested or config.model).lower()
        return model_id.startswith("deepseek-") and self.select_protocol(
            "openai_chat", config, model_id
        ) == "openai_chat"

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="catalog",
            label=self.api_key_display_name_value,
            catalog_path="/v1/models",
        )

    def model_panel_badge(self, config: ProviderConfig, model: str) -> str:
        protocol = self.select_protocol("anthropic_messages", config, model)
        label = {
            "anthropic_messages": "messages",
            "openai_chat": "chat",
            "openai_responses": "responses unsupported",
            "google_generative": "gemini unsupported",
        }.get(protocol, str(protocol))
        overrides = config.options.get("model_endpoints")
        if isinstance(overrides, Mapping) and model in overrides:
            label += " override"
        return label

    def project_router_model_metadata(
        self, config: ProviderConfig, model_id: str
    ) -> Mapping[str, object]:
        protocol = self.select_protocol("anthropic_messages", config, model_id)
        endpoint = {
            "anthropic_messages": "anthropic-messages",
            "openai_chat": "openai-chat",
            "openai_responses": "openai-responses",
            "google_generative": "google-generative",
        }.get(protocol, str(protocol).replace("_", "-"))
        return {
            "opencode_endpoint": endpoint,
            "router_supported": protocol in {"anthropic_messages", "openai_chat"},
        }

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return configuration_policy(supports_model_endpoint_overrides=True)


__all__ = [
    "OPENCODE_BUN_VERSION",
    "OPENCODE_CLIENT_VERSION",
    "OPENCODE_GO_OX_ALPHA_FREE_MODEL",
    "OPENCODE_ZEN_OX_ALPHA_FREE_MODEL",
    "OpenCodeProviderAdapter",
    "new_opencode_message_id",
    "new_opencode_session_id",
]
