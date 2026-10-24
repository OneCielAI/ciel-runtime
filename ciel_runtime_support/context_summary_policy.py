"""Pure message projection and chunking policy for context compaction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from .media_payload import opaque_media


PROMPT_TOOL_INPUT_FIELD_LIMIT = 1200
PROMPT_MESSAGE_TEXT_LIMIT = 20000
MAX_CONTEXT_SUMMARY_FRAGMENTS = 65536
MAX_CONTEXT_SUMMARY_SOURCE_BYTES = 64 * 1024 * 1024
CLAUDE_CODE_PERSISTED_OUTPUT_MARKER = "<persisted-output>"
CODEX_CONTEXT_CHECKPOINT_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work."""
CODEX_CONTEXT_CHECKPOINT_PROMPT_PREFIX = (
    "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary "
    "for another LLM that will resume the task."
)


def is_codex_context_checkpoint_prompt(text: Any) -> bool:
    """Match the local-compaction prompt emitted by Codex CLI 0.147.

    Codex sends this as an ordinary Responses input item when its configured
    provider does not implement remote ``/responses/compact``.  The captured
    0.147 contract is its distinctive leading sentence; match that sentence
    case-insensitively while allowing the client's detail list to evolve.
    """

    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized.casefold().startswith(
        CODEX_CONTEXT_CHECKPOINT_PROMPT_PREFIX.casefold()
    )


CLAUDE_COMPACT_TEXT_PREFIX = "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools."
CLAUDE_COMPACT_TASK = "Your task is to create a detailed summary of the conversation so far"
CLAUDE_COMPACT_END = (
    "REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block "
    "followed by a <summary> block. Tool calls will be rejected and you will fail the task."
)


def native_compact_text(content: Any) -> str:
    """The captured Claude auto contract appends a separate final text block."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [str(block.get("text") or "") for block in content
                 if isinstance(block, dict) and block.get("type") in {"text", "input_text"}]
        return texts[-1] if texts else ""
    return ""


def compact_instruction_kind(text: Any) -> str | None:
    """Recognize native leading instructions, not quoted markers in user prose."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if is_codex_context_checkpoint_prompt(normalized):
        return "codex"
    lowered = normalized.casefold()
    if re.match(
        r"^(?:<command-message>compact</command-message>\s*)?"
        r"<command-name>/compact</command-name>(?:\s|$)", lowered
    ):
        return "claude"
    if (lowered.startswith(CLAUDE_COMPACT_TEXT_PREFIX.casefold())
        and CLAUDE_COMPACT_TASK.casefold() in lowered
        and "your summary should include the following sections:" in lowered
        and lowered.endswith(CLAUDE_COMPACT_END.casefold())):
        return "claude"
    if (lowered.startswith(CLAUDE_COMPACT_TASK.casefold())
        and "your summary should include the following sections:" in lowered
        and "1. primary request and intent:" in lowered
        and "8. current work:" in lowered):
        return "claude"
    if "compact" in lowered and lowered.startswith((
        "create a detailed summary of the conversation",
        "summarize the conversation so far",
    )):
        return "claude"
    return None


