"""CielAiRouter provider adapter.

CielAiRouter (a fork of OmniRoute) serves Anthropic Messages, OpenAI Chat
Completions and OpenAI Responses from one server root with a router API key.
Each runtime keeps the wire it speaks natively: Claude Code -> /v1/messages,
Codex -> /v1/responses, everything else -> /v1/chat/completions.

The model list is per key and changes with connection state, so it is read
the way the CielAiRouter VS Code extension (packages/ciel-copilot) reads it:
``/v1/models?prefix=alias&configuredOnly=true&availableOnly=true`` lets the
server keep only routable models, then rows that are not chat models or are
duplicate-prefix mirrors are dropped (``selectChatModels``).  Ciel launches
agent CLIs, so only models whose catalog entry supports both tool calling and
thinking are offered.
"""

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .base import OpenAICompatibleProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


# The combo verified in CielAiRouter's own client doc; its catalog entry
# (GET /v1/models, 2026-10-02) advertised an 872,000-token context with tool
# calling and thinking.  No output limit is persisted as a default: config
# loading merges defaults back in, which would pin Claude Code's output limit
# for Claude models; the catalog profile sets it per selected model instead.
CIELAIROUTER_DEFAULT_MODEL = "ASTRA"
CIELAIROUTER_DEFAULT_CONTEXT_WINDOW = 872_000
CIELAIROUTER_MODELS_PATH = (
    "/v1/models?prefix=alias&configuredOnly=true&availableOnly=true"
)
# Surfaces CielAiRouter answers from a conversational request (ciel-copilot
# catalogFilter.ts: Responses-only models are translated for chat clients).
CIELAIROUTER_CONVERSATIONAL_ENDPOINTS = frozenset({"chat", "responses"})
# Canonical effort order; a requested effort the model does not list is
# lowered to the nearest listed tier below it.
CIELAIROUTER_EFFORT_ORDER: tuple[str, ...] = (
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
)
# Efforts the bundled Codex 0.160.0 catalog offers (codex debug models
# --bundled); others in a CielAiRouter tier list are not exposed to Codex.
CODEX_REASONING_EFFORTS: tuple[str, ...] = (
    "low", "medium", "high", "xhigh", "max", "ultra",
)
_EFFORT_SUFFIX = re.compile(
    r"-(?:" + "|".join(CIELAIROUTER_EFFORT_ORDER) + r")$"
)
_CLAUDE_MODEL = re.compile(r"(?:^|[/-])claude-", re.IGNORECASE)


