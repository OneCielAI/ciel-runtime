"""Mechanical watch of a main session's native forks (no AI decision).

The user's requirement: the router periodically watches the fork sessions of
the bound main session and tells main once every fork session's turn has
ended.  The authority is each fork's own transcript under
``<CLAUDE_CONFIG_DIR>/projects/<slug>/<session>/subagents/`` (lead rulings
R1-R6 and the 2026-11-14 mapping review, router-fork-turn-watch journal).

Per fork, the state comes from the last complete ``assistant``/``user``
record, judged by ``agent_turn_events.claude_record_closing_reason`` (the one
closing definition the transcript turn tracker also uses):

* ended   - an API error, a closing stop or the interrupt record;
* active  - any other assistant/user record (tool use, tool result, prompt,
            the SendMessage resume record);
* unknown - unreadable meta or transcript, or no complete assistant/user
            record yet.

An active fork whose last record predates the current claude.exe launch of
the bound session is reported as ``orphaned`` (a relabel only; it blocks the
notice exactly like active).  ``finished`` in the meta is never decisive.

Reads are incremental: a first sight reads the tail backwards to the last
relevant record, later polls read only bytes after the stored offset and
never consume a partially written last line.  The persisted state holds no
transcript text: offsets, record uuids, states, the debounce candidate, the
pending notice and the fired key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from .agent_turn_events import claude_record_closing_reason
from .work_parallel import WorkParallelStore

STATE_SCHEMA = 2
POLL_SECONDS = 2.0
LIST_LIMIT = 16
TAIL_CHUNK = 65536
MAX_TAIL_BYTES = 64 * 1024 * 1024
SOURCE_ANCHOR_BYTES = 4096
CONTINUITY_SEGMENT_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
EMPTY_STREAM_ERROR = "API Error: Stream ended without receiving any events"
REASONS = ("end_turn", "error", "interrupted", "max_tokens")
SOURCE = "ciel-runtime-fork-watch"
CHANNEL = "fork-watch"
EVENT_PREFIX = "fork-turn-watch:"
_AGENT_FILE = re.compile(r"^agent-([A-Za-z0-9_-]{1,200})\.meta\.json$")


def _relevant(record: Any) -> bool:
    return isinstance(record, dict) and record.get("type") in ("assistant", "user")


def _joined_text(record: dict[str, Any]) -> str:
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(block.get("text") or "") for block in content
                       if isinstance(block, dict) and block.get("type") in ("text", "input_text"))
    return ""


def is_empty_stream_error(record: Any) -> bool:
    return (isinstance(record, dict) and record.get("type") == "assistant"
            and record.get("isApiErrorMessage") is True
            and (record.get("_empty_stream_error") is True or _joined_text(record).strip() == EMPTY_STREAM_ERROR))


def records_state(records: list[dict[str, Any]], retry_limit: int, prior_count: int = 0) -> tuple[str, str, int]:
    """Apply complete relevant records in order; count only consecutive exact empty-stream errors."""

    state, reason, count = "unknown", "no_record", max(0, int(prior_count))
    for record in records:
        if not _relevant(record):
            continue
        if record.get("type") == "user":
            count = 0
        elif not is_empty_stream_error(record):
            count = 0
        closing = (str(record.get("_closing_reason") or "") or claude_record_closing_reason(record))
        if is_empty_stream_error(record):
            count += 1
            if retry_limit > 0 and count <= retry_limit:
                state, reason = "active", "empty_stream_retry"
            else:
                state, reason = "ended", "error"
        elif closing is not None:
            state, reason = "ended", closing
        else:
            state, reason = "active", "open"
    return state, reason, count


def record_state(record: dict[str, Any] | None, retry_limit: int = 0) -> tuple[str, str]:
    """Compatibility projection of one record; full turns use ``records_state``."""
    state, reason, _count = records_state([record] if isinstance(record, dict) else [], retry_limit)
    return state, reason


def _parse_time(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _decode(line: bytes) -> Any:
    if not line.strip() or len(line) > MAX_RECORD_BYTES:
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _complete_end(stream: Any, size: int) -> int:
    """Offset after the last newline, with a bounded partial-line probe.

    A record larger than ``MAX_RECORD_BYTES`` is ignored once its newline is
    present. While such a record is still being written there is no safe
    complete offset, so fail closed after a bounded probe; a later newline
    makes the next poll resolve immediately.
    """
    end = size
    floor = max(0, size - MAX_RECORD_BYTES - TAIL_CHUNK)
    while end > floor:
        start = max(floor, end - TAIL_CHUNK)
        stream.seek(start)
        data = stream.read(end - start)
        index = data.rfind(b"\n")
        if index >= 0:
            return start + index + 1
        end = start
    if floor > 0:
        raise OSError("partial transcript line exceeds bounded probe")
    return 0


def _source_identity(stat: os.stat_result) -> list[int]:
    return [int(stat.st_dev), int(stat.st_ino)]


def _anchor(stream: Any, offset: int) -> str:
    start = max(0, int(offset) - SOURCE_ANCHOR_BYTES)
    stream.seek(start)
    return hashlib.sha256(stream.read(int(offset) - start)).hexdigest()


def source_snapshot(path: Path, offset: int | None = None) -> dict[str, Any]:
    """Bounded source metadata and final anchor; full continuity is built separately."""
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        complete = _complete_end(stream, int(stat.st_size)) if offset is None else int(offset)
        if complete < 0 or complete > int(stat.st_size):
            raise OSError("invalid transcript offset")
        return {"source": _source_identity(stat), "offset": complete, "anchor": _anchor(stream, complete),
                "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def continuity_build_step(path: Path, build: dict[str, Any] | None, target_end: int,
                          *, budget: int | None = None) -> tuple[dict[str, Any], int]:
    """Hash every committed-prefix byte in independent segments, bounded per poll."""
    budget = MAX_TAIL_BYTES if budget is None else max(0, int(budget))
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = _source_identity(stat)
        if int(stat.st_size) < target_end:
            raise OSError("continuity source shrank")
        if (not isinstance(build, dict) or build.get("source") != identity
                or int(build.get("offset") or -1) != target_end):
            build = {"source": identity, "offset": target_end, "next": 0, "segments": []}
        position, read = int(build.get("next") or 0), 0
        segments = list(build.get("segments") or [])
        segment_bytes = min(CONTINUITY_SEGMENT_BYTES, max(1, budget))
        while position < target_end and read < budget:
            length = min(segment_bytes, target_end - position, budget - read)
            stream.seek(position)
            data = stream.read(length)
            read += len(data)
            if len(data) != length:
                raise OSError("continuity source unreadable")
            segments.append([position, length, hashlib.sha256(data).hexdigest()])
            position += length
        return {"source": identity, "offset": target_end, "next": position,
                "segments": segments, "done": position == target_end}, read


def continuity_verification(path: Path, continuity: dict[str, Any] | None, verify: dict[str, Any] | None,
                            *, budget: int | None = None) -> tuple[dict[str, Any] | None, bool, bool, int]:
    """Boundedly verify every committed-prefix segment hash."""
    budget = MAX_TAIL_BYTES if budget is None else max(0, int(budget))
    expected = (continuity or {}).get("segments") if isinstance(continuity, dict) else None
    offset = int((continuity or {}).get("offset") or 0) if isinstance(continuity, dict) else 0
    if not isinstance(expected, list):
        return None, True, False, 0
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = _source_identity(stat)
        size, mtime_ns = int(stat.st_size), int(stat.st_mtime_ns)
        if size < offset:
            return None, True, False, 0
        reset = (not isinstance(verify, dict) or verify.get("source") != identity
                 or int(verify.get("offset") or -1) != offset)
        if not reset:
            prior_size = int(verify.get("size") or 0)
            # Growth after the committed prefix is allowed. A same-size write, or
            # shrink above the committed offset, invalidates the verified cursor.
            reset = size <= prior_size and (size != prior_size or mtime_ns != int(verify.get("mtime_ns") or 0))
        if reset:
            verify = {"source": identity, "offset": offset, "index": 0,
                      "size": size, "mtime_ns": mtime_ns}
        index, read = int(verify.get("index") or 0), 0
        while index < len(expected):
            start, length, digest = expected[index]
            length, start = int(length), int(start)
            if length > budget - read:
                break
            stream.seek(start)
            data = stream.read(length)
            read += len(data)
            if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
                return None, True, False, read
            index += 1
        if index == len(expected):
            return None, True, True, read
        return {"source": identity, "offset": offset, "index": index,
                "size": size, "mtime_ns": mtime_ns}, False, True, read


def continuity_extend(path: Path, continuity: dict[str, Any], new_offset: int) -> tuple[dict[str, Any], int]:
    """Extend exact segment hashes after a validated normal append."""
    old_offset = int(continuity.get("offset") or 0)
    segments = list(continuity.get("segments") or [])
    base = old_offset
    if segments and old_offset % CONTINUITY_SEGMENT_BYTES:
        base = int(segments[-1][0])
        segments.pop()
    with path.open("rb") as stream:
        stream.seek(base)
        raw = stream.read(new_offset - base)
    if len(raw) != new_offset - base:
        raise OSError("continuity append unreadable")
    position = base
    for start in range(0, len(raw), CONTINUITY_SEGMENT_BYTES):
        chunk = raw[start:start + CONTINUITY_SEGMENT_BYTES]
        segments.append([position, len(chunk), hashlib.sha256(chunk).hexdigest()])
        position += len(chunk)
    return {"offset": new_offset, "segments": segments}, len(raw)


def source_continues(path: Path, old: dict[str, Any], verify: dict[str, Any] | None = None,
                     *, budget: int | None = None) -> tuple[str, dict[str, Any], dict[str, Any] | None, int]:
    """Verify the complete committed prefix before trusting every content change."""
    offset = int(old.get("offset") or 0)
    current = source_snapshot(path, offset)
    basic = (current["source"] == old.get("source") and current["size"] >= offset
             and current["anchor"] == old.get("anchor"))
    if not basic:
        return "changed", current, None, 0
    changed = (current["size"] != int(old.get("size") or offset)
               or current["mtime_ns"] != old.get("mtime_ns"))
    if not (old.get("needs_verify") or verify or changed):
        return "same", current, None, 0
    cursor, done, matches, read = continuity_verification(path, old.get("continuity"), verify, budget=budget)
    return ("same" if done and matches else "changed" if done else "verifying"), current, cursor, read


def new_records(path: Path, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    """Relevant complete records after a validated cursor; returned bytes are actual reads."""
    size = path.stat().st_size
    if size == offset:
        return [], offset, 0
    with path.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read(size - offset)
    cut = raw.rfind(b"\n") + 1
    rows = [record for line in raw[:cut].split(b"\n") if _relevant(record := _decode(line))]
    return rows, offset + cut, len(raw)


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    return {"type": record.get("type"), "uuid": str(record.get("uuid") or ""),
            "timestamp": record.get("timestamp"), "stop_reason": message.get("stop_reason"),
            "isApiErrorMessage": record.get("isApiErrorMessage") is True,
            "empty_stream_error": is_empty_stream_error(record),
            # Safe classification evidence, never the joined text itself. This
            # preserves an interrupt closing record across cold/restart scans.
            "closing_reason": claude_record_closing_reason(record)}


def _safe_as_record(safe: dict[str, Any]) -> dict[str, Any]:
    return {"type": safe.get("type"), "uuid": safe.get("uuid"), "timestamp": safe.get("timestamp"),
            "isApiErrorMessage": safe.get("isApiErrorMessage") is True,
            "_empty_stream_error": safe.get("empty_stream_error") is True,
            "_closing_reason": safe.get("closing_reason"),
            "message": {"stop_reason": safe.get("stop_reason"), "content": []}}


def appended_records(path: Path, offset: int, *, budget: int | None = None) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Bounded relevant records from growth after a validated offset.

    Small growth uses the exact incremental reader. For growth above the poll
    budget, scan only the newest budget backwards. This makes a fresh relevant
    tail resolve immediately without one unbounded read; ignored growth merely
    advances the validated cursor while the older cold backscan continues.
    """
    budget = MAX_TAIL_BYTES if budget is None else max(1, int(budget))
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        complete = _complete_end(stream, int(stat.st_size))
        if complete < offset:
            raise OSError("transcript shrank")
        delta = complete - offset
        if delta <= budget:
            rows, end, read = new_records(path, offset)
            return rows, end, read, False
        start = max(offset, complete - budget)
        stream.seek(start)
        raw = stream.read(complete - start)
        cut = raw.find(b"\n") + 1 if start > offset else 0
        lines = raw[cut:].split(b"\n") if cut else raw.split(b"\n")
        rows = [record for line in lines if _relevant(record := _decode(line))]
        return rows, complete, len(raw), True


