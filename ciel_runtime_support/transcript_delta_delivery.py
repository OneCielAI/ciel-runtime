"""Incremental transcript delivery to one configured outbound HTTP endpoint."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
import urllib.error
import urllib.request

from .agent_turn_events import TranscriptTurnTracker
from .remote_instructions import expand_environment_references
from .transcript_public_projection import content_filter, public_records
from .tool_call_events import project_transcript_tool_calls
from .web_search_result_events import project_web_search_results
from .runtime_error_events import project_runtime_errors


@dataclass(frozen=True, slots=True)
class TranscriptDeliverySettings:
    enabled: bool
    url: str
    authorization: str
    timeout_seconds: float
    poll_interval_seconds: float
    max_batch_bytes: int
    start_mode: str
    # public_only (default): the relay's public projection, made on this PC.
    # raw: the transcript JSONL as written.
    content_filter: str = "public_only"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "TranscriptDeliverySettings":
        raw = config.get("transcript_events")
        values = raw if isinstance(raw, dict) else {}
        start_mode = str(values.get("start_mode") or "tail").strip().lower()
        if start_mode not in {"tail", "beginning"}:
            start_mode = "tail"
        return cls(
            enabled=bool(values.get("enabled", False)),
            url=str(values.get("url") or "").strip(),
            authorization=str(values.get("authorization") or "").strip(),
            timeout_seconds=max(0.1, min(30.0, float(values.get("timeout_seconds") or 5))),
            poll_interval_seconds=max(
                0.1, min(60.0, float(values.get("poll_interval_ms") or 1000) / 1000.0)
            ),
            max_batch_bytes=max(
                1024, min(16_777_216, int(values.get("max_batch_bytes") or 1_048_576))
            ),
            start_mode=start_mode,
            content_filter=content_filter(values.get("content_filter")),
        )


@dataclass(frozen=True, slots=True)
class ToolCallEventSettings:
    enabled: bool
    poll_interval_seconds: float
    max_batch_bytes: int
    start_mode: str
    include_arguments: bool

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ToolCallEventSettings":
        raw = config.get("tool_call_events")
        values = raw if isinstance(raw, dict) else {}
        start_mode = str(values.get("start_mode") or "tail").strip().lower()
        if start_mode not in {"tail", "beginning"}:
            start_mode = "tail"
        return cls(
            enabled=bool(values.get("enabled", True)),
            poll_interval_seconds=max(
                0.1, min(60.0, float(values.get("poll_interval_ms") or 500) / 1000.0)
            ),
            max_batch_bytes=max(
                1024, min(16_777_216, int(values.get("max_batch_bytes") or 1_048_576))
            ),
            start_mode=start_mode,
            include_arguments=bool(values.get("include_arguments", True)),
        )


# A transcript region is read in chunks of this size when it is skipped, never whole.
_SKIP_CHUNK = 1_048_576
# How far into a transcript its first record's time is looked for.
_FIRST_RECORD_PROBE = 65_536


@dataclass(frozen=True, slots=True)
class _Batch:
    """Complete records read from a transcript.

    ``payload`` holds the records between the file offsets ``start`` and
    ``end``; a corrupt region inside that range (a NUL run, a line over
    ``max_record_bytes``) was skipped and is not in it.  ``end`` is where the
    next read starts, even when nothing was readable yet.
    """

    start: int
    end: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class TranscriptDeliveryPorts:
    load_config: Callable[[], dict[str, Any]]
    latest_transcript: Callable[[], Path | None]
    scope: Callable[[], dict[str, Any]]
    log: Callable[[str, str], None]
    epoch: Callable[[], float] = time.time
    event_publish: Callable[..., Any] = lambda **_kwargs: None
    event_recent: Callable[..., list[dict[str, Any]]] = lambda **_kwargs: []
    # Receives one agent.turn_ended event per finished turn (posted to the router).
    agent_turn_post: Callable[[dict[str, Any]], Any] | None = None


class TranscriptDeltaDeliveryService:
    """Tail complete JSONL records and commit offsets only after HTTP success."""

    def __init__(
        self,
        cursor_path: Path,
        workspace_id: str,
        ports: TranscriptDeliveryPorts,
    ) -> None:
        self.cursor_path = cursor_path
        self.workspace_id = str(workspace_id or "workspace")
        self.ports = ports
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_error = ""
        self._last_error_at = 0.0
        self._turn_source: dict[str, Any] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="ciel-transcript-delivery",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def poll_once(self) -> bool:
        settings = TranscriptDeliverySettings.from_config(self.ports.load_config())
        if not settings.enabled or not settings.url:
            return False
        path = self.ports.latest_transcript()
        if path is None:
            return False
        try:
            path = path.resolve()
            size = path.stat().st_size
        except OSError:
            return False
        scope = self.ports.scope()
        session_id = str(scope.get("session_id") or path.stem)
        runtime = str(scope.get("runtime") or "runtime")
        destination_key = self._destination_key(settings.url, path)
        cursors = self._load_cursors()
        destinations = cursors.setdefault("destinations", {})
        current = destinations.get(destination_key)
        if not isinstance(current, dict):
            offset = self._initial_offset(settings, scope, path)
            destinations[destination_key] = self._cursor_record(path, offset)
            self._save_cursors(cursors)
            if offset >= size:
                return False
            current = destinations[destination_key]
        offset = max(0, int(current.get("offset") or 0))
        rotated = size < offset
        if rotated:
            offset = 0
        batch = self._read_complete_batch(path, offset, settings.max_batch_bytes, "transcript_delivery")
        payload = batch.payload
        end_offset = batch.end
        if not payload:
            if rotated or end_offset != offset:
                destinations[destination_key] = self._cursor_record(path, end_offset)
                self._save_cursors(cursors)
            return False
        content = payload
        if settings.content_filter == "public_only":
            content = public_records(payload.decode("utf-8", errors="replace")).encode("utf-8")
            if not content:
                # Nothing public in this batch: move past it without a request.
                destinations[destination_key] = self._cursor_record(path, end_offset)
                self._save_cursors(cursors)
                return False
        event = self._cloud_event(
            runtime=runtime,
            session_id=session_id,
            path=path,
            start_offset=offset,
            end_offset=end_offset,
            content=content,
            rotated=rotated,
            content_filter=settings.content_filter,
        )
        if not self._post(settings, event):
            return False
        destinations[destination_key] = self._cursor_record(path, end_offset)
        self._save_cursors(cursors)
        self.ports.log(
            "INFO",
            "transcript_delta_delivered "
            f"runtime={runtime} session={session_id} bytes={len(content)} filter={settings.content_filter} "
            f"offset={offset}->{end_offset}",
        )
        self._last_error = ""
        return True

    def poll_tool_call_events(self) -> int:
        config = self.ports.load_config()
        settings = ToolCallEventSettings.from_config(config)
        errors_enabled = (config.get("runtime_error_events") or {}).get("enabled", True)
        if not settings.enabled and not errors_enabled:
            return 0
        path = self.ports.latest_transcript()
        if path is None:
            return 0
        try:
            path = path.resolve()
            size = path.stat().st_size
        except OSError:
            return 0
        scope = self.ports.scope()
        runtime = str(scope.get("runtime") or "runtime")
        session_id = str(scope.get("session_id") or path.stem)
        source_key = hashlib.sha256(f"tool-call\0{path}".encode("utf-8")).hexdigest()
        cursors = self._load_cursors()
        sources = cursors.setdefault("tool_call_sources", {})
        current = sources.get(source_key)
        if not isinstance(current, dict):
            offset = self._initial_tool_call_offset(settings, scope, path, size)
            sources[source_key] = self._cursor_record(path, offset)
            self._save_cursors(cursors)
            if offset >= size:
                return 0
            current = sources[source_key]
        offset = max(0, int(current.get("offset") or 0))
        if size < offset:
            offset = 0
            current.pop("web_search_results", None)
        batch = self._read_complete_batch(path, offset, settings.max_batch_bytes, "tool_call_events")
        payload = batch.payload
        search_state = current.setdefault("web_search_results", {})
        if not payload:
            if batch.end != offset:
                sources[source_key] = self._cursor_record(path, batch.end)
                sources[source_key]["web_search_results"] = search_state
                self._save_cursors(cursors)
            return 0
        count = 0
        for raw_line in payload.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if errors_enabled:
                for error in project_runtime_errors(record, runtime):
                    self.ports.event_publish(
                        level="error", category="runtime.error", message=error["message"],
                        source="cli-transcript", session_id=session_id, provider=runtime,
                        data={**error, "transcript_name": path.name},
                    )
                    count += 1
            if not settings.enabled:
                continue
            for result in project_web_search_results(record, runtime, search_state):
                self.ports.event_publish(
                    level="info", category="tool.call", message=f"{runtime} web search result URLs",
                    source="cli-transcript", session_id=session_id, provider=runtime,
                    data=result,
                )
                count += 1
            for call in project_transcript_tool_calls(record, runtime):
                call_id = str(call.get("call_id") or "")
                if call_id and any(
                    str((event.get("data") or {}).get("call_id") or "") == call_id
                    and (event.get("data") or {}).get("phase") != "result"
                    for event in self.ports.event_recent(limit=200, category="tool.call")
                ):
                    continue
                data = dict(call)
                if not settings.include_arguments:
                    data.pop("arguments", None)
                self.ports.event_publish(
                    level="info",
                    category="tool.call",
                    message=f"{runtime} tool call: {call['name']}",
                    source="cli-transcript",
                    session_id=session_id,
                    provider=runtime,
                    model=str(call.get("model") or ""),
                    data={**data, "transcript_name": path.name},
                )
                count += 1
        sources[source_key] = self._cursor_record(path, batch.end)
        sources[source_key]["web_search_results"] = search_state
        self._save_cursors(cursors)
        return count

    def poll_agent_turns(self) -> int:
        """Report turns that ended in the transcript since the last poll."""

        post = self.ports.agent_turn_post
        if post is None:
            return 0
        flush = getattr(post, "flush", None)
        if callable(flush):
            flush()
        path = self.ports.latest_transcript()
        if path is None:
            return 0
        try:
            path = path.resolve()
            size = path.stat().st_size
        except OSError:
            return 0
        scope = self.ports.scope()
        runtime = str(scope.get("runtime") or "runtime")
        # The offset is stored, so a restarted router goes on where it stopped
        # instead of reporting every turn the transcript ever held again.
        source_key = hashlib.sha256(f"agent-turn\0{path}".encode("utf-8")).hexdigest()
        source = self._turn_source
        if source.get("path") != str(path) or source.get("runtime") != runtime:
            stored = self._load_cursors()["agent_turn_sources"].get(source_key)
            if isinstance(stored, dict):
                start = max(0, int(stored.get("offset") or 0))
            else:
                tail = ToolCallEventSettings(True, 1.0, 1_048_576, "tail", False)
                start = self._initial_tool_call_offset(tail, scope, path, size)
            source.clear()
            source.update(
                path=str(path),
                runtime=runtime,
                offset=start,
                tracker=TranscriptTurnTracker(runtime),
            )
            if not isinstance(stored, dict):
                self._save_turn_offset(source_key, path, start)
        offset = int(source["offset"])
        if size < offset:
            offset = 0
            source["tracker"] = TranscriptTurnTracker(runtime)
        batch = self._read_complete_batch(path, offset, 1_048_576, "agent_turn_events")
        payload = batch.payload
        if not payload:
            if batch.end != offset:
                source["offset"] = batch.end
                self._save_turn_offset(source_key, path, batch.end)
            return 0
        tracker: TranscriptTurnTracker = source["tracker"]
        count = 0
        for raw_line in payload.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            for event in tracker.feed(record):
                post(event)
                count += 1
        source["offset"] = batch.end
        self._save_turn_offset(source_key, path, batch.end)
        return count

    def _save_turn_offset(self, source_key: str, path: Path, offset: int) -> None:
        cursors = self._load_cursors()
        cursors["agent_turn_sources"][source_key] = self._cursor_record(path, offset)
        self._save_cursors(cursors)

    @staticmethod
    def _initial_offset(
        settings: TranscriptDeliverySettings, scope: dict[str, Any], path: Path
    ) -> int:
        if settings.start_mode == "beginning":
            return 0
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        tail = ToolCallEventSettings(True, 1.0, settings.max_batch_bytes, "tail", False)
        return TranscriptDeltaDeliveryService._initial_tool_call_offset(tail, scope, path, size)

    def _run(self) -> None:
        while not self._stop.is_set():
            interval = 1.0
            try:
                config = self.ports.load_config()
                settings = TranscriptDeliverySettings.from_config(config)
                tool_settings = ToolCallEventSettings.from_config(config)
                interval = min(settings.poll_interval_seconds, tool_settings.poll_interval_seconds)
                if tool_settings.enabled or (config.get("runtime_error_events") or {}).get("enabled", True):
                    self.poll_tool_call_events()
                if settings.enabled and settings.url:
                    self.poll_once()
                if not isinstance(config.get("agent_turn_events"), dict) or config["agent_turn_events"].get("enabled", True) is not False:
                    interval = min(interval, 1.0)
                    self.poll_agent_turns()
            except Exception as exc:
                self._report_error(f"poll {type(exc).__name__}: {exc}")
            self._stop.wait(interval)

    def _load_cursors(self) -> dict[str, Any]:
        try:
            value = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        if not isinstance(value.get("destinations"), dict):
            value["destinations"] = {}
        if not isinstance(value.get("tool_call_sources"), dict):
            value["tool_call_sources"] = {}
        if not isinstance(value.get("agent_turn_sources"), dict):
            value["agent_turn_sources"] = {}
        value["version"] = 1
        return value

    @staticmethod
    def _initial_tool_call_offset(
        settings: ToolCallEventSettings,
        scope: dict[str, Any],
        path: Path,
        size: int,
    ) -> int:
        if settings.start_mode == "beginning":
            return 0
        boundary_path = scope.get("turn_scan_path")
        if boundary_path is not None:
            try:
                if Path(boundary_path).resolve() == path:
                    return max(0, int(scope.get("turn_scan_offset") or 0))
            except (OSError, TypeError, ValueError):
                pass
        # A transcript first seen without a stored cursor starts at its end,
        # unless it was created after the launch (a new session: its first
        # records belong to this launch).  Modified after the launch is not
        # enough: a resumed session writes to a transcript that may hold
        # hundreds of earlier turns (Walkie 48926).
        started_at = float(scope.get("started_at") or 0)
        if started_at > 0:
            created = TranscriptDeltaDeliveryService._created_at(path)
            if created is not None and created >= started_at - 1.0:
                return 0
        return size

    @staticmethod
    def _created_at(path: Path) -> float | None:
        """When the transcript was created: the file system's birth time where
        it keeps one (Windows, macOS), else the time on its first record."""

        try:
            status = path.stat()
        except OSError:
            return None
        birth = getattr(status, "st_birthtime", None)
        if birth is None and os.name == "nt":
            birth = status.st_ctime  # creation time on Windows before st_birthtime
        if birth:
            return float(birth)
        try:
            with path.open("rb") as stream:
                head = stream.read(_FIRST_RECORD_PROBE)
        except OSError:
            return None
        for line in head.split(b"\n"):
            try:
                record = json.loads(line.lstrip(b"\x00"))
            except ValueError:
                continue
            stamp = record.get("timestamp") if isinstance(record, dict) else None
            if isinstance(stamp, str) and stamp:
                try:
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
        return None

    def _save_cursors(self, cursors: dict[str, Any]) -> None:
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cursor_path.with_name(
            f"{self.cursor_path.name}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(cursors, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.cursor_path)

    def _cursor_record(self, path: Path, offset: int) -> dict[str, Any]:
        return {
            "path": str(path),
            "offset": max(0, int(offset)),
            "updated_at": self.ports.epoch(),
        }

    @staticmethod
    def _destination_key(url: str, path: Path) -> str:
        return hashlib.sha256(f"{url}\0{path}".encode("utf-8")).hexdigest()

    def _record_limit(self, limit: int) -> int:
        # Batch size is a throughput target, not a maximum JSONL record size.
        # Read a larger first record atomically, with a separate memory bound.
        config = self.ports.load_config()
        raw = config.get("transcript_events")
        values = raw if isinstance(raw, dict) else {}
        return max(limit, min(64 * 1024 * 1024, max(1024,
            int(values.get("max_record_bytes") or 16 * 1024 * 1024))))

    def _read_complete_batch(self, path: Path, offset: int, limit: int, consumer: str = "transcript") -> _Batch:
        """Complete records from ``offset``.

        A NUL run (a hole a crashed or racing writer left; Walkie 48926 found
        28.5 MB of them) or a line longer than ``max_record_bytes`` would stop
        every reader at that offset for good.  Each is skipped once, with one
        WARN naming the file, offset, length and reason, and the returned
        ``end`` is past it.  The skipped bytes are streamed, never held.  A NUL
        run or an oversized line that has not ended yet (no byte after it, no
        newline yet) is waited for, as an unfinished record is.
        """

        record_limit = self._record_limit(limit)
        try:
            with path.open("rb") as stream:
                stream.seek(offset)
                nuls, more = self._nul_run_length(stream)
                if nuls and not more:
                    return _Batch(offset, offset, b"")
                start = offset + nuls
                if nuls:
                    self._skipped(consumer, path, offset, nuls, "nul_run")
                stream.seek(start)
                data = stream.read(limit)
                complete_end = data.rfind(b"\n")
                if complete_end >= 0:
                    data = data[: complete_end + 1]
                    return _Batch(start, start + len(data), self._without_nul_prefixes(consumer, path, start, data))
                if len(data) < limit:
                    return _Batch(start, start, b"")  # Writer has not finished the record yet.
                data += stream.readline(record_limit - len(data) + 1)
                if len(data) <= record_limit:
                    if not data.endswith(b"\n"):
                        return _Batch(start, start, b"")
                    return _Batch(start, start + len(data), self._without_nul_prefixes(consumer, path, start, data))
                held = len(data)
                del data
                rest = self._length_through_newline(stream)
                if rest is None:
                    return _Batch(start, start, b"")  # The oversized line is still being written.
                length = held + rest
                self._skipped(consumer, path, start, length, f"record_exceeds_max_record_bytes={record_limit}")
                return _Batch(start + length, start + length, b"")
        except OSError as exc:
            self._report_error(f"read {type(exc).__name__}: {exc}")
            return _Batch(offset, offset, b"")

    @staticmethod
    def _nul_run_length(stream: Any) -> tuple[int, bool]:
        """(NUL bytes from the stream position, whether any byte follows them)."""

        first = stream.read(1)
        if first != b"\x00":
            return 0, bool(first)
        count = 1
        while True:
            chunk = stream.read(_SKIP_CHUNK)
            if not chunk:
                return count, False
            rest = chunk.lstrip(b"\x00")
            count += len(chunk) - len(rest)
            if rest:
                return count, True

    @staticmethod
    def _length_through_newline(stream: Any) -> int | None:
        """Bytes up to and including the next newline, or None before one is written."""

        length = 0
        while True:
            chunk = stream.read(_SKIP_CHUNK)
            if not chunk:
                return None
            newline = chunk.find(b"\n")
            if newline >= 0:
                return length + newline + 1
            length += len(chunk)

    def _without_nul_prefixes(self, consumer: str, path: Path, start: int, data: bytes) -> bytes:
        """``data`` with the NUL run at the head of any line removed (one WARN each)."""

        if b"\n\x00" not in data:
            return data
        lines = data.split(b"\n")
        position = start
        for index, line in enumerate(lines):
            if line.startswith(b"\x00"):
                kept = line.lstrip(b"\x00")
                self._skipped(consumer, path, position, len(line) - len(kept), "nul_run")
                lines[index] = kept
            position += len(line) + 1
        return b"\n".join(lines)

    def _skipped(self, consumer: str, path: Path, offset: int, length: int, reason: str) -> None:
        self.ports.log(
            "WARN",
            f"transcript_record_skipped consumer={consumer} path={path.name} "
            f"offset={offset} length={length} reason={reason}",
        )

    def _cloud_event(
        self,
        *,
        runtime: str,
        session_id: str,
        path: Path,
        start_offset: int,
        end_offset: int,
        content: bytes,
        rotated: bool,
        content_filter: str = "raw",
    ) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        event_id = hashlib.sha256(
            (
                f"{self.workspace_id}\0{session_id}\0{path}\0"
                f"{start_offset}\0{end_offset}\0{digest}"
            ).encode("utf-8")
        ).hexdigest()
        return {
            "specversion": "1.0",
            "id": event_id,
            "source": f"urn:ciel-runtime:workspace:{self.workspace_id}",
            "type": "ai.oneciel.ciel-runtime.transcript.delta",
            "subject": session_id,
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "datacontenttype": "application/json",
            "data": {
                "workspace_id": self.workspace_id,
                "runtime": runtime,
                "session_id": session_id,
                "transcript_name": path.name,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "content_sha256": digest,
                "record_count": content.count(b"\n"),
                "rotated": rotated,
                "format": "jsonl",
                "content_filter": content_filter,
                "content": content.decode("utf-8", errors="replace"),
            },
        }

    def _post(
        self, settings: TranscriptDeliverySettings, event: dict[str, Any]
    ) -> bool:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        headers = {
            "Content-Type": "application/cloudevents+json",
            "Accept": "application/json",
            "User-Agent": "ciel-runtime-transcript-delivery/1",
            "Idempotency-Key": str(event["id"]),
        }
        authorization, missing = expand_environment_references(settings.authorization)
        if missing:
            self._report_error(
                "authorization environment variable(s) missing: " + ", ".join(missing)
            )
            return False
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(
            settings.url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(
                request, timeout=settings.timeout_seconds
            ) as response:
                status = int(getattr(response, "status", 200) or 200)
                response.read(4096)
        except urllib.error.HTTPError as exc:
            self._report_error(f"HTTP {exc.code} from transcript endpoint")
            return False
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            self._report_error(f"delivery {type(exc).__name__}: {exc}")
            return False
        if not 200 <= status < 300:
            self._report_error(f"HTTP {status} from transcript endpoint")
            return False
        return True

    def _report_error(self, message: str) -> None:
        now = self.ports.epoch()
        if message == self._last_error and now - self._last_error_at < 30.0:
            return
        self._last_error = message
        self._last_error_at = now
        self.ports.log("WARN", f"transcript_delta_delivery_failed {message}")


__all__ = [
    "TranscriptDeliveryPorts",
    "TranscriptDeliverySettings",
    "TranscriptDeltaDeliveryService",
    "ToolCallEventSettings",
]
