from __future__ import annotations

from datetime import datetime, timezone
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support.channel_message_policy import string_list


@dataclass(frozen=True, slots=True)
class ChannelMessageRepository:
    path: Path
    log: Callable[[str, str], None]

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
