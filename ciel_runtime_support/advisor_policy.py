from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AdvisorTextServices:
    content_to_text: Callable[[Any], str]
    tool_input_for_prompt: Callable[[Any], str]


@dataclass(frozen=True)
class AdvisorDecisionServices:
    advisor_enabled: Callable[[dict[str, Any]], bool]
    is_advisor_request: Callable[[dict[str, Any]], bool]
    completed_work: Callable[[dict[str, Any]], bool]
    non_actionable_response: Callable[[str], bool]
    plan_mode_active: Callable[[dict[str, Any]], bool]
    provider_supported: Callable[[str], bool]


@dataclass(frozen=True)
class AdvisorServices:
    text: AdvisorTextServices
    decisions: AdvisorDecisionServices
    log: Callable[[str, str], None]
    feedback_marker: str


@dataclass(frozen=True, slots=True)
class AdvisorShortcutPorts:
    should_intercept: Callable[[str, dict[str, Any]], bool]
    is_request: Callable[[dict[str, Any]], bool]
    provider_supported: Callable[[str], bool]
    call_text: Callable[..., str]
    write_anthropic: Callable[..., Any]
    load_config: Callable[[], dict[str, Any]]
    current_alias: Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class AdvisorShortcutController:
    ports: AdvisorShortcutPorts

    def handle(
        self,
        handler: Any,
        provider: str,
        provider_config: dict[str, Any],
        body: dict[str, Any],
    ) -> bool:
        if not self.ports.should_intercept(provider, provider_config):
            return False
        if not self.ports.is_request(body):
            return False
        advisor_model = str(provider_config.get("advisor_model") or "").strip()
        stream = bool(body.get("stream", True))
        if not advisor_model:
            model = str(
                body.get("model")
                or self.ports.current_alias(self.ports.load_config())
            )
            self.ports.write_anthropic(
                handler,
                model,
                "Advisor is off. Choose an Advisor Model in the ciel-runtime launch menu (item 5), or run `ciel-runtime advisor-model <model-id>`, then use `/advisor` again.",
                stream,
            )
            return True
        if not self.ports.provider_supported(provider):
            self.ports.write_anthropic(
                handler,
                advisor_model,
                f"Advisor Model is configured as `{advisor_model}`, but ciel-runtime advisor calling is not implemented for provider `{provider}`.",
                stream,
            )
            return True
        try:
            text = self.ports.call_text(
                provider,
                provider_config,
                body,
                inbound_headers=handler.headers,
                allow_rate_limit_wait=False,
                retry_rate_limits=False,
                raise_errors=True,
            )
            if not text:
                text = "Advisor returned no text."
        except Exception as exc:
            text = f"Advisor request failed: {type(exc).__name__}: {exc}"
        self.ports.write_anthropic(
            handler, advisor_model, "Advisor guidance:\n\n" + text, stream
        )
        return True


def advisor_messages_and_system(
    body: dict[str, Any], services: AdvisorServices
) -> tuple[list[dict[str, Any]], list[str]]:
    messages: list[dict[str, Any]] = []
    system_texts: list[str] = []
    for message in body.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        text = services.text.content_to_text(message.get("content")).strip()
        if role == "system":
            if text:
                system_texts.append(text)
        elif role in {"user", "assistant"}:
            messages.append(message)
        elif text:
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": f"[{role or 'unknown'} message]\n{text}"}],
                }
            )
    return messages, system_texts


def advisor_tool_schema() -> dict[str, Any]:
    return {
        "name": "advisor",
        "description": (
            "Consult a second, stronger advisor model for an independent review before you make an important "
            "plan, architecture, debugging, or final-completion decision. Use this when additional scrutiny could "
            "catch gaps, risks, or better next steps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The specific decision, plan, code path, or risk you want the advisor to review.",
                }
            },
            "required": ["question"],
        },
    }


def body_with_advisor_tool(
    body: dict[str, Any], provider_config: dict[str, Any], services: AdvisorServices
) -> dict[str, Any]:
    if not services.decisions.advisor_enabled(provider_config):
        return body
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    if any(isinstance(tool, dict) and str(tool.get("name") or "") == "advisor" for tool in tools):
        return body
    output = dict(body)
    output["tools"] = [*tools, advisor_tool_schema()]
    return output


