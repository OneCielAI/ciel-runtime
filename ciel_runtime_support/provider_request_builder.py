"""Build provider wire requests from normalized Anthropic messages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderRequestBudget:
    context_limit: Callable[..., int]
    positive_int: Callable[[Any], int]
    configured_output: Callable[..., int]
    cap_output_ratio: Callable[..., int]
    reserve: Callable[..., int]
    compact_anthropic: Callable[..., dict[str, Any]]
    compact_messages: Callable[..., list[dict[str, Any]]]
    compact_requested: Callable[[dict[str, Any]], bool]
    cap_output: Callable[..., int]
    write_usage: Callable[..., None]


@dataclass(frozen=True, slots=True)
class OllamaRequestPorts:
    messages: Callable[[dict[str, Any]], list[dict[str, Any]]]
    tools: Callable[[Any], list[dict[str, Any]]]
    extra_options: Callable[[dict[str, Any]], dict[str, Any]]
    context_limit: Callable[[dict[str, Any]], int]
    num_ctx: Callable[..., int]
    think_enabled: Callable[[str | None, dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class OpenAIRequestPorts:
    messages: Callable[..., list[dict[str, Any]]]
    tools: Callable[[Any], list[dict[str, Any]]]
    context_limit: Callable[[str, dict[str, Any]], int]
    reasoning_passback: Callable[[str, str, dict[str, Any]], bool]
    repair_tools: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    is_kimi_k3: Callable[[str], bool]
    omit_tool_choice: Callable[..., bool]
    tool_choice: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class ProviderOptionPorts:
    sampling_providers: frozenset[str]
    sampling_options: tuple[str, ...]
    anthropic_runtime_hints: Callable[[str], dict[str, Any]]
    log: Callable[[str, str], None]


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
        if provider == "anthropic":
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
            full_compact_request=self.budget.compact_requested(capped),
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
        body: dict[str, Any],
        model_id: str,
    ) -> dict[str, Any]:
        if provider != "anthropic":
            return body
        unsupported = self.options.anthropic_runtime_hints(model_id).get(
            "unsupported_sampling_parameters"
        )
        if not isinstance(unsupported, list) or not unsupported:
            return body
        projected = dict(body)
        removed = [key for key in unsupported if isinstance(key, str) and key in projected]
        for key in removed:
            projected.pop(key, None)
        if removed:
            self.options.log(
                "INFO",
                f"anthropic_request_options_removed model={model_id} "
                f"keys={','.join(removed)}",
            )
        return projected

    def ollama_chat(
        self,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
        *,
        stream: bool = True,
        provider: str = "ollama",
    ) -> dict[str, Any]:
        messages = self.ollama.messages(body)
        tools = self.ollama.tools(body.get("tools"))
        context_limit = self.ollama.context_limit(config)
        configured = self.budget.configured_output(config, body, "num_predict")
        reserve = self.budget.reserve(config, context_limit)
        output_reserve = configured or self.budget.positive_int(body.get("max_tokens")) or 4096
        payload = {"messages": messages, "tools": tools}
        messages = self.budget.compact_messages(
            messages,
            tools,
            max(8192, context_limit - output_reserve - reserve),
            provider=provider,
            model=model,
            pcfg=config,
            full_compact_request=self.budget.compact_requested(body),
            wire="ollama",
        )
        payload["messages"] = messages
        self.budget.write_usage(provider, config, payload, "ollama_upstream")
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "think": self.ollama.think_enabled(model, config),
        }
        if config.get("keep_alive"):
            request["keep_alive"] = str(config["keep_alive"])
        if tools:
            request["tools"] = tools
        options = self.ollama.extra_options(config)
        token_cache: dict[int, int] = {}
        num_ctx = self.ollama.num_ctx(config, payload, _token_cache=token_cache)
        num_predict = self.budget.cap_output(
            config,
            body,
            payload,
            num_ctx,
            configured,
            _token_cache=token_cache,
        )
        if num_predict:
            options["num_predict"] = num_predict
        if num_ctx:
            options.setdefault("num_ctx", num_ctx)
        if options:
            request["options"] = options
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
        passback = self.openai.reasoning_passback(provider, model, config)
        messages = self.openai.messages(body, reasoning_passback=passback)
        tools = self.openai.tools(body.get("tools"))
        context_limit = self.openai.context_limit(provider, config)
        configured = self.budget.configured_output(config, body)
        reserve = self.budget.reserve(config, context_limit)
        output_reserve = configured or self.budget.positive_int(body.get("max_tokens")) or 4096
        messages = self.budget.compact_messages(
            messages,
            tools,
            max(8192, context_limit - output_reserve - reserve),
            provider=provider,
            model=model,
            pcfg=config,
            full_compact_request=self.budget.compact_requested(body),
            wire="openai",
        )
        messages = self.openai.repair_tools(messages)
        request: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if provider == "kimi" and self.openai.is_kimi_k3(model):
            request["reasoning_effort"] = "max"
        if tools:
            request["tools"] = tools
        if body.get("tool_choice") is not None and not self.openai.omit_tool_choice(
            provider, model, body, config
        ):
            request["tool_choice"] = self.openai.tool_choice(body.get("tool_choice"))
        if configured:
            request["max_tokens"] = configured
        for key in ("temperature", "top_p"):
            if config.get(key) is not None:
                request[key] = config[key]
        return request