def backscan_step(path: Path, scan: dict[str, Any] | None, *, budget: int | None = None) -> tuple[dict[str, Any], int]:
    """One persistent bounded backward step, storing only safe shapes and offsets.

    ``next`` is the end of the still-unscanned prefix. When a page begins in a
    line, the next step ends after that boundary line so it is read whole. A
    line with no newline in one full page is skipped as an oversized ignored
    record; no transcript bytes are persisted.
    """
    budget = MAX_TAIL_BYTES if budget is None else max(1, int(budget))
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = _source_identity(stat)
        if not isinstance(scan, dict):
            end = _complete_end(stream, int(stat.st_size))
            scan = {"source": identity, "snapshot_end": end, "next": end, "last": None,
                    "empty_count": 0, "boundary": False}
        snapshot_end, end = int(scan.get("snapshot_end") or 0), int(scan.get("next") or 0)
        if scan.get("source") != identity or snapshot_end > int(stat.st_size) or end > snapshot_end:
            raise OSError("backscan source changed")
        if source_snapshot(path, snapshot_end)["anchor"] != scan.get("snapshot_anchor") and scan.get("snapshot_anchor"):
            raise OSError("backscan anchor changed")
        if "snapshot_anchor" not in scan:
            scan["snapshot_anchor"] = _anchor(stream, snapshot_end)
        read = 0
        while end > 0 and read < budget and not scan.get("boundary"):
            start = max(0, end - min(TAIL_CHUNK, budget - read))
            stream.seek(start)
            chunk = stream.read(end - start)
            read += len(chunk)
            first_newline = chunk.find(b"\n")
            if start == 0:
                lines, next_end = chunk.split(b"\n"), 0
            elif first_newline >= 0:
                lines = chunk[first_newline + 1:].split(b"\n")
                next_end = start + first_newline + 1
                if next_end >= end:  # the page ended at the only newline of one oversized ignored record
                    lines, next_end = [], start
            else:
                lines, next_end = [], start  # oversized boundary line: bounded progress, no text retained
            for line in reversed(lines):
                record = _decode(line)
                if not _relevant(record):
                    continue
                safe = _safe_record(record)
                if scan.get("last") is None:
                    scan["last"] = safe
                if safe["type"] == "user" or (safe["type"] == "assistant" and not safe["empty_stream_error"]):
                    scan["boundary"] = True
                    break
                if safe["empty_stream_error"]:
                    scan["empty_count"] = int(scan.get("empty_count") or 0) + 1
            end = next_end
        scan["next"] = end
        if end == 0:
            scan["boundary"] = True
        scan["done"] = bool(scan.get("boundary"))
        return scan, read


