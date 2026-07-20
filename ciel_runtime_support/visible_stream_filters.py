"""Stateful removal of provider-visible thinking and tool-call artifacts."""

from __future__ import annotations

import re
from typing import Any


VISIBLE_THINKING_MARKUP_TAG_RE = re.compile(r"</?think(?:ing)?\b[^>]*>", re.I)
VISIBLE_THINKING_MARKUP_PREFIXES = ("<think", "</think", "<thinking", "</thinking")
VISIBLE_TOOL_CALL_ARTIFACT_SUFFIX_RE = re.compile(
    r"(?s)(?:^|(?:\r?\n){1,3})[ \t]*call[ \t]*(?:\r?\n)"
    r"[ \t]*ignore[ \t]*(?:\r?\n)?[ \t]*$"
)
VISIBLE_TOOL_CALL_ARTIFACT_HOLD_CHARS = 96


def visible_thinking_markup_partial_start(text: str) -> int:
    less_than = text.rfind("<")
    if less_than < 0 or less_than <= text.rfind(">"):
        return -1
    suffix = text[less_than:].lower()
    if any(
        prefix.startswith(suffix) or suffix.startswith(prefix)
        for prefix in VISIBLE_THINKING_MARKUP_PREFIXES
    ):
        return less_than
    return -1


class VisibleThinkingMarkupFilter:
    def __init__(self) -> None:
        self.in_thinking = False
        self.pending = ""

    def feed(self, text: Any) -> str:
        raw = str(text or "")
        if not raw:
            return ""
        value = self.pending + raw
        self.pending = ""
        partial_start = visible_thinking_markup_partial_start(value)
        if partial_start >= 0:
            self.pending = value[partial_start:]
            value = value[:partial_start]
        return self._strip_complete_tags(value)

    def finish(self) -> str:
        pending = self.pending
        self.pending = ""
        if (
            not pending
            or self.in_thinking
            or visible_thinking_markup_partial_start(pending) == 0
        ):
            self.in_thinking = False
            return ""
        return self._strip_complete_tags(pending)

    def _strip_complete_tags(self, text: str) -> str:
        output: list[str] = []
        position = 0
        for match in VISIBLE_THINKING_MARKUP_TAG_RE.finditer(text):
            closing = match.group(0).lstrip().lower().startswith("</")
            if self.in_thinking:
                if closing:
                    self.in_thinking = False
                position = match.end()
                continue
            output.append(text[position : match.start()])
            position = match.end()
            if not closing:
                self.in_thinking = True
        if not self.in_thinking:
            output.append(text[position:])
        return "".join(output)


def strip_visible_thinking_markup(text: Any) -> str:
    raw = str(text or "")
    if "<think" not in raw.lower() and "</think" not in raw.lower():
        return raw
    filter_state = VisibleThinkingMarkupFilter()
    return (filter_state.feed(raw) + filter_state.finish()).lstrip()


def strip_visible_tool_call_artifact_suffix(text: Any) -> str:
    raw = str(text or "")
    if "call" not in raw or "ignore" not in raw:
        return raw
    return VISIBLE_TOOL_CALL_ARTIFACT_SUFFIX_RE.sub("", raw)


class VisibleToolCallArtifactFilter:
    def __init__(self, hold_chars: int = VISIBLE_TOOL_CALL_ARTIFACT_HOLD_CHARS) -> None:
        self.hold_chars = max(16, int(hold_chars))
        self.pending = ""
        self.stripped = False

    def feed(self, text: Any) -> str:
        raw = str(text or "")
        if not raw:
            return ""
        value = self.pending + raw
        if len(value) <= self.hold_chars:
            self.pending = value
            return ""
        emit_length = len(value) - self.hold_chars
        self.pending = value[emit_length:]
        return value[:emit_length]

    def finish(self) -> str:
        pending = self.pending
        self.pending = ""
        stripped = strip_visible_tool_call_artifact_suffix(pending)
        self.stripped = self.stripped or stripped != pending
        return stripped


__all__ = [
    "VISIBLE_THINKING_MARKUP_PREFIXES",
    "VISIBLE_THINKING_MARKUP_TAG_RE",
    "VISIBLE_TOOL_CALL_ARTIFACT_HOLD_CHARS",
    "VISIBLE_TOOL_CALL_ARTIFACT_SUFFIX_RE",
    "VisibleThinkingMarkupFilter",
    "VisibleToolCallArtifactFilter",
    "strip_visible_thinking_markup",
    "strip_visible_tool_call_artifact_suffix",
    "visible_thinking_markup_partial_start",
]
