from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import json
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class ChannelWakeTranscriptServices:
    claim_prompt: Callable[[int], str]
    prompt_references_message_id: Callable[..., bool]
    prompt_message_ids: Callable[[str], set[int]]
    now: Callable[[], float]


@dataclass(frozen=True, slots=True)
class WakeStateEvidence:
    state: str
    prompt_record: int | None = None
    completion_record: int | None = None
    record_type: str = ""
    session_id: str = ""
    timestamp: str = ""


@dataclass(frozen=True, slots=True)
class ChannelWakeStateReaderPorts:
    latest_transcript: Callable[[], Any]
    read_tail_text: Callable[[Any], str]
    wake_state_evidence_from_text: Callable[..., WakeStateEvidence]
    queued_age_from_text: Callable[..., float | None]
    stale_seconds: Callable[[], float]
    log: Callable[[str, str], None]


class ChannelWakeStateReader:
    def __init__(self, ports: ChannelWakeStateReaderPorts) -> None:
        self._ports = ports

    @staticmethod
    def message_id(message: dict[str, Any]) -> int:
        try:
            return int(message.get("id") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def prompt_candidates(
        message: dict[str, Any], prompt: str | None
    ) -> tuple[str, ...]:
        candidates = [prompt] if prompt else []
        body = str(
            message.get("message") if message.get("message") is not None else ""
        )
        if body:
            candidates.append(body)
        return tuple(candidates)

    def state(self, message_id: int) -> str:
        if message_id <= 0:
            return "completed"
        path, text = self._latest_text()
        if not text:
            return "unknown"
        evidence = self._ports.wake_state_evidence_from_text(message_id, text)
        self._log_evidence(path, message_id, evidence)
        return evidence.state

    @staticmethod
    def message_not_before(message: dict[str, Any]) -> float | None:
        """Evidence horizon: transcript records older than the message itself
        can never prove ITS delivery.  Bodies repeat verbatim (fixed templates,
        re-sent text), so an unanchored text match against an older turn
        silently completes a wake that never happened."""

        raw = message.get("created_at_epoch")
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw) - 5.0
        text = str(message.get("time") or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() - 5.0
        except (TypeError, ValueError):
            return None

    def state_for_message(
        self, message: dict[str, Any], prompt: str | None = None
    ) -> str:
        message_id = self.message_id(message)
        if message_id <= 0:
            return "completed"
        path, text = self._latest_text()
        if not text:
            return "unknown"
        evidence = self._ports.wake_state_evidence_from_text(
            message_id,
            text,
            self.prompt_candidates(message, prompt),
            not_before=self.message_not_before(message),
        )
        self._log_evidence(path, message_id, evidence)
        return evidence.state

    def queued_is_stale(
        self, message: dict[str, Any], prompt: str | None = None
    ) -> bool:
        message_id = self.message_id(message)
        if message_id <= 0:
            return False
        _path, text = self._latest_text()
        if not text:
            return False
        age = self._ports.queued_age_from_text(
            message_id,
            text,
            self.prompt_candidates(message, prompt),
            not_before=self.message_not_before(message),
        )
        return age is not None and age >= self._ports.stale_seconds()

    def _latest_text(self) -> tuple[Any, str]:
        path = self._ports.latest_transcript()
        return path, self._ports.read_tail_text(path) if path is not None else ""

    def _log_evidence(
        self, path: Any, message_id: int, evidence: WakeStateEvidence
    ) -> None:
        if evidence.state != "completed":
            return
        self._ports.log(
            "INFO",
            "channel_wake_completed_evidence "
            f"message_id={message_id} transcript={path} "
            f"prompt_record={evidence.prompt_record} "
            f"completion_record={evidence.completion_record} "
            f"record_type={evidence.record_type or '-'} "
            f"session_id={evidence.session_id or '-'} "
            f"timestamp={evidence.timestamp or '-'}",
        )


def record_timestamp_seconds(record: dict[str, Any]) -> float | None:
    raw = record.get("timestamp")
    if raw is None and isinstance(record.get("attachment"), dict):
        raw = record["attachment"].get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content", "input_text", "output_text", "message"):
            raw = value.get(key)
            if isinstance(raw, str):
                parts.append(raw)
            elif isinstance(raw, (dict, list)):
                nested = content_text(raw)
                if nested:
                    parts.append(nested)
        return "\n".join(parts)
    if isinstance(value, list):
        parts = []
        for item in value:
            nested = content_text(item)
            if nested:
                parts.append(nested)
        return "\n".join(parts)
    return ""


def user_text(record: dict[str, Any]) -> str:
    record_type = str(record.get("type") or "")
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    if record_type == "user" or str(message_obj.get("role") or "") == "user":
        return content_text(message_obj.get("content"))
    payload = record.get("payload")
    payload_obj = payload if isinstance(payload, dict) else {}
    payload_type = str(payload_obj.get("type") or "")
    payload_role = str(payload_obj.get("role") or "")
    if record_type == "response_item" and payload_type == "message" and payload_role == "user":
        return content_text(payload_obj.get("content"))
    if record_type == "event_msg" and payload_type == "user_message":
        return content_text(payload_obj.get("message"))
    return ""


_LOCAL_COMMAND_MARKERS = (
    "<command-name>",
    "<local-command-stdout>",
    "<local-command-caveat>",
)


def _is_local_command_echo(record: dict[str, Any]) -> bool:
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    text = content_text(message_obj.get("content")).lstrip()
    return text.startswith(("<command-name>", "<local-command-stdout>"))


def is_non_turn_user_record(record: dict[str, Any]) -> bool:
    """User-role bookkeeping that opens no model turn and types no prompt.

    Transcript-record twin of the request-side classifier
    conversation_turn_policy.user_intent_text_from_message (isMeta and
    state-metadata records are not user intent) — that discrimination was
    lost for the transcript layer when the console-wake projection was added.
    Claude Code persists compact-continuation summaries, meta caveats,
    display-only records, and local slash-command records as user records
    WITHOUT a following assistant response.  Treating them as turn-opening
    input stalls channel wakes forever after a /compact; treating their text
    as typed-prompt evidence falsely completes wakes (summaries echo past
    message bodies verbatim).
    """

    if (
        record.get("isCompactSummary") is True
        or record.get("isMeta") is True
        or record.get("isVisibleInTranscriptOnly") is True
    ):
        return True
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    text = content_text(message_obj.get("content")).lstrip()
    return text.startswith(_LOCAL_COMMAND_MARKERS)


def is_typed_user_prompt(record: dict[str, Any]) -> bool:
    """True only for user text that was actually TYPED into the CLI.

    Tool results are persisted as user-role records too; their content is tool
    OUTPUT (log greps, file dumps) that can echo any past message body or wake
    marker verbatim.  Counting them as delivery evidence silently completes
    wakes that never happened, so prompt confirmation must ignore them.
    """

    if is_non_turn_user_record(record):
        return False
    record_type = str(record.get("type") or "")
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    if record_type == "user" or str(message_obj.get("role") or "") == "user":
        content = message_obj.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and str(block.get("type") or "") == "tool_result":
                    return False
        return True
    # Codex projections (response_item / event_msg) never carry tool results.
    return True


def is_assistant_message(record: dict[str, Any]) -> bool:
    record_type = str(record.get("type") or "")
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    message_role = str(message_obj.get("role") or "")
    if (
        record_type == "assistant"
        or message_role == "assistant"
        or str(record.get("subtype") or "") == "turn_duration"
    ):
        return True
    payload = record.get("payload")
    payload_obj = payload if isinstance(payload, dict) else {}
    return (
        record_type == "response_item"
        and str(payload_obj.get("type") or "") == "message"
        and str(payload_obj.get("role") or "") == "assistant"
    )


def tool_call_id(value: dict[str, Any]) -> str:
    for key in ("call_id", "id", "tool_call_id"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def message_content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def tool_use_ids(message: dict[str, Any]) -> set[str]:
    return {
        str(block.get("id") or "").strip()
        for block in message_content_blocks(message)
        if block.get("type") == "tool_use" and str(block.get("id") or "").strip()
    }


def tool_result_ids(message: dict[str, Any]) -> set[str]:
    return {
        str(block.get("tool_use_id") or "").strip()
        for block in message_content_blocks(message)
        if block.get("type") == "tool_result" and str(block.get("tool_use_id") or "").strip()
    }


def active_tool_call_from_text(text: str) -> bool:
    pending_tool_ids: set[str] = set()
    unknown_tool_active = False
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        record_type = str(record.get("type") or "")
        message = record.get("message")
        message_obj = message if isinstance(message, dict) else {}
        message_role = str(message_obj.get("role") or "")
        payload = record.get("payload")
        payload_obj = payload if isinstance(payload, dict) else {}
        payload_type = str(payload_obj.get("type") or "")
        if record_type == "response_item":
            if payload_type in {"function_call", "custom_tool_call", "local_shell_call"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.add(call_id)
                else:
                    unknown_tool_active = True
                continue
            if payload_type in {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.discard(call_id)
                else:
                    pending_tool_ids.clear()
                unknown_tool_active = False
                continue
            if is_assistant_message(record):
                pending_tool_ids.clear()
                unknown_tool_active = False
                continue
        if record_type == "event_msg":
            if payload_type in {"mcp_tool_call_begin", "tool_call_begin"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.add(call_id)
                else:
                    unknown_tool_active = True
                continue
            if payload_type in {"mcp_tool_call_end", "tool_call_end"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.discard(call_id)
                else:
                    pending_tool_ids.clear()
                unknown_tool_active = False
                continue
        if record_type == "assistant" or message_role == "assistant":
            use_ids = tool_use_ids(message_obj)
            if str(message_obj.get("stop_reason") or "") == "tool_use" or use_ids:
                pending_tool_ids.update(use_ids)
                if not use_ids:
                    unknown_tool_active = True
            else:
                pending_tool_ids.clear()
                unknown_tool_active = False
            continue
        if record_type == "user" or message_role == "user":
            result_ids = tool_result_ids(message_obj)
            if result_ids:
                pending_tool_ids.difference_update(result_ids)
                unknown_tool_active = False
            elif record.get("toolUseResult") is not None:
                pending_tool_ids.clear()
                unknown_tool_active = False
    return bool(pending_tool_ids or unknown_tool_active)


def active_turn_from_text(text: str, *, not_before: float | None = None) -> bool:
    """Whether a model turn is running.

    `not_before` is the current console launch time: a record older than it
    belongs to a session that has already exited, so it can neither open nor
    close the running turn. Without it, a session killed mid tool call leaves a
    dangling tool_use record that reports "active" forever and starves every
    channel wake.
    """

    active = False
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if not_before is not None:
            timestamp = record_timestamp_seconds(record)
            # Undated bookkeeping records (mode, last-prompt, latches) carry no
            # turn state, so skipping them when they cannot be dated is safe.
            if timestamp is None or timestamp < not_before:
                continue
        payload = record.get("payload")
        payload_obj = payload if isinstance(payload, dict) else {}
        event_type = str(payload_obj.get("type") or record.get("type") or "")
        if event_type in {"task_started", "turn_started"}:
            active = True
        elif event_type in {"task_complete", "turn_complete", "turn_aborted"}:
            active = False
        # Claude Code JSONL does not emit the Codex task/turn lifecycle events.
        # A real user turn (including tool results) remains active until an
        # end_turn response or the explicit turn_duration record.  Without
        # this projection, Ciel can type a new wake prompt while Claude is
        # still working; Claude then queues or truncates that prompt and the
        # wake verifier incorrectly retries it as unseen.
        record_type = str(record.get("type") or "")
        message = record.get("message")
        message_obj = message if isinstance(message, dict) else {}
        message_role = str(message_obj.get("role") or "")
        if record_type == "user" or message_role == "user":
            if _is_local_command_echo(record):
                # The CLI echoes `<command-name>`/`<local-command-stdout>`
                # right after a typed slash command: the preceding user line
                # (e.g. a bare "/compact", which carries no distinguishing
                # flag) was handled locally and no model turn will follow.
                active = False
            elif not is_non_turn_user_record(record):
                active = True
        elif record_type == "assistant" or message_role == "assistant":
            stop_reason = str(message_obj.get("stop_reason") or "").strip().lower()
            # Claude's persisted assistant records are complete messages, not
            # streaming deltas.  Only tool/pause stops promise another model
            # step; end_turn and legacy records without a stop reason close it.
            active = stop_reason in {"tool_use", "pause_turn"}
        elif record_type == "system" and str(record.get("subtype") or "") == "turn_duration":
            active = False
    return active


def queued_age_seconds_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None,
    services: ChannelWakeTranscriptServices,
    *,
    now: float | None = None,
    not_before: float | None = None,
) -> float | None:
    if message_id <= 0:
        return None
    prompts = [str(item) for item in (prompt_texts or ()) if str(item or "").strip()]
    claimed = services.claim_prompt(message_id)
    if claimed:
        prompts.append(claimed)
    latest_queued_at: float | None = None
    for record in _jsonl_records(text):
        record_type = str(record.get("type") or "")
        candidate = ""
        if record_type == "queue-operation" and record.get("operation") == "enqueue":
            raw = record.get("content")
            candidate = raw if isinstance(raw, str) else ""
        elif record_type == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                raw = attachment.get("prompt")
                candidate = raw if isinstance(raw, str) else ""
        actual_user_text = _evidence_user_text(record, not_before)
        if actual_user_text and services.prompt_references_message_id(
            actual_user_text, message_id, prompts
        ):
            return None
        if not candidate or not services.prompt_references_message_id(candidate, message_id, prompts):
            continue
        timestamp = record_timestamp_seconds(record)
        if timestamp is not None:
            latest_queued_at = max(latest_queued_at or 0.0, timestamp)
    if latest_queued_at is None:
        return None
    current = services.now() if now is None else float(now)
    return max(0.0, current - latest_queued_at)


def wake_state_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None,
    services: ChannelWakeTranscriptServices,
    *,
    not_before: float | None = None,
) -> str:
    return wake_state_evidence_from_text(
        message_id, text, prompt_texts, services, not_before=not_before
    ).state


def wake_state_evidence_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None,
    services: ChannelWakeTranscriptServices,
    *,
    not_before: float | None = None,
) -> WakeStateEvidence:
    if message_id <= 0:
        return WakeStateEvidence("completed")
    prompts = [str(item) for item in (prompt_texts or ()) if str(item or "").strip()]
    claimed = services.claim_prompt(message_id)
    if claimed:
        prompts.append(claimed)
    seen_queued_prompt = False
    seen_real_prompt = False
    prompt_record: int | None = None
    queued_record: int | None = None
    prompt_metadata: dict[str, str] = {}
    for record_index, record in enumerate(_jsonl_records(text), start=1):
        record_type = str(record.get("type") or "")
        if seen_real_prompt and is_assistant_message(record):
            return WakeStateEvidence(
                "completed",
                prompt_record=prompt_record,
                completion_record=record_index,
                record_type=prompt_metadata.get("record_type", ""),
                session_id=prompt_metadata.get("session_id", ""),
                timestamp=prompt_metadata.get("timestamp", ""),
            )
        if record_type == "queue-operation" and record.get("operation") == "enqueue":
            raw = record.get("content")
            if isinstance(raw, str) and services.prompt_references_message_id(raw, message_id, prompts):
                seen_queued_prompt = True
                queued_record = record_index
            continue
        if record_type == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                raw = attachment.get("prompt")
                if isinstance(raw, str) and services.prompt_references_message_id(raw, message_id, prompts):
                    seen_queued_prompt = True
                    queued_record = record_index
            continue
        record_user_text = _evidence_user_text(record, not_before)
        # Delivery confirmation follows the original (pre-45f4c60) semantics:
        # a message-id reference is authoritative, the prompt-text containment
        # check is only the fallback for raw tty prompts that carry no id.
        # 45f4c60 had replaced this with body-substring matching whenever
        # prompt candidates existed, which let any old record with the same
        # template body complete a wake that never happened.
        prompt_confirmed = bool(record_user_text) and services.prompt_references_message_id(
            record_user_text, message_id, prompts
        )
        if prompt_confirmed:
            seen_real_prompt = True
            prompt_record = record_index
            prompt_metadata = {
                "record_type": record_type,
                "session_id": str(record.get("sessionId") or ""),
                "timestamp": str(record.get("timestamp") or ""),
            }
    if seen_real_prompt:
        return WakeStateEvidence(
            "pending",
            prompt_record=prompt_record,
            record_type=prompt_metadata.get("record_type", ""),
            session_id=prompt_metadata.get("session_id", ""),
            timestamp=prompt_metadata.get("timestamp", ""),
        )
    if seen_queued_prompt:
        return WakeStateEvidence("queued", prompt_record=queued_record)
    return WakeStateEvidence("missing")


def _evidence_user_text(record: dict[str, Any], not_before: float | None) -> str:
    """User text admissible as delivery evidence: typed prompts only, and —
    when an evidence horizon is given — records no older than the message
    whose delivery they would prove."""

    if not is_typed_user_prompt(record):
        return ""
    if not_before is not None:
        timestamp = record_timestamp_seconds(record)
        if timestamp is None or timestamp < not_before:
            return ""
    return user_text(record)


def queued_command_ids_from_text(
    text: str, services: ChannelWakeTranscriptServices
) -> set[int]:
    ids: set[int] = set()
    for record in _jsonl_records(text):
        candidate = ""
        if record.get("type") == "queue-operation" and record.get("operation") == "enqueue":
            raw = record.get("content")
            candidate = raw if isinstance(raw, str) else ""
        elif record.get("type") == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                raw = attachment.get("prompt")
                candidate = raw if isinstance(raw, str) else ""
        if candidate:
            ids.update(services.prompt_message_ids(candidate))
    return ids


def _jsonl_records(text: str) -> Iterator[dict[str, Any]]:
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            yield record


__all__ = [
    "active_tool_call_from_text",
    "active_turn_from_text",
    "ChannelWakeTranscriptServices",
    "content_text",
    "is_assistant_message",
    "message_content_blocks",
    "record_timestamp_seconds",
    "queued_age_seconds_from_text",
    "queued_command_ids_from_text",
    "tool_call_id",
    "tool_result_ids",
    "tool_use_ids",
    "user_text",
    "wake_state_from_text",
    "wake_state_evidence_from_text",
    "WakeStateEvidence",
]