def is_claude_code_advisor_server_tool(tool: Any) -> bool:
    return bool(
        isinstance(tool, dict)
        and str(tool.get("name") or "") == "advisor"
        and str(tool.get("type") or "").startswith("advisor_")
    )


def strip_autonomous_advisor_server_tools(
    body: dict[str, Any],
    services: AdvisorServices,
    *,
    server_tool_supported: bool,
) -> dict[str, Any]:
    if server_tool_supported or services.decisions.is_advisor_request(body):
        return body
    if body_has_advisor_feedback(body, services):
        return body
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return body
    kept = [tool for tool in tools if not is_claude_code_advisor_server_tool(tool)]
    removed = len(tools) - len(kept)
    if not removed:
        return body
    output = dict(body)
    output["tools"] = kept
    services.log("INFO", f"stripped autonomous advisor server tool count={removed}")
    return output


def advisor_tool_focus_from_message(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if str(block.get("name") or "") != "advisor":
            continue
        tool_input = block.get("input")
        if isinstance(tool_input, dict):
            for key in ("question", "prompt", "focus", "task"):
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return "advisor tool call"
    return None


def tool_review_context_from_message(
    message: dict[str, Any], trigger: str, services: AdvisorServices
) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    sections: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "").strip()
        if not name or name == "advisor":
            continue
        input_text = services.text.tool_input_for_prompt(block.get("input"))
        if name == "ExitPlanMode":
            sections.append(
                "Review target: ExitPlanMode plan before user approval.\n"
                "The executor is about to submit this plan. Review the actual plan/tool input below.\n"
                f"Tool input:\n{input_text}"
            )
        elif trigger:
            sections.append(f"Review target tool: {name} ({trigger}).\nTool input:\n{input_text}")
    return "\n\n".join(sections)


def advisor_focus_for_message(
    message: dict[str, Any], trigger: str | None, services: AdvisorServices
) -> tuple[str | None, str | None]:
    explicit_focus = advisor_tool_focus_from_message(message)
    if explicit_focus:
        return "advisor tool call", explicit_focus
    if not trigger:
        return None, None
    review_context = tool_review_context_from_message(message, trigger, services)
    return trigger, review_context or trigger


def assistant_tool_call_summary_for_prompt(
    message: dict[str, Any], services: AdvisorServices
) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    sections: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "tool").strip() or "tool"
        if name != "advisor":
            sections.append(
                f"Pending Claude Code tool call: {name}\nTool input:\n"
                f"{services.text.tool_input_for_prompt(block.get('input'))}"
            )
    return "\n\n".join(sections)


def body_has_advisor_feedback(body: dict[str, Any], services: AdvisorServices) -> bool:
    for message in reversed(body.get("messages") or []):
        if not isinstance(message, dict):
            continue
        if services.feedback_marker in services.text.content_to_text(message.get("content")):
            return True
    return False


def anthropic_message_tool_names(message: dict[str, Any]) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    names: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def advisor_trigger_for_message(
    body: dict[str, Any], message: dict[str, Any], services: AdvisorServices
) -> str | None:
    names = set(anthropic_message_tool_names(message))
    if "ExitPlanMode" in names:
        return "before ExitPlanMode plan approval"
    if names:
        return None
    text = services.text.content_to_text(message.get("content")).strip()
    if (
        text
        and message.get("stop_reason") == "end_turn"
        and services.decisions.completed_work(body)
        and not services.decisions.non_actionable_response(text)
    ):
        return "before completion/final response"
    return None


def advisor_gate_reason_for_body(
    provider: str,
    provider_config: dict[str, Any],
    body: dict[str, Any],
    services: AdvisorServices,
) -> str:
    decisions = services.decisions
    if not decisions.advisor_enabled(provider_config) or not decisions.provider_supported(provider):
        return ""
    if decisions.is_advisor_request(body) or body_has_advisor_feedback(body, services):
        return ""
    if decisions.plan_mode_active(body):
        return "plan_mode"
    if decisions.completed_work(body):
        return "completed_work"
    return ""
