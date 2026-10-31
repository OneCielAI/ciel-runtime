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
    if not is_error and services.is_read_unchanged(tool_name, result_text):
        return _project_unchanged_read(
            tool_input_text,
            result_text,
            services,
            prior_success_text=prior_success_text,
            include_prior_success=include_prior_success,
            in_plan_mode=in_plan_mode,
        )
    tool_text = (
        f"Tool `{tool_name}` returned a result (is_error={str(bool(is_error)).lower()}).\n"
        f"Input:\n{tool_input_text}\n\n"
        f"Result:\n{result_text}"
    )
    return tool_text, ""


def truncate_tool_result(text: str, limit: int) -> str:
    """Bound output without discarding the final summary or exit status."""
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    # A fixed-width count keeps the retained budget and omitted count consistent.
    width = len(str(len(text)))
    template = "\n...[tool output omitted: {count} chars]...\n"
    marker_size = len(template.format(count="0" * width))
    if marker_size > limit:
        return text[-limit:] if limit else ""
    budget = limit - marker_size
    marker = template.format(count=str(len(text) - budget).rjust(width))
    head = budget // 2
    tail = budget - head
    return text[:head] + marker + (text[-tail:] if tail else "")


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
            "The unchanged result alone does not supply the requested file content."
        )
        tool_summary = (
            "The latest `Read` result says the file/range is unchanged, but no prior exact Read content is available "
            f"in this converted context.{plan_hint}"
        )
        return tool_text, tool_summary
    previous = (
        "\n\nPrevious successful Read result for this exact input remains authoritative:\n"
        f"{truncate_tool_result(prior_success_text, services.result_limit)}"
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
