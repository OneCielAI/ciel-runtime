"""Conversation-state and tool-history policies used by protocol projections."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable


READ_UNCHANGED_RESULT_RE = re.compile(
    r"(wasted call|unchanged since (?:your )?last read|file unchanged since (?:your )?last read)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ConversationPolicyServices:
    content_blocks: Callable[[dict[str, Any]], list[Any]]
    content_to_text: Callable[[Any], str]
    plan_mode_active: Callable[[dict[str, Any]], bool]
    persisted_output: Callable[[str], bool]
    transcript_event: Callable[[dict[str, Any]], bool]
    guard_feedback: Callable[[str], bool]


def message_attachment(message: dict[str, Any]) -> dict[str, Any] | None:
    attachment = message.get("attachment")
    return attachment if isinstance(attachment, dict) else None


def is_attachment_only_message(
    message: dict[str, Any], services: ConversationPolicyServices
) -> bool:
    if not message_attachment(message):
        return False
    content = message.get("content")
    if content is None:
        return True
    blocks = services.content_blocks(message)
    if any(
        isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}
        for block in blocks
    ):
        return False
    return not services.content_to_text(content).strip()


def latest_plan_attachment(body: dict[str, Any]) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        attachment = message_attachment(message)
        if attachment and attachment.get("type") in {
            "plan_mode",
            "plan_mode_reentry",
            "plan_mode_exit",
        }:
            latest = attachment
    return latest


def plan_file_written_in_body(
    body: dict[str, Any],
    plan_file_path: str,
    services: ConversationPolicyServices,
) -> bool:
    if not plan_file_path:
        return False
    tool_names_by_id: dict[str, str] = {}
    tool_inputs_by_id: dict[str, Any] = {}
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = services.content_blocks(message)
        if message.get("role") == "assistant":
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = str(block.get("id") or "")
                if tool_id:
                    tool_names_by_id[tool_id] = str(block.get("name") or "")
                    tool_inputs_by_id[tool_id] = (
                        block.get("input") if isinstance(block.get("input"), dict) else {}
                    )
        elif message.get("role") == "user":
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id") or "")
                tool_input = tool_inputs_by_id.get(tool_use_id)
                if (
                    tool_names_by_id.get(tool_use_id) in {"Write", "Edit", "MultiEdit"}
                    and isinstance(tool_input, dict)
                    and str(tool_input.get("file_path") or "") == plan_file_path
                    and not block.get("is_error")
                ):
                    return True
    return False


def claude_code_state_messages(
    body: dict[str, Any], services: ConversationPolicyServices
) -> list[dict[str, str]]:
    if not services.plan_mode_active(body):
        return []
    attachment = latest_plan_attachment(body)
    plan_file_path = str((attachment or {}).get("planFilePath") or "")
    plan_exists = (attachment or {}).get("planExists")
    plan_written = plan_file_written_in_body(body, plan_file_path, services)
    parts = [
        "Claude Code state: Plan Mode is active.",
        "In Plan Mode, exploration tools are allowed, but implementation/editing of normal project files must wait until ExitPlanMode approval.",
    ]
    if plan_file_path:
        parts.extend(
            (
                f"Plan file path: {plan_file_path}.",
                f"Plan file written or updated in this conversation: {'yes' if plan_written else 'no'}.",
            )
        )
    if plan_exists is not None:
        parts.append(f"Claude Code attachment planExists: {bool(plan_exists)}.")
    parts.append(
        "When the plan file is complete and no new information is needed, call ExitPlanMode with the plan instead of repeating unchanged Read calls."
    )
    return [{"role": "system", "content": "\n".join(parts)}]


def canonical_tool_signature(tool_name: str, tool_input: Any) -> str:
    normalized = tool_input if isinstance(tool_input, dict) else {}
    return f"{tool_name}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"


def is_read_unchanged_result(tool_name: str, result_text: str) -> bool:
    return tool_name == "Read" and bool(READ_UNCHANGED_RESULT_RE.search(result_text or ""))


def should_skip_upstream_message(
    message: dict[str, Any], services: ConversationPolicyServices
) -> bool:
    if services.transcript_event(message):
        return True
    role = message.get("role")
    if role == "user" and message.get("isMeta") is True:
        return True
    text = services.content_to_text(message.get("content", "")).strip()
    if services.guard_feedback(text):
        return True
    if role in ("assistant", "user") and (
        text in {"Upstream returned an empty stream.", "Upstream returned no stream data."}
        or text.startswith("Upstream stream error:")
    ):
        return True
    return role == "assistant" and text == "No response requested."


def upstream_relevant_message(
    message: dict[str, Any], services: ConversationPolicyServices
) -> bool:
    if is_attachment_only_message(message, services) or should_skip_upstream_message(message, services):
        return False
    if message.get("role") not in {"assistant", "user", "system", "tool"}:
        return False
    blocks = services.content_blocks(message)
    if any(
        isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}
        for block in blocks
    ):
        return True
    return bool(services.content_to_text(message.get("content", "")).strip())


def collect_tool_result_context(
    body: dict[str, Any], services: ConversationPolicyServices
) -> tuple[dict[str, str], set[str]]:
    tool_names: dict[str, str] = {}
    tool_inputs: dict[str, Any] = {}
    successful: dict[str, str] = {}
    records: list[tuple[int, str, str, str]] = []
    latest_relevant_index = -1
    for index, message in enumerate(body.get("messages") or []):
        if not isinstance(message, dict):
            continue
        if upstream_relevant_message(message, services):
            latest_relevant_index = index
        blocks = services.content_blocks(message)
        if message.get("role") == "assistant":
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = str(block.get("id") or "")
                    if tool_id:
                        tool_names[tool_id] = str(block.get("name") or "")
                        tool_inputs[tool_id] = block.get("input") if isinstance(block.get("input"), dict) else {}
        elif message.get("role") == "user":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_id = str(block.get("tool_use_id") or "")
                tool_name = tool_names.get(tool_id, "tool")
                tool_input = tool_inputs.get(tool_id) or {}
                result = services.content_to_text(block.get("content", ""))
                records.append((index, tool_id, tool_name, result))
                if (
                    not services.persisted_output(result)
                    and not is_read_unchanged_result(tool_name, result)
                    and not block.get("is_error")
                    and result.strip()
                ):
                    successful[canonical_tool_signature(tool_name, tool_input)] = result
    noops = {
        tool_id
        for index, tool_id, tool_name, result in records
        if index == latest_relevant_index and is_read_unchanged_result(tool_name, result)
    }
    return successful, noops
