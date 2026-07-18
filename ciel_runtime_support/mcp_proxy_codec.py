"""JSON-RPC response and MCP tool-result codec without transport ownership."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class McpProxyCodecPolicy:
    default_tool_result_max_chars: int
    item_text_chars: int
    positive_env_int: Callable[..., Any]
    router_log: Callable[..., Any]
    tool_leaf_name: Callable[..., Any]
    truncate_for_prompt: Callable[..., Any]


def _mcp_proxy_error_response(request_id: Any, message: str, code: int = -32000) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": str(message)},
    }


def _mcp_proxy_tool_call_name(payload: dict[str, Any]) -> str:
    if str(payload.get("method") or "") != "tools/call":
        return ""
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    return str(params.get("name") or "").strip()


def _mcp_proxy_tool_call_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    return arguments


def _mcp_proxy_tool_is_notification_wait(tool_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(tool_name or "").strip().lower()).strip("_")
    if not normalized:
        return False
    waits = normalized.startswith(("wait_", "watch_")) or normalized in {"wait", "watch"}
    if not waits:
        return False
    return any(
        term in normalized
        for term in (
            "notification",
            "notifications",
            "message",
            "messages",
            "event",
            "events",
            "response",
            "responses",
            "inbox",
            "mailbox",
            "channel",
            "channels",
        )
    )


def _mcp_proxy_wait_timeout_seconds(arguments: dict[str, Any]) -> float:
    def _float_env(name: str, default: float) -> float:
        try:
            return float(str(os.environ.get(name, default)).strip())
        except Exception:
            return default

    default_timeout = max(0.0, min(60.0, _float_env("CIEL_RUNTIME_MCP_WAIT_DEFAULT_SECONDS", 10.0)))
    max_timeout = max(1.0, min(120.0, _float_env("CIEL_RUNTIME_MCP_WAIT_MAX_SECONDS", 30.0)))
    value: Any = None
    scale = 1.0
    for key in ("timeout_ms", "wait_ms", "poll_ms"):
        if key in arguments:
            value = arguments.get(key)
            scale = 0.001
            break
    if value is None:
        for key in ("timeout_seconds", "wait_seconds"):
            if key in arguments:
                value = arguments.get(key)
                scale = 1.0
                break
    if value is None and "timeout" in arguments:
        value = arguments.get("timeout")
        scale = 0.001 if isinstance(value, (int, float)) and float(value) > 1000 else 1.0
    if value is None:
        return min(default_timeout, max_timeout)
    try:
        timeout = float(value) * scale
    except Exception:
        timeout = default_timeout
    return max(0.0, min(max_timeout, timeout))


def _mcp_proxy_notification_wait_response(
    request_id: Any,
    server_name: str,
    notifications: list[dict[str, Any]],
    *,
    timed_out: bool,
) -> dict[str, Any]:
    status = "timeout" if timed_out and not notifications else "ok"
    body = {
        "status": status,
        "source": server_name,
        "count": len(notifications),
        "notifications": notifications,
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str),
                }
            ],
            "isError": False,
        },
    }


def _mcp_proxy_tool_result_max_chars(*, policy: McpProxyCodecPolicy) -> int:
    positive_env_int = policy.positive_env_int
    MCP_PROXY_TOOL_RESULT_MAX_CHARS_DEFAULT = policy.default_tool_result_max_chars
    return max(
        4000,
        min(200000, positive_env_int("CIEL_RUNTIME_MCP_TOOL_RESULT_MAX_CHARS", MCP_PROXY_TOOL_RESULT_MAX_CHARS_DEFAULT)),
    )


def _mcp_proxy_tool_result_is_message_read(
    tool_name: str, *, policy: McpProxyCodecPolicy
) -> bool:
    _mcp_tool_leaf_name = policy.tool_leaf_name
    normalized = re.sub(r"[^a-z0-9_]+", "_", _mcp_tool_leaf_name(tool_name)).strip("_")
    if not normalized:
        return False
    if not normalized.startswith(("get_", "list_", "read_", "search_", "fetch_")):
        return False
    return any(term in normalized for term in ("message", "messages", "notification", "notifications", "inbox"))


def _mcp_proxy_compact_metadata_for_tool_result(
    value: Any, *, policy: McpProxyCodecPolicy
) -> Any:
    truncate_for_prompt = policy.truncate_for_prompt
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key in (
        "id",
        "message_id",
        "source_message_id",
        "room_id",
        "channel",
        "thread_id",
        "sender_id",
        "sender_name",
        "author_name",
        "kind",
        "type",
        "created_at",
        "updated_at",
        "stream_id",
        "seq",
        "sequence",
    ):
        if key in value and value.get(key) is not None:
            item = value.get(key)
            out[key] = truncate_for_prompt(item, 1000) if isinstance(item, str) else item
    if "snapshot" in value and isinstance(value.get("snapshot"), dict):
        out["snapshot_keys"] = sorted(str(key) for key in value["snapshot"].keys())[:40]
    remaining_keys = sorted(str(key) for key in value.keys() if key not in out and key != "snapshot")
    if remaining_keys:
        out["keys"] = remaining_keys[:80]
    return out or {"keys": sorted(str(key) for key in value.keys())[:80]}


def _mcp_proxy_compact_message_item_for_tool_result(
    item: Any, text_limit: int, *, policy: McpProxyCodecPolicy
) -> Any:
    truncate_for_prompt = policy.truncate_for_prompt
    if not isinstance(item, dict):
        if isinstance(item, str):
            return truncate_for_prompt(item, text_limit)
        return item
    out: dict[str, Any] = {}
    scalar_keys = (
        "id",
        "message_id",
        "room_id",
        "channel",
        "thread_id",
        "parent_id",
        "sender_type",
        "sender_id",
        "sender_name",
        "author_name",
        "kind",
        "type",
        "created_at",
        "updated_at",
        "timestamp",
        "stream_id",
        "seq",
        "sequence",
    )
    text_keys = ("content", "message", "text", "body", "summary")
    for key in scalar_keys:
        if key in item and item.get(key) is not None:
            value = item.get(key)
            out[key] = truncate_for_prompt(value, 1000) if isinstance(value, str) else value
    for key in text_keys:
        if key in item and item.get(key) is not None:
            value = item.get(key)
            if isinstance(value, str):
                out[key] = truncate_for_prompt(value, text_limit)
            else:
                out[key] = value
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
    if metadata:
        out["metadata"] = _mcp_proxy_compact_metadata_for_tool_result(metadata, policy=policy)
    for key, value in item.items():
        if key in out or key == "metadata":
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = truncate_for_prompt(value, 1000) if isinstance(value, str) else value
        if len(out) >= 30:
            break
    return out


def _mcp_proxy_compact_message_json_for_tool_result(
    parsed: Any, original_chars: int, max_chars: int, *, policy: McpProxyCodecPolicy
) -> str:
    MCP_PROXY_TOOL_RESULT_ITEM_TEXT_CHARS = policy.item_text_chars
    truncate_for_prompt = policy.truncate_for_prompt
    item_limit = max(1000, min(MCP_PROXY_TOOL_RESULT_ITEM_TEXT_CHARS, max_chars // 3))
    if isinstance(parsed, dict):
        out: dict[str, Any] = {}
        for key, value in parsed.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = truncate_for_prompt(value, 1000) if isinstance(value, str) else value
        for key in ("messages", "data", "items", "results", "notifications"):
            value = parsed.get(key)
            if not isinstance(value, list):
                continue
            kept_items = value[:20]
            out[key] = [_mcp_proxy_compact_message_item_for_tool_result(item, item_limit, policy=policy) for item in kept_items]
            if len(value) > len(kept_items):
                out[f"{key}_omitted"] = len(value) - len(kept_items)
            break
        else:
            out["data"] = _mcp_proxy_compact_message_item_for_tool_result(parsed, item_limit, policy=policy)
    elif isinstance(parsed, list):
        kept_items = parsed[:20]
        out = {"data": [_mcp_proxy_compact_message_item_for_tool_result(item, item_limit, policy=policy) for item in kept_items]}
        if len(parsed) > len(kept_items):
            out["data_omitted"] = len(parsed) - len(kept_items)
    else:
        return truncate_for_prompt(str(parsed), max_chars)
    out["ciel_runtime_compacted"] = True
    out["ciel_runtime_original_chars"] = original_chars
    out["ciel_runtime_note"] = (
        "Large MCP message-read result compacted before returning to Claude Code. "
        "Message text is preserved up to a bounded size; bulky metadata is summarized."
    )
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    return truncate_for_prompt(text, max_chars)


def _mcp_proxy_compact_tool_result_text(
    tool_name: str, text: str, max_chars: int, *, policy: McpProxyCodecPolicy
) -> str:
    truncate_for_prompt = policy.truncate_for_prompt
    original = str(text or "")
    if len(original) <= max_chars:
        return original
    if not _mcp_proxy_tool_result_is_message_read(tool_name, policy=policy):
        return original
    try:
        parsed = json.loads(original)
    except Exception:
        prefix = (
            f"[ciel-runtime compacted MCP tool result: tool={tool_name or '-'} "
            f"original_chars={len(original)} max_chars={max_chars}]\n"
        )
        return prefix + truncate_for_prompt(original, max(1000, max_chars - len(prefix)))
    return _mcp_proxy_compact_message_json_for_tool_result(parsed, len(original), max_chars, policy=policy)


def compact_tool_result_response(
    server_name: str, tool_name: str, payload: dict[str, Any], *, policy: McpProxyCodecPolicy
) -> dict[str, Any]:
    router_log = policy.router_log
    if not isinstance(payload, dict) or not _mcp_proxy_tool_result_is_message_read(tool_name, policy=policy):
        return payload
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if not isinstance(result, dict) or result.get("isError") or result.get("is_error"):
        return payload
    content = result.get("content")
    if not isinstance(content, list):
        return payload
    max_chars = _mcp_proxy_tool_result_max_chars(policy=policy)
    original_chars = 0
    changed = False
    new_content: list[Any] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            new_content.append(block)
            continue
        text = str(block.get("text") or "")
        original_chars += len(text)
        compacted = _mcp_proxy_compact_tool_result_text(tool_name, text, max_chars, policy=policy)
        if compacted != text:
            changed = True
            new_block = dict(block)
            new_block["text"] = compacted
            new_content.append(new_block)
        else:
            new_content.append(block)
    if not changed:
        return payload
    out = dict(payload)
    out_result = dict(result)
    out_result["content"] = new_content
    out["result"] = out_result
    compacted_chars = sum(len(str(block.get("text") or "")) for block in new_content if isinstance(block, dict) and block.get("type") == "text")
    router_log(
        "INFO",
        f"mcp_proxy_tool_result_compacted server={server_name} tool={tool_name or '-'} "
        f"original_chars={original_chars} compacted_chars={compacted_chars}",
    )
    return out

