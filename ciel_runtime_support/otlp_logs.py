"""OTLP/HTTP JSON log ingestion with durable cursor-addressable storage."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .channel_message_repository import exclusive_file_lock


OTLP_LOGS_PATH = "/v1/logs"
OTLP_JSON_CONTENT_TYPE = "application/json"
OTLP_LOG_REQUEST_MAX_BYTES = 64 * 1024 * 1024

DEFAULT_SEGMENT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SEGMENTS = 8
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
MIN_SEGMENT_MAX_BYTES = 64 * 1024
MAX_SEGMENT_MAX_BYTES = 1024 * 1024 * 1024
MAX_RETAINED_SEGMENTS = 1024
MAX_TTL_SECONDS = 365 * 24 * 60 * 60
MAX_TOOL_READ_BYTES = 1024 * 1024

POLICY_ATTRIBUTE_KEYS = {
    "segment_max_bytes": "ciel.log.roll.max_bytes",
    "max_segments": "ciel.log.retention.max_segments",
    "ttl_seconds": "ciel.log.retention.ttl_seconds",
}


@dataclass(frozen=True, slots=True)
class TelemetryLogRecord:
    logical_file: str
    text: str
    policy: dict[str, int]


@dataclass(frozen=True, slots=True)
class TelemetryLogRange:
    file: str
    segment: int
    line_start: int
    line_end: int
    offset_start: int
    offset_end: int
    records: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "segment": self.segment,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "offset_start": self.offset_start,
            "offset_end": self.offset_end,
            "records": self.records,
        }


def _bounded_policy_value(name: str, value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{POLICY_ATTRIBUTE_KEYS[name]} must be an integer") from exc
    if name == "segment_max_bytes":
        if not MIN_SEGMENT_MAX_BYTES <= parsed <= MAX_SEGMENT_MAX_BYTES:
            raise ValueError(
                f"{POLICY_ATTRIBUTE_KEYS[name]} must be between "
                f"{MIN_SEGMENT_MAX_BYTES} and {MAX_SEGMENT_MAX_BYTES}"
            )
    elif name == "max_segments":
        if not 1 <= parsed <= MAX_RETAINED_SEGMENTS:
            raise ValueError(
                f"{POLICY_ATTRIBUTE_KEYS[name]} must be between 1 and "
                f"{MAX_RETAINED_SEGMENTS}"
            )
    elif name == "ttl_seconds":
        if parsed != 0 and not 60 <= parsed <= MAX_TTL_SECONDS:
            raise ValueError(
                f"{POLICY_ATTRIBUTE_KEYS[name]} must be 0 or between 60 and "
                f"{MAX_TTL_SECONDS}"
            )
    return parsed


def decode_otlp_any_value(value: Any) -> Any:
    """Decode the JSON mapping of opentelemetry.proto.common.v1.AnyValue."""

    if not isinstance(value, Mapping):
        return None
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if key in value:
            raw = value[key]
            if key == "intValue":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return raw
            return raw
    array_value = value.get("arrayValue")
    if isinstance(array_value, Mapping):
        values = array_value.get("values")
        return [decode_otlp_any_value(item) for item in values] if isinstance(values, list) else []
    kvlist_value = value.get("kvlistValue")
    if isinstance(kvlist_value, Mapping):
        return decode_otlp_attributes(kvlist_value.get("values"))
    return None


def decode_otlp_attributes(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    attributes: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            attributes[key] = decode_otlp_any_value(item.get("value"))
    return attributes


def _logical_log_file(record: Mapping[str, Any], resource: Mapping[str, Any]) -> str:
    raw = (
        record.get("log.file.name")
        or record.get("log.file.path")
        or resource.get("log.file.name")
        or resource.get("log.file.path")
        or ""
    )
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        service = str(resource.get("service.name") or "otlp").strip()
        name = f"{service or 'otlp'}.log"
    name = name.replace("\x00", "")[:200].strip(" .")
    return name or "otlp.log"


def _record_policy(record: Mapping[str, Any], resource: Mapping[str, Any]) -> dict[str, int]:
    policy: dict[str, int] = {}
    for name, attribute in POLICY_ATTRIBUTE_KEYS.items():
        value = record.get(attribute) if attribute in record else resource.get(attribute)
        if value is not None:
            policy[name] = _bounded_policy_value(name, value)
    return policy


def _render_otlp_log_record(record: Mapping[str, Any], attributes: Mapping[str, Any]) -> str:
    original = attributes.get("log.record.original")
    if isinstance(original, str) and original:
        return original
    body = decode_otlp_any_value(record.get("body"))
    if isinstance(body, str):
        rendered_body = body
    elif body is not None:
        rendered_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    else:
        rendered_body = json.dumps(
            {
                key: value
                for key, value in record.items()
                if key not in {"attributes", "body"}
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    timestamp = str(record.get("timeUnixNano") or record.get("observedTimeUnixNano") or "").strip()
    severity = str(record.get("severityText") or "").strip()
    prefix = " ".join(value for value in (timestamp, severity) if value)
    return f"{prefix} {rendered_body}" if prefix else rendered_body


def extract_otlp_log_records(payload: Mapping[str, Any]) -> tuple[list[TelemetryLogRecord], int, list[str]]:
    """Project valid OTLP JSON LogRecords and count malformed record entries."""

    resource_logs = payload.get("resourceLogs", [])
    if resource_logs is None:
        resource_logs = []
    if not isinstance(resource_logs, list):
        raise ValueError("resourceLogs must be an array")
    records: list[TelemetryLogRecord] = []
    rejected = 0
    errors: list[str] = []
    for resource_item in resource_logs:
        if not isinstance(resource_item, Mapping):
            rejected += 1
            errors.append("resourceLogs item is not an object")
            continue
        resource = resource_item.get("resource")
        resource_attributes = decode_otlp_attributes(
            resource.get("attributes") if isinstance(resource, Mapping) else None
        )
        scope_logs = resource_item.get("scopeLogs", [])
        if not isinstance(scope_logs, list):
            rejected += 1
            errors.append("scopeLogs is not an array")
            continue
        for scope_item in scope_logs:
            if not isinstance(scope_item, Mapping):
                rejected += 1
                errors.append("scopeLogs item is not an object")
                continue
            log_records = scope_item.get("logRecords", [])
            if not isinstance(log_records, list):
                rejected += 1
                errors.append("logRecords is not an array")
                continue
            for raw_record in log_records:
                if not isinstance(raw_record, Mapping):
                    rejected += 1
                    errors.append("logRecords item is not an object")
                    continue
                try:
                    attributes = decode_otlp_attributes(raw_record.get("attributes"))
                    logical_file = _logical_log_file(attributes, resource_attributes)
                    policy = _record_policy(attributes, resource_attributes)
                    text = _render_otlp_log_record(raw_record, attributes)
                except ValueError as exc:
                    rejected += 1
                    errors.append(str(exc))
                    continue
                records.append(TelemetryLogRecord(logical_file, text, policy))
    return records, rejected, list(dict.fromkeys(errors))


class TelemetryLogRepository:
    """File-per-source segmented log store with byte and line cursors."""

    def __init__(
        self,
        root: Path,
        *,
        now: Callable[[], float] = time.time,
        default_segment_max_bytes: int = DEFAULT_SEGMENT_MAX_BYTES,
        default_max_segments: int = DEFAULT_MAX_SEGMENTS,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.root = root
        self.manifest_path = root / "manifest.json"
        self.lock_path = root / ".store"
        self.now = now
        self.defaults = {
            "segment_max_bytes": _bounded_policy_value("segment_max_bytes", default_segment_max_bytes),
            "max_segments": _bounded_policy_value("max_segments", default_max_segments),
            "ttl_seconds": _bounded_policy_value("ttl_seconds", default_ttl_seconds),
        }
        self._lock = threading.RLock()

    @staticmethod
    def _storage_key(logical_file: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", logical_file).strip("._-") or "log"
        digest = hashlib.sha256(logical_file.encode("utf-8")).hexdigest()[:10]
        return f"{stem[:80]}--{digest}"

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("version", 1)
        value.setdefault("files", {})
        if not isinstance(value["files"], dict):
            value["files"] = {}
        return value

    def _save(self, manifest: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_name(
            f"{self.manifest_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.manifest_path)

    def _file_state(self, manifest: dict[str, Any], logical_file: str) -> dict[str, Any]:
        files = manifest["files"]
        state = files.get(logical_file)
        if not isinstance(state, dict):
            state = {
                "storage_key": self._storage_key(logical_file),
                "policy": dict(self.defaults),
                "next_segment": 1,
                "segments": [],
            }
            files[logical_file] = state
        state.setdefault("storage_key", self._storage_key(logical_file))
        policy = state.get("policy")
        if not isinstance(policy, dict):
            policy = dict(self.defaults)
            state["policy"] = policy
        for key, value in self.defaults.items():
            policy.setdefault(key, value)
        if not isinstance(state.get("segments"), list):
            state["segments"] = []
        state["next_segment"] = max(1, int(state.get("next_segment") or 1))
        return state

    def _segment_path(self, state: Mapping[str, Any], segment_id: int) -> Path:
        return self.root / str(state["storage_key"]) / f"{segment_id:08d}.log"

    def _new_segment(self, state: dict[str, Any], now: float) -> dict[str, Any]:
        segment_id = int(state["next_segment"])
        state["next_segment"] = segment_id + 1
        segment = {
            "id": segment_id,
            "bytes": 0,
            "lines": 0,
            "records": 0,
            "created_at": now,
            "updated_at": now,
        }
        state["segments"].append(segment)
        return segment

    def _remove_segment(self, state: dict[str, Any], segment: Mapping[str, Any]) -> None:
        self._segment_path(state, int(segment.get("id") or 0)).unlink(missing_ok=True)
        state["segments"] = [item for item in state["segments"] if item is not segment]

    def _apply_retention(self, state: dict[str, Any], now: float) -> int:
        removed = 0
        policy = state["policy"]
        ttl_seconds = int(policy.get("ttl_seconds") or 0)
        if ttl_seconds > 0:
            cutoff = now - ttl_seconds
            for segment in list(state["segments"]):
                if float(segment.get("updated_at") or 0) < cutoff:
                    self._remove_segment(state, segment)
                    removed += 1
        maximum = int(policy.get("max_segments") or DEFAULT_MAX_SEGMENTS)
        while len(state["segments"]) > maximum:
            self._remove_segment(state, state["segments"][0])
            removed += 1
        return removed

    def append(self, records: list[TelemetryLogRecord]) -> list[TelemetryLogRange]:
        if not records:
            return []
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                now = self.now()
                ranges: list[TelemetryLogRange] = []
                for record in records:
                    state = self._file_state(manifest, record.logical_file)
                    for key, value in record.policy.items():
                        state["policy"][key] = _bounded_policy_value(key, value)
                    self._apply_retention(state, now)
                    data = record.text.encode("utf-8", errors="replace")
                    if not data.endswith(b"\n"):
                        data += b"\n"
                    segment = state["segments"][-1] if state["segments"] else None
                    maximum = int(state["policy"]["segment_max_bytes"])
                    if (
                        segment is None
                        or (int(segment.get("bytes") or 0) > 0 and int(segment.get("bytes") or 0) + len(data) > maximum)
                    ):
                        segment = self._new_segment(state, now)
                    path = self._segment_path(state, int(segment["id"]))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    offset_start = int(segment.get("bytes") or 0)
                    line_start = int(segment.get("lines") or 0) + 1
                    with path.open("ab") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    added_lines = data.count(b"\n")
                    segment["bytes"] = offset_start + len(data)
                    segment["lines"] = line_start + added_lines - 1
                    segment["records"] = int(segment.get("records") or 0) + 1
                    segment["updated_at"] = now
                    current = TelemetryLogRange(
                        file=record.logical_file,
                        segment=int(segment["id"]),
                        line_start=line_start,
                        line_end=int(segment["lines"]),
                        offset_start=offset_start,
                        offset_end=int(segment["bytes"]),
                        records=1,
                    )
                    if (
                        ranges
                        and ranges[-1].file == current.file
                        and ranges[-1].segment == current.segment
                        and ranges[-1].offset_end == current.offset_start
                    ):
                        previous = ranges[-1]
                        ranges[-1] = TelemetryLogRange(
                            previous.file,
                            previous.segment,
                            previous.line_start,
                            current.line_end,
                            previous.offset_start,
                            current.offset_end,
                            previous.records + 1,
                        )
                    else:
                        ranges.append(current)
                    self._apply_retention(state, now)
                retained = {
                    (logical_file, int(segment.get("id") or 0))
                    for logical_file in manifest["files"]
                    for segment in self._file_state(manifest, logical_file)["segments"]
                }
                ranges = [
                    item for item in ranges if (item.file, item.segment) in retained
                ]
                self._save(manifest)
                return ranges

    def cleanup(self) -> dict[str, int]:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                removed = 0
                for logical_file in list(manifest["files"]):
                    state = self._file_state(manifest, logical_file)
                    removed += self._apply_retention(state, self.now())
                self._save(manifest)
                return {"removed_segments": removed}

    def list_files(self) -> list[dict[str, Any]]:
        with self._lock:
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                items: list[dict[str, Any]] = []
                for logical_file in sorted(manifest["files"]):
                    state = self._file_state(manifest, logical_file)
                    segments = [dict(item) for item in state["segments"]]
                    items.append(
                        {
                            "file": logical_file,
                            "policy": dict(state["policy"]),
                            "segments": segments,
                            "total_bytes": sum(int(item.get("bytes") or 0) for item in segments),
                            "total_lines": sum(int(item.get("lines") or 0) for item in segments),
                            "total_records": sum(int(item.get("records") or 0) for item in segments),
                        }
                    )
                return items

    def read(
        self,
        logical_file: str,
        *,
        segment: int | None = None,
        offset: int = 0,
        max_bytes: int = 64 * 1024,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> dict[str, Any]:
        maximum = max(1, min(MAX_TOOL_READ_BYTES, int(max_bytes)))
        with self._lock:
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                state = manifest["files"].get(logical_file)
                if not isinstance(state, dict):
                    raise FileNotFoundError(f"telemetry log file is not known: {logical_file}")
                segments = state.get("segments") if isinstance(state.get("segments"), list) else []
                if not segments:
                    raise FileNotFoundError(f"telemetry log file has no retained segments: {logical_file}")
                selected = (
                    next((item for item in segments if int(item.get("id") or 0) == int(segment)), None)
                    if segment is not None
                    else segments[-1]
                )
                if selected is None:
                    raise FileNotFoundError(f"telemetry log segment is not retained: {logical_file}#{segment}")
                path = self._segment_path(state, int(selected["id"]))
                if line_start is not None:
                    first = max(1, int(line_start))
                    last = max(first, int(line_end if line_end is not None else first + 199))
                    chunks: list[bytes] = []
                    used = 0
                    actual_start = 0
                    actual_end = 0
                    start_offset = 0
                    next_offset = 0
                    with path.open("rb") as stream:
                        line_number = 0
                        while True:
                            before = stream.tell()
                            line = stream.readline()
                            if not line:
                                next_offset = stream.tell()
                                break
                            line_number += 1
                            if line_number < first:
                                continue
                            if line_number > last or used + len(line) > maximum:
                                next_offset = before
                                break
                            if not chunks:
                                start_offset = before
                                actual_start = line_number
                            chunks.append(line)
                            used += len(line)
                            actual_end = line_number
                            next_offset = stream.tell()
                    data = b"".join(chunks)
                    offset_start = start_offset
                else:
                    offset_start = max(0, int(offset))
                    with path.open("rb") as stream:
                        stream.seek(min(offset_start, int(selected.get("bytes") or 0)))
                        offset_start = stream.tell()
                        data = stream.read(maximum)
                        next_offset = stream.tell()
                    actual_start = None
                    actual_end = None
                return {
                    "file": logical_file,
                    "segment": int(selected["id"]),
                    "offset_start": offset_start,
                    "offset_end": next_offset,
                    "line_start": actual_start,
                    "line_end": actual_end,
                    "content": data.decode("utf-8", errors="replace"),
                    "next_cursor": {
                        "file": logical_file,
                        "segment": int(selected["id"]),
                        "offset": next_offset,
                    },
                    "segment_bytes": int(selected.get("bytes") or 0),
                    "eof": next_offset >= int(selected.get("bytes") or 0),
                }

    def configure(self, logical_file: str, policy: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                state = self._file_state(manifest, logical_file)
                for key in self.defaults:
                    if key in policy and policy[key] is not None:
                        state["policy"][key] = _bounded_policy_value(key, policy[key])
                self._apply_retention(state, self.now())
                self._save(manifest)
                return {"file": logical_file, "policy": dict(state["policy"])}

    def roll(self, logical_file: str) -> dict[str, Any]:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                state = self._file_state(manifest, logical_file)
                segment = self._new_segment(state, self.now())
                self._segment_path(state, int(segment["id"])).parent.mkdir(parents=True, exist_ok=True)
                self._segment_path(state, int(segment["id"])).touch(exist_ok=True)
                self._apply_retention(state, self.now())
                self._save(manifest)
                return {"file": logical_file, "segment": int(segment["id"])}

    def delete(self, logical_file: str, segment: int | None = None) -> dict[str, Any]:
        with self._lock:
            with exclusive_file_lock(self.lock_path):
                manifest = self._load()
                state = manifest["files"].get(logical_file)
                if not isinstance(state, dict):
                    raise FileNotFoundError(f"telemetry log file is not known: {logical_file}")
                if segment is None:
                    removed = len(state.get("segments") or [])
                    for item in list(state.get("segments") or []):
                        self._remove_segment(state, item)
                    manifest["files"].pop(logical_file, None)
                else:
                    item = next(
                        (value for value in state.get("segments") or [] if int(value.get("id") or 0) == int(segment)),
                        None,
                    )
                    if item is None:
                        raise FileNotFoundError(f"telemetry log segment is not retained: {logical_file}#{segment}")
                    self._remove_segment(state, item)
                    removed = 1
                self._save(manifest)
                return {"file": logical_file, "segment": segment, "removed_segments": removed}


class TelemetryLogRetentionService:
    def __init__(
        self,
        repository: TelemetryLogRepository,
        log: Callable[[str, str], None],
        interval_seconds: float = 60.0,
    ) -> None:
        self.repository = repository
        self.log = log
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ciel-telemetry-log-retention",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                result = self.repository.cleanup()
                if result["removed_segments"]:
                    self.log(
                        "INFO",
                        f"telemetry_log_retention removed_segments={result['removed_segments']}",
                    )
            except Exception as exc:
                self.log(
                    "WARN",
                    f"telemetry_log_retention_failed error={type(exc).__name__}: {exc}",
                )


@dataclass(frozen=True, slots=True)
class OtlpLogsHttpPorts:
    repository: TelemetryLogRepository
    submit_notice: Callable[[list[dict[str, Any]], int], dict[str, Any]]
    write_json: Callable[..., None]
    log: Callable[[str, str], None]
    authenticate: Callable[[Any], bool]


class OtlpLogsHttpController:
    def __init__(self, ports: OtlpLogsHttpPorts) -> None:
        self.ports = ports

    def post(self, handler: Any, path: str, raw: bytes, content_type: str) -> bool:
        if path != OTLP_LOGS_PATH:
            return False
        if not self.ports.authenticate(handler):
            self._unauthorized(handler)
            return True
        media_type = str(content_type or "").split(";", 1)[0].strip().lower()
        if media_type != OTLP_JSON_CONTENT_TYPE:
            self._status(
                handler,
                415,
                "Ciel Runtime currently accepts OTLP/HTTP JSON at /v1/logs; "
                "configure the exporter protocol as http/json",
            )
            return True
        try:
            decoded = self._decoded_body(handler, raw)
            payload = json.loads(decoded.decode("utf-8") if decoded else "{}")
            if not isinstance(payload, dict):
                raise ValueError("ExportLogsServiceRequest must be a JSON object")
            records, rejected, errors = extract_otlp_log_records(payload)
            ranges = self.ports.repository.append(records)
            if ranges:
                self.ports.submit_notice(
                    [item.as_dict() for item in ranges],
                    len(records),
                )
            response: dict[str, Any] = {}
            if rejected:
                response["partialSuccess"] = {
                    "rejectedLogRecords": str(rejected),
                    "errorMessage": "; ".join(errors)[:1200],
                }
            self.ports.write_json(handler, response, 200)
            self.ports.log(
                "INFO",
                f"otlp_logs_ingested accepted={len(records)} rejected={rejected} ranges={len(ranges)}",
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            self._status(handler, 400, str(exc))
        except OSError as exc:
            self.ports.log(
                "ERROR", f"otlp_logs_storage_failed error={type(exc).__name__}: {exc}"
            )
            self._status(handler, 503, "telemetry log storage is temporarily unavailable")
        return True

    @staticmethod
    def _decoded_body(handler: Any, raw: bytes) -> bytes:
        encoding = str(handler.headers.get("content-encoding") or "").strip().lower()
        if encoding in {"", "identity"}:
            decoded = raw
        elif encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
                decoded = stream.read(OTLP_LOG_REQUEST_MAX_BYTES + 1)
        else:
            raise ValueError(f"unsupported Content-Encoding: {encoding}")
        if len(decoded) > OTLP_LOG_REQUEST_MAX_BYTES:
            raise ValueError(
                f"decoded OTLP log request exceeds {OTLP_LOG_REQUEST_MAX_BYTES} bytes"
            )
        return decoded

    def _status(self, handler: Any, status: int, message: str) -> None:
        self.ports.write_json(handler, {"code": 3, "message": message}, status)

    @staticmethod
    def _unauthorized(handler: Any) -> None:
        body = json.dumps(
            {"code": 16, "message": "OTLP log ingestion authentication is required"}
        ).encode("utf-8")
        handler.send_response(401)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(body)))
        handler.send_header("www-authenticate", 'Bearer realm="ciel-runtime-otlp"')
        handler.end_headers()
        handler.wfile.write(body)


class TelemetryLogRuntime:
    """Own the repository, retention worker, HTTP adapter, and MCP operations."""

    def __init__(
        self,
        root: Path,
        submit_notice: Callable[[list[dict[str, Any]], int], dict[str, Any]],
        write_json: Callable[..., None],
        log: Callable[[str, str], None],
    ) -> None:
        self.repository = TelemetryLogRepository(root)
        self.token = TelemetryLogTokenRepository(root / "ingest-token")
        self.token.ensure()
        self.retention = TelemetryLogRetentionService(self.repository, log)
        self.http = OtlpLogsHttpController(
            OtlpLogsHttpPorts(
                self.repository,
                submit_notice,
                write_json,
                log,
                self.token.authenticate,
            )
        )

    def start(self) -> None:
        self.token.ensure()
        self.retention.start()

    def stop(self) -> None:
        self.retention.stop()

    def tool(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        logical_file = str(args.get("file") or "").strip()
        if action == "list":
            return {"files": self.repository.list_files()}
        if action == "read":
            return self.repository.read(
                logical_file,
                segment=int(args["segment"]) if args.get("segment") is not None else None,
                offset=int(args.get("offset") or 0),
                max_bytes=int(args.get("max_bytes") or 64 * 1024),
                line_start=int(args["line_start"]) if args.get("line_start") is not None else None,
                line_end=int(args["line_end"]) if args.get("line_end") is not None else None,
            )
        if action == "configure":
            return self.repository.configure(
                logical_file,
                {
                    key: args.get(key)
                    for key in ("segment_max_bytes", "max_segments", "ttl_seconds")
                    if args.get(key) is not None
                },
            )
        if action == "roll":
            return self.repository.roll(logical_file)
        if action == "delete":
            return self.repository.delete(
                logical_file,
                int(args["segment"]) if args.get("segment") is not None else None,
            )
        raise ValueError(f"unsupported telemetry_logs action: {action}")


class TelemetryLogTokenRepository:
    """Workspace-scoped bearer token for the input-only OTLP endpoint."""

    def __init__(self, path: Path, environment: Mapping[str, str] | None = None) -> None:
        self.path = path
        self.environment = os.environ if environment is None else environment

    def get(self) -> str:
        configured = str(self.environment.get("CIEL_RUNTIME_OTLP_LOG_TOKEN") or "").strip()
        if configured:
            return configured
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def ensure(self) -> str:
        existing = self.get()
        if existing:
            return existing
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(token + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        return token

    def authenticate(self, handler: Any) -> bool:
        try:
            authorization = str(handler.headers.get("authorization") or "")
            supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            supplied = supplied or str(handler.headers.get("x-ciel-telemetry-token") or "").strip()
        except Exception:
            return False
        expected = self.get()
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))


__all__ = [
    "DEFAULT_MAX_SEGMENTS",
    "DEFAULT_SEGMENT_MAX_BYTES",
    "DEFAULT_TTL_SECONDS",
    "MAX_TOOL_READ_BYTES",
    "OTLP_LOG_REQUEST_MAX_BYTES",
    "OTLP_LOGS_PATH",
    "OtlpLogsHttpController",
    "OtlpLogsHttpPorts",
    "TelemetryLogRange",
    "TelemetryLogRecord",
    "TelemetryLogRepository",
    "TelemetryLogRetentionService",
    "TelemetryLogRuntime",
    "TelemetryLogTokenRepository",
    "decode_otlp_any_value",
    "decode_otlp_attributes",
    "extract_otlp_log_records",
]