def _capabilities(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    value = entry.get("capabilities")
    return value if isinstance(value, Mapping) else {}


def _supports_thinking(capabilities: Mapping[str, Any]) -> bool:
    return capabilities.get("thinking") is True or capabilities.get("supportsThinking") is True


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def is_chat_model(entry: Mapping[str, Any]) -> bool:
    """A typed row is a specialty model; untyped rows need a chat surface."""

    kind = str(entry.get("type") or "").strip().lower()
    if kind and kind != "chat":
        return False
    endpoints = entry.get("supported_endpoints")
    if isinstance(endpoints, list) and endpoints:
        return any(
            str(endpoint).strip().lower() in CIELAIROUTER_CONVERSATIONAL_ENDPOINTS
            for endpoint in endpoints
        )
    return True


def select_agent_models(entries: list[Any]) -> list[Any]:
    """Keep chat models with tools and thinking; drop duplicate-prefix mirrors."""

    listed = {
        entry.get("id") for entry in entries if isinstance(entry, Mapping) and entry.get("id")
    }
    selected: list[Any] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not entry.get("id"):
            continue
        if not is_chat_model(entry):
            continue
        parent = entry.get("parent")
        if parent and parent != entry.get("id") and parent in listed:
            continue
        capabilities = _capabilities(entry)
        if capabilities.get("tool_calling") is not True or not _supports_thinking(capabilities):
            continue
        selected.append(entry)
    return selected


def lower_to_supported_effort(value: str, tiers: list[str]) -> str:
    """Return ``value`` when listed, else the nearest listed tier below it."""

    if value in tiers or value not in CIELAIROUTER_EFFORT_ORDER:
        return value
    rank = CIELAIROUTER_EFFORT_ORDER.index(value)
    ranked = sorted(
        (CIELAIROUTER_EFFORT_ORDER.index(tier), tier)
        for tier in tiers
        if tier in CIELAIROUTER_EFFORT_ORDER
    )
    if not ranked:
        return value
    lower = [tier for tier_rank, tier in ranked if tier_rank <= rank]
    return lower[-1] if lower else ranked[0][1]


@dataclass(frozen=True)
class CielAiRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    """Runtime-native protocols and catalog-driven limits for CielAiRouter."""

    name: str = "cielairouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["cielairouter"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            CIELAIROUTER_DEFAULT_MODEL,
            native_compat=True,
            context_window=CIELAIROUTER_DEFAULT_CONTEXT_WINDOW,
            max_model_len=CIELAIROUTER_DEFAULT_CONTEXT_WINDOW,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    authorization_header: str = "Authorization"
    include_x_api_key: bool = False
    require_api_key: bool = True
    api_key_display_name_value: str = "CielAiRouter"
    api_key_launch_error_value: str = (
        "Launch blocked: CielAiRouter requires a CielAiRouter API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            requires_api_key=True,
            supports_thinking=True,
            preserves_anthropic_thinking=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path=CIELAIROUTER_MODELS_PATH,
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=(CIELAIROUTER_DEFAULT_MODEL,),
            allow_configured_fallback=True,
            authoritative_upstream_catalog=True,
            per_key_catalog=True,
            reapply_catalog_profile_at_launch=True,
            request_timeout_seconds=10.0,
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        # The readiness GET of the filtered catalog took up to 2.56 s on a cold
        # server cache (measured 2026-10-02) and blocked a launch at the
        # default 2.5 s; keep it well clear of that.
        return replace(super().status_policy(config), probe_timeout_seconds=10.0)

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        # The server root has no /models API; only the filtered catalog applies.
        return (self.request_policy(config).models_path,)

    def anthropic_base_url(self, config: ProviderConfig) -> str:
        return str(config.base_url or self.default_base_url()).rstrip("/")

    def supported_protocols(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat", "openai_responses"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        if operation in self.supported_protocols(config, model):
            return operation
        return "openai_chat"

    def select_model_catalog_entries(self, config: ProviderConfig, data: Any) -> Any:
        del config
        if not isinstance(data, Mapping) or not isinstance(data.get("data"), list):
            return data
        return {**data, "data": select_agent_models(data["data"])}

    def project_model_metadata(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        metadata = dict(super().project_model_metadata(raw))
        for key in ("name", "max_output_tokens", "max_input_tokens", "supported_endpoints"):
            if raw.get(key) is not None:
                metadata[key] = raw[key]
        capabilities = _capabilities(raw)
        if capabilities:
            metadata["capabilities"] = {
                key: value
                for key, value in capabilities.items()
                if key in {"tool_calling", "reasoning", "thinking", "supportsThinking", "effort_tiers", "vision"}
            }
        return metadata

    def catalog_model_configuration(
        self, config: ProviderConfig, info: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], str | None]:
        if not info:
            return {}, None
        model = self.normalize_model_id(config.model)
        root = str(info.get("root") or model.split("/", 1)[-1])
        capabilities = _capabilities(info)
        tiers = [
            str(tier).strip().lower()
            for tier in capabilities.get("effort_tiers") or []
            if str(tier).strip()
        ]
        context = _positive_int(info.get("max_model_len"))
        output_cap = _positive_int(info.get("max_output_tokens"))
        claude = bool(_CLAUDE_MODEL.search(model) or _CLAUDE_MODEL.search(root))
        updates: dict[str, Any] = {
            "catalog_max_output_tokens": output_cap,
            "catalog_effort_tiers": tiers or None,
        }
        if context:
            updates["context_window"] = context
            updates["max_model_len"] = context
        explicit_output = bool(config.options.get("output_tokens_explicit"))
        if claude:
            # Claude Code knows Claude models natively: it picks its own output
            # limit and infers effort/thinking support from the model name,
            # exactly as on the native Anthropic provider.  The catalog limit
            # still bounds what the router forwards.
            updates["claude_code_supported_capabilities"] = None
            if not explicit_output:
                updates["max_output_tokens"] = None
        else:
            supported: list[str] = []
            if any(tier in tiers for tier in ("low", "medium", "high")):
                supported.append("effort")
            if "xhigh" in tiers:
                supported.append("xhigh_effort")
            if "max" in tiers:
                supported.append("max_effort")
            if _supports_thinking(capabilities):
                supported.append("thinking")
            updates["claude_code_supported_capabilities"] = supported or None
            if output_cap and not explicit_output:
                updates["max_output_tokens"] = output_cap
        codex_efforts = [effort for effort in CODEX_REASONING_EFFORTS if effort in tiers]
        templates = [root]
        base_root = _EFFORT_SUFFIX.sub("", root)
        if base_root != root:
            templates.append(base_root)
        codex_catalog: dict[str, Any] = {"ciel_template_slugs": templates}
        if codex_efforts:
            codex_catalog["ciel_reasoning_efforts"] = codex_efforts
        updates["codex_model_catalog"] = codex_catalog
        detail = [f"{context:,}-token context" if context else "provider context"]
        detail.append(
            f"{output_cap:,}-token output limit" if output_cap else "no catalog output limit"
        )
        if tiers:
            detail.append("efforts " + "/".join(tiers))
        return updates, f"CielAiRouter catalog profile applied for {model}: {', '.join(detail)}."

    def openai_reasoning_effort(
        self,
        config: ProviderConfig,
        model: str,
        request: Mapping[str, Any],
    ) -> str | None:
        del config, model
        metadata = request.get("metadata")
        hinted = (
            metadata.get("ciel_runtime_reasoning_effort")
            if isinstance(metadata, Mapping)
            else None
        )
        value = str(request.get("reasoning_effort") or hinted or "").strip().casefold()
        return value or None

    def normalize_request_options_for_protocol(
        self,
        config: ProviderConfig,
        request: Mapping[str, Any],
        protocol: MessageProtocol | None,
    ) -> Mapping[str, Any]:
        normalized = dict(super().normalize_request_options_for_protocol(config, request, protocol))
        requested_model = self.normalize_model_id(str(normalized.get("model") or ""))
        if requested_model and requested_model != self.normalize_model_id(config.model):
            # Limits are cached for the selected model only.
            return normalized
        cap = _positive_int(config.options.get("catalog_max_output_tokens"))
        if cap:
            for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
                value = _positive_int(normalized.get(key))
                if value and value > cap:
                    normalized[key] = cap
        tiers = config.options.get("catalog_effort_tiers")
        if isinstance(tiers, list) and tiers:
            tiers = [str(tier) for tier in tiers]
            effort = normalized.get("reasoning_effort")
            if isinstance(effort, str) and effort:
                normalized["reasoning_effort"] = lower_to_supported_effort(effort.lower(), tiers)
            for key in ("reasoning", "output_config"):
                block = normalized.get(key)
                if isinstance(block, Mapping) and isinstance(block.get("effort"), str):
                    normalized[key] = {
                        **block,
                        "effort": lower_to_supported_effort(str(block["effort"]).lower(), tiers),
                    }
        return normalized


__all__ = [
    "CIELAIROUTER_DEFAULT_MODEL",
    "CIELAIROUTER_MODELS_PATH",
    "CielAiRouterProviderAdapter",
    "is_chat_model",
    "lower_to_supported_effort",
    "select_agent_models",
]
