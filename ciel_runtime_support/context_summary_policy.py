"""Pure message projection and chunking policy for context compaction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


PROMPT_TOOL_INPUT_FIELD_LIMIT = 1200
PROMPT_MESSAGE_TEXT_LIMIT = 20000
CLAUDE_CODE_PERSISTED_OUTPUT_MARKER = "<persisted-output>"


@dataclass(frozen=True, slots=True)
class ContextSummaryPolicy:
    estimate_tokens: Callable[[Any], int]
    positive_int: Callable[[Any], int | None]
    content_to_text: Callable[[Any], str]
    compact_json: Callable[..., str]
    latest_user_text: Callable[[dict[str, Any]], str]

    def is_compact_request(self, body: dict[str, Any]) -> bool:
        if not isinstance(body, dict):
            return False
        text = self.latest_user_text(body).lower()
        return bool(
            text
            and (
                "<command-name>/compact</command-name>" in text
                or ("<command-message>compact</command-message>" in text and "<command-name>" in text)
                or ("create a detailed summary of the conversation" in text and "compact" in text)
                or ("summarize the conversation so far" in text and "compact" in text)
            )
        )

    def text_only_body(
        self,
        body: dict[str, Any],
        system_prompt: str,
        append_system: Callable[[Any, list[str]], Any],
        log: Callable[[str, str], None],
    ) -> dict[str, Any]:
        if not self.is_compact_request(body):
            return body
        result = dict(body)
        removed_tools = bool(result.pop("tools", None))
        removed_choice = bool(result.pop("tool_choice", None))
        result.pop("parallel_tool_calls", None)
        result["system"] = append_system(result.get("system"), [system_prompt])
        if removed_tools or removed_choice:
            log(
                "INFO",
                "compact_request_text_only removed_tools=%s removed_tool_choice=%s"
                % (str(removed_tools).lower(), str(removed_choice).lower()),
            )
        return result

    @staticmethod
    def truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]..."

    @staticmethod
    def is_persisted_output(text: str) -> bool:
        return CLAUDE_CODE_PERSISTED_OUTPUT_MARKER in str(text or "")

    def compact_tool_value(self, value: Any, limit: int = PROMPT_TOOL_INPUT_FIELD_LIMIT) -> Any:
        if isinstance(value, str):
            return self.truncate(value, limit)
        if isinstance(value, list):
            return [self.compact_tool_value(item, limit) for item in value[:20]]
        if isinstance(value, dict):
            return {
                key: self.truncate(item, limit)
                if key in {"content", "old_string", "new_string", "command"}
                and isinstance(item, str)
                else self.compact_tool_value(item, limit)
                for key, item in value.items()
            }
        return value

    def tool_input(self, tool_input: Any) -> str:
        if not tool_input:
            return "{}"
        return json.dumps(self.compact_tool_value(tool_input), ensure_ascii=False, sort_keys=True)

    def message_text(self, text: str) -> str:
        return self.truncate(text, PROMPT_MESSAGE_TEXT_LIMIT)

    @staticmethod
    def tool_markers(message: dict[str, Any]) -> list[str]:
        content = message.get("content")
        if not isinstance(content, list):
            return []
        markers: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                tool_id = str(block.get("id") or "")
                markers.append(f"tool_use:{name}{('/' + tool_id) if tool_id else ''}")
            elif block.get("type") == "tool_result":
                markers.append(f"tool_result:{str(block.get('tool_use_id') or 'tool')}")
        return markers

    def summary_line(self, index: int, message: dict[str, Any], text_limit: int = 700) -> str:
        role = str(message.get("role") or "unknown")
        text = " ".join(self.content_to_text(message.get("content")).split())
        parts = [f"message {index}", f"role={role}"]
        if markers := self.tool_markers(message):
            parts.append("markers=" + ",".join(markers[:6]))
        if text:
            parts.append("text=" + self.truncate(text, text_limit))
        return "- " + " | ".join(parts)

    @staticmethod
    def chunk_ranges(count: int, chunks: int) -> list[tuple[int, int]]:
        chunks = max(1, min(chunks, count))
        return [
            (start, end)
            for index in range(chunks)
            if (start := (index * count) // chunks) < (end := ((index + 1) * count) // chunks)
        ]

    def guard_chunk_count(
        self,
        omitted_messages: list[dict[str, Any]],
        budget_tokens: int | None = None,
    ) -> int:
        if not omitted_messages:
            return 0
        omitted_tokens = sum(self.estimate_tokens(message) for message in omitted_messages)
        target = 32768
        if budget := self.positive_int(budget_tokens):
            target = max(target, min(262144, max(1, budget // 4)))
        return max(1, min(12, (omitted_tokens + target - 1) // target))

    def guard_summary(
        self,
        omitted_messages: list[dict[str, Any]],
        budget_tokens: int,
        start_index: int = 0,
    ) -> str:
        count = len(omitted_messages)
        tokens = sum(self.estimate_tokens(message) for message in omitted_messages)
        if count <= 0:
            return (
                "[ciel-runtime context guard: older conversation history was compacted because "
                f"the provider context budget is {budget_tokens} tokens.]"
            )
        max_tokens = max(1024, min(24576, max(1, budget_tokens) // 10))
        max_chars = max_tokens * 4
        chunks = self.guard_chunk_count(omitted_messages, budget_tokens)
        lines = [
            f"[ciel-runtime context guard: compacted {count} older messages, approx {tokens} tokens, because the provider context budget is {budget_tokens} tokens.]",
            "The recent tail is preserved verbatim. Older history is represented below as deterministic chunk summaries; use file reads or MCP queries if exact old content is needed.",
        ]
        for number, (start, end) in enumerate(self.chunk_ranges(count, chunks), start=1):
            chunk = omitted_messages[start:end]
            chunk_tokens = sum(self.estimate_tokens(message) for message in chunk)
            lines.append(
                f"Chunk {number}/{chunks}: messages {start_index + start}-{start_index + end - 1}, approx {chunk_tokens} tokens."
            )
            offsets = list(range(len(chunk))) if len(chunk) <= 4 else [0, 1, len(chunk) - 2, len(chunk) - 1]
            for offset in dict.fromkeys(offsets):
                lines.append(self.summary_line(start_index + start + offset, chunk[offset]))
            if len("\n".join(lines)) > max_chars:
                lines.append(f"...[context guard summary truncated to {max_tokens} tokens]...")
                break
        summary = "\n".join(lines)
        return self.truncate(summary, max_chars) if len(summary) > max_chars else summary

    def compact_message(self, message: dict[str, Any], index: int) -> str:
        role = str(message.get("role") or "unknown")
        parts = [f"Message {index} role={role}"]
        if name := message.get("name") or message.get("tool_name"):
            parts.append(f"name={name}")
        if message.get("tool_call_id"):
            parts.append(f"tool_call_id={message.get('tool_call_id')}")
        content = self.content_to_text(message.get("content"))
        if message.get("tool_calls"):
            tool_calls = "tool_calls=" + self.compact_json(message.get("tool_calls"), max_chars=6000)
            content = f"{content}\n\n{tool_calls}" if content else tool_calls
        return f"{' '.join(parts)}\n{self.message_text(content)}"

    def instruction_index(self, messages: list[dict[str, Any]]) -> int | None:
        fallback: int | None = None
        for index, message in enumerate(messages):
            if str(message.get("role") or "") != "user":
                continue
            text = self.content_to_text(message.get("content")).lower()
            if text:
                fallback = index
            if (
                "<command-name>/compact</command-name>" in text
                or ("<command-message>compact</command-message>" in text and "<command-name>" in text)
                or ("create a detailed summary of the conversation" in text and "compact" in text)
                or ("summarize the conversation so far" in text and "compact" in text)
            ):
                return index
        return fallback

    def chunk_target_tokens(self, config: dict[str, Any] | None, budget_tokens: int) -> int:
        configured = self.positive_int((config or {}).get("context_compact_chunk_tokens"))
        return max(8192, configured) if configured else max(8192, min(65536, max(1, budget_tokens) // 4))

    def summary_output_tokens(self, config: dict[str, Any] | None, budget_tokens: int) -> int:
        configured = self.positive_int((config or {}).get("context_compact_summary_tokens"))
        return max(512, configured) if configured else max(1024, min(8192, max(1, budget_tokens) // 64))

    def split_messages(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
    ) -> list[tuple[int, list[dict[str, Any]]]]:
        chunks: list[tuple[int, list[dict[str, Any]]]] = []
        current: list[dict[str, Any]] = []
        current_start = 0
        current_tokens = 0
        for index, message in enumerate(messages):
            tokens = max(1, self.estimate_tokens(message))
            if current and current_tokens + tokens > target_tokens:
                chunks.append((current_start, current))
                current, current_tokens, current_start = [], 0, index
            if not current:
                current_start = index
            current.append(message)
            current_tokens += tokens
        if current:
            chunks.append((current_start, current))
        return chunks

    def chunk_prompt(
        self,
        chunk: list[dict[str, Any]],
        start_index: int,
        chunk_number: int,
        chunk_total: int,
    ) -> str:
        parts = [
            f"Segment {chunk_number}/{chunk_total}. Summarize messages {start_index}-{start_index + len(chunk) - 1}.",
            "Return only the segment summary.",
        ]
        parts.extend(
            self.compact_message(message, start_index + offset)
            for offset, message in enumerate(chunk)
        )
        return "\n\n".join(parts)

    def extract_response_text(self, data: Any, wire: str) -> str:
        if not isinstance(data, dict):
            return ""
        if wire == "ollama":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            return str(message.get("content") or data.get("response") or "").strip()
        if wire == "openai":
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0] if isinstance(choices[0], dict) else {}
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                return str(message.get("content") or "").strip()
            return ""
        if wire == "anthropic":
            return self.content_to_text(data.get("content")).strip()
        return ""

    def reduce_prompt(
        self,
        summaries: list[str],
        compact_instruction: str,
        budget_tokens: int,
        source_message_count: int,
    ) -> str:
        parts = [
            "[ciel-runtime segmented compact]",
            f"The previous conversation was too large for a single compact request. It was summarized in {len(summaries)} segment(s) from {source_message_count} message(s).",
            "Segment summaries:",
        ]
        parts.extend(
            f"## Segment {index}\n{summary.strip()}"
            for index, summary in enumerate(summaries, start=1)
        )
        parts.extend(
            (
                "Claude Code compact instruction:",
                self.message_text(compact_instruction),
                "Using the segment summaries above, return only the final compact summary text requested by Claude Code.",
            )
        )
        text = "\n\n".join(parts)
        max_chars = max(8192, max(1, budget_tokens) * 3)
        return self.truncate(text, max_chars) if len(text) > max_chars else text
