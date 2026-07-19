from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class ToolResultProjectionServices:
    is_read_unchanged: Callable[[str, str], bool]
    truncate: Callable[[str, int], str]
    result_limit: int


def project_tool_result(
    tool_name: str,
    tool_input_text: str,
    result_text: str,
    is_error: bool,
    services: ToolResultProjectionServices,
    *,
    prior_success_text: str = "",
    include_prior_success: bool = False,
    in_plan_mode: bool = False,
) -> tuple[str, str]:
    if is_error:
        tool_text = (
            f"Tool `{tool_name}` failed.\n"
            f"Input:\n{tool_input_text}\n\n"
            f"Error:\n{result_text}"
        )
        tool_summary = (
            f"The `{tool_name}` tool call above failed. Its input was {tool_input_text}. "
            "Use the error output to choose a different next step; do not blindly repeat it."
        )
        return tool_text, tool_summary
    if services.is_read_unchanged(tool_name, result_text):
        return _project_unchanged_read(
            tool_input_text,
            result_text,
            services,
            prior_success_text=prior_success_text,
            include_prior_success=include_prior_success,
            in_plan_mode=in_plan_mode,
        )
    tool_text = (
        f"Tool `{tool_name}` completed successfully.\n"
        f"Input:\n{tool_input_text}\n\n"
        f"Result:\n{result_text}\n\n"
        "If this result satisfies the user's request, provide the final answer now. "
        f"Do not call `{tool_name}` again with the same arguments."
    )
    tool_summary = (
        f"The `{tool_name}` tool call above already completed successfully. "
        "Treat its tool output as authoritative. Do not repeat the same or equivalent "
        f"`{tool_name}` call; continue with the next required concrete tool call or final answer."
    )
    return tool_text, tool_summary


def _project_unchanged_read(
    tool_input_text: str,
    result_text: str,
    services: ToolResultProjectionServices,
    *,
    prior_success_text: str,
    include_prior_success: bool,
    in_plan_mode: bool,
) -> tuple[str, str]:
    if not include_prior_success:
        return (
            "Tool `Read` returned an unchanged/no-op cache result for content that was already "
            "available earlier in this conversation. No new file content was produced by this "
            "historical duplicate observation.",
            "",
        )
    plan_hint = (
        " If you are in Plan Mode and the plan file is already complete, call ExitPlanMode."
        if in_plan_mode
        else ""
    )
    if not prior_success_text:
        tool_text = (
            "Tool `Read` returned a no-op unchanged result.\n"
            f"Input:\n{tool_input_text}\n\n"
            f"Result:\n{result_text}\n\n"
            "No previous successful Read result for this exact input is available in the converted context. "
            "Do not repeat the same Read. If the content is still needed, read a different or broader range once; "
            "otherwise proceed with the next distinct step."
        )
        tool_summary = (
            "The latest `Read` result says the file/range is unchanged, but no prior exact Read content is available "
            "in this converted context. Do not loop on the same Read; either read a different range once or continue "
            f"with the next distinct step.{plan_hint}"
        )
        return tool_text, tool_summary
    previous = (
        "\n\nPrevious successful Read result for this exact input remains authoritative:\n"
        f"{services.truncate(prior_success_text, services.result_limit)}"
    )
    tool_text = (
        "Tool `Read` returned a no-op unchanged result.\n"
        f"Input:\n{tool_input_text}\n\n"
        f"Result:\n{result_text}"
        f"{previous}\n\n"
        "This is not new file content. It means the previous successful Read result for the same input is still current."
    )
    tool_summary = (
        "The latest `Read` result is a Claude Code no-op/cache result: no file content changed and no new observation was produced. "
        "Use the previous successful Read result for this exact input as the current observation, then choose the next distinct step."
        f"{plan_hint}"
    )
    return tool_text, tool_summary
