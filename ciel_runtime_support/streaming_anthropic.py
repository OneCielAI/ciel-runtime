from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Iterable


@dataclass(frozen=True, slots=True)
class AnthropicStreamServices:
    ANTHROPIC_THINKING_BLOCK_TYPES: Any
    VisibleToolCallArtifactFilter: type[Any]
    _find_pseudo_xml_tool_start: Callable[..., Any]
    _is_mcp_notification_wait_tool: Callable[..., Any]
    _remember_channel_injected_tool_use: Callable[..., Any]
    _split_word_buffer: Callable[..., Any]
    _validate_and_fix_tool_input: Callable[..., Any]
    append_tool_call_log: Callable[..., Any]
    backfill_exit_plan_mode_allowed_prompts: Callable[..., Any]
    body_ultracode_runtime_enabled: Callable[..., Any]
    cap_mcp_notification_wait_tool_input: Callable[..., Any]
    empty_end_turn_notice_for_body: Callable[..., Any]
    has_tool: Callable[..., Any]
    infer_tool_name_from_args: Callable[..., Any]
    latest_user_intent_message_index: Callable[..., Any]
    latest_user_is_claude_code_suggestion_mode: Callable[..., Any]
    latest_user_tool_result_names: Callable[..., Any]
    mark_pending_channel_delivery_failed: Callable[..., Any]
    mark_pending_channel_delivery_success: Callable[..., Any]
    normalize_tool_arguments: Callable[..., Any]
    parse_pseudo_tool_calls: Callable[..., Any]
    plan_mode_tool_name_for_emit: Callable[..., Any]
    recent_synthetic_tasklist_count: Callable[..., Any]
    remember_suppressed_thinking_passback: Callable[..., Any]
    resolve_emitted_tool_name: Callable[..., Any]
    router_client_connection_closed: Callable[..., Any]
    router_log: Callable[..., Any]
    should_auto_continue_choice_question_with_tasklist: Callable[..., Any]
    should_auto_exit_plan_mode: Callable[..., Any]
    should_drop_duplicate_side_effect_tool_call: Callable[..., Any]
    should_drop_emitted_tool_call: Callable[..., Any]
    should_keep_work_alive_with_tasklist: Callable[..., Any]
    should_recover_empty_end_turn_with_tasklist: Callable[..., Any]
    should_repair_anthropic_passthrough_tool_input: Callable[..., Any]
    should_synthesize_tasklist_for_provider: Callable[..., Any]