def subagents_dir(claude_config_dir: str | Path, cwd: str | Path, session_id: str) -> Path:
    """``<config>/projects/<slug>/<session>/subagents`` as Claude Code lays it out."""

    slug = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    return Path(claude_config_dir) / "projects" / slug / str(session_id) / "subagents"


def _bounded(values: list[str]) -> str:
    if not values:
        return "none"
    shown = values[:LIST_LIMIT]
    rest = len(values) - len(shown)
    return ", ".join(shown) + (f" +{rest} more" if rest > 0 else "")


def _iso(epoch: float | None) -> str:
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def notice_text(forks: dict[str, dict[str, Any]], leases: dict[str, dict[str, str]]) -> str:
    """Bounded, deterministic notice: counts, Work ids and record times only."""

    counts = {reason: 0 for reason in REASONS}
    for fork in forks.values():
        counts[fork["reason"]] = counts.get(fork["reason"], 0) + 1
    lease_ids = sorted({leases[a]["lease_id"] for a in forks if a in leases})
    item_ids = sorted({leases[a]["item_id"] for a in forks if a in leases})
    ended = [t for t in (f.get("ended_at") for f in forks.values()) if t is not None]
    parts = ", ".join(f"{counts[r]} {r}" for r in REASONS if counts.get(r))
    return (f"[ciel-runtime fork watch] All {len(forks)} fork session(s) of this session have ended their "
            f"turns: {parts}. Work leases: {_bounded(lease_ids)}; items: {_bounded(item_ids)}. "
            f"Last fork turn ended at {_iso(max(ended) if ended else None)}. Fork outputs are not "
            "included; use the Work tools to read results.")


