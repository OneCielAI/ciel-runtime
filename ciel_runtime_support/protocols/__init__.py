"""Wire-protocol conversion helpers used by the runtime routers."""

from .openai_responses import (
    OpenAIResponsesProtocolAdapter,
    anthropic_message_to_openai_response,
    openai_responses_to_anthropic_messages,
)
from ..registry import AdapterRegistry


PROTOCOL_ADAPTERS: AdapterRegistry[OpenAIResponsesProtocolAdapter] = AdapterRegistry()
PROTOCOL_ADAPTERS.register(
    "openai_responses",
    lambda **kwargs: OpenAIResponsesProtocolAdapter(**kwargs),
    aliases=("openai-responses",),
)


__all__ = [
    "PROTOCOL_ADAPTERS",
    "OpenAIResponsesProtocolAdapter",
    "anthropic_message_to_openai_response",
    "openai_responses_to_anthropic_messages",
]