def semantic_payload_cost(value: Any, estimate_tokens: Callable[[Any], int]) -> int:
    """Return the available token observation without treating bytes as tokens.

    UTF-8 size is useful for transport/resource limits, but it is not evidence
    of provider token usage.  In particular, non-ASCII text can be byte-large
    while still fitting the native model context.  A zero result remains
    unknown and must not be turned into a proactive compact request.
    """
    try:
        return max(0, int(estimate_tokens(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


@dataclass(frozen=True, slots=True)
class ContextSummaryFragment:
    source_index: int
    start: int
    end: int
    source_chars: int
    text: str
    role: str = "unknown"


@dataclass(frozen=True, slots=True)
class ContextSummaryPolicy:
    estimate_tokens: Callable[[Any], int]
    positive_int: Callable[[Any], int | None]
    content_to_text: Callable[[Any], str]
    compact_json: Callable[..., str]
    latest_user_text: Callable[[dict[str, Any]], str]

    def compact_request_kind(self, body: dict[str, Any]) -> str | None:
        if not isinstance(body, dict):
            return None
        messages = body.get("messages")
        if isinstance(messages, list) and messages:
            index = self.instruction_index(messages)
            return compact_instruction_kind(native_compact_text(messages[index].get("content"))) if index is not None else None
        items = body.get("input")
        if isinstance(items, str):
            return compact_instruction_kind(items)
        if isinstance(items, list) and items:
            # Raw Responses (Codex 0.158): the checkpoint is the newest user
            # message item; a newer tool output means it is an ordinary turn.
            for item in reversed(items):
                if not isinstance(item, dict):
                    return None
                if str(item.get("type") or "").endswith("_output"):
                    return None
                if item.get("role") == "user":
                    return compact_instruction_kind(native_compact_text(item.get("content")))
            return None
        return compact_instruction_kind(self.latest_user_text(body))

    def is_compact_request(self, body: dict[str, Any]) -> bool:
        return self.compact_request_kind(body) is not None

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

    @staticmethod
    def cache_stable_summary_budget(budget_tokens: int) -> int:
        """Bucket a wire-fit budget before it can shape a cached summary.

        Provider request fitting recalculates ``budget_tokens`` from the exact
        serialized byte count.  Appending one turn can therefore move that
        value by a few tokens even when the omitted history is unchanged.  A
        prompt cache requires an exact prefix, so embedding or otherwise using
        that per-request value would regenerate the first compacted item.

        Keep the real budget in the compactor's final fit checks.  The summary
        projection only needs a conservative, stable size class; rounding down
        to the compactor's minimum quantum makes nearby fits byte-identical and
        never grants the summary a larger budget than the request has.
        """

        normalized = max(8192, int(budget_tokens))
        return max(8192, (normalized // 8192) * 8192)

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
                "the provider context budget was exceeded.]"
            )
        summary_budget = self.cache_stable_summary_budget(budget_tokens)
        max_tokens = max(1024, min(24576, summary_budget // 10))
        max_chars = max_tokens * 4
        chunks = self.guard_chunk_count(omitted_messages, summary_budget)
        lines = [
            f"[ciel-runtime context guard: compacted {count} older messages, approx {tokens} tokens, because the provider context budget was exceeded.]",
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

    def compact_message(
        self, message: dict[str, Any] | ContextSummaryFragment, index: int
    ) -> str:
        if isinstance(message, ContextSummaryFragment):
            return (
                f"Message {message.source_index} role={message.role} fragment chars="
                f"{message.start}:{message.end}/{message.source_chars}\n{message.text}"
            )
        # Unlike the deterministic wire guard this is the source of a semantic
        # summary. Preserve every field, block, tool argument and result in order,
        # but do not put binary image bytes into a text-only map prompt.
        projected, _media = opaque_media(message)
        serialized = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
        return f"Message {index} role={message.get('role') or 'unknown'}\n{serialized}"

    def instruction_index(self, messages: list[dict[str, Any]]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, dict) or str(message.get("role") or "") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return index
            if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") in {"text", "input_text"}
                and str(block.get("text") or "").strip() for block in content
            ):
                return index
            # A later tool-result-only user message is not a checkpoint request;
            # never resurrect an older compact instruction behind it.
            return None
        return None

    @staticmethod
    def segmented_compaction_mode(
        config: dict[str, Any],
        compact_instruction: str,
        parse_bool: Callable[..., bool],
    ) -> str | None:
        configured = config.get("context_compact_llm")
        if configured is None:
            kind = compact_instruction_kind(compact_instruction)
            return f"auto_{kind}" if kind else None
        return "explicit" if parse_bool(configured, default=False) else None

    def chunk_target_tokens(self, config: dict[str, Any] | None, budget_tokens: int) -> int:
        """Clamp the map target in token units, independently of source bytes."""
        configured = self.positive_int((config or {}).get("context_compact_chunk_tokens"))
        # A map call is one HTTP round trip on a shared gateway: a smaller
        # segment finishes well inside the request timeout and isolates a
        # dropped connection to that segment (observed 261K-token segments
        # losing the connection in the Celly session).
        ceiling = max(256, min(65536, max(1, budget_tokens) // 2))
        return min(ceiling, max(256, configured or ceiling))

    def summary_output_tokens(self, config: dict[str, Any] | None, budget_tokens: int) -> int:
        configured = self.positive_int((config or {}).get("context_compact_summary_tokens"))
        ceiling = max(32, min(8192, max(1, budget_tokens) // 8))
        return min(ceiling, max(32, configured or max(512, min(4096, budget_tokens // 64))))

    def split_messages(
        self, messages: list[dict[str, Any]], target_tokens: int,
    ) -> list[tuple[int, list[ContextSummaryFragment]]]:
        # Each interval is disjoint and exhaustive in the original projection.
        # Character offsets avoid breaking Unicode; fragment costs use the
        # available token estimator. Source bytes have their own resource cap.
        target = max(256, int(target_tokens))
        fragments: list[ContextSummaryFragment] = []
        source_bytes = 0
        for index, message in enumerate(messages):
            source = self.compact_message(message, index)
            source_bytes += len(source.encode("utf-8"))
            if source_bytes > MAX_CONTEXT_SUMMARY_SOURCE_BYTES:
                raise ValueError("semantic context compaction source byte limit exceeded")
            start = 0
            while start < len(source):
                low, high = start + 1, len(source)
                end = start
                while low <= high:
                    middle = (low + high) // 2
                    fragment = ContextSummaryFragment(index, start, middle, len(source), source[start:middle], str(message.get("role") or "unknown"))
                    if semantic_payload_cost(self.compact_message(fragment, index), self.estimate_tokens) <= target:
                        end, low = middle, middle + 1
                    else:
                        high = middle - 1
                if end == start:
                    raise ValueError("context compact fragment metadata exceeds chunk budget")
                fragments.append(ContextSummaryFragment(index, start, end, len(source), source[start:end], str(message.get("role") or "unknown")))
                start = end
                if len(fragments) > MAX_CONTEXT_SUMMARY_FRAGMENTS:
                    raise ValueError("semantic context compaction source fragment limit exceeded")
        chunks: list[tuple[int, list[ContextSummaryFragment]]] = []
        current: list[ContextSummaryFragment] = []
        current_cost = 0
        for fragment in fragments:
            cost = semantic_payload_cost(self.compact_message(fragment, fragment.source_index), self.estimate_tokens) + 4
            if current and current_cost + cost > target:
                chunks.append((current[0].source_index, current))
                current, current_cost = [], 0
            current.append(fragment)
            current_cost += cost
        if current:
            chunks.append((current[0].source_index, current))
        return chunks

    def chunk_prompt(
        self,
        chunk: list[dict[str, Any] | ContextSummaryFragment],
        start_index: int,
        chunk_number: int,
        chunk_total: int,
    ) -> str:
        parts = [
            f"Segment {chunk_number}/{chunk_total}. Summarize the ordered message fragments below.",
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
        if wire == "responses":
            output = data.get("output")
            if not isinstance(output, list):
                return ""
            return "\n".join(
                block["text"]
                for item in output if isinstance(item, dict) and item.get("type") == "message"
                for block in (item.get("content") if isinstance(item.get("content"), list) else [])
                if isinstance(block, dict) and block.get("type") == "output_text"
                and isinstance(block.get("text"), str)
            ).strip()
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
            f"## Segment {index}\n{summary}"
            for index, summary in enumerate(summaries, start=1)
        )
        parts.extend(
            (
                "Client compact instruction:",
                compact_instruction,
                "Using the segment summaries above, return only the final compact summary text requested by the client.",
            )
        )
        return "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class ContextSummaryCompatibilityApi:
    """Typed facade adapter for context-summary projections."""

    policy_factory: Callable[[], ContextSummaryPolicy]
    compact_system_prompt: str
    append_system: Callable[[Any, list[str]], Any]
    log: Callable[[str, str], None]
    parse_bool: Callable[..., bool]

    def is_compact_request(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().is_compact_request(body)

    def compact_request_kind(self, body: dict[str, Any]) -> str | None:
        return self.policy_factory().compact_request_kind(body)

    def text_only_body(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.policy_factory().text_only_body(
            body, self.compact_system_prompt, self.append_system, self.log
        )

    def compact_tool_value(self, value: Any, limit: int = PROMPT_TOOL_INPUT_FIELD_LIMIT) -> Any:
        return self.policy_factory().compact_tool_value(value, limit)

    def tool_input(self, tool_input: Any) -> str:
        return self.policy_factory().tool_input(tool_input)

    def message_text(self, text: str) -> str:
        return self.policy_factory().message_text(text)

    def summary_line(
        self, index: int, message: dict[str, Any], *, text_limit: int = 700
    ) -> str:
        return self.policy_factory().summary_line(index, message, text_limit)

    def guard_chunk_count(
        self,
        omitted_messages: list[dict[str, Any]],
        budget_tokens: int | None = None,
    ) -> int:
        return self.policy_factory().guard_chunk_count(omitted_messages, budget_tokens)

    def guard_summary(
        self,
        omitted_messages: list[dict[str, Any]],
        budget_tokens: int,
        *,
        start_index: int = 0,
    ) -> str:
        return self.policy_factory().guard_summary(
            omitted_messages, budget_tokens, start_index
        )

    def compact_message(
        self, message: dict[str, Any] | ContextSummaryFragment, index: int
    ) -> str:
        return self.policy_factory().compact_message(message, index)

    def instruction_index(self, messages: list[dict[str, Any]]) -> int | None:
        return self.policy_factory().instruction_index(messages)

    def segmented_compaction_mode(
        self,
        config: dict[str, Any],
        compact_instruction: str,
    ) -> str | None:
        return self.policy_factory().segmented_compaction_mode(
            config, compact_instruction, self.parse_bool
        )

    def chunk_target_tokens(
        self, config: dict[str, Any] | None, budget_tokens: int
    ) -> int:
        return self.policy_factory().chunk_target_tokens(config, budget_tokens)

    def summary_output_tokens(
        self, config: dict[str, Any] | None, budget_tokens: int
    ) -> int:
        return self.policy_factory().summary_output_tokens(config, budget_tokens)

    def split_messages(
        self, messages: list[dict[str, Any]], target_tokens: int
    ) -> list[tuple[int, list[ContextSummaryFragment]]]:
        return self.policy_factory().split_messages(messages, target_tokens)

    def chunk_prompt(
        self,
        chunk: list[dict[str, Any] | ContextSummaryFragment],
        start_index: int,
        chunk_no: int,
        chunk_total: int,
    ) -> str:
        return self.policy_factory().chunk_prompt(
            chunk, start_index, chunk_no, chunk_total
        )

    def extract_response_text(self, data: Any, wire: str) -> str:
        return self.policy_factory().extract_response_text(data, wire)

    def reduce_prompt(
        self,
        summaries: list[str],
        compact_instruction: str,
        *,
        budget_tokens: int,
        source_message_count: int,
    ) -> str:
        return self.policy_factory().reduce_prompt(
            summaries, compact_instruction, budget_tokens, source_message_count
        )
