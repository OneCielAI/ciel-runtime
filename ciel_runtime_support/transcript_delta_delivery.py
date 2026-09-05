"""Incremental transcript delivery to one configured outbound HTTP endpoint."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any
import urllib.error
import urllib.request

from .remote_instructions import expand_environment_references
from .tool_call_events import project_transcript_tool_calls
from .web_search_result_events import project_web_search_results


@dataclass(frozen=True, slots=True)
class TranscriptDeliverySettings:
    enabled: bool
    url: str
    authorization: str
    timeout_seconds: float
    poll_interval_seconds: float
    max_batch_bytes: int
    start_mode: str

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


@dataclass(frozen=True, slots=True)
class TranscriptDeliveryPorts:
    load_config: Callable[[], dict[str, Any]]
    latest_transcript: Callable[[], Path | None]
    scope: Callable[[], dict[str, Any]]
    log: Callable[[str, str], None]
    epoch: Callable[[], float] = time.time
    event_publish: Callable[..., Any] = lambda **_kwargs: None
    event_recent: Callable[..., list[dict[str, Any]]] = lambda **_kwargs: []


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
        payload = self._read_complete_batch(path, offset, settings.max_batch_bytes)
        if not payload:
            if rotated:
                destinations[destination_key] = self._cursor_record(path, offset)
                self._save_cursors(cursors)
            return False
        end_offset = offset + len(payload)
        event = self._cloud_event(
            runtime=runtime,
            session_id=session_id,
            path=path,
            start_offset=offset,
            end_offset=end_offset,
            content=payload,
            rotated=rotated,
        )
        if not self._post(settings, event):
            return False
        destinations[destination_key] = self._cursor_record(path, end_offset)
        self._save_cursors(cursors)
        self.ports.log(
            "INFO",
            "transcript_delta_delivered "
            f"runtime={runtime} session={session_id} bytes={len(payload)} "
            f"offset={offset}->{end_offset}",
        )
        self._last_error = ""
        return True

    def poll_tool_call_events(self) -> int:
        settings = ToolCallEventSettings.from_config(self.ports.load_config())
        if not settings.enabled:
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
        payload = self._read_complete_batch(path, offset, settings.max_batch_bytes)
        if not payload:
            return 0
        count = 0
        search_state = current.setdefault("web_search_results", {})
        for raw_line in payload.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
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
        sources[source_key] = self._cursor_record(path, offset + len(payload))
        sources[source_key]["web_search_results"] = search_state
        self._save_cursors(cursors)
        return count

    @staticmethod
    def _initial_offset(
        settings: TranscriptDeliverySettings, scope: dict[str, Any], path: Path
    ) -> int:
        if settings.start_mode == "beginning":
            return 0
        boundary_path = scope.get("turn_scan_path")
        if boundary_path is None:
            return 0
        try:
            if Path(boundary_path).resolve() != path:
                return 0
        except (OSError, TypeError, ValueError):
            return 0
        return max(0, int(scope.get("turn_scan_offset") or 0))

    def _run(self) -> None:
        while not self._stop.is_set():
            interval = 1.0
            try:
                config = self.ports.load_config()
                settings = TranscriptDeliverySettings.from_config(config)
                tool_settings = ToolCallEventSettings.from_config(config)
                interval = min(settings.poll_interval_seconds, tool_settings.poll_interval_seconds)
                if tool_settings.enabled:
                    self.poll_tool_call_events()
                if settings.enabled and settings.url:
                    self.poll_once()
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
        started_at = float(scope.get("started_at") or 0)
        if started_at > 0:
            try:
                if path.stat().st_mtime >= started_at - 1.0:
                    return 0
            except OSError:
                pass
        return size

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

    def _read_complete_batch(self, path: Path, offset: int, limit: int) -> bytes:
        try:
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(limit + 1)
        except OSError as exc:
            self._report_error(f"read {type(exc).__name__}: {exc}")
            return b""
        if not data:
            return b""
        bounded = data[:limit]
        complete_end = bounded.rfind(b"\n")
        if complete_end < 0:
            if len(data) > limit:
                self._report_error(
                    f"record exceeds max_batch_bytes={limit} path={path.name} offset={offset}"
                )
            return b""
        return bounded[: complete_end + 1]

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