def rebatch_anthropic_sse_text(
    handler: BaseHTTPRequestHandler,
    resp: Any,
    model: str = "ciel-runtime-upstream",
    word_chunking: bool = True,
    source_body: dict[str, Any] | None = None,
    preserve_thinking: bool = True,
    normalize_tool_use: bool = False,
    provider: str = "",
    *,
    services: AnthropicStreamServices,
) -> None:
    """
    Parse upstream Anthropic SSE and re-emit it with text_delta events buffered
    to word boundaries. Non-text events are forwarded in the same SSE framing.
    When the selected provider cannot preserve Anthropic's thinking passback
    contract, thinking blocks are suppressed and later content block indices are
    compacted.
    """

    ANTHROPIC_THINKING_BLOCK_TYPES = services.ANTHROPIC_THINKING_BLOCK_TYPES
    VisibleToolCallArtifactFilter = services.VisibleToolCallArtifactFilter
    _find_pseudo_xml_tool_start = services._find_pseudo_xml_tool_start
    _is_mcp_notification_wait_tool = services._is_mcp_notification_wait_tool
    _remember_channel_injected_tool_use = services._remember_channel_injected_tool_use
    _split_word_buffer = services._split_word_buffer
    _validate_and_fix_tool_input = services._validate_and_fix_tool_input
    append_tool_call_log = services.append_tool_call_log
    backfill_exit_plan_mode_allowed_prompts = services.backfill_exit_plan_mode_allowed_prompts
    body_ultracode_runtime_enabled = services.body_ultracode_runtime_enabled
    cap_mcp_notification_wait_tool_input = services.cap_mcp_notification_wait_tool_input
    empty_end_turn_notice_for_body = services.empty_end_turn_notice_for_body
    has_tool = services.has_tool
    infer_tool_name_from_args = services.infer_tool_name_from_args
    latest_user_intent_message_index = services.latest_user_intent_message_index
    latest_user_is_claude_code_suggestion_mode = services.latest_user_is_claude_code_suggestion_mode
    latest_user_tool_result_names = services.latest_user_tool_result_names
    mark_pending_channel_delivery_failed = services.mark_pending_channel_delivery_failed
    mark_pending_channel_delivery_success = services.mark_pending_channel_delivery_success
    normalize_tool_arguments = services.normalize_tool_arguments
    parse_pseudo_tool_calls = services.parse_pseudo_tool_calls
    plan_mode_tool_name_for_emit = services.plan_mode_tool_name_for_emit
    recent_synthetic_tasklist_count = services.recent_synthetic_tasklist_count
    remember_suppressed_thinking_passback = services.remember_suppressed_thinking_passback
    resolve_emitted_tool_name = services.resolve_emitted_tool_name
    router_client_connection_closed = services.router_client_connection_closed
    router_log = services.router_log
    should_auto_continue_choice_question_with_tasklist = services.should_auto_continue_choice_question_with_tasklist
    should_auto_exit_plan_mode = services.should_auto_exit_plan_mode
    should_drop_duplicate_side_effect_tool_call = services.should_drop_duplicate_side_effect_tool_call
    should_drop_emitted_tool_call = services.should_drop_emitted_tool_call
    should_keep_work_alive_with_tasklist = services.should_keep_work_alive_with_tasklist
    should_recover_empty_end_turn_with_tasklist = services.should_recover_empty_end_turn_with_tasklist
    should_repair_anthropic_passthrough_tool_input = services.should_repair_anthropic_passthrough_tool_input
    should_synthesize_tasklist_for_provider = services.should_synthesize_tasklist_for_provider
    text_buffers: dict[int, str] = {}
    pending_event_type: str | None = None
    pending_event_lines: list[str] = []
    saw_message_start = False
    saw_message_stop = False
    text_so_far = ""
    saw_tool_use = False
    emitted_tool_use = False
    next_content_index = 0
    open_content_blocks: set[int] = set()
    content_index_map: dict[int, int] = {}
    suppressed_content_indices: set[int] = set()
    suppressed_thinking_blocks: dict[int, dict[str, Any]] = {}
    suppressed_thinking_passback_blocks: list[dict[str, Any]] = []
    buffered_tool_uses: dict[int, dict[str, Any]] = {}
    held_pseudo_tool_text: dict[int, str] = {}
    pending_message_delta: tuple[str | None, str] | None = None
    pending_message_stop: tuple[str | None, str] | None = None
    last_suppressed_keepalive_at = 0.0
    stream_success = False
    allow_tasklist_synthesis = should_synthesize_tasklist_for_provider(provider)
    filter_visible_tool_call_artifacts = bool(
        provider == "anthropic"
        and isinstance(source_body, dict)
        and (has_tool(source_body, "Workflow") or body_ultracode_runtime_enabled(source_body))
    )
    visible_tool_call_artifact_filters: dict[int, VisibleToolCallArtifactFilter] = {}

    class ClientStreamDisconnected(Exception):
        pass

    def downstream_keepalive_interval() -> float:
        raw = os.environ.get("CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS")
        if raw is None:
            return 15.0
        try:
            return max(0.0, min(120.0, float(raw)))
        except Exception:
            return 15.0

    def emit_raw(event_type: str | None, data_str: str) -> None:
        try:
            if event_type:
                handler.wfile.write(f"event: {event_type}\ndata: {data_str}\n\n".encode())
            else:
                handler.wfile.write(f"data: {data_str}\n\n".encode())
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            raise ClientStreamDisconnected(f"{type(exc).__name__}: {exc}") from exc

    def emit_suppressed_keepalive(force: bool = False) -> None:
        nonlocal last_suppressed_keepalive_at
        now = time.time()
        if not force and now - last_suppressed_keepalive_at < 1.0:
            return
        try:
            handler.wfile.write(b": suppressed-thinking\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            raise ClientStreamDisconnected(f"{type(exc).__name__}: {exc}") from exc
        last_suppressed_keepalive_at = now

    def emit_downstream_keepalive() -> None:
        try:
            handler.wfile.write(b": ciel-runtime-keepalive\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            raise ClientStreamDisconnected(f"{type(exc).__name__}: {exc}") from exc

    def upstream_lines_with_downstream_keepalive() -> Iterable[Any]:
        interval = downstream_keepalive_interval()
        if interval <= 0:
            yield from resp
            return
        line_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        def reader() -> None:
            try:
                for raw_line in resp:
                    line_queue.put(("line", raw_line))
                line_queue.put(("eof", None))
            except Exception as exc:
                line_queue.put(("error", exc))

        threading.Thread(target=reader, daemon=True, name=f"ciel-anthropic-sse-{model}").start()
        while True:
            try:
                kind, value = line_queue.get(timeout=interval)
            except queue.Empty:
                if router_client_connection_closed(handler):
                    raise ClientStreamDisconnected("downstream client disconnected during upstream wait")
                emit_downstream_keepalive()
                continue
            if kind == "line":
                yield value
                continue
            if kind == "error":
                if router_client_connection_closed(handler):
                    raise ClientStreamDisconnected("downstream client disconnected during upstream read") from value
                raise value
            return

    def emit_text_delta_raw(index: int, text: str) -> None:
        if not text:
            return
        payload = {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
        emit_raw("content_block_delta", json.dumps(payload, ensure_ascii=False))

    def emit_text_delta(index: int, text: str) -> None:
        if not text:
            return
        if filter_visible_tool_call_artifacts:
            filter_state = visible_tool_call_artifact_filters.setdefault(index, VisibleToolCallArtifactFilter())
            text = filter_state.feed(text)
        emit_text_delta_raw(index, text)

    def finish_visible_tool_call_artifact_filter(index: int) -> None:
        if not filter_visible_tool_call_artifacts:
            return
        filter_state = visible_tool_call_artifact_filters.pop(index, None)
        if filter_state is None:
            return
        text = filter_state.finish()
        if filter_state.stripped:
            router_log(
                "WARN",
                f"stripped visible Anthropic workflow tool-call artifact provider={provider} model={model} index={index}",
            )
        emit_text_delta_raw(index, text)

    def emit_text_block(index: int, text: str) -> None:
        emit_raw(
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
                ensure_ascii=False,
            ),
        )
        emit_text_delta(index, text)
        finish_visible_tool_call_artifact_filter(index)
        emit_raw("content_block_stop", json.dumps({"type": "content_block_stop", "index": index}, ensure_ascii=False))

    def flush_buffer(index: int, force: bool = False) -> None:
        buf = text_buffers.get(index, "")
        if not buf:
            return
        to_flush, remainder = _split_word_buffer(buf, force=force)
        text_buffers[index] = remainder
        emit_text_delta(index, to_flush)

    def emit_tasklist_tool(index: int) -> None:
        nonlocal emitted_tool_use
        tool_id = f"toolu_anthropic_choice_{int(time.time() * 1000)}"
        emit_raw(
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "tool_use", "id": tool_id, "name": "TaskList", "input": {}},
                },
                ensure_ascii=False,
            ),
        )
        emit_raw(
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
                ensure_ascii=False,
            ),
        )
        emit_raw("content_block_stop", json.dumps({"type": "content_block_stop", "index": index}, ensure_ascii=False))
        emitted_tool_use = True

    def emit_exit_plan_mode_tool(index: int) -> None:
        nonlocal emitted_tool_use
        tool_id = f"toolu_anthropic_exit_plan_{int(time.time() * 1000)}"
        tool_input = {}
        if isinstance(source_body, dict):
            tool_input = backfill_exit_plan_mode_allowed_prompts(source_body, tool_input)
        emit_raw(
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "tool_use", "id": tool_id, "name": "ExitPlanMode", "input": {}},
                },
                ensure_ascii=False,
            ),
        )
        emit_raw(
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input, ensure_ascii=False)},
                },
                ensure_ascii=False,
            ),
        )
        emit_raw("content_block_stop", json.dumps({"type": "content_block_stop", "index": index}, ensure_ascii=False))
        emitted_tool_use = True

    def mapped_content_index(index: Any) -> int | None:
        if not isinstance(index, int):
            return None
        if index in suppressed_content_indices:
            return None
        return content_index_map.get(index, index)

    def append_suppressed_thinking_delta(index: Any, delta: dict[str, Any]) -> None:
        if not isinstance(index, int):
            return
        block = suppressed_thinking_blocks.get(index)
        if not isinstance(block, dict):
            return
        delta_type = delta.get("type")
        if delta_type == "thinking_delta":
            block["thinking"] = str(block.get("thinking") or "") + str(delta.get("thinking") or "")
        elif delta_type == "signature_delta":
            block["signature"] = str(delta.get("signature") or "")

    def finish_suppressed_thinking_block(index: Any) -> None:
        if not isinstance(index, int):
            return
        block = suppressed_thinking_blocks.pop(index, None)
        if isinstance(block, dict) and block.get("type") in ANTHROPIC_THINKING_BLOCK_TYPES:
            suppressed_thinking_passback_blocks.append(block)

    def flush_suppressed_thinking_passback() -> None:
        if preserve_thinking or not suppressed_thinking_passback_blocks:
            return
        if source_body is not None and latest_user_is_claude_code_suggestion_mode(source_body):
            router_log(
                "DEBUG",
                f"discarded suppressed Anthropic thinking passback blocks for suggestion-mode request "
                f"provider={provider} model={model} blocks={len(suppressed_thinking_passback_blocks)}",
            )
            suppressed_thinking_passback_blocks.clear()
            return
        remember_suppressed_thinking_passback(provider, model, suppressed_thinking_passback_blocks)
        suppressed_thinking_passback_blocks.clear()

    def patched_message_delta(stop_reason: str) -> str:
        event: dict[str, Any] = {}
        if pending_message_delta is not None:
            try:
                parsed = json.loads(pending_message_delta[1])
                if isinstance(parsed, dict):
                    event = dict(parsed)
            except Exception:
                event = {}
        if not event:
            event = {
                "type": "message_delta",
                "delta": {"stop_reason": None, "stop_sequence": None},
                "usage": {"output_tokens": max(1, len(text_so_far) // 4)},
            }
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        patched_delta = dict(delta)
        patched_delta["stop_reason"] = stop_reason
        patched_delta.setdefault("stop_sequence", None)
        event["delta"] = patched_delta
        event.setdefault("type", "message_delta")
        event.setdefault("usage", {"output_tokens": max(1, len(text_so_far) // 4)})
        return json.dumps(event, ensure_ascii=False)

    def emit_pending_message_end(default_stop_reason: str = "end_turn") -> None:
        stop_reason = default_stop_reason
        if pending_message_delta is not None:
            try:
                parsed = json.loads(pending_message_delta[1])
                if isinstance(parsed, dict):
                    delta = parsed.get("delta") if isinstance(parsed.get("delta"), dict) else {}
                    stop_reason = str(delta.get("stop_reason") or stop_reason)
            except Exception:
                pass
        emit_raw(
            pending_message_delta[0] if pending_message_delta is not None else "message_delta",
            patched_message_delta(stop_reason),
        )
        emit_raw(
            pending_message_stop[0] if pending_message_stop is not None else "message_stop",
            pending_message_stop[1] if pending_message_stop is not None else "{\"type\":\"message_stop\"}",
        )

    def recover_hidden_only_response_if_needed() -> None:
        nonlocal next_content_index, saw_tool_use, emitted_tool_use, text_so_far, pending_message_delta
        recovery_reason = ""
        latest_names: list[str] = []
        synthetic_count = 0
        has_tasklist_tool = False
        if source_body is not None:
            try:
                latest_names = latest_user_tool_result_names(source_body)
                intent_index = latest_user_intent_message_index(source_body)
                synthetic_count = recent_synthetic_tasklist_count(source_body, after_message_index=intent_index)
                has_tasklist_tool = has_tool(source_body, "TaskList")
                if emitted_tool_use:
                    recovery_reason = ""
                elif allow_tasklist_synthesis and should_recover_empty_end_turn_with_tasklist(source_body, text_so_far, []):
                    recovery_reason = "hidden-only" if suppressed_thinking_passback_blocks else "empty"
                elif allow_tasklist_synthesis and should_keep_work_alive_with_tasklist(source_body, text_so_far, []):
                    recovery_reason = "keepalive"
            except Exception as exc:
                router_log(
                    "WARN",
                    "anthropic_hidden_recovery_state_error "
                    f"provider={provider} model={model} error={type(exc).__name__}: {exc}",
                )
        if recovery_reason:
            router_log(
                "WARN",
                f"auto-synthesized TaskList from {recovery_reason} Anthropic-compatible stream "
                f"latest_tool_results={','.join(latest_names) or '-'} synthetic_tasklists={synthetic_count}",
            )
            emit_tasklist_tool(next_content_index)
            next_content_index += 1
            saw_tool_use = True
            pending_message_delta = (
                pending_message_delta[0] if pending_message_delta is not None else "message_delta",
                patched_message_delta("tool_use"),
            )
            return
        if text_so_far.strip() or emitted_tool_use:
            if suppressed_thinking_passback_blocks:
                router_log(
                    "DEBUG",
                    "anthropic_hidden_recovery_skipped "
                    f"provider={provider} model={model} reason=visible_or_tool "
                    f"text_len={len(text_so_far.strip())} emitted_tool_use={emitted_tool_use} "
                    f"latest_tool_results={','.join(latest_names) or '-'} "
                    f"synthetic_tasklists={synthetic_count} suppressed_blocks={len(suppressed_thinking_passback_blocks)}",
                )
            return
        if not suppressed_thinking_passback_blocks:
            return
        router_log(
            "WARN",
            "anthropic_hidden_recovery_not_applicable "
            f"provider={provider} model={model} has_tasklist={has_tasklist_tool} "
            f"latest_tool_results={','.join(latest_names) or '-'} synthetic_tasklists={synthetic_count} "
            f"suppressed_blocks={len(suppressed_thinking_passback_blocks)}",
        )
        notice = empty_end_turn_notice_for_body(source_body) if source_body is not None else ""
        router_log("WARN", f"anthropic_hidden_only_stream provider={provider} model={model}")
        emit_text_block(next_content_index, notice)
        next_content_index += 1
        if notice:
            text_so_far = notice
        pending_message_delta = (
            pending_message_delta[0] if pending_message_delta is not None else "message_delta",
            patched_message_delta("end_turn"),
        )

    def append_tool_partial(tool_state: dict[str, Any], partial: Any) -> None:
        if partial is None:
            return
        if isinstance(partial, str):
            tool_state["partial_json"] = str(tool_state.get("partial_json") or "") + partial
        else:
            tool_state["partial_json"] = str(tool_state.get("partial_json") or "") + json.dumps(partial, ensure_ascii=False)

    def emit_normalized_tool_use(index: int, tool_state: dict[str, Any]) -> None:
        nonlocal emitted_tool_use
        raw_name = str(tool_state.get("name") or "")
        raw_args = str(tool_state.get("partial_json") or "")
        parsed_args = normalize_tool_arguments(raw_name, raw_args)
        if not raw_name:
            raw_name = infer_tool_name_from_args(parsed_args)
        matched_name = resolve_emitted_tool_name(raw_name, source_body)
        if not matched_name:
            matched_name = infer_tool_name_from_args(parsed_args)
        fixed_input = _validate_and_fix_tool_input(matched_name, parsed_args, source_body)
        if isinstance(source_body, dict):
            mapped_name, mapped_input = plan_mode_tool_name_for_emit(source_body, matched_name, fixed_input)
            if mapped_name is None:
                router_log(
                    "WARN",
                    f"dropped upstream tool_use before emit raw_name={raw_name!r} matched_name={matched_name!r}",
                )
                return
            matched_name, fixed_input = mapped_name, mapped_input
        fixed_input = cap_mcp_notification_wait_tool_input(matched_name, fixed_input)
        if should_drop_emitted_tool_call(matched_name, fixed_input, raw_name, source_body):
            return
        if should_drop_duplicate_side_effect_tool_call(matched_name, fixed_input, raw_name):
            return
        tool_id = str(tool_state.get("id") or f"toolu_anthropic_{int(time.time() * 1000)}_{index}")
        _remember_channel_injected_tool_use(source_body, tool_id, matched_name, fixed_input)
        append_tool_call_log(
            "anthropic_stream_tool_call",
            {
                "model": model,
                "raw_name": raw_name,
                "matched_name": matched_name,
                "raw_arguments": raw_args,
                "emitted_input": fixed_input,
                "sse_index": index,
            },
        )
        emit_raw(
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "tool_use", "id": tool_id, "name": matched_name, "input": {}},
                },
                ensure_ascii=False,
            ),
        )
        emit_raw(
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(fixed_input, ensure_ascii=False)},
                },
                ensure_ascii=False,
            ),
        )
        emit_raw("content_block_stop", json.dumps({"type": "content_block_stop", "index": index}, ensure_ascii=False))
        emitted_tool_use = True

    def emit_pseudo_tool_uses(pseudo_tool_calls: list[dict[str, Any]]) -> bool:
        nonlocal next_content_index, saw_tool_use
        if not pseudo_tool_calls:
            return False
        for call in pseudo_tool_calls:
            fn = call.get("function") if isinstance(call, dict) else {}
            if not isinstance(fn, dict) or not fn.get("name"):
                continue
            tool_index = next_content_index
            next_content_index += 1
            tool_state = {
                "id": str(call.get("id") or ""),
                "name": str(fn.get("name") or ""),
                "partial_json": json.dumps(fn.get("arguments") or {}, ensure_ascii=False),
            }
            emit_normalized_tool_use(tool_index, tool_state)
            saw_tool_use = True
        return True

    def process_event(event_type: str | None, data_str: str) -> None:
        nonlocal saw_message_start, saw_message_stop, text_so_far, saw_tool_use, emitted_tool_use, next_content_index, pending_message_delta, pending_message_stop
        try:
            event = json.loads(data_str)
        except Exception:
            emit_raw(event_type, data_str)
            return
        if not isinstance(event, dict):
            emit_raw(event_type, data_str)
            return
        evt_type = event.get("type") or event_type
        if evt_type == "message_start":
            saw_message_start = True
        elif evt_type == "message_stop":
            saw_message_stop = True
            pending_message_stop = (event_type, data_str)
            return
        elif evt_type == "content_block_start":
            index = event.get("index")
            content_block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            mapped_index: int | None = None
            if isinstance(index, int):
                if not preserve_thinking and content_block.get("type") in ANTHROPIC_THINKING_BLOCK_TYPES:
                    suppressed_content_indices.add(index)
                    suppressed_thinking_blocks[index] = dict(content_block)
                    router_log("WARN", f"suppressed Anthropic thinking response block for non-Anthropic provider model={model}")
                    emit_suppressed_keepalive(force=True)
                    return
                if index in content_index_map:
                    mapped_index = content_index_map[index]
                else:
                    mapped_index = next_content_index
                    content_index_map[index] = mapped_index
                    next_content_index += 1
                open_content_blocks.add(mapped_index)
                patched = dict(event)
                patched["index"] = mapped_index
                event = patched
                data_str = json.dumps(event, ensure_ascii=False)
            if content_block.get("type") == "tool_use":
                saw_tool_use = True
                tool_name = str(content_block.get("name") or "")
                should_buffer_tool_use = bool(
                    mapped_index is not None
                    and (
                        normalize_tool_use
                        or _is_mcp_notification_wait_tool(tool_name)
                        or should_repair_anthropic_passthrough_tool_input(provider, tool_name, source_body)
                    )
                )
                if should_buffer_tool_use and mapped_index is not None:
                    buffered_tool_uses[mapped_index] = {
                        "id": str(content_block.get("id") or ""),
                        "name": tool_name,
                        "partial_json": "",
                    }
                    initial_input = content_block.get("input")
                    if isinstance(initial_input, dict) and initial_input:
                        append_tool_partial(buffered_tool_uses[mapped_index], initial_input)
                    return
                emitted_tool_use = True
        elif evt_type == "content_block_stop":
            index = event.get("index")
            mapped_index = mapped_content_index(index)
            if isinstance(index, int) and mapped_index is None:
                finish_suppressed_thinking_block(index)
                return
            if mapped_index is not None:
                open_content_blocks.discard(mapped_index)
                if mapped_index in buffered_tool_uses:
                    emit_normalized_tool_use(mapped_index, buffered_tool_uses.pop(mapped_index))
                    return
                patched = dict(event)
                patched["index"] = mapped_index
                data_str = json.dumps(patched, ensure_ascii=False)
                if isinstance(mapped_index, int) and word_chunking:
                    flush_buffer(mapped_index, force=True)
                if isinstance(mapped_index, int) and mapped_index in held_pseudo_tool_text:
                    held_text = held_pseudo_tool_text.pop(mapped_index)
                    visible_text, pseudo_tool_calls = parse_pseudo_tool_calls(held_text, source_body)
                    if pseudo_tool_calls:
                        if visible_text.strip():
                            emit_text_delta(mapped_index, visible_text)
                        finish_visible_tool_call_artifact_filter(mapped_index)
                        emit_raw(event_type, data_str)
                        emit_pseudo_tool_uses(pseudo_tool_calls)
                        return
                    else:
                        emit_text_delta(mapped_index, held_text)
                finish_visible_tool_call_artifact_filter(mapped_index)
                emit_raw(event_type, data_str)
                return
        elif evt_type == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            stop_reason = str(delta.get("stop_reason") or "")
            tool_calls = [{"type": "tool_use"}] if emitted_tool_use else []
            if stop_reason == "tool_use" and not emitted_tool_use:
                for index in list(text_buffers.keys()):
                    flush_buffer(index, force=True)
                if source_body is not None and should_auto_exit_plan_mode(source_body, text_so_far, []):
                    router_log("WARN", "auto-synthesized ExitPlanMode from malformed Anthropic-compatible tool_use stream")
                    emit_exit_plan_mode_tool(next_content_index)
                    next_content_index += 1
                    saw_tool_use = True
                    pending_message_delta = (
                        event_type,
                        patched_message_delta("tool_use"),
                    )
                    return
                if (
                    allow_tasklist_synthesis
                    and source_body is not None
                    and should_keep_work_alive_with_tasklist(source_body, text_so_far, [])
                ):
                    router_log("WARN", "auto-synthesized TaskList after dropped Anthropic-compatible tool_use")
                    emit_tasklist_tool(next_content_index)
                    next_content_index += 1
                    saw_tool_use = True
                    pending_message_delta = (
                        event_type,
                        patched_message_delta("tool_use"),
                    )
                    return
                router_log(
                    "WARN",
                    f"downgraded malformed Anthropic-compatible tool_use stop without emitted tool "
                    f"provider={provider} model={model} text_len={len(text_so_far.strip())}",
                )
                pending_message_delta = (
                    event_type,
                    patched_message_delta("end_turn"),
                )
                return
            if emitted_tool_use and stop_reason == "end_turn":
                patched = dict(event)
                patched_delta = dict(delta)
                patched_delta["stop_reason"] = "tool_use"
                patched["delta"] = patched_delta
                pending_message_delta = (event_type, json.dumps(patched, ensure_ascii=False))
                return
            if (
                allow_tasklist_synthesis
                and
                stop_reason == "end_turn"
                and source_body is not None
                and should_auto_continue_choice_question_with_tasklist(source_body, text_so_far, tool_calls)
            ):
                for index in list(text_buffers.keys()):
                    flush_buffer(index, force=True)
                router_log("WARN", "auto-synthesized TaskList after clarification question Anthropic-compatible stream")
                emit_tasklist_tool(next_content_index)
                next_content_index += 1
                saw_tool_use = True
                patched = dict(event)
                patched_delta = dict(delta)
                patched_delta["stop_reason"] = "tool_use"
                patched["delta"] = patched_delta
                pending_message_delta = (event_type, json.dumps(patched, ensure_ascii=False))
                return
            should_recover = (
                allow_tasklist_synthesis
                and source_body is not None
                and should_recover_empty_end_turn_with_tasklist(source_body, text_so_far, tool_calls)
            )
            should_keep_alive = (
                allow_tasklist_synthesis
                and
                source_body is not None
                and not should_recover
                and should_keep_work_alive_with_tasklist(source_body, text_so_far, tool_calls)
            )
            if should_recover or should_keep_alive:
                for index in list(text_buffers.keys()):
                    flush_buffer(index, force=True)
                reason = "empty" if should_recover else "keepalive"
                router_log(
                    "WARN",
                    f"auto-synthesized TaskList from {reason} Anthropic-compatible message_delta "
                    f"stop_reason={stop_reason or '-'}",
                )
                emit_tasklist_tool(next_content_index)
                next_content_index += 1
                saw_tool_use = True
                patched = dict(event)
                patched_delta = dict(delta)
                patched_delta["stop_reason"] = "tool_use"
                patched["delta"] = patched_delta
                pending_message_delta = (event_type, json.dumps(patched, ensure_ascii=False))
                return
            pending_message_delta = (event_type, data_str)
            return
        if evt_type == "content_block_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            index = event.get("index")
            mapped_index = mapped_content_index(index)
            if isinstance(index, int) and mapped_index is None:
                append_suppressed_thinking_delta(index, delta)
                emit_suppressed_keepalive()
                return
            if not preserve_thinking and delta.get("type") in {"thinking_delta", "signature_delta"}:
                emit_suppressed_keepalive()
                return
            if isinstance(mapped_index, int) and mapped_index in buffered_tool_uses:
                if delta.get("type") == "input_json_delta":
                    append_tool_partial(buffered_tool_uses[mapped_index], delta.get("partial_json"))
                return
            if mapped_index is not None:
                patched = dict(event)
                patched["index"] = mapped_index
                event = patched
                data_str = json.dumps(event, ensure_ascii=False)
            if isinstance(mapped_index, int) and delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                if not text:
                    return
                text_so_far += text
                if provider != "anthropic" and mapped_index in held_pseudo_tool_text:
                    held_pseudo_tool_text[mapped_index] += text
                    return
                pseudo_start = _find_pseudo_xml_tool_start(text, source_body) if provider != "anthropic" else -1
                if pseudo_start >= 0:
                    prefix = text[:pseudo_start]
                    held_pseudo_tool_text[mapped_index] = text[pseudo_start:]
                    if not prefix:
                        return
                    if not word_chunking:
                        emit_text_delta(mapped_index, prefix)
                        return
                    text_buffers[mapped_index] = text_buffers.get(mapped_index, "") + prefix
                    flush_buffer(mapped_index, force=False)
                    return
                if not word_chunking:
                    emit_text_delta(mapped_index, text)
                    return
                text_buffers[mapped_index] = text_buffers.get(mapped_index, "") + text
                flush_buffer(mapped_index, force=False)
                return
            emit_raw(event_type, data_str)
            return
        if evt_type == "content_block_stop":
            index = event.get("index")
            mapped_index = mapped_content_index(index)
            if isinstance(index, int) and mapped_index is None:
                finish_suppressed_thinking_block(index)
                return
            if mapped_index is not None:
                if mapped_index in buffered_tool_uses:
                    emit_normalized_tool_use(mapped_index, buffered_tool_uses.pop(mapped_index))
                    return
                patched = dict(event)
                patched["index"] = mapped_index
                event = patched
                data_str = json.dumps(event, ensure_ascii=False)
            if isinstance(mapped_index, int) and word_chunking:
                flush_buffer(mapped_index, force=True)
            if isinstance(mapped_index, int):
                finish_visible_tool_call_artifact_filter(mapped_index)
            emit_raw(event_type, data_str)
            return
        if evt_type == "message_stop":
            flush_suppressed_thinking_passback()
        emit_raw(event_type, data_str)

    try:
        for raw in upstream_lines_with_downstream_keepalive():
            line = raw.decode("utf-8", errors="ignore")
            stripped = line.rstrip("\r\n")
            if stripped == "":
                if pending_event_lines:
                    data_str = "\n".join(pending_event_lines)
                    process_event(pending_event_type, data_str)
                pending_event_type = None
                pending_event_lines = []
                continue
            if stripped.startswith("event:"):
                pending_event_type = stripped[len("event:"):].strip() or None
                continue
            if stripped.startswith("data:"):
                pending_event_lines.append(stripped[len("data:"):].lstrip())
                continue
        if pending_event_lines:
            data_str = "\n".join(pending_event_lines)
            process_event(pending_event_type, data_str)
        for index in list(text_buffers.keys()):
            flush_buffer(index, force=True)
        for index in list(suppressed_thinking_blocks.keys()):
            finish_suppressed_thinking_block(index)
        recover_hidden_only_response_if_needed()
        flush_suppressed_thinking_passback()
        if pending_message_delta is not None or pending_message_stop is not None:
            emit_pending_message_end()
        stream_success = bool(saw_message_stop)
    except ClientStreamDisconnected as exc:
        mark_pending_channel_delivery_failed(handler, "anthropic_stream_client_disconnected")
        router_log(
            "WARN",
            f"anthropic_sse_client_disconnected model={model} "
            f"text_len={len(text_so_far)} emitted_tool_use={emitted_tool_use} "
            f"suppressed_blocks={len(suppressed_thinking_passback_blocks) + len(suppressed_thinking_blocks)} "
            f"error={exc}",
        )
    except Exception as exc:
        router_log("ERROR", f"anthropic_sse_forward_error model={model} error={type(exc).__name__}: {exc}")
        try:
            if pending_event_lines:
                data_str = "\n".join(pending_event_lines)
                process_event(pending_event_type, data_str)
                pending_event_lines = []
                pending_event_type = None
            for index in list(text_buffers.keys()):
                flush_buffer(index, force=True)
            for index in list(suppressed_thinking_blocks.keys()):
                finish_suppressed_thinking_block(index)
            recover_hidden_only_response_if_needed()
            flush_suppressed_thinking_passback()
            if pending_message_delta is not None or pending_message_stop is not None:
                emit_pending_message_end()
            for index in sorted(open_content_blocks):
                emit_raw("content_block_stop", json.dumps({"type": "content_block_stop", "index": index}, ensure_ascii=False))
            open_content_blocks.clear()
            if not saw_message_stop:
                if not saw_message_start:
                    payload = {
                        "type": "message_start",
                        "message": {
                            "id": f"msg_ciel_runtime_forward_{int(time.time() * 1000)}",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    }
                    emit_raw("message_start", json.dumps(payload, ensure_ascii=False))
                    emit_raw(
                        "content_block_start",
                        json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}, ensure_ascii=False),
                    )
                    emit_text_delta(0, f"Upstream stream error: {type(exc).__name__}: {exc}")
                    emit_raw("content_block_stop", json.dumps({"type": "content_block_stop", "index": 0}, ensure_ascii=False))
                emit_raw(
                    "message_delta",
                    json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}, ensure_ascii=False),
                )
                emit_raw("message_stop", "{\"type\":\"message_stop\"}")
        except Exception:
            pass
    finally:
        if stream_success:
            mark_pending_channel_delivery_success(handler, "anthropic_stream_message_stop")
        else:
            reason = str(getattr(handler, "_ciel_runtime_channel_delivery_reason", "anthropic_stream_incomplete") or "anthropic_stream_incomplete")
            mark_pending_channel_delivery_failed(handler, reason)
        try:
            resp.close()
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class OllamaStreamServices:
    UpstreamClientDisconnected: type[BaseException]
    VisibleThinkingMarkupFilter: type[Any]
    _remember_channel_injected_tool_use: Callable[..., Any]
    _split_word_buffer: Callable[..., Any]
    _validate_and_fix_tool_input: Callable[..., Any]
    append_tool_call_log: Callable[..., Any]
    cap_mcp_notification_wait_tool_input: Callable[..., Any]
    dump_response_for_trace: Callable[..., Any]
    empty_end_turn_notice_for_body: Callable[..., Any]
    estimate_tokens: Callable[..., Any]
    finish_outgoing_sse_trace: Callable[..., Any]
    iter_upstream_lines_until_client_disconnect: Callable[..., Any]
    make_outgoing_sse_trace: Callable[..., Any]
    mark_pending_channel_delivery_failed: Callable[..., Any]
    mark_pending_channel_delivery_success: Callable[..., Any]
    normalize_tool_arguments: Callable[..., Any]
    plan_mode_tool_name_for_emit: Callable[..., Any]
    record_outgoing_sse_event: Callable[..., Any]
    resolve_emitted_tool_name: Callable[..., Any]
    router_log: Callable[..., Any]
    should_auto_continue_choice_question_with_tasklist: Callable[..., Any]
    should_auto_enter_plan_mode: Callable[..., Any]
    should_drop_duplicate_side_effect_tool_call: Callable[..., Any]
    should_drop_emitted_tool_call: Callable[..., Any]
    should_keep_work_alive_with_tasklist: Callable[..., Any]
    should_recover_empty_end_turn_with_tasklist: Callable[..., Any]
    write_router_activity: Callable[..., Any]


