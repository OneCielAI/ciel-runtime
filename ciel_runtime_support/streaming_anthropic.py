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

from .runaway_output_guard import (
    STOPPED,
    RunawayOutputDetector,
    RunawayOutputPolicy,
    RunawayVerdict,
    policy_from_env,
    recent_runaway_notices,
)
from .remote_bridge import is_remote_bridge_request
from .sse_diagnostics import payload_shape, response_shape
from .upstream_stream_io import UpstreamClientDisconnected, client_connection_closed


def downstream_keepalive_interval() -> float:
    """One keepalive policy for semantic admission and upstream SSE relay."""
    raw = os.environ.get("CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS")
    if raw is None:
        return 15.0
    try:
        return max(0.0, min(120.0, float(raw)))
    except (TypeError, ValueError, OverflowError):
        return 15.0


class SemanticCompactionSSE:
    """Request-local early envelope; the sidecar only probes and writes pings.

    The request thread must join the sole heartbeat writer before relaying or
    reporting an error. A stuck writer fails closed instead of handing off.
    Summary generation deliberately never runs on this thread.
    """

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.state = "UNSTARTED"
        self.cancel_event = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def started(self) -> bool:
        return self.state != "UNSTARTED"

    def cancelled(self) -> bool:
        if not self.cancel_event.is_set() and client_connection_closed(self.handler):
            self.cancel_event.set()
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.started and self.cancelled():
            raise UpstreamClientDisconnected("downstream client disconnected during compaction")

    def _ping(self) -> None:
        self.handler.wfile.write(b'event: ping\ndata: {"type":"ping"}\n\n')
        self.handler.wfile.flush()

    def start(self) -> None:
        if self.cancelled():
            raise UpstreamClientDisconnected("downstream client disconnected before compaction")
        if self.started:
            return
        # Mark before writing any headers: even a partial commit cannot be
        # answered with another HTTP status line.
        self.state = "EARLY_SSE"
        self.handler.close_connection = True
        try:
            self.handler.send_response(200)
            self.handler.send_header("content-type", "text/event-stream")
            self.handler.send_header("cache-control", "no-cache")
            self.handler.send_header("connection", "close")
            self.handler.end_headers()
            self._ping()
        except Exception:
            self.cancel_event.set()
            raise
        self._thread = threading.Thread(
            target=self._heartbeat, daemon=True, name="ciel-compaction-heartbeat",
        )
        self._thread.start()

    def _heartbeat(self) -> None:
        interval = downstream_keepalive_interval()
        next_ping = time.monotonic() + interval
        try:
            while not self._stop.wait(0.25):
                if self.cancelled():
                    return
                if interval > 0 and time.monotonic() >= next_ping:
                    if self._stop.is_set():
                        return
                    self._ping()
                    next_ping = time.monotonic() + interval
        except Exception:
            self.cancel_event.set()

    def stop(self) -> bool:
        """Bounded writer join. False forbids any further downstream writes."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
            if thread.is_alive():
                self.cancel_event.set()
                # Interrupt a blocked socket write; never race it with relay.
                connection = getattr(self.handler, "connection", None)
                if connection is not None:
                    try:
                        connection.shutdown(2)
                    except OSError:
                        pass
                thread.join(timeout=1.0)
                return False
        return True

    def handoff(self) -> None:
        if not self.stop():
            raise UpstreamClientDisconnected("compaction heartbeat writer did not stop")
        self.check_cancelled()
        self.state = "RELAYING"

    def fail(self, payload: dict[str, Any]) -> None:
        """At most one typed error, and only before the existing relay owns IO."""
        stopped = self.stop()
        if self.state != "EARLY_SSE":
            return
        self.state = "TERMINAL"
        self.handler.close_connection = True
        if not stopped or self.cancelled():
            return
        try:
            data = json.dumps(payload, ensure_ascii=False)
            self.handler.wfile.write(f"event: error\ndata: {data}\n\n".encode("utf-8"))
            self.handler.wfile.flush()
        except Exception:
            self.cancel_event.set()

    def finish(self) -> None:
        self.stop()
        if self.started:
            self.state = "TERMINAL"


def _remote_noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _remote_false(*_args: Any, **_kwargs: Any) -> bool:
    return False


def _remote_tool_name(
    _body: Any, name: str, tool_input: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    return name, tool_input


def _remote_tool_input(_body: Any, tool_input: dict[str, Any]) -> dict[str, Any]:
    return tool_input


def _remote_tool_validation(
    _name: str,
    tool_input: dict[str, Any],
    _source_body: Any,
) -> dict[str, Any]:
    return tool_input


def _stream_runaway_policy(handler: Any) -> RunawayOutputPolicy:
    if is_remote_bridge_request(handler):
        return RunawayOutputPolicy(enabled=False, recover=False)
    return policy_from_env(os.environ.get)


def _remote_empty_list(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def _ignore_stream_failure(_metadata: dict[str, Any]) -> None:
    return None


def _ignore_stream_model(_native_model: str) -> None:
    return None


def _stream_session_id(handler: Any) -> str | None:
    getter = getattr(getattr(handler, "headers", None), "get", None)
    value = getter("x-claude-code-session-id") if callable(getter) else None
    if not isinstance(value, str):
        return None
    return value.strip()[:128] or None


def _complete_sse_data(parts: list[str]) -> bool:
    body = "\n".join(parts).strip()
    if not body:
        return False
    if body == "[DONE]":
        return True
    try:
        json.loads(body)
    except ValueError:
        return False
    return True


def _iter_openai_chat_sse_data(
    lines: Iterable[Any], *, strict_utf8: bool
) -> Iterable[str]:
    """Yield complete OpenAI-compatible SSE data records.

    Standards-compliant SSE joins multiple ``data:`` fields with newlines and
    ends the record at a blank line. Some compatible servers omit that blank
    separator, so a complete JSON value also closes the preceding record when
    the next ``data:`` field arrives.
    """

    pending: list[str] = []
    for raw in lines:
        if isinstance(raw, (bytes, bytearray)):
            decoded = raw.decode(
                "utf-8", errors="strict" if strict_utf8 else "ignore"
            )
        else:
            decoded = str(raw)
        for line in decoded.splitlines() or [""]:
            if not line:
                if pending:
                    yield "\n".join(pending)
                    pending.clear()
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if field == "data":
                if separator and value.startswith(" "):
                    value = value[1:]
                if pending and _complete_sse_data(pending):
                    yield "\n".join(pending)
                    pending.clear()
                pending.append(value)
                continue
            if field in {"event", "id", "retry"}:
                continue
            # Preserve compatibility with providers that stream bare JSON
            # records despite advertising an SSE content type.
            if pending:
                yield "\n".join(pending)
                pending.clear()
            yield line
    if pending:
        yield "\n".join(pending)


@dataclass(frozen=True, slots=True)
class AnthropicStreamIO:
    ANTHROPIC_THINKING_BLOCK_TYPES: Any
    VisibleToolCallArtifactFilter: type[Any]
    _find_pseudo_xml_tool_start: Callable[..., Any]
    _split_word_buffer: Callable[..., Any]
    mark_pending_channel_delivery_failed: Callable[..., Any]
    mark_pending_channel_delivery_success: Callable[..., Any]
    remember_suppressed_thinking_passback: Callable[..., Any]
    router_client_connection_closed: Callable[..., Any]
    router_log: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AnthropicToolProjection:
    _is_mcp_notification_wait_tool: Callable[..., Any]
    _remember_channel_injected_tool_use: Callable[..., Any]
    _validate_and_fix_tool_input: Callable[..., Any]
    append_tool_call_log: Callable[..., Any]
    cap_mcp_notification_wait_tool_input: Callable[..., Any]
    infer_tool_name_from_args: Callable[..., Any]
    normalize_tool_arguments: Callable[..., Any]
    parse_pseudo_tool_calls: Callable[..., Any]
    plan_mode_tool_name_for_emit: Callable[..., Any]
    resolve_emitted_tool_name: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AnthropicToolPolicy:
    should_drop_duplicate_side_effect_tool_call: Callable[..., Any]
    should_drop_emitted_tool_call: Callable[..., Any]
    should_repair_anthropic_passthrough_tool_input: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AnthropicConversationContext:
    backfill_exit_plan_mode_allowed_prompts: Callable[..., Any]
    body_ultracode_runtime_enabled: Callable[..., Any]
    empty_end_turn_notice_for_body: Callable[..., Any]
    has_tool: Callable[..., Any]
    latest_user_intent_message_index: Callable[..., Any]
    latest_user_is_claude_code_suggestion_mode: Callable[..., Any]
    latest_user_tool_result_names: Callable[..., Any]
    recent_synthetic_tasklist_count: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AnthropicContinuationPolicy:
    should_auto_continue_choice_question_with_tasklist: Callable[..., Any]
    should_auto_exit_plan_mode: Callable[..., Any]
    should_keep_work_alive_with_tasklist: Callable[..., Any]
    should_recover_empty_end_turn_with_tasklist: Callable[..., Any]
    should_synthesize_tasklist_for_provider: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AnthropicStreamServices:
    io: AnthropicStreamIO
    tool_projection: AnthropicToolProjection
    tool_policy: AnthropicToolPolicy
    conversation: AnthropicConversationContext
    continuation: AnthropicContinuationPolicy


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
    stream_failure_observer: Callable[[dict[str, Any]], None] = _ignore_stream_failure,
    stream_model_observer: Callable[[str], None] = _ignore_stream_model,
) -> None:
    """
    Parse upstream Anthropic SSE and re-emit it with text_delta events buffered
    to word boundaries. Non-text events are forwarded in the same SSE framing.
    When the selected provider cannot preserve Anthropic's thinking passback
    contract, thinking blocks are suppressed and later content block indices are
    compacted.

    ``stream_failure_observer`` receives one metadata dict after a stream that
    did not complete, once the upstream response is closed. It carries counts
    and flags only, never body text, tool arguments or credentials, and it
    cannot change what was sent downstream.

    ``stream_model_observer`` receives message_start's ``message.model`` (the
    model that actually answered, which an upstream alias may name differently
    from the request). It cannot change what is sent downstream either.
    """

    ANTHROPIC_THINKING_BLOCK_TYPES = services.io.ANTHROPIC_THINKING_BLOCK_TYPES
    VisibleToolCallArtifactFilter = services.io.VisibleToolCallArtifactFilter
    _find_pseudo_xml_tool_start = services.io._find_pseudo_xml_tool_start
    _split_word_buffer = services.io._split_word_buffer
    mark_pending_channel_delivery_failed = services.io.mark_pending_channel_delivery_failed
    mark_pending_channel_delivery_success = services.io.mark_pending_channel_delivery_success
    remember_suppressed_thinking_passback = services.io.remember_suppressed_thinking_passback
    router_client_connection_closed = services.io.router_client_connection_closed
    router_log = services.io.router_log
    _is_mcp_notification_wait_tool = services.tool_projection._is_mcp_notification_wait_tool
    _remember_channel_injected_tool_use = services.tool_projection._remember_channel_injected_tool_use
    _validate_and_fix_tool_input = services.tool_projection._validate_and_fix_tool_input
    append_tool_call_log = services.tool_projection.append_tool_call_log
    cap_mcp_notification_wait_tool_input = services.tool_projection.cap_mcp_notification_wait_tool_input
    infer_tool_name_from_args = services.tool_projection.infer_tool_name_from_args
    normalize_tool_arguments = services.tool_projection.normalize_tool_arguments
    parse_pseudo_tool_calls = services.tool_projection.parse_pseudo_tool_calls
    plan_mode_tool_name_for_emit = services.tool_projection.plan_mode_tool_name_for_emit
    resolve_emitted_tool_name = services.tool_projection.resolve_emitted_tool_name
    should_drop_duplicate_side_effect_tool_call = services.tool_policy.should_drop_duplicate_side_effect_tool_call
    should_drop_emitted_tool_call = services.tool_policy.should_drop_emitted_tool_call
    should_repair_anthropic_passthrough_tool_input = services.tool_policy.should_repair_anthropic_passthrough_tool_input
    backfill_exit_plan_mode_allowed_prompts = services.conversation.backfill_exit_plan_mode_allowed_prompts
    body_ultracode_runtime_enabled = services.conversation.body_ultracode_runtime_enabled
    empty_end_turn_notice_for_body = services.conversation.empty_end_turn_notice_for_body
    has_tool = services.conversation.has_tool
    latest_user_intent_message_index = services.conversation.latest_user_intent_message_index
    latest_user_is_claude_code_suggestion_mode = services.conversation.latest_user_is_claude_code_suggestion_mode
    latest_user_tool_result_names = services.conversation.latest_user_tool_result_names
    recent_synthetic_tasklist_count = services.conversation.recent_synthetic_tasklist_count
    should_auto_continue_choice_question_with_tasklist = services.continuation.should_auto_continue_choice_question_with_tasklist
    should_auto_exit_plan_mode = services.continuation.should_auto_exit_plan_mode
    should_keep_work_alive_with_tasklist = services.continuation.should_keep_work_alive_with_tasklist
    should_recover_empty_end_turn_with_tasklist = services.continuation.should_recover_empty_end_turn_with_tasklist
    should_synthesize_tasklist_for_provider = services.continuation.should_synthesize_tasklist_for_provider
    if is_remote_bridge_request(handler):
        mark_pending_channel_delivery_failed = _remote_noop
        mark_pending_channel_delivery_success = _remote_noop
        remember_suppressed_thinking_passback = _remote_noop
        _remember_channel_injected_tool_use = _remote_noop
        append_tool_call_log = _remote_noop
        plan_mode_tool_name_for_emit = _remote_tool_name
        _validate_and_fix_tool_input = _remote_tool_validation
        should_drop_duplicate_side_effect_tool_call = _remote_false
        should_drop_emitted_tool_call = _remote_false
        should_repair_anthropic_passthrough_tool_input = _remote_false
        backfill_exit_plan_mode_allowed_prompts = _remote_tool_input
        should_auto_continue_choice_question_with_tasklist = _remote_false
        should_auto_exit_plan_mode = _remote_false
        should_keep_work_alive_with_tasklist = _remote_false
        should_recover_empty_end_turn_with_tasklist = _remote_false
        should_synthesize_tasklist_for_provider = _remote_false
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
    # Only an upstream message_stop completes a turn. Once any ending (terminal
    # events or an error) starts downstream, nothing may follow it.
    upstream_error_forwarded = False
    ending_started = False
    reading_upstream = True
    completing_stream = False
    failure_reason: str | None = None
    # Failure metadata: how the upstream side ended, as far as it was observed.
    upstream_message_id: str | None = None
    upstream_error_type = ""
    upstream_eof = False
    upstream_read_failed = False
    client_disconnected = False
    # Text the client actually received: counted only after emit_raw flushed it.
    visible_text_len = 0
    # Any upstream block whose type ends in "tool_use" (tool_use,
    # server_tool_use, mcp_tool_use, ...) or any tool-input delta. Metadata
    # only: it never changes what is relayed.
    saw_tool_like_block = False
    # A pseudo XML tool call was detected in streamed text at least once.
    saw_pseudo_tool_text = False
    allow_tasklist_synthesis = should_synthesize_tasklist_for_provider(provider)
    filter_visible_tool_call_artifacts = bool(
        provider == "anthropic"
        and isinstance(source_body, dict)
        and (has_tool(source_body, "Workflow") or body_ultracode_runtime_enabled(source_body))
    )
    visible_tool_call_artifact_filters: dict[int, VisibleToolCallArtifactFilter] = {}
    runaway_policy = _stream_runaway_policy(handler)
    text_runaway = RunawayOutputDetector(runaway_policy)
    thinking_runaway = RunawayOutputDetector(runaway_policy)
    runaway_verdict: RunawayVerdict | None = None

    class ClientStreamDisconnected(Exception):
        pass

    def note_runaway(detector: RunawayOutputDetector, text: str) -> None:
        nonlocal runaway_verdict
        if runaway_verdict is None:
            runaway_verdict = detector.feed(text)

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

    def note_visible_text(text: Any) -> None:
        """Call only after emit_raw returned, i.e. the bytes were written and flushed."""

        nonlocal visible_text_len
        if isinstance(text, str):
            visible_text_len += len(text)

    def emit_text_delta_raw(index: int, text: str) -> None:
        if not text:
            return
        payload = {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
        emit_raw("content_block_delta", json.dumps(payload, ensure_ascii=False))
        note_visible_text(text)

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
        nonlocal ending_started
        ending_started = True
        stop_reason = default_stop_reason
        if pending_message_delta is not None:
            try:
                parsed = json.loads(pending_message_delta[1])
                if isinstance(parsed, dict):
                    delta = parsed.get("delta") if isinstance(parsed.get("delta"), dict) else {}
                    stop_reason = str(delta.get("stop_reason") or stop_reason)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                router_log("WARN", f"anthropic_stream_pending_delta_parse_failed error={type(exc).__name__}: {exc}")
        emit_raw(
            pending_message_delta[0] if pending_message_delta is not None else "message_delta",
            patched_message_delta(stop_reason),
        )
        emit_raw(
            pending_message_stop[0] if pending_message_stop is not None else "message_stop",
            pending_message_stop[1] if pending_message_stop is not None else "{\"type\":\"message_stop\"}",
        )

    def emit_runaway_stop() -> None:
        """Close a looping turn instead of relaying the rest of the loop."""

        nonlocal next_content_index, ending_started
        if runaway_verdict is None:
            return
        ending_started = True
        router_log(
            "WARN",
            f"anthropic_stream_runaway_repetition provider={provider} model={model} "
            f"text_len={len(text_so_far)} {runaway_verdict.log_fields()}",
        )
        for index in sorted(open_content_blocks):
            finish_visible_tool_call_artifact_filter(index)
            emit_raw(
                "content_block_stop",
                json.dumps({"type": "content_block_stop", "index": index}, ensure_ascii=False),
            )
        open_content_blocks.clear()
        notice_index = next_content_index
        next_content_index += 1
        emit_text_block(notice_index, runaway_verdict.notice(STOPPED))
        # Claude Code automatically continues responses marked max_tokens.
        # This path is the terminal circuit-breaker after one recovery already
        # looped, so end the turn instead of creating an outer continuation loop.
        stop_reason = "end_turn"
        if (
            not emitted_tool_use
            and runaway_policy.recover
            and recent_runaway_notices(source_body) == 0
        ):
            # Cutting the loop should not cost the user the turn. A tool call
            # brings the CLI back for a fresh one; the history check above stops
            # the recovery from becoming a loop of its own.
            router_log("WARN", f"anthropic_stream_runaway_recovery provider={provider} model={model}")
            tool_index = next_content_index
            next_content_index += 1
            emit_tasklist_tool(tool_index)
            stop_reason = "tool_use"
        emit_raw("message_delta", patched_message_delta(stop_reason))
        emit_raw("message_stop", "{\"type\":\"message_stop\"}")

    def tracked_upstream_lines() -> Iterable[Any]:
        """Mark whether a failure came from reading upstream or from relaying a line."""

        nonlocal reading_upstream, upstream_eof
        for raw_line in upstream_lines_with_downstream_keepalive():
            reading_upstream = False
            yield raw_line
            reading_upstream = True
        # Reached only when the upstream ran out, not when the relay stopped reading.
        upstream_eof = True

    def flush_received_text(*, unfinished: bool = False) -> None:
        for index in list(text_buffers.keys()):
            flush_buffer(index, force=True)
        if unfinished:
            # A failed stream never reaches content_block_stop, where the
            # artifact filter would release the text it holds back.
            for index in list(visible_tool_call_artifact_filters.keys()):
                finish_visible_tool_call_artifact_filter(index)

    def forward_upstream_error(event_type: str | None, data_str: str) -> None:
        """Relay Anthropic's in-stream error after what preceded it; nothing follows it."""

        nonlocal upstream_error_forwarded, ending_started, upstream_error_type
        if provider == "cielairouter":
            shape = {"payload": payload_shape(data_str, event_type), "response": response_shape(resp)}
            router_log("WARN", "anthropic_sse_error_shape "
                       + json.dumps(shape, separators=(",", ":")))
        try:
            parsed_error = json.loads(data_str).get("error")
            upstream_error_type = str(parsed_error.get("type") or "")[:64] if isinstance(parsed_error, dict) else ""
        except (AttributeError, TypeError, ValueError):
            upstream_error_type = ""
        flush_received_text(unfinished=True)
        upstream_error_forwarded = True
        ending_started = True
        if pending_message_delta is not None:
            emit_raw(pending_message_delta[0] or "message_delta", pending_message_delta[1])
        # A data-only record is ignored by Anthropic SSE clients; the error
        # must arrive as an ``error`` event to fail the request.
        emit_raw("error", data_str)

    def process_tail_event() -> None:
        """An unterminated final record counts only when its data is complete JSON."""

        nonlocal pending_event_type, pending_event_lines
        if not pending_event_lines:
            return
        event_type = pending_event_type
        data_str = "\n".join(pending_event_lines)
        pending_event_type = None
        pending_event_lines = []
        if event_type != "error":
            try:
                json.loads(data_str)
            except (ValueError, RecursionError):
                router_log(
                    "WARN",
                    f"anthropic_sse_upstream_truncated_event provider={provider} model={model} "
                    f"event={event_type or '-'} data_len={len(data_str)}",
                )
                return
        process_event(event_type, data_str)

    def finish_completed_stream() -> None:
        """Upstream sent message_stop: apply completion policies, relay the ending once."""

        nonlocal stream_success, completing_stream
        flush_received_text()
        for index in list(suppressed_thinking_blocks.keys()):
            finish_suppressed_thinking_block(index)
        # message_delta recovery can emit tools. Defer it until the upstream
        # terminal is known, so an interrupted response cannot trigger a tool.
        if pending_message_delta is not None:
            event_type, data_str = pending_message_delta
            completing_stream = True
            try:
                process_event(event_type, data_str)
            finally:
                completing_stream = False
        recover_hidden_only_response_if_needed()
        flush_suppressed_thinking_passback()
        emit_pending_message_end()
        stream_success = True

    def report_incomplete_stream(message: str, reason: str) -> None:
        """No upstream message_stop: relay what arrived, then fail; never invent an ending.

        Open blocks stay open. Closing them would hand the client a finished
        block (a truncated tool_use included) that it may keep and act on.
        """

        nonlocal failure_reason, ending_started
        failure_reason = reason
        flush_received_text(unfinished=True)
        for index in list(suppressed_thinking_blocks.keys()):
            finish_suppressed_thinking_block(index)
        flush_suppressed_thinking_passback()
        ending_started = True
        if pending_message_delta is not None:
            emit_raw(pending_message_delta[0] or "message_delta", pending_message_delta[1])
        emit_raw(
            "error",
            json.dumps({"type": "error", "error": {"type": "api_error", "message": message}}, ensure_ascii=False),
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
        emitted_name = matched_name
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
        # Plan mode can retarget the call at a different tool, and then the
        # original arguments no longer describe the schema being checked.
        supplied_input = parsed_args if matched_name == emitted_name else None
        if should_drop_emitted_tool_call(matched_name, fixed_input, raw_name, source_body, supplied_input):
            return
        if should_drop_duplicate_side_effect_tool_call(matched_name, fixed_input, raw_name, source_body):
            return
        tool_id = str(tool_state.get("id") or f"toolu_anthropic_{int(time.time() * 1000)}_{index}")
        _remember_channel_injected_tool_use(source_body, tool_id, matched_name, fixed_input)
        append_tool_call_log(
            "anthropic_stream_tool_call",
            {
                "model": model,
                "tool_id": tool_id,
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
        nonlocal saw_message_start, saw_message_stop, text_so_far, saw_tool_use, emitted_tool_use, next_content_index, pending_message_delta, pending_message_stop, upstream_message_id, saw_tool_like_block, saw_pseudo_tool_text
        if saw_message_stop and not completing_stream:
            router_log("WARN", f"anthropic_sse_event_after_message_stop_ignored provider={provider} model={model} event={event_type or '-'}")
            return
        try:
            event = json.loads(data_str)
        except Exception:
            if event_type == "error":
                forward_upstream_error(event_type, data_str)
                return
            emit_raw(event_type, data_str)
            return
        if not isinstance(event, dict):
            emit_raw(event_type, data_str)
            return
        evt_type = event.get("type") or event_type
        if event_type == "error" or evt_type == "error":
            forward_upstream_error(event_type, data_str)
            return
        if evt_type == "message_start":
            saw_message_start = True
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("id"), str):
                upstream_message_id = message["id"][:128]
            if isinstance(message, dict) and isinstance(message.get("model"), str) and message["model"].strip():
                try:
                    stream_model_observer(message["model"][:128])
                except Exception as exc:
                    router_log("WARN", f"anthropic_stream_model_observer_failed error={type(exc).__name__}")
        elif evt_type == "message_stop":
            saw_message_stop = True
            pending_message_stop = (event_type, data_str)
            return
        elif evt_type == "content_block_start":
            index = event.get("index")
            content_block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            block_type = content_block.get("type")
            if isinstance(block_type, str) and block_type.endswith("tool_use"):
                saw_tool_like_block = True
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
            if not completing_stream:
                for index in list(text_buffers.keys()):
                    flush_buffer(index, force=True)
                pending_message_delta = (event_type, data_str)
                return
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
            if (
                stop_reason == "end_turn"
                and source_body is not None
                and should_auto_exit_plan_mode(source_body, text_so_far, tool_calls)
            ):
                for index in list(text_buffers.keys()):
                    flush_buffer(index, force=True)
                router_log(
                    "WARN",
                    "auto-synthesized ExitPlanMode from explicit plan-exit text "
                    "with Anthropic-compatible end_turn",
                )
                emit_exit_plan_mode_tool(next_content_index)
                next_content_index += 1
                saw_tool_use = True
                pending_message_delta = (
                    event_type,
                    patched_message_delta("tool_use"),
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
            if delta.get("type") == "input_json_delta":
                saw_tool_like_block = True
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
            if delta.get("type") == "thinking_delta":
                note_runaway(thinking_runaway, str(delta.get("thinking") or ""))
            if isinstance(mapped_index, int) and delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                if not text:
                    return
                text_so_far += text
                note_runaway(text_runaway, text)
                if provider != "anthropic" and mapped_index in held_pseudo_tool_text:
                    held_pseudo_tool_text[mapped_index] += text
                    return
                pseudo_start = _find_pseudo_xml_tool_start(text, source_body) if provider != "anthropic" else -1
                if pseudo_start >= 0:
                    saw_pseudo_tool_text = True
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
            if delta.get("type") == "text_delta":
                note_visible_text(delta.get("text"))
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
        if evt_type == "content_block_start":
            # A text block may carry initial text in content_block_start.
            started_block = event.get("content_block")
            if isinstance(started_block, dict) and started_block.get("type") == "text":
                note_visible_text(started_block.get("text"))

    def stream_state() -> str:
        return (
            f"text_len={len(text_so_far)} emitted_tool_use={emitted_tool_use} "
            f"message_start={saw_message_start} message_delta={pending_message_delta is not None} "
            f"open_blocks={len(open_content_blocks) + len(buffered_tool_uses)}"
        )

    def stream_failure_metadata(reason: str) -> dict[str, Any]:
        """Counts and flags only; no body text, tool arguments or credentials."""

        return {
            "event": "anthropic_stream_failure",
            "provider": provider,
            "model": model,
            "session_id": _stream_session_id(handler),
            "upstream_message_id": upstream_message_id,
            # The upstream itself ended (EOF, read error or error event) without
            # message_stop. False when the relay stopped reading first, so the
            # upstream's own ending is unknown.
            "confirmed_incomplete": bool(
                not saw_message_stop and (upstream_eof or upstream_read_failed or upstream_error_forwarded)
            ),
            # Text received from upstream, including text the relay still held.
            "text_len": len(text_so_far),
            # Text the client received: text_delta (and initial text-block
            # text) counted only after the write and flush succeeded.
            "visible_text_len": visible_text_len,
            "emitted_tool_use": emitted_tool_use,
            # Only blocks typed exactly "tool_use"; see saw_tool_like_block.
            "saw_tool_use": saw_tool_use,
            "saw_tool_like_block": saw_tool_like_block,
            "saw_pseudo_tool_text": saw_pseudo_tool_text,
            # Indices whose pseudo tool-call text was held and never relayed.
            "held_pseudo_tool_text": len(held_pseudo_tool_text),
            # Conservative: any client, server or MCP tool block, tool-input
            # delta, held tool_use, or pseudo tool text makes this True.
            "tool_uncertain": bool(
                saw_tool_use
                or emitted_tool_use
                or saw_tool_like_block
                or buffered_tool_uses
                or saw_pseudo_tool_text
                or held_pseudo_tool_text
            ),
            # Blocks started downstream without a content_block_stop; buffered
            # tool_use blocks were never started downstream and count separately.
            "open_blocks": len(open_content_blocks.difference(buffered_tool_uses)),
            "held_tool_uses": len(buffered_tool_uses),
            "upstream_error": upstream_error_forwarded,
            # Anthropic's error.type from the forwarded in-stream error event.
            "upstream_error_type": upstream_error_type,
            "client_disconnected": client_disconnected,
            "saw_message_start": saw_message_start,
            "saw_message_stop": saw_message_stop,
            "failure_reason": reason,
        }

    def notify_stream_failure(reason: str) -> None:
        try:
            stream_failure_observer(stream_failure_metadata(reason))
        except Exception as exc:
            router_log("WARN", f"anthropic_stream_failure_observer_failed error={type(exc).__name__}")

    try:
        for raw in tracked_upstream_lines():
            line = raw.decode("utf-8", errors="ignore")
            stripped = line.rstrip("\r\n")
            if stripped == "":
                if pending_event_lines:
                    data_str = "\n".join(pending_event_lines)
                    process_event(pending_event_type, data_str)
                pending_event_type = None
                pending_event_lines = []
                if runaway_verdict is not None or upstream_error_forwarded:
                    # Stop reading upstream; the finally block closes the
                    # response so the loop stops being generated and billed.
                    break
                continue
            if stripped.startswith("event:"):
                pending_event_type = stripped[len("event:"):].strip() or None
                continue
            if stripped.startswith("data:"):
                pending_event_lines.append(stripped[len("data:"):].lstrip())
                continue
        reading_upstream = False
        process_tail_event()
        if upstream_error_forwarded:
            failure_reason = "anthropic_stream_upstream_error_event"
            router_log("WARN", f"anthropic_sse_upstream_error_event provider={provider} model={model} {stream_state()}")
        elif runaway_verdict is not None:
            flush_received_text()
            for index in list(suppressed_thinking_blocks.keys()):
                finish_suppressed_thinking_block(index)
            emit_runaway_stop()
            stream_success = True
        elif saw_message_stop:
            finish_completed_stream()
        else:
            router_log("WARN", f"anthropic_sse_upstream_incomplete provider={provider} model={model} {stream_state()}")
            report_incomplete_stream(
                "Upstream Anthropic stream ended before message_stop.",
                "anthropic_stream_upstream_incomplete",
            )
    except ClientStreamDisconnected as exc:
        client_disconnected = True
        failure_reason = failure_reason or "anthropic_stream_client_disconnected"
        router_log(
            "WARN",
            f"anthropic_sse_client_disconnected model={model} "
            f"text_len={len(text_so_far)} emitted_tool_use={emitted_tool_use} "
            f"suppressed_blocks={len(suppressed_thinking_passback_blocks) + len(suppressed_thinking_blocks)} "
            f"error={exc}",
        )
    except Exception as exc:
        source = "upstream" if reading_upstream else "relay"
        upstream_read_failed = source == "upstream"
        try:
            if ending_started:
                # Part of an ending is already downstream; anything more would
                # duplicate or contradict it.
                failure_reason = failure_reason or f"anthropic_stream_{source}_error:{type(exc).__name__}"
                router_log(
                    "ERROR",
                    f"anthropic_sse_forward_error provider={provider} model={model} source={source} "
                    f"stage=ending {stream_state()} error={type(exc).__name__}: {exc}",
                )
            else:
                if source == "upstream":
                    process_tail_event()
                if upstream_error_forwarded:
                    failure_reason = "anthropic_stream_upstream_error_event"
                    router_log(
                        "WARN",
                        f"anthropic_sse_upstream_error_event provider={provider} model={model} {stream_state()} "
                        f"read_error={type(exc).__name__}",
                    )
                elif source == "upstream" and saw_message_stop:
                    # The upstream already finished the turn; a transport error
                    # afterwards does not undo a complete response.
                    router_log(
                        "WARN",
                        f"anthropic_sse_upstream_error_after_message_stop provider={provider} model={model} "
                        f"error={type(exc).__name__}: {exc}",
                    )
                    finish_completed_stream()
                else:
                    router_log(
                        "ERROR",
                        f"anthropic_sse_forward_error provider={provider} model={model} source={source} "
                        f"stage=before_message_stop {stream_state()} error={type(exc).__name__}: {exc}",
                    )
                    report_incomplete_stream(
                        (
                            f"Upstream Anthropic stream failed before message_stop ({type(exc).__name__})."
                            if source == "upstream"
                            else f"Ciel Runtime failed to relay the Anthropic stream ({type(exc).__name__})."
                        ),
                        f"anthropic_stream_{source}_error:{type(exc).__name__}",
                    )
        except ClientStreamDisconnected as emit_exc:
            client_disconnected = True
            failure_reason = failure_reason or "anthropic_stream_client_disconnected"
            router_log(
                "WARN",
                f"anthropic_sse_client_disconnected model={model} while_reporting={type(exc).__name__} error={emit_exc}",
            )
        except Exception as emit_exc:
            router_log("WARN", f"anthropic_stream_fallback_emit_failed error={type(emit_exc).__name__}: {emit_exc}")
    finally:
        reported_failure_reason: str | None = None
        if stream_success:
            mark_pending_channel_delivery_success(handler, "anthropic_stream_message_stop")
        else:
            reason = failure_reason or str(
                getattr(handler, "_ciel_runtime_channel_delivery_reason", "anthropic_stream_incomplete")
                or "anthropic_stream_incomplete"
            )
            mark_pending_channel_delivery_failed(handler, reason)
            reported_failure_reason = reason
        try:
            resp.close()
        except Exception as exc:
            router_log("WARN", f"anthropic_stream_response_close_failed error={type(exc).__name__}: {exc}")
        if reported_failure_reason is not None:
            notify_stream_failure(reported_failure_reason)


@dataclass(frozen=True, slots=True)
class OllamaStreamIO:
    UpstreamClientDisconnected: type[BaseException]
    VisibleThinkingMarkupFilter: type[Any]
    _split_word_buffer: Callable[..., Any]
    estimate_tokens: Callable[..., Any]
    iter_upstream_lines_until_client_disconnect: Callable[..., Any]
    mark_pending_channel_delivery_failed: Callable[..., Any]
    mark_pending_channel_delivery_success: Callable[..., Any]
    router_log: Callable[..., Any]
    write_router_activity: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaStreamTrace:
    dump_response_for_trace: Callable[..., Any]
    finish_outgoing_sse_trace: Callable[..., Any]
    make_outgoing_sse_trace: Callable[..., Any]
    record_outgoing_sse_event: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaToolProjection:
    _remember_channel_injected_tool_use: Callable[..., Any]
    _validate_and_fix_tool_input: Callable[..., Any]
    append_tool_call_log: Callable[..., Any]
    cap_mcp_notification_wait_tool_input: Callable[..., Any]
    normalize_tool_arguments: Callable[..., Any]
    plan_mode_tool_name_for_emit: Callable[..., Any]
    resolve_emitted_tool_name: Callable[..., Any]
    should_drop_duplicate_side_effect_tool_call: Callable[..., Any]
    should_drop_emitted_tool_call: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaContinuationPolicy:
    empty_end_turn_notice_for_body: Callable[..., Any]
    reasoning_only_notice: Callable[..., Any]
    should_auto_continue_choice_question_with_tasklist: Callable[..., Any]
    should_auto_enter_plan_mode: Callable[..., Any]
    should_keep_work_alive_with_tasklist: Callable[..., Any]
    should_recover_empty_end_turn_with_tasklist: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaStreamServices:
    io: OllamaStreamIO
    trace: OllamaStreamTrace
    tool_projection: OllamaToolProjection
    continuation: OllamaContinuationPolicy


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
    semantic_retries: int = 0,
    reopen_semantic: Callable[[int, str], Any] | None = None,
) -> None:
    """Stream Ollama NDJSON /api/chat response as Anthropic SSE /v1/messages format."""

    from .ollama_semantic_retry import exhausted_notice, retry_reason

    remote_bridge = is_remote_bridge_request(handler)
    UpstreamClientDisconnected = services.io.UpstreamClientDisconnected
    VisibleThinkingMarkupFilter = services.io.VisibleThinkingMarkupFilter
    _split_word_buffer = services.io._split_word_buffer
    estimate_tokens = services.io.estimate_tokens
    iter_upstream_lines_until_client_disconnect = services.io.iter_upstream_lines_until_client_disconnect
    mark_pending_channel_delivery_failed = services.io.mark_pending_channel_delivery_failed
    mark_pending_channel_delivery_success = services.io.mark_pending_channel_delivery_success
    router_log = services.io.router_log
    write_router_activity = services.io.write_router_activity
    dump_response_for_trace = services.trace.dump_response_for_trace
    finish_outgoing_sse_trace = services.trace.finish_outgoing_sse_trace
    make_outgoing_sse_trace = services.trace.make_outgoing_sse_trace
    record_outgoing_sse_event = services.trace.record_outgoing_sse_event
    _remember_channel_injected_tool_use = services.tool_projection._remember_channel_injected_tool_use
    _validate_and_fix_tool_input = services.tool_projection._validate_and_fix_tool_input
    append_tool_call_log = services.tool_projection.append_tool_call_log
    cap_mcp_notification_wait_tool_input = services.tool_projection.cap_mcp_notification_wait_tool_input
    normalize_tool_arguments = services.tool_projection.normalize_tool_arguments
    plan_mode_tool_name_for_emit = services.tool_projection.plan_mode_tool_name_for_emit
    resolve_emitted_tool_name = services.tool_projection.resolve_emitted_tool_name
    should_drop_duplicate_side_effect_tool_call = services.tool_projection.should_drop_duplicate_side_effect_tool_call
    should_drop_emitted_tool_call = services.tool_projection.should_drop_emitted_tool_call
    empty_end_turn_notice_for_body = services.continuation.empty_end_turn_notice_for_body
    reasoning_only_notice = services.continuation.reasoning_only_notice
    should_auto_continue_choice_question_with_tasklist = services.continuation.should_auto_continue_choice_question_with_tasklist
    should_auto_enter_plan_mode = services.continuation.should_auto_enter_plan_mode
    should_keep_work_alive_with_tasklist = services.continuation.should_keep_work_alive_with_tasklist
    should_recover_empty_end_turn_with_tasklist = services.continuation.should_recover_empty_end_turn_with_tasklist
    if remote_bridge:
        mark_pending_channel_delivery_failed = _remote_noop
        mark_pending_channel_delivery_success = _remote_noop
        _remember_channel_injected_tool_use = _remote_noop
        append_tool_call_log = _remote_noop
        plan_mode_tool_name_for_emit = _remote_tool_name
        _validate_and_fix_tool_input = _remote_tool_validation
        should_drop_duplicate_side_effect_tool_call = _remote_false
        should_drop_emitted_tool_call = _remote_false
        should_auto_continue_choice_question_with_tasklist = _remote_false
        should_auto_enter_plan_mode = _remote_false
        should_keep_work_alive_with_tasklist = _remote_false
        should_recover_empty_end_turn_with_tasklist = _remote_false
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache")
    handler.send_header("connection", "close")
    handler.end_headers()
    msg_id = f"msg_ollama_{int(time.time() * 1000)}"
    started = False
    thinking_started = False
    text_started = False
    text_suppressed_for_plan = False
    next_content_index = 0
    text_index: int | None = None
    text_block_open = False
    thinking_index: int | None = None
    thinking_block_open = False
    thinking_block_text = ""
    thinking_so_far = ""
    text_so_far = ""
    text_buffer = ""
    tool_calls: list[dict[str, Any]] = []
    tool_indices: list[int] = []
    stopped_tool_indices: set[int] = set()
    input_tokens = estimate_tokens(source_body) if isinstance(source_body, dict) else 0
    output_tokens = 0
    chunk: dict[str, Any] = {}
    chunks_seen = 0
    last_activity_update = 0.0
    thinking_markup_filter = VisibleThinkingMarkupFilter()
    thinking_markup_suppressed = False
    repeated_completed_tool_dropped = False
    runaway_policy = _stream_runaway_policy(handler)
    text_runaway = RunawayOutputDetector(runaway_policy)
    thinking_runaway = RunawayOutputDetector(runaway_policy)
    runaway_verdict: RunawayVerdict | None = None
    sse_trace = make_outgoing_sse_trace(provider, model, "ollama_stream", source_body)
    sse_trace_outcome = "started"
    sse_trace_error: str | None = None
    terminal_received = False
    try:
        semantic_retries = max(0, min(2, int(semantic_retries)))
    except (TypeError, ValueError):
        semantic_retries = 0
    semantic_attempt = 0
    semantic_failed = False
    semantic_enabled = bool(not remote_bridge and source_body is not None
                            and reopen_semantic is not None and semantic_retries > 0)
    usage_input = 0
    usage_output = 0

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

    def emit_tool_block(index: int, tool_id: str, name: str, tool_input: dict[str, Any]) -> None:
        emit(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": {},
                },
            },
        )
        emit(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input, ensure_ascii=False),
                },
            },
        )
        emit("content_block_stop", {"type": "content_block_stop", "index": index})
        stopped_tool_indices.add(index)

    def open_text_block() -> int:
        nonlocal next_content_index, text_block_open, text_index, text_started
        if text_block_open and text_index is not None:
            return text_index
        text_index = next_content_index
        next_content_index += 1
        text_started = True
        text_block_open = True
        emit(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": text_index,
                "content_block": {"type": "text", "text": ""},
            },
        )
        return text_index

    def close_text_block() -> None:
        nonlocal text_block_open, text_buffer
        if not text_block_open or text_index is None:
            return
        if word_chunking and text_buffer:
            to_flush, text_buffer = _split_word_buffer(text_buffer, force=True)
            if to_flush:
                emit(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_index,
                        "delta": {"type": "text_delta", "text": to_flush},
                    },
                )
        emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
        text_block_open = False

    def open_thinking_block() -> int:
        nonlocal next_content_index, thinking_block_open, thinking_block_text
        nonlocal thinking_index, thinking_started
        if thinking_block_open and thinking_index is not None:
            return thinking_index
        thinking_index = next_content_index
        next_content_index += 1
        thinking_started = True
        thinking_block_open = True
        thinking_block_text = ""
        emit(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": thinking_index,
                "content_block": {"type": "thinking", "thinking": ""},
            },
        )
        return thinking_index

    def close_thinking_block() -> None:
        nonlocal thinking_block_open
        if not thinking_block_open or thinking_index is None:
            return
        digest = hashlib.sha256(
            thinking_block_text.encode("utf-8", errors="replace")
        ).hexdigest()[:24]
        emit(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": thinking_index,
                "delta": {
                    "type": "signature_delta",
                    "signature": f"ciel-runtime-ollama-thinking-{digest}",
                },
            },
        )
        emit("content_block_stop", {"type": "content_block_stop", "index": thinking_index})
        thinking_block_open = False

    def update_stream_activity(force: bool = False) -> None:
        nonlocal last_activity_update
        now = time.time()
        if not force and now - last_activity_update < 0.5:
            return
        last_activity_update = now
        estimated_output = output_tokens or max(
            0, (len(thinking_so_far) + len(text_so_far)) // 4
        )
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
        nonlocal text_buffer, text_so_far, text_suppressed_for_plan, runaway_verdict
        if not text_chunk:
            return
        if runaway_verdict is None:
            runaway_verdict = text_runaway.feed(text_chunk)
        close_thinking_block()
        if source_body is not None and not text_started and not tool_calls and should_auto_enter_plan_mode(source_body, text_so_far + text_chunk, []):
            text_so_far += text_chunk
            text_suppressed_for_plan = True
            return
        if text_suppressed_for_plan and not text_started and text_so_far:
            pending_text = text_so_far + text_chunk
            text_so_far = pending_text
            text_suppressed_for_plan = False
            active_text_index = open_text_block()
            if word_chunking:
                text_buffer += pending_text
                to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
                if to_flush:
                    event = {
                        "type": "content_block_delta",
                        "index": active_text_index,
                        "delta": {"type": "text_delta", "text": to_flush},
                    }
                    emit("content_block_delta", event)
            else:
                event = {
                    "type": "content_block_delta",
                    "index": active_text_index,
                    "delta": {"type": "text_delta", "text": pending_text},
                }
                emit("content_block_delta", event)
            update_stream_activity()
            return
        active_text_index = open_text_block()
        text_so_far += text_chunk
        if word_chunking:
            text_buffer += text_chunk
            to_flush, text_buffer = _split_word_buffer(text_buffer, force=False)
            if to_flush:
                event = {
                    "type": "content_block_delta",
                    "index": active_text_index,
                    "delta": {"type": "text_delta", "text": to_flush},
                }
                emit("content_block_delta", event)
        else:
            event = {
                "type": "content_block_delta",
                "index": active_text_index,
                "delta": {"type": "text_delta", "text": text_chunk},
            }
            emit("content_block_delta", event)
        update_stream_activity()

    def handle_thinking_chunk(thinking_chunk: str) -> None:
        nonlocal thinking_block_text, thinking_so_far, runaway_verdict
        if not thinking_chunk:
            return
        if runaway_verdict is None:
            runaway_verdict = thinking_runaway.feed(thinking_chunk)
        close_text_block()
        active_thinking_index = open_thinking_block()
        thinking_block_text += thinking_chunk
        thinking_so_far += thinking_chunk
        emit(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": active_thinking_index,
                "delta": {"type": "thinking_delta", "thinking": thinking_chunk},
            },
        )
        update_stream_activity()

    try:
        while True:
            terminal_received = False
            chunk = {}
            attempt_invalid = False
            attempt_tool_intent = False
            attempt_input = 0
            attempt_output = 0
            estimated_input = estimate_tokens(source_body) if isinstance(source_body, dict) else 0
            attempt_thinking_start = len(thinking_so_far)
            for line in iter_upstream_lines_until_client_disconnect(handler, resp, idle_timeout):
                chunks_seen += 1
                line = line.decode(
                    "utf-8", errors="strict" if remote_bridge else "ignore"
                ).strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception as exc:
                    attempt_invalid = True
                    if remote_bridge:
                        raise RuntimeError(
                            "invalid upstream Ollama stream payload"
                        ) from exc
                    continue
                if not isinstance(chunk, dict):
                    attempt_invalid = True
                    chunk = {}
                    if remote_bridge:
                        raise RuntimeError(
                            "invalid upstream Ollama stream payload"
                        )
                    continue
                if chunk.get("error") is not None:
                    attempt_invalid = True
                if remote_bridge and chunk.get("error") is not None:
                    raise RuntimeError("upstream Ollama stream reported an error")
                if chunk.get("done") is True:
                    terminal_received = True
                raw_message = chunk.get("message")
                if remote_bridge and not isinstance(raw_message, dict):
                    raise RuntimeError(
                        "upstream Ollama stream payload requires a message object"
                    )
                if not isinstance(raw_message, dict):
                    attempt_invalid = True
                message = raw_message if isinstance(raw_message, dict) else {}
                attempt_input = max(attempt_input, int(chunk.get("prompt_eval_count") or 0))
                attempt_output = max(attempt_output, int(chunk.get("eval_count") or 0))
                input_tokens = usage_input + (attempt_input or estimated_input)
                output_tokens = usage_output + attempt_output
                if not started:
                    ensure_message_started()
                # Native Ollama thinking is a distinct field, not visible answer text.
                handle_thinking_chunk(str(message.get("thinking") or ""))
                # Handle text content
                raw_text_chunk = str(message.get("content") or "")
                if any(marker in raw_text_chunk.lower() for marker in
                       ("<tool_call", "<function", "[tool_call", "<invoke")):
                    attempt_tool_intent = True
                text_chunk = thinking_markup_filter.feed(raw_text_chunk)
                if text_chunk != raw_text_chunk:
                    thinking_markup_suppressed = True
                if text_chunk:
                    handle_text_chunk(text_chunk)
                # Handle tool calls
                raw_tool_calls = message.get("tool_calls")
                if raw_tool_calls is not None and raw_tool_calls != []:
                    attempt_tool_intent = True
                if (
                    remote_bridge
                    and raw_tool_calls is not None
                    and not isinstance(raw_tool_calls, list)
                ):
                    raise RuntimeError(
                        "upstream Ollama tool_calls must be an array"
                    )
                for call in raw_tool_calls or []:
                    if not isinstance(call, dict):
                        if remote_bridge:
                            raise RuntimeError(
                                "upstream Ollama tool call must be an object"
                            )
                        continue
                    raw_function = call.get("function")
                    if remote_bridge and not isinstance(raw_function, dict):
                        raise RuntimeError(
                            "upstream Ollama tool call requires a function object"
                        )
                    fn = raw_function if isinstance(raw_function, dict) else {}
                    raw_function_name = fn.get("name")
                    if remote_bridge and (
                        not isinstance(raw_function_name, str)
                        or not raw_function_name.strip()
                    ):
                        raise RuntimeError(
                            "upstream Ollama tool call requires a function name"
                        )
                    if not fn.get("name"):
                        continue
                    raw_name = str(fn["name"])
                    matched_name = resolve_emitted_tool_name(raw_name, source_body)
                    raw_args = fn.get("arguments")
                    if remote_bridge and not isinstance(raw_args, dict):
                        raise RuntimeError(
                            "upstream Ollama tool call arguments must be an object"
                        )
                    normalized_args = (
                        dict(raw_args)
                        if remote_bridge
                        else normalize_tool_arguments(matched_name, raw_args)
                    )
                    emitted_name = matched_name
                    fixed_input = _validate_and_fix_tool_input(
                        matched_name, normalized_args, source_body
                    )
                    if source_body is not None:
                        matched_name, fixed_input = plan_mode_tool_name_for_emit(source_body, matched_name, fixed_input)
                        if matched_name is None:
                            continue
                    fixed_input = cap_mcp_notification_wait_tool_input(matched_name, fixed_input)
                    supplied_input = normalized_args if matched_name == emitted_name else None
                    if should_drop_emitted_tool_call(matched_name, fixed_input, raw_name, source_body, supplied_input):
                        continue
                    if should_drop_duplicate_side_effect_tool_call(matched_name, fixed_input, raw_name, source_body):
                        repeated_completed_tool_dropped = True
                        continue
                    close_thinking_block()
                    close_text_block()
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
                            "tool_id": tool_id,
                            "raw_name": raw_name,
                            "matched_name": matched_name,
                            "raw_arguments": raw_args,
                            "normalized_arguments": normalized_args,
                            "emitted_input": fixed_input,
                            "sse_index": tool_index,
                        },
                    )
                    emit_tool_block(tool_index, tool_id, matched_name, fixed_input)
                    update_stream_activity()
                update_stream_activity()
                if runaway_verdict is not None:
                    # Stop reading upstream. The finally block closes the response,
                    # which ends generation instead of paying for the whole loop.
                    router_log(
                        "WARN",
                        f"ollama_stream_runaway_repetition provider={provider} model={model} "
                        f"chunks={chunks_seen} {runaway_verdict.log_fields()}",
                    )
                    write_router_activity(
                        "error", provider, model, error="runaway_repetition", stream=True
                    )
                    break
                if terminal_received:
                    break
            if remote_bridge and not terminal_received:
                raise RuntimeError(
                    "upstream Ollama stream ended before a terminal event"
                )
            runaway_stopped = runaway_verdict is not None
            trailing_text = thinking_markup_filter.finish()
            if trailing_text and not runaway_stopped:
                handle_text_chunk(trailing_text)
            if thinking_markup_suppressed:
                router_log("WARN", f"suppressed visible Ollama thinking markup from stream model={model}")
            update_stream_activity(force=True)
            close_thinking_block()
            close_text_block()
            reason = retry_reason(
                terminal_received=terminal_received,
                done_reason=str(chunk.get("done_reason") or ""),
                text=text_so_far,
                thinking=thinking_so_far[attempt_thinking_start:],
                tool_intent=attempt_tool_intent or bool(tool_calls),
                invalid=attempt_invalid,
                runaway=runaway_stopped,
            )
            usage_input += attempt_input
            usage_output += attempt_output
            input_tokens = usage_input or estimated_input
            output_tokens = usage_output
            if not semantic_enabled or not reason:
                break
            if semantic_attempt >= semantic_retries:
                semantic_failed = True
                break
            semantic_attempt += 1
            router_log("WARN", f"ollama_semantic_retry provider={provider} model={model} "
                       f"attempt={semantic_attempt}/{semantic_retries} reason={reason}")
            write_router_activity("retry", provider, model, reason=reason,
                                  attempt=semantic_attempt, total=semantic_retries, stream=True)
            resp.close()
            try:
                resp = reopen_semantic(semantic_attempt, reason)
            except UpstreamClientDisconnected:
                raise
            except Exception as exc:
                semantic_failed = True
                router_log("WARN", f"ollama_semantic_retry_open_failed provider={provider} "
                           f"model={model} error={type(exc).__name__}")
                break
            # Preserve emitted blocks and indices; discard only per-attempt filters.
            text_so_far = ""
            text_buffer = ""
            text_suppressed_for_plan = False
            thinking_markup_filter = VisibleThinkingMarkupFilter()
            text_runaway = RunawayOutputDetector(runaway_policy)
            thinking_runaway = RunawayOutputDetector(runaway_policy)
        if not runaway_stopped and repeated_completed_tool_dropped and not text_so_far.strip() and not tool_calls:
            handle_text_chunk(
                "[ciel-runtime] Stopped an identical completed tool call from repeating. "
                "The previous result is already in context; choose a different action or finish the turn."
            )
        reasoning_only = thinking_started and not text_so_far.strip() and not tool_calls and not runaway_stopped
        if semantic_failed:
            reasoning_only = False
        if remote_bridge and (reasoning_only or (not text_started and not tool_calls)):
            raise RuntimeError(
                "upstream Ollama stream completed without assistant content or a tool call"
            )
        # A looping turn must not be continued for the model. Every synthesis
        # below exists to keep work moving, which is the opposite of what a
        # runaway needs, so they are all skipped once the guard has fired.
        # Flush any remaining buffered text when word-chunking is active
        if not semantic_failed and not runaway_stopped and not reasoning_only and source_body is not None and should_auto_enter_plan_mode(source_body, text_so_far, tool_calls):
            ensure_message_started()
            close_text_block()
            router_log("WARN", "auto-synthesized EnterPlanMode from short/empty upstream stream")
            tool_calls.append({"function": {"name": "EnterPlanMode", "arguments": {}}})
            tool_id = f"toolu_ollama_plan_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            emit_tool_block(tool_index, tool_id, "EnterPlanMode", {})
        elif not semantic_failed and not runaway_stopped and not reasoning_only and source_body is not None and should_recover_empty_end_turn_with_tasklist(source_body, text_so_far, tool_calls):
            ensure_message_started()
            close_text_block()
            router_log("WARN", "auto-synthesized TaskList from empty upstream end_turn stream")
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            tool_id = f"toolu_ollama_empty_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            emit_tool_block(tool_index, tool_id, "TaskList", {})
        elif not semantic_failed and text_suppressed_for_plan and not text_started and text_so_far:
            active_text_index = open_text_block()
            event = {
                "type": "content_block_delta",
                "index": active_text_index,
                "delta": {"type": "text_delta", "text": text_so_far},
            }
            emit("content_block_delta", event)
        if not semantic_failed and not runaway_stopped and not reasoning_only and source_body is not None and should_keep_work_alive_with_tasklist(source_body, text_so_far, tool_calls):
            ensure_message_started()
            close_text_block()
            router_log("WARN", "auto-synthesized TaskList to keep work moving after tool result stream")
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            tool_id = f"toolu_ollama_keepalive_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            emit_tool_block(tool_index, tool_id, "TaskList", {})
        if not semantic_failed and not runaway_stopped and not reasoning_only and source_body is not None and should_auto_continue_choice_question_with_tasklist(source_body, text_so_far, tool_calls):
            ensure_message_started()
            close_text_block()
            router_log("WARN", "auto-synthesized TaskList after clarification question stream")
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            tool_id = f"toolu_ollama_choice_{int(time.time() * 1000)}"
            tool_index = next_content_index
            next_content_index += 1
            tool_indices.append(tool_index)
            emit_tool_block(tool_index, tool_id, "TaskList", {})
        # Send content_block_stop for text if any
        close_text_block()
        # Send content_block_stop for each tool call
        for tool_index in tool_indices:
            if tool_index in stopped_tool_indices:
                continue
            event = {"type": "content_block_stop", "index": tool_index}
            emit("content_block_stop", event)
            stopped_tool_indices.add(tool_index)
        if not started:
            ensure_message_started()
        if runaway_stopped and runaway_verdict is not None:
            notice = runaway_verdict.notice(STOPPED)
            text_so_far += notice
            notice_index = next_content_index
            next_content_index += 1
            emit_text_block(notice_index, notice)
            # Cutting the loop should not cost the user the turn. Hand the agent
            # a tool call so the CLI comes back for a fresh one -- unless a
            # recent turn already carries this notice, which would mean the
            # recovery itself is looping.
            if (
                not tool_calls
                and runaway_policy.recover
                and recent_runaway_notices(source_body) == 0
            ):
                router_log("WARN", f"ollama_stream_runaway_recovery provider={provider} model={model}")
                tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
                tool_id = f"toolu_ollama_runaway_{int(time.time() * 1000)}"
                tool_index = next_content_index
                next_content_index += 1
                tool_indices.append(tool_index)
                emit_tool_block(tool_index, tool_id, "TaskList", {})
        elif semantic_failed:
            notice = exhausted_notice(semantic_attempt + 1)
            text_so_far = notice
            notice_index = next_content_index
            next_content_index += 1
            emit_text_block(notice_index, notice)
            router_log("WARN", f"ollama_semantic_exhausted provider={provider} model={model} "
                       f"attempts={semantic_attempt + 1}")
        elif reasoning_only:
            router_log(
                "WARN",
                f"ollama_reasoning_only_stream provider={provider} model={model} "
                f"chunks={chunks_seen} thinking_chars={len(thinking_so_far)} "
                f"done_reason={str(chunk.get('done_reason') or '-')}",
            )
            notice = reasoning_only_notice(str(chunk.get("done_reason") or ""))
            text_so_far = notice
            notice_index = next_content_index
            next_content_index += 1
            emit_text_block(notice_index, notice)
        elif not text_so_far.strip() and not tool_indices:
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
        if runaway_stopped and not tool_calls:
            # Claude Code treats max_tokens as a continuation request. Once a
            # recovery has already looped, this is a deliberate terminal stop.
            stop_reason = "end_turn"
        # Send message_delta with final stop_reason
        event = {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        }
        emit("message_delta", event)
        # Send message_stop
        emit("message_stop", {"type": "message_stop"})
        sse_trace_outcome = "exhausted" if semantic_failed else "success"
        if semantic_failed:
            # The input reached the model; only its output was empty. Keep the
            # delivery confirmed as before so the channel cursor commits and the
            # same external message is not injected again on a later request.
            write_router_activity("error", provider, model, error="ollama_semantic_exhausted",
                                  attempts=semantic_attempt + 1, stream=True,
                                  tokens=input_tokens, output_tokens=output_tokens)
        elif semantic_attempt:
            router_log("INFO", f"ollama_semantic_recovered provider={provider} model={model} "
                       f"attempts={semantic_attempt + 1}")
        if not semantic_failed and (thinking_started or text_started or tool_indices):
            write_router_activity(
                "success",
                provider,
                model,
                tokens=input_tokens,
                output_tokens=output_tokens
                or max(1, (len(thinking_so_far) + len(text_so_far)) // 4),
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
            f"chunks={chunks_seen} thinking_len={len(thinking_so_far)} "
            f"text_len={len(text_so_far)} error={exc}",
        )
        write_router_activity(
            "cancel",
            provider,
            model,
            error=type(exc).__name__,
            tokens=input_tokens,
            output_tokens=output_tokens
            or max(0, (len(thinking_so_far) + len(text_so_far)) // 4),
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
            if remote_bridge:
                if thinking_block_open:
                    close_thinking_block()
                if text_block_open:
                    close_text_block()
                for tool_index in tool_indices:
                    if tool_index not in stopped_tool_indices:
                        emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
                        stopped_tool_indices.add(tool_index)
                emit(
                    "error",
                    {
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": "Upstream Ollama stream failed before completion.",
                        },
                    },
                )
            else:
                ensure_message_started()
                if thinking_block_open:
                    close_thinking_block()
                if text_block_open:
                    close_text_block()
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
        except Exception as exc:
            router_log("WARN", f"ollama_stream_fallback_emit_failed model={model} error={type(exc).__name__}: {exc}")
    finally:
        try:
            resp.close()
        except Exception as exc:
            router_log("WARN", f"ollama_stream_response_close_failed model={model} error={type(exc).__name__}: {exc}")
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
        except Exception as exc:
            router_log("WARN", f"ollama_stream_trace_finalize_failed model={model} error={type(exc).__name__}: {exc}")


@dataclass(frozen=True, slots=True)
class OpenAIChatStreamIO:
    PSEUDO_TOOL_END: str
    PSEUDO_TOOL_START: str
    _split_word_buffer: Callable[..., Any]
    positive_int: Callable[..., Any]
    router_log: Callable[..., Any]
    write_anthropic_open_stream_stop: Callable[..., Any]
    write_router_activity: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIChatToolProjection:
    _remember_channel_injected_tool_use: Callable[..., Any]
    _validate_and_fix_tool_input: Callable[..., Any]
    append_tool_call_log: Callable[..., Any]
    cap_mcp_notification_wait_tool_input: Callable[..., Any]
    normalize_tool_arguments: Callable[..., Any]
    parse_pseudo_tool_calls: Callable[..., Any]
    plan_mode_tool_name_for_emit: Callable[..., Any]
    resolve_emitted_tool_name: Callable[..., Any]
    should_drop_duplicate_side_effect_tool_call: Callable[..., Any]
    should_drop_emitted_tool_call: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIChatContinuationPolicy:
    empty_end_turn_notice_for_body: Callable[..., Any]
    latest_user_tool_result_names: Callable[..., Any]
    should_auto_continue_choice_question_with_tasklist: Callable[..., Any]
    should_auto_enter_plan_mode: Callable[..., Any]
    should_keep_work_alive_with_tasklist: Callable[..., Any]
    should_recover_empty_end_turn_with_tasklist: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIChatStreamServices:
    io: OpenAIChatStreamIO
    tool_projection: OpenAIChatToolProjection
    continuation: OpenAIChatContinuationPolicy


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

    remote_bridge = is_remote_bridge_request(handler)
    PSEUDO_TOOL_END = services.io.PSEUDO_TOOL_END
    PSEUDO_TOOL_START = services.io.PSEUDO_TOOL_START
    _split_word_buffer = services.io._split_word_buffer
    positive_int = services.io.positive_int
    router_log = services.io.router_log
    write_anthropic_open_stream_stop = services.io.write_anthropic_open_stream_stop
    write_router_activity = services.io.write_router_activity
    _remember_channel_injected_tool_use = services.tool_projection._remember_channel_injected_tool_use
    _validate_and_fix_tool_input = services.tool_projection._validate_and_fix_tool_input
    append_tool_call_log = services.tool_projection.append_tool_call_log
    cap_mcp_notification_wait_tool_input = services.tool_projection.cap_mcp_notification_wait_tool_input
    normalize_tool_arguments = services.tool_projection.normalize_tool_arguments
    parse_pseudo_tool_calls = services.tool_projection.parse_pseudo_tool_calls
    plan_mode_tool_name_for_emit = services.tool_projection.plan_mode_tool_name_for_emit
    resolve_emitted_tool_name = services.tool_projection.resolve_emitted_tool_name
    should_drop_duplicate_side_effect_tool_call = services.tool_projection.should_drop_duplicate_side_effect_tool_call
    should_drop_emitted_tool_call = services.tool_projection.should_drop_emitted_tool_call
    empty_end_turn_notice_for_body = services.continuation.empty_end_turn_notice_for_body
    latest_user_tool_result_names = services.continuation.latest_user_tool_result_names
    should_auto_continue_choice_question_with_tasklist = services.continuation.should_auto_continue_choice_question_with_tasklist
    should_auto_enter_plan_mode = services.continuation.should_auto_enter_plan_mode
    should_keep_work_alive_with_tasklist = services.continuation.should_keep_work_alive_with_tasklist
    should_recover_empty_end_turn_with_tasklist = services.continuation.should_recover_empty_end_turn_with_tasklist
    if remote_bridge:
        _remember_channel_injected_tool_use = _remote_noop
        append_tool_call_log = _remote_noop
        plan_mode_tool_name_for_emit = _remote_tool_name
        _validate_and_fix_tool_input = _remote_tool_validation
        should_drop_duplicate_side_effect_tool_call = _remote_false
        should_drop_emitted_tool_call = _remote_false
        latest_user_tool_result_names = _remote_empty_list
        should_auto_continue_choice_question_with_tasklist = _remote_false
        should_auto_enter_plan_mode = _remote_false
        should_keep_work_alive_with_tasklist = _remote_false
        should_recover_empty_end_turn_with_tasklist = _remote_false
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
    reported_input_tokens = max(0, int(input_tokens or 0))
    cache_read_tokens = 0
    cache_creation_tokens = 0
    finish_reason = "stop"
    terminal_received = False
    chunks_seen = 0
    last_activity_update = 0.0
    runaway_policy = _stream_runaway_policy(handler)
    text_runaway = RunawayOutputDetector(runaway_policy)
    reasoning_runaway = RunawayOutputDetector(runaway_policy)
    runaway_verdict: RunawayVerdict | None = None
    runaway_stopped = False

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
        for line in _iter_openai_chat_sse_data(
            resp, strict_utf8=remote_bridge
        ):
            chunks_seen += 1
            line = line.strip()
            if not line:
                continue
            if line == "[DONE]":
                if not remote_bridge:
                    terminal_received = True
                break
            try:
                event = json.loads(line)
            except Exception as exc:
                if remote_bridge:
                    raise RuntimeError(
                        "invalid upstream OpenAI Chat stream payload"
                    ) from exc
                continue
            if not isinstance(event, dict):
                if remote_bridge:
                    raise RuntimeError(
                        "invalid upstream OpenAI Chat stream payload"
                    )
                continue
            if remote_bridge and (
                event.get("error") is not None
                or str(event.get("type") or "").lower() == "error"
            ):
                raise RuntimeError("upstream OpenAI Chat stream reported an error")
            usage = event.get("usage")
            if isinstance(usage, dict):
                output_tokens = max(output_tokens, positive_int(usage.get("completion_tokens")) or 0)
                prompt_tokens = positive_int(usage.get("prompt_tokens")) or 0
                details = usage.get("prompt_tokens_details")
                details = details if isinstance(details, dict) else {}
                cache_read_tokens = max(
                    cache_read_tokens,
                    positive_int(usage.get("prompt_cache_hit_tokens"))
                    or positive_int(usage.get("cache_read_input_tokens"))
                    or positive_int(details.get("cached_tokens"))
                    or 0,
                )
                cache_creation_tokens = max(
                    cache_creation_tokens,
                    positive_int(usage.get("cache_creation_input_tokens"))
                    or positive_int(details.get("cache_write_tokens"))
                    or 0,
                )
                if prompt_tokens:
                    reported_input_tokens = max(
                        0,
                        prompt_tokens - cache_read_tokens - cache_creation_tokens,
                    )
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0] if isinstance(choices[0], dict) else {}
            choice_usage = choice.get("usage")
            if isinstance(choice_usage, dict):
                output_tokens = max(
                    output_tokens,
                    positive_int(choice_usage.get("completion_tokens")) or 0,
                )
                prompt_tokens = positive_int(choice_usage.get("prompt_tokens")) or 0
                details = choice_usage.get("prompt_tokens_details")
                details = details if isinstance(details, dict) else {}
                cache_read_tokens = max(
                    cache_read_tokens,
                    positive_int(choice_usage.get("prompt_cache_hit_tokens"))
                    or positive_int(choice_usage.get("cache_read_input_tokens"))
                    or positive_int(details.get("cached_tokens"))
                    or 0,
                )
                if prompt_tokens:
                    reported_input_tokens = max(0, prompt_tokens - cache_read_tokens)
            if choice.get("finish_reason"):
                finish_reason = str(choice.get("finish_reason"))
                terminal_received = True
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            reasoning_chunk = delta.get("reasoning_content") or ""
            if reasoning_chunk:
                reasoning_so_far += str(reasoning_chunk)
                runaway_verdict = runaway_verdict or reasoning_runaway.feed(str(reasoning_chunk))
                emit_reasoning_delta(str(reasoning_chunk))
                update_stream_activity()
            text_chunk = delta.get("content") or ""
            if text_chunk:
                runaway_verdict = runaway_verdict or text_runaway.feed(str(text_chunk))
            if runaway_verdict is not None:
                # Stop reading upstream; the finally block closes the response.
                runaway_stopped = True
                router_log(
                    "WARN",
                    f"openai_stream_runaway_repetition provider={provider} model={model} "
                    f"chunks={chunks_seen} {runaway_verdict.log_fields()}",
                )
                break
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
            raw_tool_calls = delta.get("tool_calls")
            if (
                remote_bridge
                and raw_tool_calls is not None
                and not isinstance(raw_tool_calls, list)
            ):
                raise RuntimeError(
                    "upstream OpenAI Chat tool_calls must be an array"
                )
            for call in raw_tool_calls or []:
                if not isinstance(call, dict):
                    if remote_bridge:
                        raise RuntimeError(
                            "upstream OpenAI Chat tool call must be an object"
                        )
                    continue
                try:
                    call_index = int(call.get("index"))
                except Exception:
                    call_index = len(tool_fragments)
                slot = tool_fragments.setdefault(call_index, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    if remote_bridge and not isinstance(call.get("id"), str):
                        raise RuntimeError(
                            "upstream OpenAI Chat tool call id must be a string"
                        )
                    slot["id"] = str(call.get("id"))
                raw_function = call.get("function")
                if remote_bridge and not isinstance(raw_function, dict):
                    raise RuntimeError(
                        "upstream OpenAI Chat tool call requires a function object"
                    )
                fn = raw_function if isinstance(raw_function, dict) else {}
                if fn.get("name"):
                    if remote_bridge and not isinstance(fn.get("name"), str):
                        raise RuntimeError(
                            "upstream OpenAI Chat tool call name must be a string"
                        )
                    slot["name"] += str(fn.get("name"))
                if fn.get("arguments"):
                    if remote_bridge and not isinstance(fn.get("arguments"), str):
                        raise RuntimeError(
                            "upstream OpenAI Chat tool call arguments must be a string"
                        )
                    slot["arguments"] += str(fn.get("arguments"))
                update_stream_activity()
            if remote_bridge and terminal_received:
                break
        if remote_bridge and not terminal_received:
            raise RuntimeError(
                "upstream OpenAI Chat stream ended before a terminal event"
            )
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
        remote_tool_inputs: dict[int, dict[str, Any]] = {}
        if remote_bridge:
            if finish_reason == "content_filter":
                raise RuntimeError(
                    "upstream OpenAI Chat stream was stopped by content filtering"
                )
            if bool(tool_fragments) != (finish_reason == "tool_calls"):
                raise RuntimeError(
                    "upstream OpenAI Chat finish_reason is inconsistent with tool calls"
                )
            for fragment_index, fragment in sorted(tool_fragments.items()):
                if not str(fragment.get("id") or "").strip():
                    raise RuntimeError(
                        f"upstream OpenAI Chat tool call {fragment_index} requires an id"
                    )
                if not str(fragment.get("name") or "").strip():
                    raise RuntimeError(
                        "upstream OpenAI Chat tool call "
                        f"{fragment_index} requires a function name"
                    )
                try:
                    parsed_arguments = json.loads(
                        str(fragment.get("arguments") or "")
                    )
                except ValueError as exc:
                    raise RuntimeError(
                        "upstream OpenAI Chat tool call "
                        f"{fragment_index} contained malformed arguments"
                    ) from exc
                if not isinstance(parsed_arguments, dict):
                    raise RuntimeError(
                        "upstream OpenAI Chat tool call "
                        f"{fragment_index} arguments must be a JSON object"
                    )
                remote_tool_inputs[fragment_index] = parsed_arguments
            if not text_started and not tool_fragments:
                raise RuntimeError(
                    "upstream OpenAI Chat stream completed without assistant content or a tool call"
                )
        for fragment_index, fragment in sorted(tool_fragments.items()):
            raw_name = str(fragment.get("name") or "")
            if not raw_name:
                continue
            matched_name = resolve_emitted_tool_name(raw_name, source_body)
            normalized_args = (
                remote_tool_inputs[fragment_index]
                if remote_bridge
                else normalize_tool_arguments(
                    matched_name, fragment.get("arguments") or {}
                )
            )
            emitted_name = matched_name
            fixed_input = _validate_and_fix_tool_input(
                matched_name, normalized_args, source_body
            )
            if source_body is not None:
                matched_name, fixed_input = plan_mode_tool_name_for_emit(source_body, matched_name, fixed_input)
                if matched_name is None:
                    continue
            fixed_input = cap_mcp_notification_wait_tool_input(matched_name, fixed_input)
            supplied_input = normalized_args if matched_name == emitted_name else None
            if should_drop_emitted_tool_call(matched_name, fixed_input, raw_name, source_body, supplied_input):
                continue
            if should_drop_duplicate_side_effect_tool_call(matched_name, fixed_input, raw_name, source_body):
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
                    "tool_id": tool_id,
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

        if not runaway_stopped and source_body is not None and should_auto_enter_plan_mode(source_body, text_so_far, tool_calls):
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
        elif not runaway_stopped and source_body is not None and should_recover_empty_end_turn_with_tasklist(source_body, text_so_far, tool_calls):
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

        if not runaway_stopped and source_body is not None and should_keep_work_alive_with_tasklist(source_body, text_so_far, tool_calls):
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

        if not runaway_stopped and source_body is not None and should_auto_continue_choice_question_with_tasklist(source_body, text_so_far, tool_calls):
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

        if runaway_stopped and runaway_verdict is not None:
            notice = ("\n\n" if text_started else "") + runaway_verdict.notice(STOPPED)
            text_so_far += notice
            emit_text_delta(notice)
        if text_started and text_index is not None:
            emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
            text_stopped = True
        if (
            runaway_stopped
            and not tool_calls
            and runaway_policy.recover
            and recent_runaway_notices(source_body) == 0
        ):
            # Give the agent another turn instead of ending on a cut-off answer.
            router_log("WARN", f"openai_stream_runaway_recovery provider={provider} model={model}")
            tool_index = next_content_index
            next_content_index += 1
            tool_calls.append({"function": {"name": "TaskList", "arguments": {}}})
            emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": tool_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": f"toolu_openai_runaway_{int(time.time() * 1000)}",
                        "name": "TaskList",
                        "input": {},
                    },
                },
            )
            emit("content_block_delta", {"type": "content_block_delta", "index": tool_index, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
            emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
        if not runaway_stopped and not text_started and not tool_calls:
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
        if runaway_stopped and not tool_calls:
            # Claude Code treats max_tokens as a continuation request. Once a
            # recovery has already looped, this is a deliberate terminal stop.
            stop_reason = "end_turn"
        final_output_tokens = output_tokens or max(1, len(text_so_far) // 4)
        final_usage = {
            "input_tokens": reported_input_tokens,
            "output_tokens": final_output_tokens,
        }
        if cache_read_tokens:
            final_usage["cache_read_input_tokens"] = cache_read_tokens
        if cache_creation_tokens:
            final_usage["cache_creation_input_tokens"] = cache_creation_tokens
        write_anthropic_open_stream_stop(
            handler,
            {"stop_reason": stop_reason, "usage": final_usage},
        )
        write_router_activity(
            "success",
            provider,
            model,
            input_tokens=reported_input_tokens,
            output_tokens=final_output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            chunks=chunks_seen,
            stream=True,
        )
        return True
    except Exception as exc:
        router_log("ERROR", f"openai_stream_error provider={provider} model={model} error={type(exc).__name__}: {exc}")
        write_router_activity("error", provider, model, error=type(exc).__name__, stream=True)
        try:
            if word_chunking and text_buffer:
                to_flush, text_buffer = _split_word_buffer(text_buffer, force=True)
                emit_text_delta(to_flush)
            if not remote_bridge and not text_started:
                emit_text_delta(f"Upstream stream error: {type(exc).__name__}: {exc}")
            if reasoning_started and not reasoning_stopped:
                close_reasoning_block()
            if text_started and text_index is not None and not text_stopped:
                emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
                text_stopped = True
            if remote_bridge:
                emit(
                    "error",
                    {
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": "Upstream OpenAI Chat stream failed before completion.",
                        },
                    },
                )
            else:
                write_anthropic_open_stream_stop(
                    handler,
                    {"stop_reason": "end_turn", "usage": {"output_tokens": output_tokens or max(1, len(text_so_far) // 4)}},
                )
        except Exception as exc:
            router_log("WARN", f"openai_stream_fallback_emit_failed provider={provider} model={model} error={type(exc).__name__}: {exc}")
        return False
    finally:
        try:
            resp.close()
        except Exception as exc:
            router_log("WARN", f"openai_stream_response_close_failed provider={provider} model={model} error={type(exc).__name__}: {exc}")


__all__ = [
    "AnthropicContinuationPolicy",
    "AnthropicConversationContext",
    "AnthropicStreamIO",
    "AnthropicStreamServices",
    "AnthropicToolPolicy",
    "AnthropicToolProjection",
    "OllamaContinuationPolicy",
    "OllamaStreamIO",
    "OllamaStreamServices",
    "OllamaStreamTrace",
    "OllamaToolProjection",
    "OpenAIChatContinuationPolicy",
    "OpenAIChatStreamIO",
    "OpenAIChatStreamServices",
    "OpenAIChatToolProjection",
    "forward_openai_chat_to_anthropic_sse",
    "ollama_stream_to_anthropic_sse",
    "rebatch_anthropic_sse_text",
]
