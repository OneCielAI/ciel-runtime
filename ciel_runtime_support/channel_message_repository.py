from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import errno
import json
from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable
import time
import uuid

from ciel_runtime_support.channel_message_policy import string_list

# Ids still visible in a damaged row; ignoring the row must not reissue them.
_RECOVERABLE_ID = re.compile(
    rb'"id"\s*:\s*(?:"([0-9]{1,100})"|([0-9]{1,100}))(?=\s*[,}]|\s*$)'
)

# Legacy id frontier per queue, keyed by the stat identity of its leftover files
# so an unchanged multi-megabyte ``.jsonl.1`` is not rescanned on every append.
_LEGACY_FLOOR_CACHE: dict[str, tuple[tuple[Any, ...], int]] = {}
_LEGACY_FLOOR_LOCK = threading.Lock()


def _stat_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


class ChannelFileLockBusy(OSError):
    """A Windows advisory lock acquisition exhausted its bounded wait."""


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
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise ChannelFileLockBusy(exc.errno, f"channel file lock acquisition busy: {lock_path}") from exc
                raise
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


def _no_issued_ids() -> int:
    return 0


@dataclass(frozen=True, slots=True)
class ChannelMessageRepository:
    path: Path
    log: Callable[[str, str], None]
    max_bytes: int = 10 * 1024 * 1024
    # Highest id recorded outside this file (the runtime input status history).
    # A row lost from the queue keeps its id there, so it is never reissued.
    issued_id_floor: Callable[[], int] = _no_issued_ids

    def append(self, payload: dict[str, Any], ports: ChannelMessageAppendPorts) -> dict[str, Any]:
        with ports.condition:
            with ports.file_lock():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                own_max_id, issued_floor = self._frontier()
                previous_max_id = max(own_max_id, issued_floor)
                next_id = previous_max_id + 1
                if issued_floor > own_max_id:
                    self.log("WARN", f"chat_message_id_above_issued_floor queue={self.path.name} "
                             f"queue_max_id={own_max_id} issued_floor={issued_floor} next_id={next_id}")
                created_at = time.time()
                message = {
                    "id": next_id,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(created_at)),
                    "created_at_epoch": created_at,
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
                # A truncated last row must not swallow the next valid input.
                separator = "\n" if self._ends_unterminated() else ""
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(separator + json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
                    # Durable before the status history refers to it: an unflushed
                    # append lost in an unclean shutdown left a NUL run where 31
                    # rows had been (2026-10-26, workspace aae7db361c7f).
                    stream.flush()
                    os.fsync(stream.fileno())
                # Bound the queue only after the new row is durable in it: that row
                # carries the id frontier and is always kept by compaction.
                self._compact_if_oversized()
            ports.condition.notify_all()
            return message

    def _compact_if_oversized(self) -> None:
        """Delete the oldest rows in place once the queue exceeds ``max_bytes``.

        Keeps the newest decodable rows totalling about ``max_bytes // 2`` and
        always the newest one, drops damaged rows, and writes no archive.  The
        caller holds the queue lock.  A failed replace (for example a Windows
        reader holding the file) leaves the queue intact and retries next append.
        """

        if self.max_bytes <= 0:
            return
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
            raw = self.path.read_bytes()
        except OSError as exc:
            self.log("WARN", f"chat compaction skipped error={type(exc).__name__}: {exc}")
            return
        budget = self.max_bytes // 2
        kept: list[bytes] = []
        kept_bytes = 0
        kept_max_id = 0
        file_max_id = 0
        full = False
        for line in reversed(raw.splitlines()):
            try:
                item = json.loads(line)
            except (ValueError, TypeError, RecursionError):
                item = None
            if not isinstance(item, dict):
                for quoted, bare in _RECOVERABLE_ID.findall(line):
                    file_max_id = max(file_max_id, int(quoted or bare))
                continue
            item_id = self._items_max_id([item])
            file_max_id = max(file_max_id, item_id)
            row = line.rstrip(b"\r") + b"\n"
            # Keep one contiguous newest tail; older rows are only scanned for ids.
            full = full or (bool(kept) and kept_bytes + len(row) > budget)
            if full:
                continue
            kept.append(row)
            kept_bytes += len(row)
            kept_max_id = max(kept_max_id, item_id)
        if not kept:
            return
        if kept_max_id < file_max_id:
            # Never let deletion lower the allocator frontier kept in the file.
            self.log(
                "WARN",
                f"chat compaction refused kept_max_id={kept_max_id} file_max_id={file_max_id}",
            )
            return
        temporary = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(b"".join(reversed(kept)))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            self.log(
                "WARN",
                f"chat compaction deferred size={len(raw)} error={type(exc).__name__}: {exc}",
            )
            try:
                temporary.unlink()
            except OSError:
                pass
            return
        self.log(
            "INFO",
            f"chat compaction removed_bytes={len(raw) - kept_bytes} kept_rows={len(kept)}",
        )

    @staticmethod
    def timestamp_seconds(item: dict[str, Any]) -> float | None:
        raw_epoch = item.get("created_at_epoch")
        if raw_epoch is not None:
            try:
                return float(raw_epoch)
            except (TypeError, ValueError):
                pass
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
            # Historical messages were written with time.strftime(), i.e. in
            # the machine's local timezone.  Treating those values as UTC can
            # move the replay boundary by several hours.
            return parsed.timestamp()
        return parsed.timestamp()

    def _ends_unterminated(self) -> bool:
        try:
            with self.path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                if not stream.tell():
                    return False
                stream.seek(-1, os.SEEK_END)
                return stream.read(1) != b"\n"
        except FileNotFoundError:
            return False

    def _scan(self, path: Path, operation: str) -> tuple[list[dict[str, Any]], int, bool]:
        """Return decodable rows, the highest id recoverable from damaged rows,
        and whether the file was read completely (a missing file counts as read).

        Damaged rows are ignored for delivery, but any id still visible in their
        bytes stays reserved so ignoring corruption never reissues an id.
        """

        items: list[dict[str, Any]] = []
        damaged_max_id = 0
        complete = True
        try:
            with path.open("rb") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except (ValueError, TypeError, RecursionError):
                        item = None
                    if isinstance(item, dict):
                        items.append(item)
                        continue
                    for quoted, bare in _RECOVERABLE_ID.findall(line):
                        damaged_max_id = max(damaged_max_id, int(quoted or bare))
        except FileNotFoundError:
            pass
        except OSError as exc:
            complete = False
            self.log(
                "WARN",
                f"chat {operation} failed error={type(exc).__name__}: {exc}",
            )
        return items, damaged_max_id, complete

    def _jsonl_items(self, operation: str) -> list[dict[str, Any]]:
        return self._scan(self.path, operation)[0]

    @staticmethod
    def _items_max_id(items: list[dict[str, Any]]) -> int:
        max_id = 0
        for item in items:
            try:
                max_id = max(max_id, int(item.get("id") or 0))
            except (TypeError, ValueError, OverflowError):
                continue
        return max_id

    def _legacy_id_floor(self) -> int:
        """Highest id an older build may have issued outside the live file.

        Older builds rotated the queue to ``.jsonl.1`` and an offline reset
        could empty it behind a ``.recovery.json`` id floor.  Both are read
        only, as allocator frontier data: nothing is created, modified,
        required or replayed, and unreadable leftovers are ignored.
        """

        rotated = self.path.with_suffix(".jsonl.1")
        record_path = self.path.with_name(self.path.name + ".recovery.json")
        signature = (_stat_identity(rotated), _stat_identity(record_path))
        if signature == (None, None):
            return 0
        key = str(self.path.absolute())
        with _LEGACY_FLOOR_LOCK:
            cached = _LEGACY_FLOOR_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        floor = 0
        complete = True
        if signature[0] is not None:
            items, damaged_max_id, complete = self._scan(rotated, "legacy id scan")
            floor = max(self._items_max_id(items), damaged_max_id)
        if signature[1] is not None:
            try:
                record = json.loads(record_path.read_bytes())
            except FileNotFoundError:
                record = None
            except OSError:
                record, complete = None, False
            except (ValueError, RecursionError):
                record = None
            id_floor = record.get("id_floor") if isinstance(record, dict) else None
            if type(id_floor) is int and id_floor > 0:
                floor = max(floor, id_floor - 1)
        if complete:
            # An unreadable leftover is retried next time, never cached low.
            with _LEGACY_FLOOR_LOCK:
                _LEGACY_FLOOR_CACHE[key] = (signature, floor)
        return floor

    def max_id(self) -> int:
        return max(self._frontier())

    def _frontier(self) -> tuple[int, int]:
        """The queue's own id frontier and the floor recorded outside it."""
        items, damaged_max_id, _complete = self._scan(self.path, "max id scan")
        own = max(self._items_max_id(items), damaged_max_id, self._legacy_id_floor())
        return own, self._issued_floor()

    def _issued_floor(self) -> int:
        try:
            floor = self.issued_id_floor()
        except (OSError, ValueError, TypeError) as exc:
            # Unreadable history: allocate from the queue alone, as before.
            self.log("WARN", f"chat_message_issued_floor_unavailable queue={self.path.name} "
                     f"error={type(exc).__name__}")
            return 0
        return floor if type(floor) is int and floor > 0 else 0

    def min_id(self) -> int:
        """Lowest positive id among decodable live rows; 0 when there are none."""

        lowest = 0
        for item in self._jsonl_items("min id scan"):
            try:
                item_id = int(item.get("id") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if item_id > 0 and (lowest == 0 or item_id < lowest):
                lowest = item_id
        return lowest

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