def transition_key(session_id: str, forks: dict[str, dict[str, Any]]) -> str:
    pairs = sorted((agent, str(fork.get("uuid") or "")) for agent, fork in forks.items())
    return hashlib.sha256(json.dumps([session_id, pairs], separators=(",", ":")).encode()).hexdigest()


def work_lease_map(snapshot: dict[str, Any], scope: str) -> dict[str, dict[str, str]]:
    """Read-only agent-id → bounded Work ids for one session scope."""

    out: dict[str, dict[str, str]] = {}
    leases = snapshot.get("leases") if isinstance(snapshot, dict) else None
    if not isinstance(leases, dict):
        return out
    for lease_id, row in leases.items():
        if not isinstance(row, dict) or row.get("scope") != scope:
            continue
        agent = str(row.get("host_id") or "").strip()
        item = str(row.get("item_id") or "").strip()
        lease = str(lease_id or "").strip()
        if agent and item and lease:
            out[agent] = {"lease_id": lease[:200], "item_id": item[:200]}
    return out


def notification_body(key: str, text: str) -> dict[str, Any]:
    """The submit_notification body (meta reviewed by the lead, amendment 5)."""

    return {"channel": CHANNEL, "sender_id": "ciel-runtime", "kind": "notification", "message": text,
            "meta": {"source": SOURCE, "source_kind": "notification", "kind": "fork_turns_ended",
                     "event_id": EVENT_PREFIX + key, "topic": EVENT_PREFIX + key[:16],
                     "input_transport": "session_socket", "raw_injection": True}}


@dataclass(frozen=True, slots=True)
class ForkTurnWatchPorts:
    """``scope`` returns the bound launch: runtime, session_id, cwd,
    claude_config_dir, epoch, started_at (the launch identity's time)."""

    scope: Callable[[], dict[str, Any]]
    submit: Callable[[dict[str, Any]], dict[str, Any]]
    work_leases: Callable[[str], dict[str, dict[str, str]]]
    log: Callable[[str, str], None]
    now: Callable[[], float] = time.time


STATE_DIR_NAME = "fork-turn-watch"


def state_path_for(state_root: Path, identity: str) -> Path:
    """One state file per conversation identity ("claude:<session>"), beside the Work ledger."""

    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:32]
    return Path(state_root) / STATE_DIR_NAME / f"{digest}.json"