def ollama_stream_to_anthropic_sse(
    handler: BaseHTTPRequestHandler,
    resp: Any,
    model: str,
    word_chunking: bool = False,
    provider: str = "ollama",
    source_body: dict[str, Any] | None = None,
    idle_timeout: float = 30.0,
    *,
    services: OllamaStreamServices,
) -> None:
    """Stream Ollama NDJSON /api/chat response as Anthropic SSE /v1/messages format."""

    UpstreamClientDisconnected = services.UpstreamClientDisconnected
    VisibleThinkingMarkupFilter = services.VisibleThinkingMarkupFilter
    _remember_channel_injected_tool_use = services._remember_channel_injected_tool_use
    _split_word_buffer = services._split_word_buffer
    _validate_and_fix_tool_input = services._validate_and_fix_tool_input
    append_tool_call_log = services.append_tool_call_log
    cap_mcp_notification_wait_tool_input = services.cap_mcp_notification_wait_tool_input
    dump_response_for_trace = services.dump_response_for_trace
    empty_end_turn_notice_for_body = services.empty_end_turn_notice_for_body
    estimate_tokens = services.estimate_tokens
    finish_outgoing_sse_trace = services.finish_outgoing_sse_trace
    iter_upstream_lines_until_client_disconnect = services.iter_upstream_lines_until_client_disconnect
    make_outgoing_sse_trace = services.make_outgoing_sse_trace
    mark_pending_channel_delivery_failed = services.mark_pending_channel_delivery_failed
    mark_pending_channel_delivery_success = services.mark_pending_channel_delivery_success
    normalize_tool_arguments = services.normalize_tool_arguments
    plan_mode_tool_name_for_emit = services.plan_mode_tool_name_for_emit
    record_outgoing_sse_event = services.record_outgoing_sse_event
    resolve_emitted_tool_name = services.resolve_emitted_tool_name
    router_log = services.router_log
    should_auto_continue_choice_question_with_tasklist = services.should_auto_continue_choice_question_with_tasklist
    should_auto_enter_plan_mode = services.should_auto_enter_plan_mode
    should_drop_duplicate_side_effect_tool_call = services.should_drop_duplicate_side_effect_tool_call
    should_drop_emitted_tool_call = services.should_drop_emitted_tool_call
    should_keep_work_alive_with_tasklist = services.should_keep_work_alive_with_tasklist
    should_recover_empty_end_turn_with_tasklist = services.should_recover_empty_end_turn_with_tasklist
    write_router_activity = services.write_router_activity
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache")
    handler.send_header("connection", "close")
    handler.end_headers()
    msg_id = f"msg_ollama_{int(time.time() * 1000)}"
    started = False
    text_started = False
    text_suppressed_for_plan = False
    next_content_index = 0
    text_index: int | None = None
    text_so_far = ""
    text_buffer = ""
    tool_calls: list[dict[str, Any]] = []
    tool_indices: list[int] = []
    stopped_tool_indices: set[int] = set()
    input_tokens = estimate_tokens(source_body) if isinstance(source_body, dict) else 0
    output_tokens = 0
    chunk: dict[str, Any] = {}
    chunks_seen = 0
    text_stopped = False
    last_activity_update = 0.0
    thinking_markup_filter = VisibleThinkingMarkupFilter()
    thinking_markup_suppressed = False
    sse_trace = make_outgoing_sse_trace(provider, model, "ollama_stream", source_body)
    sse_trace_outcome = "started"
    sse_trace_error: str | None = None

    def emit(event_name: str, payload: dict[str, Any]) -> None:
        try:
            record_outgoing_sse_event(sse_trace, event_name, payload)
            handler.wfile.write(f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError) as exc:
            raise UpstreamClientDisconnected(f"downstream write failed: {type(exc).__name__}: {exc}") from exc

    def ensure_message_started() -> None:
        nonlocal started
        if started:
            return
        started = True
        event = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        }
        emit("message_start", event)

    def emit_text_block(index: int, text: str) -> None:
        emit("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
        if text:
            emit("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}})
        emit("content_block_stop", {"type": "content_block_stop", "index": index})

    def update_stream_activity(force: bool = False) -> None:
        nonlocal last_activity_update
        now = time.time()
        if not force and now - last_activity_update < 0.5:
            return
        last_activity_update = now
        estimated_output = output_tokens or max(0, len(text_so_far) // 4)
        write_router_activity(
            "request",
            provider,
            model,
            tokens=input_tokens,
            output_tokens=estimated_output,
            chunks=chunks_seen,
            stream=True,
        )

    def handle_text_chunk(text_chunk: str) -> None:
        nonlocal next_content_index, text_buffer, text_index, text_so_far, text_started, text_suppressed_for_plan
        if not text_chunk:
            return
        if source_body is not None and not text_started and not tool_calls and should_auto_enter_plan_mode(source_body, text_so_far + text_chunk, []):
            text_so_far += text_chunk
            text_suppressed_for_plan = True
            return
        if text_suppressed_for_plan and not text_started and text_so_far:
            pending_text = text_so_far + text_chunk
            text_so_far = pending_text
            text_suppressed_for_plan = False
            text_started = True
            text_index = next_content_index
            next_content_index += 1
            event = {
                "type": "content_block_start",
                "index": text_index,
                "content_block": {"type": "text", "text": ""},
            }
            emit("content_block_start", event)
            if word_chunking:
                text_buffer += pending_text
                to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
                if to_flush:
                    event = {
                        "type": "content_block_delta",
                        "index": text_index,
                        "delta": {"type": "text_delta", "text": to_flush},
                    }
                    emit("content_block_delta", event)
            else:
                event = {
                    "type": "content_block_delta",
                    "index": text_index,
                    "delta": {"type": "text_delta", "text": pending_text},
                }
                emit("content_block_delta", event)
            update_stream_activity()
            return
        if not text_started:
            text_started = True
            text_index = next_content_index
            next_content_index += 1
            event = {
                "type": "content_block_start",
                "index": text_index,
                "content_block": {"type": "text", "text": ""},
            }
            emit("content_block_start", event)
        text_so_far += text_chunk
        if word_chunking:
            text_buffer += text_chunk
            to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
            if to_flush:
                event = {
                    "type": "content_block_delta",
                    "index": text_index,
                    "delta": {"type": "text_delta", "text": to_flush},
                }
                emit("content_block_delta", event)
        else:
            event = {
                "type": "content_block_delta",
                "index": text_index,
                "delta": {"type": "text_delta", "text": text_chunk},
            }
            emit("content_block_delta", event)
        update_stream_activity()

    try:
        for line in iter_upstream_lines_until_client_disconnect(handler, resp, idle_timeout):
            chunks_seen += 1
            line = line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except Exception:
                continue
            if not isinstance(chunk, dict):
                continue
            message = chunk.get("message") if isinstance(chunk.get("message"), dict) else {}
            input_tokens = max(input_tokens, int(chunk.get("prompt_eval_count") or 0))
            output_tokens = max(output_tokens, int(chunk.get("eval_count") or 0))
            if not started:
                ensure_message_started()
            # Handle text content
            raw_text_chunk = str(message.get("content") or "")
            text_chunk = thinking_markup_filter.feed(raw_text_chunk)
            if text_chunk != raw_text_chunk:
                thinking_markup_suppressed = True
            if text_chunk:
                handle_text_chunk(text_chunk)
            # Handle tool calls
            for call in message.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                if not isinstance(fn, dict) or not fn.get("name"):
                    continue
                raw_name = str(fn["name"])
                matched_name = resolve_emitted_tool_name(raw_name, source_body)
                raw_args = fn.get("arguments")
                normalized_args = normalize_tool_arguments(matched_name, raw_args)
                fixed_input = _validate_and_fix_tool_input(matched_name, normalized_args)
                if source_body is not None:
                    matched_name, fixed_input = plan_mode_tool_name_for_emit(source_body, matched_name, fixed_input)
                    if matched_name is None:
                        continue
                fixed_input = cap_mcp_notification_wait_tool_input(matched_name, fixed_input)
                if should_drop_emitted_tool_call(matched_name, fixed_input, raw_name, source_body):
                    continue
                if should_drop_duplicate_side_effect_tool_call(matched_name, fixed_input, raw_name):
                    continue
                tool_calls.append({"function": {"name": matched_name, "arguments": fixed_input}})
                tool_id = f"toolu_ollama_{int(time.time() * 1000)}_{len(tool_calls) - 1}"
                tool_index = next_content_index
                next_content_index += 1
                tool_indices.append(tool_index)
                _remember_channel_injected_tool_use(source_body, tool_id, matched_name, fixed_input)
                append_tool_call_log(
                    "ollama_stream_tool_call",
                    {
                        "model": model,
                        "raw_name": raw_name,
                        "matched_name": matched_name,
                        "raw_arguments": raw_args,
                        "normalized_arguments": normalized_args,
                        "emitted_input": fixed_input,
                        "sse_index": tool_index,
                    },
                )
                tool_event = {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": matched_name,
                        "input": {},
                    },
                }
                emit("content_block_start", tool_event)
                delta_event = {
                    "type": "content_block_delta",
                    "index": tool_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(fixed_input, ensure_ascii=False),
                    },
                }
                emit("content_block_delta", delta_event)
                update_stream_activity()
            update_stream_activity()
        trailing_text = thinking_markup_filter.finish()
        if trailing_text:
            handle_text_chunk(trailing_text)
        if thinking_markup_suppressed:
            router_log("WARN", f"suppressed visible Ollama thinking markup from stream model={model}")
        update_stream_activity(force=True)
        # Flush any remaining buffered text when word-chunking is active
        if source_body is not None and should_auto_enter_plan_mode(source_body, text_so_far, tool_calls):
            ensure_message_started()
            router_log("WARN", "auto-synthesized EnterPlanMode from short/empty upstream stream")
            tool_calls.append({"function": {"name": "EnterPlanMode", "arguments": {}}})
            tool_id = f"toolu_ollama_plan_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            tool_event = {
                "type": "content_block_start",
                "index": tool_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "EnterPlanMode",
                    "input": {},
                },
            }
            emit("content_block_start", tool_event)
            delta_event = {
                "type": "content_block_delta",
                "index": tool_index,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
            emit("content_block_delta", delta_event)
        elif source_body is not None and should_recover_empty_end_turn_with_tasklist(source_body, text_so_far, tool_calls):
            ensure_message_started()
            router_log("WARN", "auto-synthesized TaskList from empty upstream end_turn stream")
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            tool_id = f"toolu_ollama_empty_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            tool_event = {
                "type": "content_block_start",
                "index": tool_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "TaskList",
                    "input": {},
                },
            }
            emit("content_block_start", tool_event)
            delta_event = {
                "type": "content_block_delta",
                "index": tool_index,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
            emit("content_block_delta", delta_event)
        elif text_suppressed_for_plan and not text_started and text_so_far:
            text_started = True
            text_index = next_content_index
            next_content_index += 1
            event = {
                "type": "content_block_start",
                "index": text_index,
                "content_block": {"type": "text", "text": ""},
            }
            emit("content_block_start", event)
            event = {
                "type": "content_block_delta",
                "index": text_index,
                "delta": {"type": "text_delta", "text": text_so_far},
            }
            emit("content_block_delta", event)
        if word_chunking and text_started and text_buffer:
            to_flush, text_buffer = _split_word_buffer(text_buffer, force=True)
            if to_flush:
                event = {
                    "type": "content_block_delta",
                    "index": text_index,
                    "delta": {"type": "text_delta", "text": to_flush},
                }
                emit("content_block_delta", event)
        if source_body is not None and should_keep_work_alive_with_tasklist(source_body, text_so_far, tool_calls):
            ensure_message_started()
            router_log("WARN", "auto-synthesized TaskList to keep work moving after tool result stream")
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            tool_id = f"toolu_ollama_keepalive_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            tool_event = {
                "type": "content_block_start",
                "index": tool_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "TaskList",
                    "input": {},
                },
            }
            emit("content_block_start", tool_event)
            delta_event = {
                "type": "content_block_delta",
                "index": tool_index,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
            emit("content_block_delta", delta_event)
        if source_body is not None and should_auto_continue_choice_question_with_tasklist(source_body, text_so_far, tool_calls):
            ensure_message_started()
            router_log("WARN", "auto-synthesized TaskList after clarification question stream")
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            tool_id = f"toolu_ollama_choice_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            tool_event = {
                "type": "content_block_start",
                "index": tool_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "TaskList",
                    "input": {},
                },
            }
            emit("content_block_start", tool_event)
            delta_event = {
                "type": "content_block_delta",
                "index": tool_index,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
            emit("content_block_delta", delta_event)
        # Send content_block_stop for text if any
        if text_started:
            event = {"type": "content_block_stop", "index": text_index}
            emit("content_block_stop", event)
            text_stopped = True
        # Send content_block_stop for each tool call
        for tool_index in tool_indices:
            event = {"type": "content_block_stop", "index": tool_index}
            emit("content_block_stop", event)
            stopped_tool_indices.add(tool_index)
        if not started:
            ensure_message_started()
        if not text_started and not tool_indices:
            router_log("WARN", f"ollama_empty_stream provider={provider} model={model} chunks={chunks_seen}")
            write_router_activity("error", provider, model, error="empty_stream", stream=True)
            empty_index = next_content_index
            next_content_index += 1
            notice = empty_end_turn_notice_for_body(source_body) if source_body is not None else ""
            if notice:
                text_so_far = notice
            emit_text_block(empty_index, notice)
        # Determine stop reason
        stop_reason = "tool_use" if tool_calls else "end_turn"
        if chunk.get("done_reason") == "length":
            stop_reason = "max_tokens"
        # Send message_delta with final stop_reason
        event = {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        }
        emit("message_delta", event)
        # Send message_stop
        emit("message_stop", {"type": "message_stop"})
        sse_trace_outcome = "success"
        if text_started or tool_indices:
            write_router_activity(
                "success",
                provider,
                model,
                tokens=input_tokens,
                output_tokens=output_tokens or max(1, len(text_so_far) // 4),
                chunks=chunks_seen,
                stream=True,
            )
        mark_pending_channel_delivery_success(handler, "ollama_stream_message_stop")
    except UpstreamClientDisconnected as exc:
        sse_trace_outcome = "client_disconnected"
        sse_trace_error = f"{type(exc).__name__}: {exc}"
        mark_pending_channel_delivery_failed(handler, "ollama_stream_client_disconnected")
        router_log(
            "WARN",
            f"ollama_stream_client_disconnected provider={provider} model={model} "
            f"chunks={chunks_seen} text_len={len(text_so_far)} error={exc}",
        )
        write_router_activity(
            "cancel",
            provider,
            model,
            error=type(exc).__name__,
            tokens=input_tokens,
            output_tokens=output_tokens or max(0, len(text_so_far) // 4),
            chunks=chunks_seen,
            stream=True,
        )
    except Exception as exc:
        sse_trace_outcome = "error"
        sse_trace_error = f"{type(exc).__name__}: {exc}"
        mark_pending_channel_delivery_failed(handler, f"ollama_stream_error:{type(exc).__name__}")
        router_log("ERROR", f"ollama_stream_error provider={provider} model={model} error={type(exc).__name__}: {exc}")
        write_router_activity("error", provider, model, error=type(exc).__name__, stream=True)
        try:
            ensure_message_started()
            if text_started and not text_stopped:
                emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
            if not text_started and not tool_indices:
                error_index = next_content_index
                next_content_index += 1
                emit_text_block(error_index, f"Upstream stream error: {type(exc).__name__}: {exc}")
            for tool_index in tool_indices:
                if tool_index not in stopped_tool_indices:
                    emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
                    stopped_tool_indices.add(tool_index)
            emit(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens or 1},
                },
            )
            emit("message_stop", {"type": "message_stop"})
        except Exception:
            pass
    finally:
        try:
            resp.close()
        except Exception:
            pass
        try:
            final_stop_reason = locals().get("stop_reason")
            finish_outgoing_sse_trace(
                sse_trace,
                outcome=sse_trace_outcome,
                text_len=len(text_so_far),
                tool_call_count=len(tool_calls),
                chunks=chunks_seen,
                stop_reason=final_stop_reason if isinstance(final_stop_reason, str) else None,
                error=sse_trace_error,
            )
            dump_response_for_trace(
                provider=provider,
                model=model,
                text_so_far=text_so_far,
                tool_calls=tool_calls,
                stop_reason=final_stop_reason if isinstance(final_stop_reason, str) else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                last_chunk=chunk if isinstance(chunk, dict) else None,
            )
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class OpenAIChatStreamServices:
    PSEUDO_TOOL_END: str
    PSEUDO_TOOL_START: str
    _remember_channel_injected_tool_use: Callable[..., Any]
    _split_word_buffer: Callable[..., Any]
    _validate_and_fix_tool_input: Callable[..., Any]
    append_tool_call_log: Callable[..., Any]
    cap_mcp_notification_wait_tool_input: Callable[..., Any]
    empty_end_turn_notice_for_body: Callable[..., Any]
    latest_user_tool_result_names: Callable[..., Any]
    normalize_tool_arguments: Callable[..., Any]
    parse_pseudo_tool_calls: Callable[..., Any]
    plan_mode_tool_name_for_emit: Callable[..., Any]
    positive_int: Callable[..., Any]
    resolve_emitted_tool_name: Callable[..., Any]
    router_log: Callable[..., Any]
    should_auto_continue_choice_question_with_tasklist: Callable[..., Any]
    should_auto_enter_plan_mode: Callable[..., Any]
    should_drop_duplicate_side_effect_tool_call: Callable[..., Any]
    should_drop_emitted_tool_call: Callable[..., Any]
    should_keep_work_alive_with_tasklist: Callable[..., Any]
    should_recover_empty_end_turn_with_tasklist: Callable[..., Any]
    write_anthropic_open_stream_stop: Callable[..., Any]
    write_router_activity: Callable[..., Any]


def forward_openai_chat_to_anthropic_sse(
    handler: BaseHTTPRequestHandler,
    resp: Any,
    model: str,
    provider: str,
    source_body: dict[str, Any] | None = None,
    start_index: int = 0,
    word_chunking: bool = False,
    input_tokens: int | None = None,
    input_bytes: int | None = None,
    *,
    services: OpenAIChatStreamServices,
) -> bool:

    PSEUDO_TOOL_END = services.PSEUDO_TOOL_END
    PSEUDO_TOOL_START = services.PSEUDO_TOOL_START
    _remember_channel_injected_tool_use = services._remember_channel_injected_tool_use
    _split_word_buffer = services._split_word_buffer
    _validate_and_fix_tool_input = services._validate_and_fix_tool_input
    append_tool_call_log = services.append_tool_call_log
    cap_mcp_notification_wait_tool_input = services.cap_mcp_notification_wait_tool_input
    empty_end_turn_notice_for_body = services.empty_end_turn_notice_for_body
    latest_user_tool_result_names = services.latest_user_tool_result_names
    normalize_tool_arguments = services.normalize_tool_arguments
    parse_pseudo_tool_calls = services.parse_pseudo_tool_calls
    plan_mode_tool_name_for_emit = services.plan_mode_tool_name_for_emit
    positive_int = services.positive_int
    resolve_emitted_tool_name = services.resolve_emitted_tool_name
    router_log = services.router_log
    should_auto_continue_choice_question_with_tasklist = services.should_auto_continue_choice_question_with_tasklist
    should_auto_enter_plan_mode = services.should_auto_enter_plan_mode
    should_drop_duplicate_side_effect_tool_call = services.should_drop_duplicate_side_effect_tool_call
    should_drop_emitted_tool_call = services.should_drop_emitted_tool_call
    should_keep_work_alive_with_tasklist = services.should_keep_work_alive_with_tasklist
    should_recover_empty_end_turn_with_tasklist = services.should_recover_empty_end_turn_with_tasklist
    write_anthropic_open_stream_stop = services.write_anthropic_open_stream_stop
    write_router_activity = services.write_router_activity
    next_content_index = start_index
    text_started = False
    text_suppressed_for_plan = False
    text_index: int | None = None
    text_so_far = ""
    pseudo_text = ""
    pseudo_mode = False
    text_buffer = ""
    text_stopped = False
    reasoning_started = False
    reasoning_stopped = False
    reasoning_index: int | None = None
    reasoning_so_far = ""
    tool_fragments: dict[int, dict[str, Any]] = {}
    output_tokens = 0
    finish_reason = "stop"
    chunks_seen = 0
    last_activity_update = 0.0

    def emit(event_name: str, payload: dict[str, Any]) -> None:
        handler.wfile.write(f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
        handler.wfile.flush()

    def ensure_text_started() -> int:
        nonlocal text_started, text_index, next_content_index, text_stopped
        if text_started and text_index is not None:
            return text_index
        text_started = True
        text_stopped = False
        text_index = next_content_index
        next_content_index += 1
        emit(
            "content_block_start",
            {"type": "content_block_start", "index": text_index, "content_block": {"type": "text", "text": ""}},
        )
        return text_index

    def ensure_reasoning_started() -> int:
        nonlocal reasoning_started, reasoning_index, next_content_index, reasoning_stopped
        if reasoning_started and reasoning_index is not None:
            return reasoning_index
        reasoning_started = True
        reasoning_stopped = False
        reasoning_index = next_content_index
        next_content_index += 1
        emit(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": reasoning_index,
                "content_block": {"type": "thinking", "thinking": ""},
            },
        )
        return reasoning_index

    def emit_reasoning_delta(text: str) -> None:
        if not text:
            return
        idx = ensure_reasoning_started()
        emit(
            "content_block_delta",
            {"type": "content_block_delta", "index": idx, "delta": {"type": "thinking_delta", "thinking": text}},
        )

    def close_reasoning_block() -> None:
        nonlocal reasoning_stopped
        if not reasoning_started or reasoning_index is None or reasoning_stopped:
            return
        digest = hashlib.sha256(reasoning_so_far.encode("utf-8", errors="replace")).hexdigest()[:24]
        emit(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": reasoning_index,
                "delta": {
                    "type": "signature_delta",
                    "signature": f"ciel-runtime-openai-reasoning-{digest}",
                },
            },
        )
        emit("content_block_stop", {"type": "content_block_stop", "index": reasoning_index})
        reasoning_stopped = True

    def emit_text_delta(text: str) -> None:
        if not text:
            return
        idx = ensure_text_started()
        emit(
            "content_block_delta",
            {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": text}},
        )

    def update_stream_activity(force: bool = False) -> None:
        nonlocal last_activity_update
        now = time.time()
        if not force and now - last_activity_update < 0.5:
            return
        last_activity_update = now
        estimated_output = output_tokens or max(0, len(text_so_far) // 4)
        write_router_activity(
            "request",
            provider,
            model,
            tokens=input_tokens,
            bytes=input_bytes,
            output_tokens=estimated_output,
            chunks=chunks_seen,
            stream=True,
        )

    try:
        for raw_line in resp:
            chunks_seen += 1
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                break
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            usage = event.get("usage")
            if isinstance(usage, dict):
                output_tokens = max(output_tokens, positive_int(usage.get("completion_tokens")) or 0)
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0] if isinstance(choices[0], dict) else {}
            if choice.get("finish_reason"):
                finish_reason = str(choice.get("finish_reason"))
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            reasoning_chunk = delta.get("reasoning_content") or ""
            if reasoning_chunk:
                reasoning_so_far += str(reasoning_chunk)
                emit_reasoning_delta(str(reasoning_chunk))
                update_stream_activity()
            text_chunk = delta.get("content") or ""
            if text_chunk:
                close_reasoning_block()
                if pseudo_mode or PSEUDO_TOOL_START in text_chunk:
                    before, sep, after = text_chunk.partition(PSEUDO_TOOL_START)
                    if before and not pseudo_mode:
                        text_so_far += before
                        if word_chunking:
                            text_buffer += before
                            to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
                            emit_text_delta(to_flush)
                        else:
                            emit_text_delta(before)
                    pseudo_mode = True
                    pseudo_text += (sep + after) if sep else text_chunk
                    if PSEUDO_TOOL_END in pseudo_text:
                        pseudo_mode = False
                    continue
                if source_body is not None and not text_started and not tool_fragments and should_auto_enter_plan_mode(source_body, text_so_far + text_chunk, []):
                    text_so_far += text_chunk
                    text_suppressed_for_plan = True
                    continue
                if text_suppressed_for_plan and not text_started and text_so_far:
                    pending_text = text_so_far + text_chunk
                    text_so_far = pending_text
                    text_suppressed_for_plan = False
                    if word_chunking:
                        text_buffer += pending_text
                        to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
                        emit_text_delta(to_flush)
                    else:
                        emit_text_delta(pending_text)
                    update_stream_activity()
                    continue
                text_so_far += text_chunk
                if word_chunking:
                    text_buffer += text_chunk
                    to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
                    emit_text_delta(to_flush)
                else:
                    emit_text_delta(text_chunk)
                update_stream_activity()
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                try:
                    call_index = int(call.get("index"))
                except Exception:
                    call_index = len(tool_fragments)
                slot = tool_fragments.setdefault(call_index, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    slot["id"] = str(call.get("id"))
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                if fn.get("name"):
                    slot["name"] += str(fn.get("name"))
                if fn.get("arguments"):
                    slot["arguments"] += str(fn.get("arguments"))
                update_stream_activity()
        update_stream_activity(force=True)
        if word_chunking and text_buffer:
            to_flush, text_buffer = _split_word_buffer(text_buffer, force=True)
            emit_text_delta(to_flush)
        close_reasoning_block()

        tool_calls: list[dict[str, Any]] = []
        _, pseudo_tool_calls = parse_pseudo_tool_calls(pseudo_text, source_body)
        for i, pseudo in enumerate(pseudo_tool_calls):
            fn = pseudo.get("function") if isinstance(pseudo, dict) else {}
            if isinstance(fn, dict):
                tool_fragments.setdefault(100000 + i, {
                    "id": str(pseudo.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": json.dumps(fn.get("arguments") or {}, ensure_ascii=False),
                })
        for _, fragment in sorted(tool_fragments.items()):
            raw_name = str(fragment.get("name") or "")
            if not raw_name:
                continue
            matched_name = resolve_emitted_tool_name(raw_name, source_body)
            normalized_args = normalize_tool_arguments(matched_name, fragment.get("arguments") or {})
            fixed_input = _validate_and_fix_tool_input(matched_name, normalized_args)
            if source_body is not None:
                matched_name, fixed_input = plan_mode_tool_name_for_emit(source_body, matched_name, fixed_input)
                if matched_name is None:
                    continue
            fixed_input = cap_mcp_notification_wait_tool_input(matched_name, fixed_input)
            if should_drop_emitted_tool_call(matched_name, fixed_input, raw_name, source_body):
                continue
            if should_drop_duplicate_side_effect_tool_call(matched_name, fixed_input, raw_name):
                continue
            tool_calls.append({"function": {"name": matched_name, "arguments": fixed_input}})
            tool_index = next_content_index
            next_content_index += 1
            tool_id = str(fragment.get("id") or f"toolu_openai_{int(time.time() * 1000)}_{tool_index}")
            _remember_channel_injected_tool_use(source_body, tool_id, matched_name, fixed_input)
            append_tool_call_log(
                "openai_stream_tool_call",
                {
                    "model": model,
                    "raw_name": raw_name,
                    "matched_name": matched_name,
                    "raw_arguments": fragment.get("arguments"),
                    "emitted_input": fixed_input,
                    "sse_index": tool_index,
                },
            )
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {"type": "tool_use", "id": tool_id, "name": matched_name, "input": {}},
                },
            )
            emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": tool_index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(fixed_input, ensure_ascii=False)},
                },
            )
            emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})

        if source_body is not None and should_auto_enter_plan_mode(source_body, text_so_far, tool_calls):
            router_log("WARN", "auto-synthesized EnterPlanMode from short/empty upstream OpenAI stream")
            tool_index = next_content_index
            next_content_index += 1
            tool_calls.append({"function": {"name": "EnterPlanMode", "arguments": {}}})
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {"type": "tool_use", "id": f"toolu_openai_plan_{int(time.time() * 1000)}", "name": "EnterPlanMode", "input": {}},
                },
            )
            emit("content_block_delta", {"type": "content_block_delta", "index": tool_index, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
            emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
        elif source_body is not None and should_recover_empty_end_turn_with_tasklist(source_body, text_so_far, tool_calls):
            router_log("WARN", "auto-synthesized TaskList from empty upstream end_turn OpenAI stream")
            tool_index = next_content_index
            next_content_index += 1
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {"type": "tool_use", "id": f"toolu_openai_empty_{int(time.time() * 1000)}", "name": "TaskList", "input": {}},
                },
            )
            emit("content_block_delta", {"type": "content_block_delta", "index": tool_index, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
            emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
        elif text_suppressed_for_plan and not text_started and text_so_far:
            emit_text_delta(text_so_far)

        if source_body is not None and should_keep_work_alive_with_tasklist(source_body, text_so_far, tool_calls):
            router_log("WARN", "auto-synthesized TaskList to keep work moving after OpenAI stream")
            tool_index = next_content_index
            next_content_index += 1
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {"type": "tool_use", "id": f"toolu_openai_keepalive_{int(time.time() * 1000)}", "name": "TaskList", "input": {}},
                },
            )
            emit("content_block_delta", {"type": "content_block_delta", "index": tool_index, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
            emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})

        if source_body is not None and should_auto_continue_choice_question_with_tasklist(source_body, text_so_far, tool_calls):
            router_log("WARN", "auto-synthesized TaskList after clarification question OpenAI stream")
            tool_index = next_content_index
            next_content_index += 1
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {"type": "tool_use", "id": f"toolu_openai_choice_{int(time.time() * 1000)}", "name": "TaskList", "input": {}},
                },
            )
            emit("content_block_delta", {"type": "content_block_delta", "index": tool_index, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
            emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})

        if text_started and text_index is not None:
            emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
            text_stopped = True
        if not text_started and not tool_calls:
            text_so_far = empty_end_turn_notice_for_body(source_body) if source_body is not None else ""
            if source_body is not None:
                router_log(
                    "WARN",
                    f"openai_empty_end_turn_notice provider={provider} model={model} "
                    f"latest_tool_results={','.join(latest_user_tool_result_names(source_body)) or '-'}",
                )
            emit_text_delta(text_so_far)
            if text_index is not None:
                emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
                text_stopped = True
        stop_reason = "tool_use" if tool_calls else ("max_tokens" if finish_reason == "length" else "end_turn")
        write_anthropic_open_stream_stop(handler, {"stop_reason": stop_reason, "usage": {"output_tokens": output_tokens or max(1, len(text_so_far) // 4)}})
        return True
    except Exception as exc:
        router_log("ERROR", f"openai_stream_error provider={provider} model={model} error={type(exc).__name__}: {exc}")
        write_router_activity("error", provider, model, error=type(exc).__name__, stream=True)
        try:
            if word_chunking and text_buffer:
                to_flush, text_buffer = _split_word_buffer(text_buffer, force=True)
                emit_text_delta(to_flush)
            if not text_started:
                emit_text_delta(f"Upstream stream error: {type(exc).__name__}: {exc}")
            if text_started and text_index is not None and not text_stopped:
                emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
                text_stopped = True
            write_anthropic_open_stream_stop(
                handler,
                {"stop_reason": "end_turn", "usage": {"output_tokens": output_tokens or max(1, len(text_so_far) // 4)}},
            )
        except Exception:
            pass
        return False
    finally:
        try:
            resp.close()
        except Exception:
            pass


__all__ = [
    "AnthropicStreamServices",
    "OllamaStreamServices",
    "OpenAIChatStreamServices",
    "forward_openai_chat_to_anthropic_sse",
    "ollama_stream_to_anthropic_sse",
    "rebatch_anthropic_sse_text",
]
