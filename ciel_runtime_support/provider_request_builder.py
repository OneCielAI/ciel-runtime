"""Build provider wire requests from normalized Anthropic messages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER


def _remote_client_reasoning_effort(body: Mapping[str, Any]) -> str | None:
    candidates: list[Any] = []
    if "reasoning_effort" in body:
        candidates.append(body.get("reasoning_effort"))
    for field in ("reasoning", "output_config", "thinking"):
        value = body.get(field)
        if isinstance(value, Mapping) and "effort" in value:
            candidates.append(value.get("effort"))
    thinking = body.get("thinking")
    if (
        isinstance(thinking, Mapping)
        and str(thinking.get("type") or "").strip().lower() == "disabled"
    ):
        candidates.append("none")
    normalized: list[str] = []
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("remote reasoning effort must be a non-empty string")
        normalized.append(value.strip().lower())
    unique = list(dict.fromkeys(normalized))
    if len(unique) > 1:
        raise ValueError("remote reasoning effort fields conflict")
    return unique[0] if unique else None


@dataclass(frozen=True, slots=True)
class ProviderRequestBudget:
    context_limit: Callable[..., int]
    positive_int: Callable[[Any], int]
    configured_output: Callable[..., int]
    cap_output_ratio: Callable[..., int]
    reserve: Callable[..., int]
    compact_anthropic: Callable[..., dict[str, Any]]
    compact_messages: Callable[..., list[dict[str, Any]]]
    compact_kind: Callable[[dict[str, Any]], str | None]
    cap_output: Callable[..., int]
    write_usage: Callable[..., None]


@dataclass(frozen=True, slots=True)
class OllamaRequestPorts:
    messages: Callable[[dict[str, Any]], list[dict[str, Any]]]
    tools: Callable[[Any], list[dict[str, Any]]]
    context_limit: Callable[[dict[str, Any]], int]
    num_ctx: Callable[..., int | None]
    apply_optional: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAIRequestPorts:
    messages: Callable[..., list[dict[str, Any]]]
    tools: Callable[[Any], list[dict[str, Any]]]
    context_limit: Callable[[str, dict[str, Any]], int]
    reasoning_passback: Callable[[str, str, dict[str, Any]], bool]
    repair_tools: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    reasoning_effort: Callable[..., str | None]
    sampling_allowed: Callable[..., bool]
    omit_tool_choice: Callable[..., bool]
    tool_choice: Callable[[Any], Any]
    normalize_request: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ProviderOptionPorts:
    sampling_providers: frozenset[str]
    sampling_options: tuple[str, ...]
    anthropic_runtime_hints: Callable[[str], dict[str, Any]]
    log: Callable[[str, str], None]
    finalize_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] = (
        lambda messages: messages
    )
    normalize_anthropic_request: Callable[
        [str, dict[str, Any], dict[str, Any], str], dict[str, Any]
    ] = lambda _provider, _config, body, _protocol: body


class ProviderRequestBuilder:
    def __init__(
        self,
        budget: ProviderRequestBudget,
        ollama: OllamaRequestPorts,
        openai: OpenAIRequestPorts,
        options: ProviderOptionPorts,
    ) -> None:
        self.budget = budget
        self.ollama = ollama
        self.openai = openai
        self.options = options

    def cap_anthropic_body(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        capped = dict(body)
        if config.get(REMOTE_BRIDGE_CONFIG_MARKER):
            # A network client owns its prompt and output budget.  Host-local
            # compaction both mutates that request and records local activity,
            # so let the selected upstream return its normal context error.
            return capped
        compact_kind = self.budget.compact_kind(capped)
        local_compact_refresh = bool(compact_kind)
        # Anthropic/Claude owns persistent compaction for its native Messages
        # protocol.  The JSON-size estimator below is only a provider-neutral
        # fallback and must never rewrite an ordinary native Claude turn.
        # The sole exception is an explicitly translated Codex checkpoint,
        # whose client cannot perform Anthropic-native compaction itself.
        if provider == "anthropic" and compact_kind != "codex":
            return capped
        context_limit = (
            self.budget.context_limit(provider, config)
            or self.budget.positive_int(config.get("max_model_len"))
            or self.budget.positive_int(config.get("context_window"))
            or (32768 if provider == "vllm" else 0)
        )
        if not context_limit:
            return capped
        configured = self.budget.configured_output(config, capped)
        ratio_capped = self.budget.cap_output_ratio(provider, config, configured)
        if ratio_capped:
            capped["max_tokens"] = ratio_capped
        reserve = self.budget.reserve(config, context_limit)
        output_reserve = self.budget.positive_int(capped.get("max_tokens")) or configured or 4096
        input_budget = max(8192, context_limit - output_reserve - reserve)

        capped = self.budget.compact_anthropic(
            capped,
            input_budget,
            provider=provider,
            pcfg=config,
            model=str(capped.get("model") or config.get("current_model") or ""),
            full_compact_request=local_compact_refresh,
            compact_runtime=compact_kind if local_compact_refresh else None,
        )
        output_tokens = self.budget.cap_output(
            config,
            capped,
            {key: value for key, value in capped.items() if key != "max_tokens"},
            context_limit,
            self.budget.positive_int(capped.get("max_tokens")) or configured,
        )
        if output_tokens:
            capped["max_tokens"] = output_tokens
        return capped

    def apply_options(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        if config.get(REMOTE_BRIDGE_CONFIG_MARKER):
            return body
        if provider not in self.options.sampling_providers:
            return body
        projected = dict(body)
        for key in self.options.sampling_options:
            if config.get(key) is not None:
                projected[key] = config[key]
        return projected

    def normalize_anthropic_options(
        self,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        model_id: str,
    ) -> dict[str, Any]:
        projected = body
        if provider == "anthropic":
            unsupported = self.options.anthropic_runtime_hints(model_id).get(
                "unsupported_sampling_parameters"
            )
            if isinstance(unsupported, list) and unsupported:
                projected = dict(body)
                removed = [
                    key
                    for key in unsupported
                    if isinstance(key, str) and key in projected
                ]
                for key in removed:
                    projected.pop(key, None)
                if removed:
                    self.options.log(
                        "INFO",
                        f"anthropic_request_options_removed model={model_id} "
                        f"keys={','.join(removed)}",
                    )
        return self.options.normalize_anthropic_request(
            provider, config, projected, "anthropic_messages"
        )

    def ollama_chat(
        self,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
        *,
        stream: bool = True,
        provider: str = "ollama",
    ) -> dict[str, Any]:
        remote_bridge = bool(config.get(REMOTE_BRIDGE_CONFIG_MARKER))
        include_memory = not remote_bridge
        messages = self.ollama.messages(body, include_memory=include_memory)
        tools = self.ollama.tools(body.get("tools"))
        context_limit = self.ollama.context_limit(config)
        configured = (
            self.budget.positive_int(body.get("max_tokens"))
            if remote_bridge
            else self.budget.configured_output(config, body, "num_predict")
        )
        reserve = self.budget.reserve(config, context_limit)
        output_reserve = configured or self.budget.positive_int(body.get("max_tokens")) or 4096
        compact_kind = self.budget.compact_kind(body)
        local_compact_refresh = bool(compact_kind)
        payload = {"messages": messages, "tools": tools}
        if not remote_bridge:
            messages = self.budget.compact_messages(
                messages,
                tools,
                max(8192, context_limit - output_reserve - reserve),
                provider=provider,
                model=model,
                pcfg=config,
                full_compact_request=local_compact_refresh,
                compact_runtime=compact_kind if local_compact_refresh else None,
                wire="ollama",
            )
        payload["messages"] = messages
        if not remote_bridge:
            self.budget.write_usage(provider, config, payload, "ollama_upstream")
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            request["tools"] = tools
        token_cache: dict[int, int] = {}
        num_ctx = self.ollama.num_ctx(config, payload, _token_cache=token_cache)
        if not num_ctx:
            num_ctx = self.ollama.context_limit(config)
        num_predict = configured
        if not remote_bridge:
            num_predict = self.budget.cap_output(
                config,
                body,
                payload,
                num_ctx,
                configured,
                _token_cache=token_cache,
            )
        wire_config = config
        if remote_bridge:
            wire_config = dict(config)
            wire_config.pop("max_output_tokens", None)
            wire_config.pop("num_predict", None)
            raw_options = config.get("ollama_options")
            wire_config["ollama_options"] = {
                key: value
                for key, value in (
                    raw_options.items() if isinstance(raw_options, dict) else ()
                )
                if key != "num_predict"
            }
            for marker in (
                "ollama_explicit_options",
                "ollama_transient_options",
            ):
                raw_markers = config.get(marker)
                if isinstance(raw_markers, (list, tuple, set)):
                    wire_config[marker] = [
                        value
                        for value in raw_markers
                        if str(value) != "num_predict"
                    ]
            wire_config["output_tokens_explicit"] = bool(configured)
        request = self.ollama.apply_optional(
            request,
            provider,
            model,
            wire_config,
            body,
            output_limit=num_predict,
        )
        if not remote_bridge:
            request["messages"] = self.options.finalize_messages(
                request["messages"]
            )
        return request

    def openai_chat(
        self,
        provider: str,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        remote_bridge = bool(config.get(REMOTE_BRIDGE_CONFIG_MARKER))
        passback = self.openai.reasoning_passback(provider, model, config)
        include_memory = not remote_bridge
        messages = self.openai.messages(
            body,
            reasoning_passback=passback,
            include_memory=include_memory,
        )
        tools = self.openai.tools(body.get("tools"))
        context_limit = self.openai.context_limit(provider, config)
        configured = (
            self.budget.positive_int(body.get("max_tokens"))
            if remote_bridge
            else self.budget.configured_output(config, body)
        )
        reserve = self.budget.reserve(config, context_limit)
        output_reserve = configured or self.budget.positive_int(body.get("max_tokens")) or 4096
        compact_kind = self.budget.compact_kind(body)
        local_compact_refresh = bool(compact_kind)
        if not remote_bridge:
            messages = self.budget.compact_messages(
                messages,
                tools,
                max(8192, context_limit - output_reserve - reserve),
                provider=provider,
                model=model,
                pcfg=config,
                full_compact_request=local_compact_refresh,
                compact_runtime=compact_kind if local_compact_refresh else None,
                wire="openai",
            )
            messages = self.openai.repair_tools(messages)
        request: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if remote_bridge:
            client_effort = _remote_client_reasoning_effort(body)
            if client_effort is None:
                reasoning_effort = None
            else:
                effort_body = dict(body)
                effort_body["reasoning_effort"] = client_effort
                metadata = body.get("metadata")
                effort_body["metadata"] = {
                    **(dict(metadata) if isinstance(metadata, Mapping) else {}),
                    "ciel_runtime_reasoning_effort": client_effort,
                }
                effort_config = dict(config)
                effort_config.pop("effort_level", None)
                reasoning_effort = self.openai.reasoning_effort(
                    provider, model, effort_body, effort_config
                ) or client_effort
        else:
            reasoning_effort = self.openai.reasoning_effort(
                provider, model, body, config
            )
        if reasoning_effort:
            request["reasoning_effort"] = reasoning_effort
        if tools:
            request["tools"] = tools
        if body.get("tool_choice") is not None and not self.openai.omit_tool_choice(
            provider, model, body, config
        ):
            request["tool_choice"] = self.openai.tool_choice(body.get("tool_choice"))
        if configured:
            request["max_tokens"] = configured
        if isinstance(body.get("response_format"), dict):
            request["response_format"] = dict(body["response_format"])
        for key in ("temperature", "top_p"):
            value = (
                body.get(key)
                if remote_bridge
                else config.get(key)
            )
            if self.openai.sampling_allowed(provider, config) and value is not None:
                request[key] = value
        normalized = self.openai.normalize_request(provider, config, request)
        if not remote_bridge:
            normalized["messages"] = self.options.finalize_messages(
                normalized["messages"]
            )
        return normalized


@dataclass(frozen=True, slots=True)
class ProviderRequestCompatibilityApi:
    builder: Callable[[], ProviderRequestBuilder]

    def cap_anthropic_body(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        return self.builder().cap_anthropic_body(provider, config, body)

    def apply_options(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        return self.builder().apply_options(provider, config, body)

    def normalize_anthropic_options(
        self,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        model_id: str,
    ) -> dict[str, Any]:
        return self.builder().normalize_anthropic_options(
            provider, config, body, model_id
        )

    def ollama_chat(
        self,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
        stream: bool = True,
        provider: str = "ollama",
    ) -> dict[str, Any]:
        return self.builder().ollama_chat(
            model, body, config, stream=stream, provider=provider
        )

    def openai_chat(
        self,
        provider: str,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
        stream: bool = False,
    ) -> dict[str, Any]:
        return self.builder().openai_chat(
            provider, model, body, config, stream=stream
        )
