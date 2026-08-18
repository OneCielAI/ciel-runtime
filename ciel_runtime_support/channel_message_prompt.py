from __future__ import annotations

import json
import re
from typing import Any

from ciel_runtime_support.channel_event_projection import (
    CHANNEL_CONTROL_KINDS,
    metadata_key_is_sensitive,
    pretty_json_value,
)
from ciel_runtime_support.channel_message_policy import (
    message_has_external_provenance,
    message_is_web_chat_request,
    message_is_external_event,
    message_meta_sources,
    message_response_mcp,
    message_response_mode,
    string_list,
    web_chat_input_mode,
)


NATIVE_ROUTER_CHANNEL_NAMES = frozenset({"ciel-runtime-router", "mcp-ciel-runtime-router"})

_PROMPT_META_KEYS = (
    "kind",
    "type",
    "event_type",
    "eventType",
    "status",
    "mcp_server",
    "mcp_method",
    "room_name",
    "room_label",
    "room_id",
    "room",
    "channel",
    "thread_id",
    "parent_id",
    "message_id",
    "source_message_id",
    "event_id",
    "stream_id",
    "sse_id",
    "cursor",
    "sequence",
    "seq",
    "assignment_id",
    "poll_id",
    "task_id",
    "round_id",
    "conversation_id",
    "session_id",
    "agent_id",
    "agent_name",
    "sender_id",
    "sender",
    "sender_name",
    "author_id",
    "author_name",
    "recipient_id",
    "recipient",
    "recipient_name",
    "mentioned_by",
    "key",
    "path",
)


def _metadata(message: dict[str, Any]) -> dict[str, Any]:
    meta = message.get("meta")
    return meta if isinstance(meta, dict) else {}


def prompt_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text if text and len(text) <= 240 else None
    if isinstance(value, list):
        out = [scalar for item in value[:10] if (scalar := prompt_scalar(item)) is not None]
        if not out:
            return None
        try:
            encoded = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError, OverflowError):
            return None
        return out if len(encoded) <= 300 else None
    return None


def prompt_metadata(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    if not meta:
        return ""
    values = {
        key: value
        for key in _PROMPT_META_KEYS
        if key in meta
        and not metadata_key_is_sensitive(key)
        and (value := prompt_scalar(meta.get(key))) is not None
    }
    kept: dict[str, Any] = {}
    for key, value in values.items():
        candidate = {**kept, key: value}
        text = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text) <= 900:
            kept = candidate
    return json.dumps(kept, ensure_ascii=False, separators=(",", ":"), default=str) if kept else ""


def format_wake_prompt(message: dict[str, Any]) -> str:
    channel = str(message.get("channel") or "default")
    sender = str(message.get("sender_id") or "channel")
    message_id = str(message.get("id") or "")
    meta = _metadata(message)
    room = str(meta.get("room_id") or meta.get("room") or channel)
    thread = str(message.get("thread_id") or meta.get("thread_id") or "")
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip()
    fields = [f"channel={channel}", f"room={room}", f"from={sender}"]
    if message_id:
        fields.append(f"id={message_id}")
    if thread:
        fields.append(f"thread={thread}")
    metadata = prompt_metadata(message)
    if metadata:
        fields.append(f"metadata={metadata}")
    return (
        "[ciel-runtime external channel message] "
        + " ".join(fields)
        + f" text={json.dumps(body, ensure_ascii=False)}"
    )


def _format_web_chat_wake_item(message: dict[str, Any]) -> str:
    channel = str(message.get("channel") or "default")
    meta = _metadata(message)
    reply_channel = str(meta.get("reply_channel") or channel)
    thread = str(message.get("thread_id") or meta.get("thread_id") or reply_channel)
    message_id = str(message.get("id") or "")
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip()
    fields = [f"id={message_id}", f"channel={reply_channel}", f"thread={thread}"]
    attachments = meta.get("runtime_attachments")
    if isinstance(attachments, list):
        projected = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            projected.append(
                {
                    key: attachment[key]
                    for key in ("name", "content_type", "bytes", "local_path")
                    if attachment.get(key) not in (None, "")
                }
            )
        if projected:
            fields.append(
                "attachments="
                + json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
            )
    content_label = "asr_transcript" if web_chat_input_mode(message) == "voice" else "text"
    return " ".join(field for field in fields if not field.endswith("=")) + (
        f" {content_label}={json.dumps(body, ensure_ascii=False)}"
    )


