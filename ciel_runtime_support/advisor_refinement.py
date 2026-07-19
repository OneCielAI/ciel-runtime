"""Apply Advisor feedback as a bounded refinement decorator around a response."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AdvisorRefinementText:
    content_text: Callable[[Any], str]
    tool_names: Callable[[dict[str, Any]], list[str]]
    tool_summary: Callable[[dict[str, Any]], str]
    compact_text: Callable[[str], str]
    prepend_text: Callable[[dict[str, Any], str], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AdvisorRefinementPolicy:
    model_enabled: Callable[[dict[str, Any]], bool]
    provider_supported: Callable[[str], bool]
    is_advisor_request: Callable[[dict[str, Any]], bool]
    has_feedback: Callable[[dict[str, Any]], bool]
    trigger: Callable[[dict[str, Any], dict[str, Any]], str]
    focus: Callable[[dict[str, Any], str], tuple[str, str]]


@dataclass(frozen=True, slots=True)
class AdvisorRefinementIO:
    call_advisor: Callable[..., str]
    call_provider: Callable[..., dict[str, Any]]
    log: Callable[[str, str], None]
    write_activity: Callable[..., None]


class AdvisorRefinementService:
    def __init__(
        self,
        feedback_marker: str,
        text: AdvisorRefinementText,
        policy: AdvisorRefinementPolicy,
        io: AdvisorRefinementIO,
    ) -> None:
        self.feedback_marker = feedback_marker
        self.text = text
        self.policy = policy
        self.io = io

    def body_with_feedback(
        self,
        body: dict[str, Any],
        assistant_message: dict[str, Any],
        advisor_text: str,
        trigger: str,
    ) -> dict[str, Any]:
        messages = [item for item in body.get("messages", []) if isinstance(item, dict)]
        assistant_text = self.text.content_text(assistant_message.get("content")).strip()
        tool_names = self.text.tool_names(assistant_message)
        tool_summary = self.text.tool_summary(assistant_message)
        if assistant_text:
            summary = self.text.compact_text(assistant_text)
        elif tool_summary:
            summary = self.text.compact_text(tool_summary)
        elif tool_names:
            summary = (
                "I was about to call Claude Code tool(s): "
                + ", ".join(tool_names)
                + "."
            )
        else:
            summary = ""
        if summary:
            messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": summary}]}
            )
        feedback = (
            f"{self.feedback_marker}\n"
            f"Advisor review trigger: {trigger}.\n\n"
            f"{advisor_text}\n\n"
            "Apply this advisor feedback now. If the plan is still ready, call "
            "ExitPlanMode with the improved plan. If the task is complete, provide "
            "the final answer. If the advisor found a gap, continue with concrete "
            "Claude Code tool calls instead of stopping after an announcement."
        )
        messages.append(
            {"role": "user", "content": [{"type": "text", "text": feedback}]}
        )
        projected = dict(body)
        projected["messages"] = messages
        projected.pop("tool_choice", None)
        return projected

    @staticmethod
    def visible_summary(advisor_text: str, trigger: str, limit: int = 700) -> str:
        text = re.sub(r"\s+", " ", str(advisor_text or "")).strip()
        if not text:
            return ""
        if len(text) > limit:
            text = text[: max(0, limit - 1)].rstrip() + "…"
        return f"Advisor review ({trigger}): {text}\n\n"

    def refine(
        self,
        provider: str,
        config: dict[str, Any],
        original_body: dict[str, Any],
        message: dict[str, Any],
        main_model: str,
    ) -> dict[str, Any]:
        if not self.policy.model_enabled(config):
            return message
        if not self.policy.provider_supported(provider):
            return message
        if self.policy.is_advisor_request(original_body) or self.policy.has_feedback(
            original_body
        ):
            return message
        trigger = self.policy.trigger(original_body, message)
        trigger, advisor_focus = self.policy.focus(message, trigger)
        if not trigger:
            return message
        advisor_text = self.io.call_advisor(
            provider,
            config,
            original_body,
            focus=advisor_focus or trigger,
        )
        if not advisor_text:
            return message
        follow_body = self.body_with_feedback(
            original_body, message, advisor_text, trigger
        )
        summary = self.visible_summary(advisor_text, trigger)
        try:
            self.io.log(
                "INFO",
                f"advisor_refinement_call trigger={trigger} main_model={main_model}",
            )
            refined = self.io.call_provider(provider, config, follow_body, main_model)
            self.io.log(
                "INFO",
                f"advisor_refinement_done trigger={trigger} "
                f"stop_reason={refined.get('stop_reason')}",
            )
            return self.text.prepend_text(refined, summary)
        except Exception as exc:
            self.io.log(
                "WARN",
                f"advisor_refinement_failed trigger={trigger} model={main_model} "
                f"error={type(exc).__name__}: {exc}",
            )
            self.io.write_activity(
                "advisor_refinement_error",
                provider,
                main_model,
                error=type(exc).__name__,
            )
            return self.text.prepend_text(message, summary)
