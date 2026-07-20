from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable
import time

from ciel_runtime_support.channel_message_policy import string_list


@contextmanager
def exclusive_file_lock(path: Path):
    """Hold a cross-platform advisory lock beside a repository artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class ChannelMessageAppendPorts:
    condition: Any
    file_lock: Callable[..., Any]
    duplicate: Callable[[dict[str, Any]], dict[str, Any] | None]
    normalize_recipients: Callable[[Any], list[str]]


@dataclass(frozen=True, slots=True)
class ChannelMessageRepository:
    path: Path
    log: Callable[[str, str], None]
    max_bytes: int = 10 * 1024 * 1024

    def append(self, payload: dict[str, Any], ports: ChannelMessageAppendPorts) -> dict[str, Any]:
        with ports.condition:
            with ports.file_lock():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    self.path.replace(self.path.with_suffix(".jsonl.1"))
                next_id = self.max_id() + 1
                message = {
                    "id": next_id,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "channel": str(payload.get("channel") or "default"),
                    "sender_id": str(payload.get("sender_id") or payload.get("sender") or "anonymous"),
                    "recipients": ports.normalize_recipients(
                        payload.get("recipients", payload.get("recipient_id"))
                    ),
                    "thread_id": str(payload.get("thread_id") or payload.get("parent_id") or next_id),
                    "parent_id": payload.get("parent_id"),
                    "message": str(payload.get("message") or payload.get("text") or ""),
                    "kind": str(payload.get("kind") or "message"),
                    "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
                }
                if payload.get("visibility") is not None:
                    message["visibility"] = str(payload.get("visibility") or "user")
                if payload.get("delivery") is not None:
                    message["delivery"] = ports.normalize_recipients(payload.get("delivery"))
                duplicate = ports.duplicate(message)
                if duplicate:
                    returned = dict(duplicate)
                    returned["_ciel_runtime_duplicate"] = True
                    self.log(
                        "INFO",
                        f"chat_message_skipped_duplicate existing_id={duplicate.get('id')} "
                        f"channel={message.get('channel')} kind={message.get('kind')}",
                    )
                    return returned
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            ports.condition.notify_all()
            return message

    @staticmethod
    def timestamp_seconds(item: dict[str, Any]) -> float | None:
        raw = item.get("time") or item.get("created_at") or item.get("updated_at")
        if not raw:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        text = str(raw).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    def _jsonl_items(self, operation: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(item, dict):
                        items.append(item)
        except OSError as exc:
            self.log(
                "WARN",
                f"chat {operation} failed error={type(exc).__name__}: {exc}",
            )
        return items

    def max_id(self) -> int:
        max_id = 0
        for item in self._jsonl_items("max id scan"):
            try:
                max_id = max(max_id, int(item.get("id") or 0))
            except (TypeError, ValueError):
                continue
        return max_id

    def max_id_before_epoch(self, cutoff_epoch: float) -> int:
        max_id = 0
        for item in self._jsonl_items("cutoff scan"):
            try:
                item_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if item_id <= 0:
                continue
            item_epoch = self.timestamp_seconds(item)
            if item_epoch is not None and item_epoch < cutoff_epoch:
                max_id = max(max_id, item_id)
        return max_id

    @staticmethod
    def _visible_to(message: dict[str, Any], recipient: str | None) -> bool:
        if not recipient:
            return True
        recipients = string_list(message.get("recipients"))
        if not recipients or "all" in [value.lower() for value in recipients] or "*" in recipients:
            return True
        return recipient in recipients or recipient == str(message.get("sender_id") or "")

    @classmethod
    def _matches(
        cls,
        message: dict[str, Any],
        channel: str | None,
        recipient: str | None,
    ) -> bool:
        if channel:
            meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
            aliases = {
                str(message.get("channel") or ""),
                str(meta.get("room_id") or ""),
                str(meta.get("room") or ""),
                str(meta.get("channel") or ""),
            }
            if channel not in aliases:
                return False
        return cls._visible_to(message, recipient)

    def read(
        self,
        after_id: int = 0,
        channel: str | None = None,
        recipient: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in self._jsonl_items("read"):
            try:
                if int(item.get("id") or 0) <= after_id:
                    continue
            except (TypeError, ValueError):
                continue
            if self._matches(item, channel, recipient):
                messages.append(item)
                if len(messages) >= limit:
                    break
        return messages

    def read_before(
        self,
        before_id: int = 0,
        channel: str | None = None,
        recipient: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in self._jsonl_items("read before"):
            try:
                item_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if before_id > 0 and item_id >= before_id:
                continue
            if self._matches(item, channel, recipient):
                messages.append(item)
                if len(messages) > limit:
                    messages = messages[-limit:]
        return messages

    def recent_rows(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return self._jsonl_items("duplicate scan")[-limit:]
