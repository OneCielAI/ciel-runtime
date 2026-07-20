"""Plan-mode and TaskList conversation-turn state machine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

CHANNEL_LLM_WAKE_PREFIX = "[external input pending]"
CHANNEL_LLM_WAKE_LEGACY_PREFIXES = ("[ciel-runtime channel wake]", "[channel pending]")
PLAN_GUARD_MARKER = "[ciel-runtime-plan-guard]"
SYSTEM_REMINDER_BLOCK_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>", re.DOTALL
)
CLAUDE_CODE_SUGGESTION_MODE_PREFIX = "[SUGGESTION MODE:"
WORK_CONTINUATION_RESULT_TOOLS = frozenset(
    {
        "Bash",
        "Glob",
        "Grep",
        "LS",
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
        "TaskCreate",
        "TaskList",
        "TaskUpdate",
        "TaskStop",
        "ExitPlanMode",
    }
)
WORK_COMPLETION_RESULT_TOOLS = frozenset(
    {"Write", "Edit", "MultiEdit", "TaskUpdate", "TaskStop"}
)


@dataclass(frozen=True, slots=True)
class ConversationTurnPorts:
    content_blocks: Callable[[dict[str, Any]], list[Any]]
    lookup_tool_schema: Callable[[str], dict[str, Any] | None]
    tool_schema: Callable[[dict[str, Any], str], dict[str, Any] | None]
    log: Callable[[str, str], None]
    has_tool: Callable[[dict[str, Any], str], bool]
    ultracode_preferred: Callable[[dict[str, Any]], bool]
    content_to_text: Callable[[Any], str]


class ConversationTurnPolicy:
    def __init__(self, ports: ConversationTurnPorts) -> None:
        self.ports = ports

    def plan_mode_active(self, body: dict[str, Any]) -> bool:
        """Infer Claude Code Plan Mode from tool history and plan-mode attachments."""
        active = False
        tool_names_by_id: dict[str, str] = {}
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            attachment = message.get("attachment")
            if isinstance(attachment, dict):
                attachment_type = attachment.get("type")
                if attachment_type in {"plan_mode", "plan_mode_reentry"}:
                    active = True
                elif attachment_type == "plan_mode_exit":
                    active = False
            if message.get("role") == "assistant":
                for block in self.ports.content_blocks(message):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = str(block.get("id") or "")
                    name = str(block.get("name") or "")
                    if tool_id and name:
                        tool_names_by_id[tool_id] = name
            elif message.get("role") == "user":
                for block in self.ports.content_blocks(message):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        tool_use_id = str(block.get("tool_use_id") or "")
                        tool_name = tool_names_by_id.get(tool_use_id)
                        if tool_name == "EnterPlanMode":
                            active = True
                        elif tool_name == "ExitPlanMode":
                            active = False
                    elif block.get("type") in {"plan_mode", "plan_mode_reentry"}:
                        active = True
                    elif block.get("type") == "plan_mode_exit":
                        active = False
        return active

    def channel_llm_wake_text(self, text: str) -> bool:
        text = re.sub(r"^[\x00-\x1f\x7f\s]+", "", text)
        return text.startswith(CHANNEL_LLM_WAKE_PREFIX) or any(
            text.startswith(prefix) for prefix in CHANNEL_LLM_WAKE_LEGACY_PREFIXES
        )

    def channel_llm_wake_request(self, body: dict[str, Any]) -> bool:
        return self.channel_llm_wake_text(self.latest_user_text(body))

    def body_without_channel_llm_wake_prompt(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.channel_llm_wake_request(body):
            return body
        messages = [m for m in body.get("messages", []) if isinstance(m, dict)]
        removed = False
        for index in range(len(messages) - 1, -1, -1):
            if self.channel_llm_wake_text(
                self.user_intent_text_from_message(messages[index])
            ):
                del messages[index]
                removed = True
                break
        if not removed:
            return body
        out = dict(body)
        out["messages"] = messages
        self.ports.log("INFO", "channel_llm_wake_prompt_stripped")
        return out

    def has_plan_mode_exit(self, body: dict[str, Any]) -> bool:
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            attachment = message.get("attachment")
            if (
                isinstance(attachment, dict)
                and attachment.get("type") == "plan_mode_exit"
            ):
                return True
            if message.get("role") != "assistant":
                continue
            for block in self.ports.content_blocks(message):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                ):
                    return True
        return False

    def allowed_prompt_tools_for_exit_plan_mode(
        self, body: dict[str, Any]
    ) -> list[str]:
        schema = self.ports.tool_schema(
            body, "ExitPlanMode"
        ) or self.ports.lookup_tool_schema("ExitPlanMode")
        if not isinstance(schema, dict):
            return []
        properties = (
            schema.get("properties")
            if isinstance(schema.get("properties"), dict)
            else {}
        )
        allowed_schema = (
            properties.get("allowedPrompts")
            if isinstance(properties.get("allowedPrompts"), dict)
            else None
        )
        if not allowed_schema:
            return []
        items = (
            allowed_schema.get("items")
            if isinstance(allowed_schema.get("items"), dict)
            else {}
        )
        item_properties = (
            items.get("properties") if isinstance(items.get("properties"), dict) else {}
        )
        tool_schema = (
            item_properties.get("tool")
            if isinstance(item_properties.get("tool"), dict)
            else {}
        )
        enum_values = tool_schema.get("enum")
        if not isinstance(enum_values, list):
            return []
        return [
            str(item) for item in enum_values if isinstance(item, str) and item.strip()
        ]

    def exit_plan_mode_default_prompt_for_tool(self, tool_name: str) -> str:
        return f"use {tool_name} as needed to implement and verify the approved plan"

    def backfill_exit_plan_mode_allowed_prompts(
        self, body: dict[str, Any], tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        existing = (
            tool_input.get("allowedPrompts") if isinstance(tool_input, dict) else None
        )
        if isinstance(existing, list) and any(
            isinstance(item, dict) for item in existing
        ):
            return tool_input
        allowed_tools = self.allowed_prompt_tools_for_exit_plan_mode(body)
        if not allowed_tools:
            return tool_input
        out = dict(tool_input)
        out["allowedPrompts"] = [
            {
                "tool": tool_name,
                "prompt": self.exit_plan_mode_default_prompt_for_tool(tool_name),
            }
            for tool_name in allowed_tools
        ]
        self.ports.log(
            "INFO",
            f"backfilled ExitPlanMode allowedPrompts tools={','.join(allowed_tools)}",
        )
        return out

    def plan_mode_tool_name_for_emit(
        self, body: dict[str, Any], name: str, tool_input: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        active = self.plan_mode_active(body)
        if name == "EnterPlanMode" and self.body_is_channel_prompt(body):
            self.ports.log("WARN", "dropped EnterPlanMode for external channel prompt")
            return None, tool_input
        if (
            name == "EnterPlanMode"
            and "ExitPlanMode" in self.latest_user_tool_result_names(body)
        ):
            self.ports.log(
                "WARN", "dropped EnterPlanMode immediately after ExitPlanMode result"
            )
            return None, tool_input
        if name == "EnterPlanMode" and active:
            self.ports.log(
                "WARN", "dropped repeated EnterPlanMode while plan mode is active"
            )
            return None, tool_input
        if name == "EnterPlanMode" and self.ports.ultracode_preferred(body):
            self.ports.log(
                "WARN", "dropped EnterPlanMode because ultracode workflow is preferred"
            )
            return None, tool_input
        if name == "ExitPlanMode" and not active:
            self.ports.log("WARN", "dropped ExitPlanMode while plan mode is not active")
            return None, tool_input
        if name == "ExitPlanMode":
            tool_input = self.backfill_exit_plan_mode_allowed_prompts(body, tool_input)
        return name, tool_input

    def is_guard_feedback_text(self, text: str) -> bool:
        stripped = (text or "").strip()
        return (
            stripped.startswith("Stop hook feedback:")
            or stripped.startswith("Ciel Runtime plan guard:")
            or PLAN_GUARD_MARKER in stripped
        )

    SYSTEM_REMINDER_BLOCK_RE = re.compile(
        r"<system-reminder>.*?</system-reminder>", re.DOTALL
    )
    CLAUDE_CODE_SUGGESTION_MODE_PREFIX = "[SUGGESTION MODE:"

    def strip_claude_code_system_reminders(self, text: str) -> str:
        return SYSTEM_REMINDER_BLOCK_RE.sub("", text or "").strip()

    def is_claude_code_suggestion_mode_text(self, text: str) -> bool:
        return (
            self.strip_claude_code_system_reminders(text)
            .lstrip()
            .startswith(CLAUDE_CODE_SUGGESTION_MODE_PREFIX)
        )

    def user_intent_text_from_message(self, message: dict[str, Any]) -> str:
        if not isinstance(message, dict) or message.get("role") != "user":
            return ""
        if message.get("isMeta") is True:
            return ""
        content = message.get("content")
        if isinstance(content, str):
            text = self.strip_claude_code_system_reminders(content)
            return "" if self.is_guard_feedback_text(text) else text
        if not isinstance(content, list):
            # Claude Code can inject user-role attachment records such as
            # plan_mode_exit. They are state metadata, not new user intent.
            return ""
        # Claude Code sends tool_result blocks as user-role messages. Those are not
        # user intent. System reminders can also arrive as text blocks adjacent to
        # the real prompt, so remove them before classifying resume prompts.
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text = self.strip_claude_code_system_reminders(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = self.strip_claude_code_system_reminders(
                    str(block.get("text", ""))
                )
            else:
                continue
            if text and not self.is_guard_feedback_text(text):
                parts.append(text)
        return "\n".join(parts).strip()

    def latest_user_text(self, body: dict[str, Any]) -> str:
        for message in reversed(body.get("messages") or []):
            text = (
                self.user_intent_text_from_message(message)
                if isinstance(message, dict)
                else ""
            )
            if not text:
                continue
            return text
        return ""

    def latest_user_intent_message_index(self, body: dict[str, Any]) -> int | None:
        messages = body.get("messages") or []
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, dict) and self.user_intent_text_from_message(
                message
            ):
                return index
        return None

    def latest_user_is_claude_code_suggestion_mode(self, body: dict[str, Any]) -> bool:
        latest = self.latest_user_text(body)
        return bool(latest and self.is_claude_code_suggestion_mode_text(latest))

    def likely_implementation_planning_request(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if len(normalized) >= 120:
            return True
        # Multi-line prompts usually carry enough task structure that a one-line
        # "I'll make a plan" style response is not a useful final answer.
        non_empty_lines = [line for line in (text or "").splitlines() if line.strip()]
        if len(non_empty_lines) >= 3 and len(normalized) >= 80:
            return True
        return False

    def non_actionable_short_response(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return True
        # Language-agnostic: for a long implementation request, a short single-line
        # text response with no tool call is not actionable. Do not inspect words.
        if len(normalized) <= 80 and "\n" not in (text or ""):
            return True
        if (
            len(normalized) <= 160
            and "\n" not in (text or "")
            and not re.search(r"[`{};/\\\\]|https?://", normalized)
        ):
            return True
        return False

    def body_is_channel_prompt(self, body: dict[str, Any]) -> bool:
        metadata = (
            body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        )
        latest_text = self.latest_user_text(body)
        return bool(
            metadata.get("ciel_runtime_channel_injected")
            or latest_text.startswith("[external channel input]")
            or latest_text.startswith("[ciel-runtime channel inbox]")
            or latest_text.startswith(CHANNEL_LLM_WAKE_PREFIX)
            or any(
                latest_text.startswith(prefix)
                for prefix in CHANNEL_LLM_WAKE_LEGACY_PREFIXES
            )
            or latest_text.startswith("[ciel-runtime external channel message")
        )

    def should_auto_enter_plan_mode(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        if tool_calls:
            return False
        if self.body_is_channel_prompt(body):
            return False
        if self.ports.ultracode_preferred(body):
            return False
        if not self.ports.has_tool(body, "EnterPlanMode"):
            return False
        if self.plan_mode_active(body):
            return False
        if self.has_plan_mode_exit(body):
            return False
        if self.latest_tool_result_indicates_completed_work(body):
            return False
        if not self.non_actionable_short_response(response_text):
            return False
        return self.likely_implementation_planning_request(self.latest_user_text(body))

    def response_text_signals_plan_exit(self, text: str) -> bool:
        lowered = str(text or "").lower()
        if "exitplanmode" in lowered:
            return True
        mentions_plan_mode = (
            "plan mode" in lowered
            or "plan-mode" in lowered
            or "플랜모드" in lowered
            or "플랜 모드" in lowered
        )
        if not mentions_plan_mode:
            return False
        return any(
            token in lowered
            for token in ("exit", "leave", "exiting", "leaving", "종료", "탈출", "나가")
        )

    def should_auto_exit_plan_mode(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        if tool_calls:
            return False
        if not self.plan_mode_active(body):
            return False
        if not self.ports.has_tool(body, "ExitPlanMode"):
            return False
        if not self.response_text_signals_plan_exit(response_text):
            return False
        return True

    WORK_CONTINUATION_RESULT_TOOLS: frozenset[str] = frozenset(
        {
            "Bash",
            "Glob",
            "Grep",
            "LS",
            "Read",
            "Write",
            "Edit",
            "MultiEdit",
            "TaskCreate",
            "TaskList",
            "TaskUpdate",
            "TaskStop",
            "ExitPlanMode",
        }
    )

    WORK_COMPLETION_RESULT_TOOLS: frozenset[str] = frozenset(
        {
            "Write",
            "Edit",
            "MultiEdit",
            "TaskUpdate",
            "TaskStop",
        }
    )

    def bash_command_looks_mutating(self, command: str) -> bool:
        normalized = re.sub(r"\s+", " ", command or "").strip()
        if not normalized:
            return False
        return bool(
            re.search(
                r"(^|[;&|]\s*|\b)(rm|rmdir|mv|cp|mkdir|touch|chmod|chown|ln|install|git\s+(commit|push|pull|merge|rebase|checkout|switch|restore|reset|clean)|npm\s+(install|update|run|publish)|pnpm\s+(install|update|run|publish)|yarn\s+(install|add|run|publish)|python\d*\s+-m\s+pip\s+install|pip\d*\s+install|docker\s+(run|compose|build|up|down|rm|rmi)|kubectl\s+(apply|delete|create|replace|patch))\b",
                normalized,
            )
        )

    def latest_user_tool_result_details(
        self, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        tools_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        latest: list[dict[str, Any]] = []
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if message.get("role") == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = str(block.get("id") or "")
                    name = str(block.get("name") or "")
                    tool_input = (
                        block.get("input")
                        if isinstance(block.get("input"), dict)
                        else {}
                    )
                    if tool_id and name:
                        tools_by_id[tool_id] = (name, tool_input)
            elif message.get("role") == "user" and isinstance(content, list):
                current: list[dict[str, Any]] = []
                for block in content:
                    if (
                        not isinstance(block, dict)
                        or block.get("type") != "tool_result"
                    ):
                        continue
                    tool_use_id = str(block.get("tool_use_id") or "")
                    name, tool_input = tools_by_id.get(tool_use_id, ("tool", {}))
                    current.append(
                        {
                            "name": name,
                            "input": tool_input,
                            "text": self.ports.content_to_text(
                                block.get("content", "")
                            ),
                            "is_error": bool(block.get("is_error")),
                        }
                    )
                if current:
                    latest = current
        return latest

    def latest_tool_result_indicates_completed_work(self, body: dict[str, Any]) -> bool:
        details = self.latest_user_tool_result_details(body)
        if not details:
            return False
        for item in details:
            if item.get("is_error"):
                continue
            name = str(item.get("name") or "")
            tool_input = (
                item.get("input") if isinstance(item.get("input"), dict) else {}
            )
            if name in WORK_COMPLETION_RESULT_TOOLS:
                return True
            if name == "Bash" and self.bash_command_looks_mutating(
                str(tool_input.get("command") or "")
            ):
                return True
        return False

    def latest_user_tool_result_names(self, body: dict[str, Any]) -> list[str]:
        tool_names_by_id: dict[str, str] = {}
        latest: list[str] = []
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if message.get("role") == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = str(block.get("id") or "")
                    name = str(block.get("name") or "")
                    if tool_id and name:
                        tool_names_by_id[tool_id] = name
            elif message.get("role") == "user" and isinstance(content, list):
                current: list[str] = []
                for block in content:
                    if (
                        not isinstance(block, dict)
                        or block.get("type") != "tool_result"
                    ):
                        continue
                    tool_use_id = str(block.get("tool_use_id") or "")
                    if tool_use_id:
                        current.append(tool_names_by_id.get(tool_use_id, "tool"))
                if current:
                    latest = current
        return latest

    def latest_user_tool_result_text(self, body: dict[str, Any]) -> str:
        latest = ""
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            if message.get("role") != "user" or not isinstance(
                message.get("content"), list
            ):
                continue
            parts: list[str] = []
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                parts.append(self.ports.content_to_text(block.get("content", "")))
            if parts:
                latest = "\n".join(part for part in parts if part)
        return latest

    def synthetic_tasklist_tool_use_id(self, tool_id: str, name: str) -> bool:
        if name != "TaskList":
            return False
        prefixes = (
            "toolu_ollama_keepalive_",
            "toolu_ollama_choice_",
            "toolu_openai_keepalive_",
            "toolu_openai_choice_",
            "toolu_anthropic_choice_",
            "toolu_ciel_runtime_TaskList_",
        )
        return any(tool_id.startswith(prefix) for prefix in prefixes)

    def recent_synthetic_tasklist_count(
        self, body: dict[str, Any], after_message_index: int | None = None
    ) -> int:
        count = 0
        messages = body.get("messages") or []
        if after_message_index is not None:
            messages = messages[after_message_index + 1 :]
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if (
                after_message_index is None
                and message.get("role") == "user"
                and self.user_intent_text_from_message(message)
            ):
                break
            content = message.get("content")
            if message.get("role") != "assistant" or not isinstance(content, list):
                continue
            found_keepalive = False
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if self.synthetic_tasklist_tool_use_id(
                    str(block.get("id") or ""), str(block.get("name") or "")
                ):
                    found_keepalive = True
            if found_keepalive:
                count += 1
        return count

    def tasklist_result_has_active_work(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text or "").strip().lower()
        if not normalized:
            return False
        # This parses Claude Code's task-list tool output, not user-facing prose.
        if re.search(r"\[\s*(in progress|open|pending)\s*\]", normalized):
            return True
        for label in ("in progress", "open", "pending"):
            for match in re.finditer(rf"\b(\d+)\s+{re.escape(label)}\b", normalized):
                if int(match.group(1)) > 0:
                    return True
        return False

    def latest_tasklist_result_has_no_active_work(self, body: dict[str, Any]) -> bool:
        latest_names = self.latest_user_tool_result_names(body)
        if "TaskList" not in latest_names:
            return False
        return not self.tasklist_result_has_active_work(
            self.latest_user_tool_result_text(body)
        )

    def latest_assistant_text(self, body: dict[str, Any]) -> str:
        for message in reversed(body.get("messages") or []):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            return self.ports.content_to_text(message.get("content"))
        return ""

    def short_resume_prompt(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return False
        if len(normalized) > 32:
            return False
        # Language-agnostic: a very short imperative with no question or code-like
        # syntax after an unfinished assistant turn is a request to proceed.
        return not re.search(r"[?？`{};/\\\\]|https?://", normalized)

    def latest_user_looks_like_work_request(self, body: dict[str, Any]) -> bool:
        latest = self.latest_user_text(body)
        normalized = re.sub(r"\s+", " ", latest or "").strip()
        if not normalized:
            return False
        if self.likely_implementation_planning_request(latest):
            return True
        if self.short_resume_prompt(latest) and self.latest_assistant_text(body):
            return True
        if self.short_resume_prompt(latest) and self.latest_user_tool_result_names(
            body
        ):
            return True
        return False

    def response_asks_for_user_choice_or_permission(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return False
        if "?" not in normalized and "？" not in normalized:
            return False
        # Structural guard: do not inspect language-specific words. A question-only
        # end_turn inside Plan Mode is a pause, not progress.
        return len(normalized) <= 1200

    def should_auto_continue_choice_question_with_tasklist(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        if tool_calls:
            return False
        if self.latest_user_is_claude_code_suggestion_mode(body):
            return False
        if not self.ports.has_tool(body, "TaskList"):
            return False
        latest_names = self.latest_user_tool_result_names(body)
        if self.latest_tool_result_indicates_completed_work(body):
            return False
        if "TaskList" in latest_names and not self.tasklist_result_has_active_work(
            self.latest_user_tool_result_text(body)
        ):
            return False
        intent_index = self.latest_user_intent_message_index(body)
        if (
            self.recent_synthetic_tasklist_count(body, after_message_index=intent_index)
            >= 2
        ):
            return False
        if not self.response_asks_for_user_choice_or_permission(response_text):
            return False
        if self.plan_mode_active(body):
            return True
        return bool(
            self.latest_user_tool_result_names(body)
            and self.latest_user_looks_like_work_request(body)
        )

    def should_synthesize_tasklist_for_provider(self, provider: str) -> bool:
        # TaskList synthesis is a recovery path for non-Anthropic backends that
        # return empty or prose-only turns. Anthropic routed mode can emit native
        # tool_use blocks, so synthesizing an extra TaskList there corrupts the turn.
        return (provider or "").strip().lower() != "anthropic"

    def should_keep_work_alive_with_tasklist(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        if tool_calls:
            return False
        if self.latest_user_is_claude_code_suggestion_mode(body):
            return False
        if not self.ports.has_tool(body, "TaskList"):
            return False
        latest_names = self.latest_user_tool_result_names(body)
        if not latest_names:
            return False
        latest_result_text = self.latest_user_tool_result_text(body)
        if latest_names == ["TaskList"] and "No tasks found" in latest_result_text:
            return False
        if "TaskList" in latest_names:
            if not self.tasklist_result_has_active_work(latest_result_text):
                return False
            max_keepalive = 6
            intent_index = self.latest_user_intent_message_index(body)
            if (
                self.recent_synthetic_tasklist_count(
                    body, after_message_index=intent_index
                )
                >= max_keepalive
            ):
                return False
        if not any(name in WORK_CONTINUATION_RESULT_TOOLS for name in latest_names):
            return False
        if (
            self.latest_tool_result_indicates_completed_work(body)
            and response_text.strip()
        ):
            return False
        if self.non_actionable_short_response(response_text):
            return True
        normalized = re.sub(r"\s+", " ", response_text or "").strip()
        # A resume/continue prompt after a tool result should not end the loop with
        # prose only. Keep this structural and bounded: do not inspect task names,
        # domains, languages, or provider-specific text.
        return (
            self.latest_user_looks_like_work_request(body) and len(normalized) <= 1200
        )

    def should_recover_empty_end_turn_with_tasklist(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        """Recover from non-Anthropic providers returning an empty end_turn.

        Claude Code treats an assistant end_turn with no text and no tool call as a
        completed turn. For implementation/resume prompts, ask Claude Code for the
        task list to give the model a concrete next tool result and keep the loop
        alive.
        """
        if tool_calls:
            return False
        if self.latest_user_is_claude_code_suggestion_mode(body):
            return False
        if response_text.strip():
            return False
        if not self.ports.has_tool(body, "TaskList"):
            return False
        latest_tool_results = self.latest_user_tool_result_names(body)
        if latest_tool_results:
            if (
                "TaskList" in latest_tool_results
                and not self.tasklist_result_has_active_work(
                    self.latest_user_tool_result_text(body)
                )
            ):
                return False
            return True
        latest = self.latest_user_text(body)
        if not latest.strip():
            return False
        if self.short_resume_prompt(latest) and (
            self.latest_assistant_text(body) or self.plan_mode_active(body)
        ):
            return True
        return self.likely_implementation_planning_request(latest)

    def empty_end_turn_notice(
        self,
    ) -> str:
        return (
            "[ciel-runtime] Upstream model returned an empty end_turn with no text or "
            "tool call. No work was performed; please retry or ask me to continue."
        )

    def empty_end_turn_notice_for_body(self, body: dict[str, Any] | None) -> str:
        if isinstance(body, dict) and self.latest_tasklist_result_has_no_active_work(
            body
        ):
            return (
                "[ciel-runtime] TaskList returned no active tasks. No automatic continuation "
                "is available; provide the next instruction or ask for current status."
            )
        return self.empty_end_turn_notice()


@dataclass(frozen=True, slots=True)
class ConversationTurnCompatibilityApi:
    """Typed compatibility adapter that preserves late-bound policy assembly."""

    policy_factory: Callable[[], ConversationTurnPolicy]

    def plan_mode_active(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().plan_mode_active(body)

    def channel_llm_wake_text(self, text: str) -> bool:
        return self.policy_factory().channel_llm_wake_text(text)

    def channel_llm_wake_request(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().channel_llm_wake_request(body)

    def body_without_channel_llm_wake_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.policy_factory().body_without_channel_llm_wake_prompt(body)

    def has_plan_mode_exit(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().has_plan_mode_exit(body)

    def allowed_prompt_tools_for_exit_plan_mode(self, body: dict[str, Any]) -> list[str]:
        return self.policy_factory().allowed_prompt_tools_for_exit_plan_mode(body)

    def exit_plan_mode_default_prompt_for_tool(self, tool_name: str) -> str:
        return self.policy_factory().exit_plan_mode_default_prompt_for_tool(tool_name)

    def backfill_exit_plan_mode_allowed_prompts(
        self, body: dict[str, Any], tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        return self.policy_factory().backfill_exit_plan_mode_allowed_prompts(
            body, tool_input
        )

    def plan_mode_tool_name_for_emit(
        self, body: dict[str, Any], name: str, tool_input: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        return self.policy_factory().plan_mode_tool_name_for_emit(body, name, tool_input)

    def is_guard_feedback_text(self, text: str) -> bool:
        return self.policy_factory().is_guard_feedback_text(text)

    def strip_claude_code_system_reminders(self, text: str) -> str:
        return self.policy_factory().strip_claude_code_system_reminders(text)

    def is_claude_code_suggestion_mode_text(self, text: str) -> bool:
        return self.policy_factory().is_claude_code_suggestion_mode_text(text)

    def user_intent_text_from_message(self, message: dict[str, Any]) -> str:
        return self.policy_factory().user_intent_text_from_message(message)

    def latest_user_text(self, body: dict[str, Any]) -> str:
        return self.policy_factory().latest_user_text(body)

    def latest_user_intent_message_index(self, body: dict[str, Any]) -> int | None:
        return self.policy_factory().latest_user_intent_message_index(body)

    def latest_user_is_claude_code_suggestion_mode(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().latest_user_is_claude_code_suggestion_mode(body)

    def likely_implementation_planning_request(self, text: str) -> bool:
        return self.policy_factory().likely_implementation_planning_request(text)

    def non_actionable_short_response(self, text: str) -> bool:
        return self.policy_factory().non_actionable_short_response(text)

    def body_is_channel_prompt(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().body_is_channel_prompt(body)

    def should_auto_enter_plan_mode(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        return self.policy_factory().should_auto_enter_plan_mode(
            body, response_text, tool_calls
        )

    def response_text_signals_plan_exit(self, text: str) -> bool:
        return self.policy_factory().response_text_signals_plan_exit(text)

    def should_auto_exit_plan_mode(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        return self.policy_factory().should_auto_exit_plan_mode(
            body, response_text, tool_calls
        )

    def bash_command_looks_mutating(self, command: str) -> bool:
        return self.policy_factory().bash_command_looks_mutating(command)

    def latest_user_tool_result_details(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        return self.policy_factory().latest_user_tool_result_details(body)

    def latest_tool_result_indicates_completed_work(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().latest_tool_result_indicates_completed_work(body)

    def latest_user_tool_result_names(self, body: dict[str, Any]) -> list[str]:
        return self.policy_factory().latest_user_tool_result_names(body)

    def latest_user_tool_result_text(self, body: dict[str, Any]) -> str:
        return self.policy_factory().latest_user_tool_result_text(body)

    def synthetic_tasklist_tool_use_id(self, tool_id: str, name: str) -> bool:
        return self.policy_factory().synthetic_tasklist_tool_use_id(tool_id, name)

    def recent_synthetic_tasklist_count(
        self, body: dict[str, Any], after_message_index: int | None = None
    ) -> int:
        return self.policy_factory().recent_synthetic_tasklist_count(
            body, after_message_index
        )

    def tasklist_result_has_active_work(self, text: str) -> bool:
        return self.policy_factory().tasklist_result_has_active_work(text)

    def latest_tasklist_result_has_no_active_work(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().latest_tasklist_result_has_no_active_work(body)

    def latest_assistant_text(self, body: dict[str, Any]) -> str:
        return self.policy_factory().latest_assistant_text(body)

    def short_resume_prompt(self, text: str) -> bool:
        return self.policy_factory().short_resume_prompt(text)

    def latest_user_looks_like_work_request(self, body: dict[str, Any]) -> bool:
        return self.policy_factory().latest_user_looks_like_work_request(body)

    def response_asks_for_user_choice_or_permission(self, text: str) -> bool:
        return self.policy_factory().response_asks_for_user_choice_or_permission(text)

    def should_auto_continue_choice_question_with_tasklist(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        return self.policy_factory().should_auto_continue_choice_question_with_tasklist(
            body, response_text, tool_calls
        )

    def should_synthesize_tasklist_for_provider(self, provider: str) -> bool:
        return self.policy_factory().should_synthesize_tasklist_for_provider(provider)

    def should_keep_work_alive_with_tasklist(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        return self.policy_factory().should_keep_work_alive_with_tasklist(
            body, response_text, tool_calls
        )

    def should_recover_empty_end_turn_with_tasklist(
        self, body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]
    ) -> bool:
        return self.policy_factory().should_recover_empty_end_turn_with_tasklist(
            body, response_text, tool_calls
        )

    def empty_end_turn_notice(self) -> str:
        return self.policy_factory().empty_end_turn_notice()

    def empty_end_turn_notice_for_body(self, body: dict[str, Any] | None) -> str:
        return self.policy_factory().empty_end_turn_notice_for_body(body)
