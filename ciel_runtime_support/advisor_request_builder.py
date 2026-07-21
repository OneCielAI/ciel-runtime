"""Build and decode provider-specific Advisor requests without performing I/O."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ADVISOR_REVIEW_PROMPT = (
    "You are ciel-runtime Advisor, a stronger reviewer model. Review the current task state and provide "
    "concise, actionable guidance for the executor model. Review now; do not say that you will review later. "
    "Do not write code unless a small exact patch is the clearest advice. Use this exact structure: "
    "Verdict: approve, revise, or continue. Key findings: concrete gaps or risks. Required next action: "
    "the next action or Claude Code tool call. Validation: the check that proves the work. "
    "If the executor is stuck after progress announcements, tell it the exact next Claude Code tool to call."
)


@dataclass(frozen=True, slots=True)
class AdvisorProjectionPorts:
    provider_kind: Callable[..., str]
    anthropic_messages: Callable[..., tuple[list[dict[str, Any]], list[str]]]
    openai_messages: Callable[..., list[dict[str, Any]]]
    ollama_messages: Callable[[dict[str, Any]], list[dict[str, Any]]]
    focus_from_body: Callable[[dict[str, Any]], str]
    compact_text: Callable[[str], str]
    anthropic_system: Callable[[Any, list[str]], Any]
    anthropic_text: Callable[[Any], str]


@dataclass(frozen=True, slots=True)
class AdvisorBudgetPorts:
    ollama_context: Callable[[dict[str, Any]], int]
    provider_context: Callable[[str, dict[str, Any]], int]
    openai_context: Callable[[str, dict[str, Any]], int]
    reserve: Callable[[dict[str, Any], int], int]
    compact_messages: Callable[..., list[dict[str, Any]]]
    configured_output: Callable[..., int]
    ollama_options: Callable[[dict[str, Any]], dict[str, Any]]
    positive_int: Callable[[Any], int]
    ollama_num_ctx: Callable[..., int]
    think_enabled: Callable[[str | None, dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class AdvisorEndpointPorts:
    join_url: Callable[[str, str], str]
    upstream_query: Callable[[dict[str, Any], str, str], str]
    provider_request_base: Callable[[str, dict[str, Any]], str]
    upstream_model: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class AdvisorAnthropicSystemPolicy:
    review_prompt: str
    content_to_text: Callable[[Any], str]

    def project(
        self,
        system: Any,
        extra_system_texts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        original_text = ""
        if isinstance(system, str):
            clean = system.strip()
            if clean:
                blocks.append({"type": "text", "text": clean})
        elif isinstance(system, list) and system:
            first = system[0]
            if (
                isinstance(first, dict)
                and str(first.get("type") or "") == "text"
                and str(first.get("text") or "").strip()
            ):
                blocks.append(dict(first))
                original_text = self.content_to_text(system[1:]).strip()
            else:
                original_text = self.content_to_text(system).strip()
        elif system:
            original_text = self.content_to_text(system).strip()
        blocks.append({"type": "text", "text": self.review_prompt})
        if original_text:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "Original session system context:\n"
                        + original_text
                    ),
                }
            )
        for text in extra_system_texts or []:
            clean = str(text or "").strip()
            if clean:
                blocks.append(
                    {
                        "type": "text",
                        "text": (
                            "Additional system context from message "
                            "history:\n" + clean
                        ),
                    }
                )
        return blocks


class AdvisorRequestBuilder:
    def __init__(
        self,
        review_prompt: str,
        projection: AdvisorProjectionPorts,
        budget: AdvisorBudgetPorts,
        endpoint: AdvisorEndpointPorts,
    ) -> None:
        self.review_prompt = review_prompt
        self.projection = projection
        self.budget = budget
        self.endpoint = endpoint

    def messages(
        self, provider: str, body: dict[str, Any], focus_override: str = ""
    ) -> list[dict[str, Any]]:
        kind = self.projection.provider_kind(provider)
        if kind == "anthropic":
            messages, _ = self.projection.anthropic_messages(body)
        elif kind == "openai-compatible":
            messages = self.projection.openai_messages(body)
        else:
            messages = self.projection.ollama_messages(body)
        focus = focus_override or self.projection.focus_from_body(body)
        if kind != "anthropic":
            messages.insert(0, {"role": "system", "content": self.review_prompt})
        if focus:
            focus_text = f"Advisor focus:\n{self.projection.compact_text(focus)}"
            content: Any = (
                [{"type": "text", "text": focus_text}]
                if kind == "anthropic"
                else focus_text
            )
            messages.append({"role": "user", "content": content})
        return messages

    def input_budget(self, provider: str, config: dict[str, Any]) -> int:
        kind = self.projection.provider_kind(provider)
        if kind == "ollama":
            context_limit = self.budget.ollama_context(config)
        elif kind == "anthropic":
            context_limit = self.budget.provider_context(provider, config) or 200000
        else:
            context_limit = self.budget.openai_context(provider, config)
        return max(
            8192,
            context_limit - 4096 - self.budget.reserve(config, context_limit),
        )

    def model(self, provider: str, model: str) -> str:
        return self.endpoint.upstream_model(model) if provider == "nvidia-hosted" else model

    def request(
        self,
        provider: str,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
        focus_override: str = "",
    ) -> dict[str, Any]:
        kind = self.projection.provider_kind(provider)
        messages = self.messages(provider, body, focus_override)
        if kind != "anthropic":
            messages = self.budget.compact_messages(
                messages,
                [],
                self.input_budget(provider, config),
                provider=provider,
                model=model,
            )
        upstream_model = self.model(provider, model)
        if kind == "anthropic":
            _, extra_system = self.projection.anthropic_messages(body)
            request = {
                "model": upstream_model,
                "system": self.projection.anthropic_system(
                    body.get("system"), extra_system
                ),
                "messages": messages,
                "stream": False,
                "max_tokens": min(
                    4096, self.budget.configured_output(config, body) or 4096
                ),
            }
            return self._sampling_options(request, config)
        if kind == "openai-compatible":
            request = {
                "model": upstream_model,
                "messages": messages,
                "stream": False,
                "max_tokens": min(
                    4096, self.budget.configured_output(config, body) or 4096
                ),
            }
            return self._sampling_options(request, config)
        request = {
            "model": upstream_model,
            "messages": messages,
            "stream": False,
            "think": self.budget.think_enabled(upstream_model, config),
        }
        options = self.budget.ollama_options(config)
        options.setdefault(
            "num_predict",
            min(4096, self.budget.positive_int(options.get("num_predict")) or 4096),
        )
        num_ctx = self.budget.ollama_num_ctx(
            config, {"messages": messages, "tools": []}
        )
        if num_ctx:
            options.setdefault("num_ctx", num_ctx)
        if options:
            request["options"] = options
        return request

    @staticmethod
    def _sampling_options(
        request: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        for key in ("temperature", "top_p"):
            if config.get(key) is not None:
                request[key] = config[key]
        return request

    def response_text(self, provider: str, data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        kind = self.projection.provider_kind(provider)
        if kind == "anthropic":
            return self.projection.anthropic_text(data.get("content")).strip()
        if kind == "openai-compatible":
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else {}
                if isinstance(message, dict):
                    return str(message.get("content") or "").strip()
            return ""
        message = data.get("message")
        return str((message or {}).get("content") or "").strip()

    def endpoint_url(self, provider: str, config: dict[str, Any]) -> str:
        base = str(config.get("base_url") or "").rstrip("/")
        kind = self.projection.provider_kind(provider)
        if kind == "anthropic":
            url = self.endpoint.join_url(base, "/v1/messages")
            query = self.endpoint.upstream_query(config, "", provider)
            return f"{url}?{query}" if query else url
        if kind == "openai-compatible":
            return self.endpoint.join_url(
                self.endpoint.provider_request_base(provider, config),
                "/v1/chat/completions",
            )
        return self.endpoint.join_url(base, "/api/chat")