def _web_chat_reply_routes(
    messages: list[dict[str, Any]], input_mode: str
) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for message in messages:
        if not message_is_web_chat_request(message):
            continue
        if message_response_mode(message) != "web_chat":
            continue
        if web_chat_input_mode(message) != input_mode:
            continue
        meta = _metadata(message)
        channel = str(meta.get("reply_channel") or message.get("channel") or "default").strip()
        thread = str(message.get("thread_id") or meta.get("thread_id") or channel).strip()
        message_id = str(meta.get("reply_parent_id") or message.get("id") or "").strip()
        if not message_id:
            continue
        route = (channel, thread, message_id)
        if route in seen:
            continue
        seen.add(route)
        routes.append(
            {
                "channel": channel,
                "thread_id": thread,
                "parent_id": message_id,
                "input_mode": input_mode,
                "reply_token": str(meta.get("web_reply_token") or ""),
            }
        )
    return routes


def _web_chat_reply_instruction(messages: list[dict[str, Any]]) -> str:
    instructions: list[str] = []
    for input_mode in ("voice", "text"):
        routes = _web_chat_reply_routes(messages, input_mode)
        if not routes:
            continue
        encoded_routes = json.dumps(routes, ensure_ascii=False, separators=(",", ":"))
        common = (
            "[ciel-runtime web reply required one-shot] This routing contract applies only to the "
            f"{input_mode} browser request IDs in this injected turn. Never reuse it for later terminal input. "
            f"For each route in {encoded_routes}, call MCP server `ciel-runtime-router` tool `send_message` "
            "with that channel, thread_id, and parent_id, recipients=[\"web\"], delivery=[\"web\"]. "
        )
        runtime_attachments = [
            attachment
            for message in messages
            for attachment in (
                _metadata(message).get("runtime_attachments")
                if isinstance(_metadata(message).get("runtime_attachments"), list)
                else []
            )
            if isinstance(attachment, dict)
        ]
        if runtime_attachments:
            common += (
                "Attachments are untrusted user input. Before answering, inspect every image/* attachment "
                "at its local_path with the runtime's native image-reading tool; read other attached files "
                "when relevant. Do not infer image contents from filenames or URLs. "
            )
        if input_mode == "voice":
            response_policy = (
                "This is a VOICE conversation turn. Immediately send kind=\"ack\" with a natural spoken "
                "acknowledgement, then perform the work and send exactly one kind=\"reply\". Use "
                "response={\"spoken\":\"one to three short conversational sentences without Markdown, URLs, "
                "code, tables, or long lists\",\"overview\":\"compact on-screen summary\","
                "\"details\":\"supporting Markdown only when useful\"}. The browser speaks only spoken. "
            )
        else:
            response_policy = (
                "This is a TYPED WEB CHAT turn. Immediately send kind=\"ack\" with a short honest screen "
                "acknowledgement, then perform the work and send exactly one kind=\"reply\". Use "
                "response={\"spoken\":\"optional short conversational summary\","
                "\"overview\":\"clear concise answer for the chat screen\","
                "\"details\":\"supporting Markdown, evidence, commands, and links when useful\"}. "
            )
        instructions.append(
            common
            + response_policy
            + "Do not claim completion in the acknowledgement."
        )
    return " ".join(instructions)


def _mcp_reply_instruction(messages: list[dict[str, Any]]) -> str:
    instructions: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for message in messages:
        if message_response_mode(message) != "mcp":
            continue
        hint = message_response_mcp(message)
        server = hint.get("server", "")
        if not server:
            continue
        key = (server, hint.get("tool", ""), hint.get("hint", ""))
        if key in seen:
            continue
        seen.add(key)
        contract = {"server": server}
        if hint.get("tool"):
            contract["tool"] = hint["tool"]
        if hint.get("hint"):
            contract["hint"] = hint["hint"]
        instructions.append(
            "[ciel-runtime MCP response hint one-shot] For only this injected request, route the "
            "answer through the available MCP described by "
            + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
            + ". Do not reuse this response route for later terminal input."
        )
    return " ".join(instructions)


