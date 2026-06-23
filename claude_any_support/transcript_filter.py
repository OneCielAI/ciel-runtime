from __future__ import annotations

from typing import Any


CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "queue-operation",
        "ai-title",
        "agent-name",
        "last-prompt",
        "permission-mode",
        "file-history-snapshot",
    }
)


def is_claude_code_transcript_event(message: dict[str, Any]) -> bool:
    """Return true for Claude Code transcript records, not Anthropic messages."""
    if not isinstance(message, dict):
        return False
    # Anthropic request messages always carry a top-level role. Claude Code
    # transcript records do not, and defaulting them to role=user can leak
    # queued prompts, ai titles, and session metadata back into the model.
    if "role" not in message:
        return True
    event_type = message.get("type")
    if event_type in CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES:
        return True
    return False