def status_view(state_root: Path, identity: str) -> dict[str, Any]:
    """Read-only view for the menu, web, REST and ciel-runtimectl: counts, last notice time and key only."""

    empty = {"available": False, "forks": 0, "counts": {name: 0 for name in ("ended", "active", "unknown", "orphaned")},
             "armed": False, "notice_pending": False, "last_notice_at": None, "last_notice_key": None,
             "observed_at": None}
    try:
        value = json.loads(state_path_for(state_root, identity).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    view = value.get("view") if isinstance(value, dict) else None
    if not isinstance(view, dict) or value.get("session_id") != str(identity).partition(":")[2]:
        return empty
    counts = view.get("counts") if isinstance(view.get("counts"), dict) else {}
    return {"available": True, "forks": int(view.get("forks") or 0),
            "counts": {name: int(counts.get(name) or 0) for name in ("ended", "active", "unknown", "orphaned")},
            "armed": bool(value.get("armed")), "notice_pending": isinstance(value.get("pending"), dict),
            "last_notice_at": _iso(value["fired_at"]) if value.get("fired_at") else None,
            "last_notice_key": (str(value.get("fired_key") or "")[:16]) or None,
            "observed_at": _iso(view.get("observed_at")) if view.get("observed_at") else None}


def status_rows(state_root: Path, limit: int = LIST_LIMIT) -> dict[str, Any]:
    """Every persisted session's read-only view, newest observation first (menu and ctl).

    Only the state files this module wrote are listed (the file name must be
    the digest of the session it records); each row is ``status_view``.
    """

    rows = []
    for path in sorted((Path(state_root) / STATE_DIR_NAME).glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        session = str(value.get("session_id") or "") if isinstance(value, dict) else ""
        if not session or path.name != state_path_for(state_root, "claude:" + session).name:
            continue
        view = status_view(state_root, "claude:" + session)
        if view["available"]:
            rows.append({"session": "claude:" + session, **view})
    rows.sort(key=lambda row: row.get("observed_at") or "", reverse=True)
    return {"sessions": rows[:limit], "more": max(0, len(rows) - limit)}


@dataclass
class ForkTurnWatch:
    state_root: Path
    ports: ForkTurnWatchPorts
    period: float = POLL_SECONDS
    _state: dict[str, Any] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _meta: dict[str, tuple[int, int, Any]] = field(default_factory=dict)
    bytes_read: int = 0

    # -- persistence (atomic, no transcript text) --------------------------
    def _path(self, session: str) -> Path:
        return state_path_for(self.state_root, "claude:" + session)

    def _load(self, session: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self._path(session).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # Schema 1 predates launch_started_at. It is read only to establish a
        # safe epoch baseline; every fork is re-observed from a validated source.
        if not isinstance(value, dict) or value.get("schema") not in (1, STATE_SCHEMA):
            return None
        if value.get("schema") == 1:
            value["schema"] = STATE_SCHEMA
            value.pop("launch_started_at", None)
            for fork in (value.get("forks") or {}).values():
                if isinstance(fork, dict):
                    fork.pop("source", None)
        else:
            # Memory cannot certify the persisted prefix across a router restart.
            # Every complete continuity map is rechecked incrementally before its
            # semantic state is trusted again.
            for fork in (value.get("forks") or {}).values():
                if isinstance(fork, dict) and isinstance(fork.get("continuity"), dict):
                    fork["needs_verify"] = True
                    # A persisted verification cursor is safe to resume: its
                    # source/offset/size/mtime token is revalidated before use.
                    # Dropping it would make repeated router restarts starve a
                    # large-prefix verification forever.
        return value

    def _save(self, state: dict[str, Any]) -> None:
        path = self._path(state["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            for attempt in range(20):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.005)
        finally:
            tmp.unlink(missing_ok=True)

    # -- one fork ----------------------------------------------------------
    @staticmethod
    def _unknown(old: dict[str, Any] | None, reason: str) -> dict[str, Any]:
        return {**(old or {}), "state": "unknown", "reason": reason}

    def _fresh_backscan(self, path: Path, retry_limit: int) -> dict[str, Any]:
        try:
            scan, read = backscan_step(path, None)
            self.bytes_read += read
            snapshot = source_snapshot(path, int(scan.get("snapshot_end") or 0))
        except OSError:
            return {"state": "unknown", "reason": "transcript_unreadable", "offset": 0, "uuid": "", "at": None}
        fork = {"state": "unknown", "reason": "continuity_building", "offset": int(scan["snapshot_end"]),
                "source": snapshot["source"], "anchor": snapshot["anchor"], "size": snapshot["size"],
                "mtime_ns": snapshot["mtime_ns"], "uuid": "", "at": None, "empty_count": 0,
                "backscan": scan, "continuity_build": None, "source_reset": True}
        return self._advance_cold(path, fork, retry_limit)

    def _advance_cold(self, path: Path, fork: dict[str, Any], retry_limit: int) -> dict[str, Any]:
        """Advance the exact prefix build, then the independent bounded tail scan."""
        budget = MAX_TAIL_BYTES
        try:
            build, read = continuity_build_step(path, fork.get("continuity_build"),
                                                int(fork.get("offset") or 0), budget=budget)
            self.bytes_read += read
            budget -= read
        except OSError:
            return self._unknown(fork, "transcript_unreadable")
        if not build.get("done"):
            return {**fork, "state": "unknown", "reason": "continuity_building", "continuity_build": build}
        continuity = {"offset": int(build["offset"]), "segments": list(build["segments"])}
        out = {**fork, "continuity": continuity, "continuity_build": None,
               "state": "unknown", "reason": "backscan_incomplete"}
        scan = out.get("backscan") or {}
        if scan.get("done"):
            return self._finish_backscan(path, out, retry_limit)
        if budget <= 0:
            return out
        try:
            scan, read = backscan_step(path, scan, budget=budget)
            self.bytes_read += read
        except OSError:
            return self._fresh_backscan(path, retry_limit)
        out["backscan"] = scan
        return self._finish_backscan(path, out, retry_limit) if scan.get("done") else out

    def _finish_backscan(self, path: Path, fork: dict[str, Any], retry_limit: int) -> dict[str, Any]:
        scan = fork.get("backscan") or {}
        last_safe = scan.get("last")
        if not isinstance(last_safe, dict):
            return {**fork, "state": "unknown", "reason": "no_record", "backscan": None}
        last = _safe_as_record(last_safe)
        prior = int(scan.get("empty_count") or 0) - (1 if is_empty_stream_error(last) else 0)
        state, reason, count = records_state([last], retry_limit, max(0, prior))
        out = {**fork, "state": state, "reason": reason, "uuid": str(last_safe.get("uuid") or ""),
               "at": _parse_time(last_safe.get("timestamp")), "empty_count": count, "backscan": None,
               "continuity_build": None, "verify": None, "needs_verify": False, "source_reset": False}
        if state == "ended":
            out["ended_at"] = out["at"]
        return out

    def _continue_backscan(self, path: Path, old: dict[str, Any], retry_limit: int) -> dict[str, Any]:
        if old.get("continuity_build") is not None or not isinstance(old.get("continuity"), dict):
            return self._advance_cold(path, old, retry_limit)
        try:
            continuity, _current, verify, verified_read = source_continues(path, old, old.get("verify"))
            self.bytes_read += verified_read
        except OSError:
            return self._unknown(old, "transcript_unreadable")
        if continuity == "changed":
            return self._fresh_backscan(path, retry_limit)
        if continuity == "verifying":
            return {**old, "state": "unknown", "reason": "continuity_verifying", "verify": verify,
                    "pending_size": _current["size"], "pending_mtime_ns": _current["mtime_ns"]}
        old = {**old, "verify": None, "needs_verify": False, "pending_size": None,
               "pending_mtime_ns": None}
        try:
            rows, new_offset, read, large = appended_records(path, int(old.get("offset") or 0))
            self.bytes_read += read
            if large:
                return self._fresh_backscan(path, retry_limit)
            if rows:
                return self._apply_rows(path, old, rows, new_offset, retry_limit)
            scan, read = backscan_step(path, old.get("backscan"))
            self.bytes_read += read
        except OSError:
            return self._unknown(old, "transcript_unreadable")
        out = {**old, "backscan": scan}
        return self._finish_backscan(path, out, retry_limit) if scan.get("done") else out

    def _apply_rows(self, path: Path, old: dict[str, Any], rows: list[dict[str, Any]], offset: int,
                    retry_limit: int) -> dict[str, Any]:
        prior_count = int(old.get("empty_count") or 0)
        scan = old.get("backscan")
        if isinstance(scan, dict):
            prior_count = max(prior_count, int(scan.get("empty_count") or 0))
        state, reason, count = records_state(rows, retry_limit, prior_count)
        last = rows[-1]
        snap = source_snapshot(path, offset)
        continuity, read = continuity_extend(path, old["continuity"], offset)
        self.bytes_read += read
        out = {"state": state, "reason": reason, "offset": offset, "source": snap["source"],
               "anchor": snap["anchor"], "size": snap["size"], "mtime_ns": snap["mtime_ns"],
               "continuity": continuity, "uuid": str(last.get("uuid") or ""),
               "at": _parse_time(last.get("timestamp")), "empty_count": count, "backscan": None,
               "continuity_build": None, "verify": None, "needs_verify": False, "source_reset": False}
        if state == "ended":
            out["ended_at"] = out["at"]
        return out

    def _observe(self, path: Path, old: dict[str, Any] | None, retry_limit: int) -> tuple[dict[str, Any], bool]:
        """Observed fork plus whether its transcript source identity reset."""
        if old is None or not old.get("source"):
            return self._fresh_backscan(path, retry_limit), old is not None
        if old.get("state") == "unknown" and old.get("reason") not in (
                "continuity_building", "backscan_incomplete", "continuity_verifying"):
            return self._fresh_backscan(path, retry_limit), True
        if old.get("backscan") or old.get("continuity_build") is not None or not isinstance(old.get("continuity"), dict):
            fork = self._continue_backscan(path, old, retry_limit)
            return fork, bool(old.get("source_reset") or fork.get("source_reset"))
        try:
            continuity, _current, verify, verified_read = source_continues(path, old, old.get("verify"))
            self.bytes_read += verified_read
        except OSError:
            return self._unknown(old, "transcript_unreadable"), False
        if continuity == "changed":
            fork = self._fresh_backscan(path, retry_limit)
            fork["source_reset"] = True
            return fork, True
        if continuity == "verifying":
            return {**old, "state": "unknown", "reason": "continuity_verifying", "verify": verify}, False
        old = {**old, "verify": None, "needs_verify": False, "pending_size": None,
               "pending_mtime_ns": None}
        try:
            rows, offset, read, large = appended_records(path, int(old["offset"]))
            self.bytes_read += read
            if large:
                return self._fresh_backscan(path, retry_limit), True
            if not rows:
                snap = source_snapshot(path, offset)
                return {**old, "source": snap["source"], "anchor": snap["anchor"],
                        "size": snap["size"], "mtime_ns": snap["mtime_ns"]}, False
            return self._apply_rows(path, old, rows, offset, retry_limit), False
        except OSError:
            return self._unknown(old, "transcript_unreadable"), False

    # -- one poll ----------------------------------------------------------
    def poll(self) -> dict[str, Any]:
        with self._lock:
            return self._poll()

    def _poll(self) -> dict[str, Any]:
        scope = self.ports.scope() or {}
        session = str(scope.get("session_id") or "")
        if scope.get("runtime") != "claude" or not session or not scope.get("claude_config_dir") or not scope.get("cwd"):
            return {"watching": False}
        started = float(scope.get("started_at") or 0)
        retry_limit = max(0, int(scope.get("direct_empty_stream_retries") or 0))
        directory = subagents_dir(scope["claude_config_dir"], scope["cwd"], session)
        state = self._state if self._state is not None and self._state.get("session_id") == session else None
        if state is None:
            persisted = self._load(session)
            state = persisted if persisted and persisted.get("session_id") == session else None
        first = state is None
        if first:
            state = {"schema": STATE_SCHEMA, "session_id": session, "launch_started_at": started,
                     "forks": {}, "armed": False, "candidate": None, "pending": None,
                     "fired_key": "", "fired_at": None}
        previous_epoch = state.get("launch_started_at")
        epoch_known = isinstance(previous_epoch, (int, float))
        epoch_changed = epoch_known and float(previous_epoch) != started
        baseline_epoch = not epoch_known
        forks: dict[str, dict[str, Any]] = state["forks"]
        changed, armed = first or baseline_epoch, bool(state["armed"])
        try:
            names = sorted(os.listdir(directory))
            listing_ok = True
        except OSError:
            names, listing_ok = [], False
        if not listing_ok:
            # Do not commit an epoch change on an unreadable sample. The next
            # successful listing still compares prior durable state to the new launch.
            if state.get("candidate") is not None:
                state["candidate"] = None
                changed = True
            state["directory_unreadable"] = True
            if changed:
                self._save(state)
            self._state = state
            return self._status_from_forks(session, state, {a: self._unknown(f, "directory_unreadable") for a, f in forks.items()})
        if state.pop("directory_unreadable", None) is not None:
            changed = True
        if baseline_epoch or epoch_changed:
            state["launch_started_at"] = started
            changed = True
        seen: set[str] = set()
        for name in names:
            match = _AGENT_FILE.match(name)
            if not match:
                continue
            agent = match.group(1)
            meta_path = directory / name
            old = forks.get(agent)
            try:
                stamp = meta_path.stat()
                token = (stamp.st_mtime_ns, stamp.st_size)
                cached = self._meta.get(agent)
                meta = cached[2] if cached is not None and cached[:2] == token else json.loads(meta_path.read_text(encoding="utf-8"))
                self._meta[agent] = (token[0], token[1], meta)
            except (OSError, ValueError):
                meta = None
            if old is None and isinstance(meta, dict) and meta.get("isFork") is not True:
                continue
            seen.add(agent)
            if not isinstance(meta, dict):
                fork, source_reset = self._unknown(old, "meta_unreadable"), False
            elif meta.get("isFork") is not True:
                fork, source_reset = self._unknown(old, "meta_identity_changed"), False
            else:
                transcript = directory / f"agent-{agent}.jsonl"
                if not transcript.is_file():
                    fork, source_reset = self._unknown(old, "transcript_missing"), False
                else:
                    fork, source_reset = self._observe(transcript, old, retry_limit)
            if (epoch_changed and old is not None and old.get("state") == "active"
                    and old.get("at") is not None and started > 0 and float(old["at"]) < started
                    and fork.get("uuid") == old.get("uuid")):
                fork = {**fork, "state": "orphaned", "reason": "previous_launch_active"}
            if old is None:
                armed = armed or not first
            elif source_reset or (fork.get("uuid"), fork.get("state")) != (old.get("uuid"), old.get("state")):
                armed = True
            if fork != old:
                forks[agent], changed = fork, True
        for agent, old in list(forks.items()):
            if agent in seen:
                continue
            fork = self._unknown(old, "meta_missing")
            if fork != old:
                forks[agent], changed = fork, True
        view = {agent: dict(fork) for agent, fork in forks.items()}
        blocked = not view or any(f.get("state") != "ended" for f in view.values())
        if first or baseline_epoch:
            armed = False
        elif any(f.get("state") != "ended" for f in view.values()):
            armed = True
        state["armed"] = armed
        delivered = self._deliver_pending(state)
        if blocked or not armed:
            if state.get("candidate") is not None:
                state["candidate"], changed = None, True
        else:
            key = transition_key(session + ":" + str(started), view)
            if key == state.get("fired_key"):
                state["armed"], changed = False, True
            elif (state.get("candidate") or {}).get("key") == key:
                text = notice_text(view, self._leases(session))
                state["pending"] = {"key": key, "text": text}
                state["candidate"] = None
                self._save(state)
                self._state = state
                delivered = self._deliver_pending(state)
                changed = True
            else:
                state["candidate"], changed = {"key": key, "at": self.ports.now()}, True
        if changed or delivered:
            self._save(state)
        self._state = state
        return self._status_from_forks(session, state, view)

    def _status_from_forks(self, session: str, state: dict[str, Any], view: dict[str, dict[str, Any]]) -> dict[str, Any]:
        counts = {name: 0 for name in ("ended", "active", "unknown", "orphaned")}
        for fork in view.values():
            counts[fork.get("state") if fork.get("state") in counts else "unknown"] += 1
        summary = {"forks": len(view), "counts": counts}
        if {k: v for k, v in (state.get("view") or {}).items() if k != "observed_at"} != summary:
            state["view"] = {**summary, "observed_at": self.ports.now()}
            self._save(state)
        return status_view(self.state_root, "claude:" + session)

    def _leases(self, session: str) -> dict[str, dict[str, str]]:
        try:
            return self.ports.work_leases("claude:" + session) or {}
        except Exception as exc:  # noqa: BLE001 - the notice still goes out without Work ids
            self.ports.log("WARN", f"fork_turn_watch_work_leases_unreadable error={type(exc).__name__}")
            return {}

    def _deliver_pending(self, state: dict[str, Any]) -> bool:
        pending = state.get("pending")
        if not isinstance(pending, dict):
            return False
        try:
            row = self.ports.submit(notification_body(pending["key"], pending["text"]))
        except Exception as exc:  # noqa: BLE001 - kept pending, retried next poll with the same text
            self.ports.log("WARN", f"fork_turn_watch_submit_failed error={type(exc).__name__}")
            return False
        state.update(fired_key=pending["key"], fired_at=self.ports.now(), pending=None, armed=False)
        self.ports.log("INFO", f"fork_turn_watch_notice_queued id={(row or {}).get('id') or '-'} "
                       f"duplicate={bool((row or {}).get('_ciel_runtime_duplicate'))} key={pending['key'][:16]}")
        return True

    def status(self) -> dict[str, Any]:
        """The current session's view (the same one the four surfaces read from disk)."""

        session = str((self._state or {}).get("session_id") or "")
        return status_view(self.state_root, "claude:" + session) if session else status_view(self.state_root, "")

    # -- thread --------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ciel-fork-turn-watch", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:  # noqa: BLE001 - one bad poll never stops the watch
                self.ports.log("WARN", f"fork_turn_watch_poll_failed error={type(exc).__name__}: {str(exc)[:200]}")
            self._stop.wait(self.period)


# -- router composition (keeps ciel_runtime.py within its line budget) -------
_ROUTER_WATCH: ForkTurnWatch | None = None


def bound_claude_scope(compaction: Any) -> dict[str, Any]:
    """The bound launch from the router's runtime-compaction binding, or {}.

    ``started_at`` is the time the current claude.exe launch bound its scope
    (runtime_launch: one bind per claude.exe run), never a process-absence test.
    """

    try:
        scope = compaction.scope()
        launch = compaction.current_launch_scope()
    except Exception:  # noqa: BLE001 - an unreadable scope means no poll this period
        return {}
    if scope is None or getattr(scope, "runtime", "") != "claude" or launch.get("epoch") != scope.epoch:
        return {}
    return {"runtime": "claude", "session_id": scope.session_id, "epoch": scope.epoch,
            "cwd": launch.get("cwd"), "claude_config_dir": launch.get("claude_config_dir"),
            "started_at": launch.get("started_at"),
            "direct_empty_stream_retries": int(launch.get("direct_empty_stream_retries") or 0)}


def start_router_watch(config_dir: Path, compaction: Any, gateway: Callable[[], Any],
                       log: Callable[[str, str], None]) -> ForkTurnWatch:
    """Start the one router watch (idempotent).

    State lives beside the Work parallel ledger (``<CONFIG_DIR>/fork-turn-watch``),
    the directory ciel-runtimectl and the menu already read; Work ids come from
    the same ledger, read only.
    """

    global _ROUTER_WATCH
    if _ROUTER_WATCH is None:
        ledger = Path(config_dir) / "work-parallel.json"
        ports = ForkTurnWatchPorts(
            scope=lambda: bound_claude_scope(compaction),
            submit=lambda body: gateway().submit_notification(body),
            work_leases=lambda identity: work_lease_map(WorkParallelStore(ledger).read(), identity),
            log=log)
        _ROUTER_WATCH = ForkTurnWatch(Path(config_dir), ports)
    _ROUTER_WATCH.start()
    return _ROUTER_WATCH


def stop_router_watch() -> None:
    if _ROUTER_WATCH is not None:
        _ROUTER_WATCH.stop()