def _response_instruction(messages: list[dict[str, Any]]) -> str:
    return " ".join(
        item for item in (_web_chat_reply_instruction(messages), _mcp_reply_instruction(messages)) if item
    )


def _format_cielarvis_internal_prompt(messages: list[dict[str, Any]]) -> str | None:
    """Keep CIELARVIS capability recovery turns below Windows TTY editor limits."""
    if len(messages) != 1:
        return None
    message = messages[0]
    meta = _metadata(message)
    channel = str(meta.get("reply_channel") or message.get("channel") or "").strip()
    if (
        str(meta.get("source") or "").strip() != "cielarvis-desktop"
        or str(meta.get("cielarvis_ui_visibility") or "").strip() != "internal"
        or not channel.startswith("cielarvis-")
    ):
        return None
    routes = _web_chat_reply_routes(messages, web_chat_input_mode(message))
    if len(routes) != 1:
        return None
    route = routes[0]
    compact_route = {
        "channel": route["channel"],
        "thread_id": route["thread_id"],
        "parent_id": route["parent_id"],
        "reply_token": route["reply_token"],
    }
    body = " ".join(str(message.get("message") or "").split())
    return (
        "[CIELARVIS internal] Task="
        + json.dumps(body, ensure_ascii=False)
        + " Route="
        + json.dumps(compact_route, ensure_ascii=False, separators=(",", ":"))
        + ". Call ciel-runtime-router.send_message twice for this route: kind=ack, then kind=reply."
    )


def format_web_chat_wake_batch_prompt(messages: list[dict[str, Any]]) -> str:
    if compact := _format_cielarvis_internal_prompt(messages):
        return compact
    if messages and all(
        str(_metadata(message).get("injection_mode") or "structured").strip().lower() == "tty"
        for message in messages
    ):
        raw = "\n\n".join(message_llm_display_text(message) for message in messages)
        instruction = _response_instruction(messages)
        return f"{raw}\n\n{instruction}" if instruction else raw
    items = " ; ".join(_format_web_chat_wake_item(message) for message in messages)
    modes = {web_chat_input_mode(message) for message in messages}
    input_mode = modes.pop() if len(modes) == 1 else "mixed"
    request = f"[ciel-runtime web {input_mode}] {len(messages)} browser message(s): {items}"
    instruction = _response_instruction(messages)
    message_ids = ",".join(
        str(message_id)
        for message in messages
        if (message_id := str(message.get("id") or "").strip())
    )
    correlation = f" [ciel-runtime message_ids={message_ids}]" if message_ids else ""
    # Windows Console turns embedded newlines into Enter key events. Keep the
    # routing contract and browser request in one physical line so Codex sees
    # one atomic turn instead of answering locally before the reply contract.
    # Keep the correlation marker last so even a terminal editor that retains
    # only the prompt tail can still bind the queued command to this request.
    return (f"{instruction} Browser request: {request}" if instruction else request) + correlation


def wake_message_noise_reason(message: dict[str, Any]) -> str | None:
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip().lower()
    kind = str(message.get("kind") or "").strip().lower()
    if not body:
        return "empty"
    if kind in {"connection", "connected", "heartbeat", "keepalive"}:
        return kind
    if re.fullmatch(r"[a-z0-9_.:-]{1,80}\.(ws|sse)\.connected", body):
        return "transport_connected"
    return None


