"""Wire-protocol conversion helpers used by the runtime routers."""

from .openai_responses import anthropic_message_to_openai_response, openai_responses_to_anthropic_messages

__all__ = ["anthropic_message_to_openai_response", "openai_responses_to_anthropic_messages"]
