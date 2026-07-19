"""Protocol-aware system prompt transformation policies.

The router calls one application service while wire-format strategies own the
placement details.  This keeps cross-cutting prompt features independent from
providers: a provider only declares its protocol and does not need custom
prompt mutation code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ciel_runtime_support.architecture import MessageProtocol


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class SystemPromptInjection:
    """A provider-neutral request to add trusted system context."""

    texts: tuple[str, ...]

    @classmethod
    def from_texts(cls, texts: list[str] | tuple[str, ...] | None) -> SystemPromptInjection:
        return cls(tuple(clean for item in (texts or ()) if (clean := str(item or "").strip())))

    @property
    def empty(self) -> bool:
        return not self.texts


class PromptInjectionStrategy(Protocol):
    """Wire-format strategy used by :class:`PromptInjector`."""

    def inject(self, body: Mapping[str, Any], injection: SystemPromptInjection) -> JsonObject: ...


def append_anthropic_system_texts(system: Any, texts: list[str] | tuple[str, ...] | None) -> Any:
    """Append Anthropic text blocks without mutating or reordering existing blocks."""

    injection = SystemPromptInjection.from_texts(texts)
    if injection.empty:
        return system
    blocks: list[Any]
    if isinstance(system, list):
        blocks = [dict(block) if isinstance(block, dict) else block for block in system]
    elif isinstance(system, str):
        blocks = [{"type": "text", "text": system.strip()}] if system.strip() else []
    elif system:
        blocks = [{"type": "text", "text": str(system).strip()}]
    else:
        blocks = []
    blocks.extend({"type": "text", "text": text} for text in injection.texts)
    return blocks


class AnthropicPromptInjectionStrategy:
    def inject(self, body: Mapping[str, Any], injection: SystemPromptInjection) -> JsonObject:
        out = dict(body)
        out["system"] = append_anthropic_system_texts(body.get("system"), injection.texts)
        return out


@dataclass(frozen=True)
class ChatPromptInjectionStrategy:
    """Insert context after existing system/developer messages."""

    role: str = "system"

    def inject(self, body: Mapping[str, Any], injection: SystemPromptInjection) -> JsonObject:
        messages = body.get("messages")
        copied = [dict(item) if isinstance(item, dict) else item for item in messages] if isinstance(messages, list) else []
        insertion = 0
        while insertion < len(copied):
            item = copied[insertion]
            if not isinstance(item, dict) or str(item.get("role") or "") not in {"system", "developer"}:
                break
            insertion += 1
        copied[insertion:insertion] = ({"role": self.role, "content": text} for text in injection.texts)
        out = dict(body)
        out["messages"] = copied
        return out


class OpenAIResponsesPromptInjectionStrategy:
    def inject(self, body: Mapping[str, Any], injection: SystemPromptInjection) -> JsonObject:
        out = dict(body)
        parts = [str(body.get("instructions") or "").strip(), *injection.texts]
        out["instructions"] = "\n\n".join(part for part in parts if part)
        return out


class GooglePromptInjectionStrategy:
    """Append Gemini ``systemInstruction.parts`` while preserving key casing."""

    def inject(self, body: Mapping[str, Any], injection: SystemPromptInjection) -> JsonObject:
        out = dict(body)
        key = "system_instruction" if "system_instruction" in body else "systemInstruction"
        current = body.get(key)
        if isinstance(current, dict):
            system_instruction = dict(current)
            raw_parts = current.get("parts")
            parts = [dict(item) if isinstance(item, dict) else item for item in raw_parts] if isinstance(raw_parts, list) else []
        elif isinstance(current, str) and current.strip():
            system_instruction = {}
            parts = [{"text": current.strip()}]
        else:
            system_instruction = {}
            parts = []
        parts.extend({"text": text} for text in injection.texts)
        system_instruction["parts"] = parts
        out[key] = system_instruction
        return out


class PromptInjector:
    """Application service dispatching to protocol transformation strategies."""

    def __init__(self, strategies: Mapping[MessageProtocol, PromptInjectionStrategy] | None = None) -> None:
        self._strategies: dict[MessageProtocol, PromptInjectionStrategy] = dict(strategies or default_strategies())

    def inject(
        self,
        body: Mapping[str, Any],
        protocol: MessageProtocol,
        texts: list[str] | tuple[str, ...] | None,
    ) -> JsonObject:
        injection = SystemPromptInjection.from_texts(texts)
        if injection.empty:
            return dict(body)
        try:
            strategy = self._strategies[protocol]
        except KeyError as exc:  # pragma: no cover - MessageProtocol keeps this defensive
            raise ValueError(f"unsupported prompt injection protocol: {protocol}") from exc
        return strategy.inject(body, injection)


def default_strategies() -> Mapping[MessageProtocol, PromptInjectionStrategy]:
    chat = ChatPromptInjectionStrategy()
    return {
        "anthropic_messages": AnthropicPromptInjectionStrategy(),
        "openai_chat": chat,
        "openai_responses": OpenAIResponsesPromptInjectionStrategy(),
        "ollama_chat": chat,
        "google_generative": GooglePromptInjectionStrategy(),
    }


def normalize_anthropic_system_role_messages(
    body: Mapping[str, Any],
    content_to_text: Callable[[Any], str],
) -> JsonObject:
    """Move non-standard Anthropic system-role messages to top-level blocks."""

    messages = body.get("messages")
    if not isinstance(messages, list):
        return dict(body)
    next_messages: list[Any] = []
    system_texts: list[str] = []
    changed = False
    for message in messages:
        if not isinstance(message, dict) or str(message.get("role") or "").strip() != "system":
            next_messages.append(message)
            continue
        changed = True
        if text := content_to_text(message.get("content")).strip():
            system_texts.append(text)
    if not changed:
        return dict(body)
    out = dict(body)
    out["messages"] = next_messages
    out["system"] = append_anthropic_system_texts(body.get("system"), system_texts)
    return out