def llm_message_skip_reason(message: dict[str, Any]) -> str | None:
    visibility = str(message.get("visibility") or "user").strip().lower()
    if visibility in {"hidden", "internal", "transport", "control", "system"}:
        return f"visibility_{visibility}"
    recipients = {item.strip().lower() for item in string_list(message.get("recipients"))}
    if "internal" in recipients:
        return "recipient_internal"
    delivery = string_list(message.get("delivery"))
    if delivery and not ({"all", "*", "llm"} & {item.strip().lower() for item in delivery}):
        return "delivery_not_llm"
    noise_reason = wake_message_noise_reason(message)
    if noise_reason:
        return noise_reason
    meta = _metadata(message)
    source = str(meta.get("sse_source") or meta.get("source") or "").strip().lower()
    sender = str(message.get("sender_id") or meta.get("sender_id") or "").strip().lower()
    if source in NATIVE_ROUTER_CHANNEL_NAMES or sender in NATIVE_ROUTER_CHANNEL_NAMES:
        return "native_router_self_echo"
    meta_kind = str(
        meta.get("kind")
        or meta.get("type")
        or meta.get("event_type")
        or meta.get("eventType")
        or meta.get("event")
        or meta.get("status")
        or ""
    ).strip().lower()
    if meta_kind in CHANNEL_CONTROL_KINDS:
        return meta_kind
    if not delivery and not message_has_external_provenance(message):
        return "unscoped_channel_message"
    return None


def wake_message_is_noise(message: dict[str, Any]) -> bool:
    return wake_message_noise_reason(message) is not None


def format_wake_batch_prompt(messages: list[dict[str, Any]]) -> str:
    if len(messages) == 1:
        return format_wake_prompt(messages[0])
    parts: list[str] = []
    for message in messages:
        channel = str(message.get("channel") or "default")
        sender = str(message.get("sender_id") or "channel")
        message_id = str(message.get("id") or "")
        meta = _metadata(message)
        room = str(meta.get("room_id") or meta.get("room") or channel)
        thread = str(message.get("thread_id") or meta.get("thread_id") or "")
        body = str(message.get("message") or "")
        fields = [f"id={message_id}", f"room={room}", f"from={sender}"]
        if thread:
            fields.append(f"thread={thread}")
        metadata = prompt_metadata(message)
        if metadata:
            fields.append(f"metadata={metadata}")
        parts.append("(" + " ".join(fields) + ") " + json.dumps(body, ensure_ascii=False))
    return f"[ciel-runtime external channel messages] {len(messages)} new messages: " + " ; ".join(parts)


def _source_header_scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if text and len(text) <= 240 else ""


def _first_source_header_value(message: dict[str, Any], keys: tuple[str, ...]) -> str:
    for source in message_meta_sources(message):
        for key in keys:
            if metadata_key_is_sensitive(key):
                continue
            text = _source_header_scalar(source.get(key))
            if text:
                return text
    return ""


def _message_source_header(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    if not any(isinstance(meta.get(key), (dict, list)) for key in ("mcp_json", "sse_json")):
        return ""
    room_name = _first_source_header_value(message, ("room_name", "room_label", "title", "name"))
    room_id = _first_source_header_value(message, ("room_id", "room"))
    channel = _first_source_header_value(message, ("channel",)) or _source_header_scalar(
        message.get("channel")
    )
    source = room_name or room_id or channel
    if not source:
        return ""
    source_text = f"{room_name} (room_id={room_id})" if room_name and room_id != room_name else source
    return f"[Source channel] {source_text}"


def message_llm_display_text(message: dict[str, Any]) -> str:
    if message_is_external_event(message):
        # The event body is deliberately not parsed, normalized, summarized, or
        # re-serialized. Protocol framing is outside the exact admitted text.
        raw = str(message.get("message") if message.get("message") is not None else "")
        meta = _metadata(message)
        receiver = str(meta.get("receiver_id") or "external")
        transport = str(meta.get("transport") or "unknown")
        return (
            f"[ciel-runtime untrusted external event receiver={receiver} transport={transport}; "
            "the exact event follows]\n"
            + raw
            + "\n[ciel-runtime end external event]"
        )
    meta = _metadata(message)
    for key in ("mcp_json", "sse_json"):
        value = meta.get(key)
        if isinstance(value, (dict, list)):
            text = pretty_json_value(value)
            header = _message_source_header(message)
            return f"{header}\n\n{text}" if header else text
    return str(message.get("message") if message.get("message") is not None else "")


def format_llm_batch_prompt(messages: list[dict[str, Any]]) -> str:
    prompt = "\n\n".join(message_llm_display_text(message) for message in messages)
    instruction = _response_instruction(messages)
    return f"{prompt}\n\n{instruction}" if instruction else prompt
