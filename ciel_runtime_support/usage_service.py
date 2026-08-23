"""Workspace usage ledger, authenticated HTTP API, and reliable push delivery."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from .remote_instructions import expand_environment_references
from .router_access import router_request_bearer_token
from .usage_events import UsageEvent


def _utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None, default: float) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValueError(f"invalid time value: {text}") from exc


class SqliteUsageLedger:
    """Restart-safe workspace ledger and delivery cursor repository."""

    def __init__(self, path: Path, workspace_id: str, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.workspace_id = workspace_id
        self.clock = clock
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with self._database() as db:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS usage_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        timestamp REAL NOT NULL,
                        request_started_at REAL NOT NULL,
                        request_completed_at REAL NOT NULL,
                        duration_ms REAL NOT NULL,
                        workspace_id TEXT NOT NULL,
                        runtime TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        protocol TEXT NOT NULL,
                        status TEXT NOT NULL,
                        input_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL,
                        cache_read_input_tokens INTEGER NOT NULL,
                        cache_write_input_tokens INTEGER NOT NULL,
                        input_tokens_total INTEGER NOT NULL,
                        reasoning_output_tokens INTEGER NOT NULL,
                        usage_source TEXT NOT NULL,
                        is_estimated INTEGER NOT NULL,
                        is_incomplete INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS usage_events_completed_idx
                        ON usage_events(request_completed_at, seq);
                    CREATE INDEX IF NOT EXISTS usage_events_route_idx
                        ON usage_events(provider, model, request_completed_at);
                    CREATE INDEX IF NOT EXISTS usage_events_runtime_idx
                        ON usage_events(runtime, request_completed_at);
                    CREATE TABLE IF NOT EXISTS usage_delivery_cursors (
                        destination_key TEXT PRIMARY KEY,
                        endpoint_id TEXT NOT NULL,
                        event_cursor INTEGER NOT NULL,
                        audit_start REAL NOT NULL,
                        audit_end REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS usage_api_keys (
                        key_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        secret_hash TEXT NOT NULL UNIQUE,
                        scopes TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        revoked_at REAL NOT NULL,
                        last_used_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS usage_import_cursors (
                        source_path TEXT PRIMARY KEY,
                        source_head_sha256 TEXT NOT NULL,
                        byte_offset INTEGER NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    """
                )
            self._initialized = True

    def record(self, event: UsageEvent) -> None:
        normalized = event.normalized(self.clock)
        if not normalized.workspace_id:
            normalized = replace(normalized, workspace_id=self.workspace_id)
        with self._condition:
            self._ensure()
            values = asdict(normalized)
            with self._database() as db:
                db.execute(
                    """
                    INSERT OR IGNORE INTO usage_events (
                        event_id,timestamp,request_started_at,request_completed_at,duration_ms,
                        workspace_id,runtime,session_id,turn_id,request_id,provider,model,
                        protocol,status,input_tokens,output_tokens,cache_read_input_tokens,
                        cache_write_input_tokens,input_tokens_total,reasoning_output_tokens,
                        usage_source,is_estimated,is_incomplete
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        values["event_id"], values["timestamp"], values["request_started_at"],
                        values["request_completed_at"], values["duration_ms"], values["workspace_id"],
                        values["runtime"], values["session_id"], values["turn_id"], values["request_id"],
                        values["provider"], values["model"], values["protocol"], values["status"],
                        values["input_tokens"], values["output_tokens"],
                        values["cache_read_input_tokens"], values["cache_write_input_tokens"],
                        values["input_tokens_total"], values["reasoning_output_tokens"],
                        values["usage_source"],
                        int(values["is_estimated"]), int(values["is_incomplete"]),
                    ),
                )
            self._condition.notify_all()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["is_estimated"] = bool(value["is_estimated"])
        value["is_incomplete"] = bool(value["is_incomplete"])
        value["observed_at"] = _utc_text(float(value["timestamp"]))
        value["request_started_at_iso"] = _utc_text(float(value["request_started_at"]))
        value["request_completed_at_iso"] = _utc_text(float(value["request_completed_at"]))
        value["total_tokens"] = value["input_tokens_total"] + int(value["output_tokens"])
        return value

    def events(
        self,
        *,
        start: float = 0.0,
        end: float | None = None,
        after: int = 0,
        limit: int = 200,
        runtime: str = "",
        provider: str = "",
        model: str = "",
    ) -> list[dict[str, Any]]:
        self._ensure()
        clauses = ["seq > ?", "request_completed_at >= ?", "request_completed_at <= ?"]
        values: list[Any] = [max(0, int(after)), float(start), float(end or self.clock())]
        for column, value in (("runtime", runtime), ("provider", provider), ("model", model)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        values.append(max(1, min(1000, int(limit))))
        with self._lock, self._database() as db:
            rows = db.execute(
                "SELECT * FROM usage_events WHERE " + " AND ".join(clauses) + " ORDER BY seq LIMIT ?",
                values,
            ).fetchall()
        return [self._row(row) for row in rows]

    def max_sequence(self) -> int:
        self._ensure()
        with self._lock, self._database() as db:
            row = db.execute("SELECT COALESCE(MAX(seq), 0) AS value FROM usage_events").fetchone()
        return int(row["value"])

    def wait_after(self, after: int, timeout: float) -> list[dict[str, Any]]:
        events = self.events(after=after, limit=200)
        if events:
            return events
        with self._condition:
            self._condition.wait(max(0.0, timeout))
        return self.events(after=after, limit=200)

    def summary(self, start: float, end: float) -> dict[str, Any]:
        self._ensure()
        with self._lock, self._database() as db:
            totals = db.execute(
                """
                SELECT COUNT(*) AS requests, COALESCE(MIN(seq),0) AS first_sequence,
                       COALESCE(MAX(seq),0) AS last_sequence,
                       COALESCE(SUM(input_tokens),0) AS input_tokens,
                       COALESCE(SUM(output_tokens),0) AS output_tokens,
                       COALESCE(SUM(cache_read_input_tokens),0) AS cache_read_input_tokens,
                       COALESCE(SUM(cache_write_input_tokens),0) AS cache_write_input_tokens,
                       COALESCE(SUM(input_tokens_total),0) AS input_tokens_total,
                       COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens,
                       COALESCE(SUM(is_estimated),0) AS estimated_events,
                       COALESCE(SUM(is_incomplete),0) AS incomplete_events
                FROM usage_events WHERE request_completed_at >= ? AND request_completed_at <= ?
                """,
                (start, end),
            ).fetchone()
            groups = db.execute(
                """
                SELECT runtime,provider,model,COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens),0) AS input_tokens,
                       COALESCE(SUM(output_tokens),0) AS output_tokens,
                       COALESCE(SUM(cache_read_input_tokens),0) AS cache_read_input_tokens,
                       COALESCE(SUM(cache_write_input_tokens),0) AS cache_write_input_tokens,
                       COALESCE(SUM(input_tokens_total),0) AS input_tokens_total,
                       COALESCE(SUM(reasoning_output_tokens),0) AS reasoning_output_tokens
                FROM usage_events WHERE request_completed_at >= ? AND request_completed_at <= ?
                GROUP BY runtime,provider,model ORDER BY runtime,provider,model
                """,
                (start, end),
            ).fetchall()
        total = dict(totals)
        total["total_tokens"] = total["input_tokens_total"] + total["output_tokens"]
        duration_seconds = max(0.0, end - start)
        hours = duration_seconds / 3600.0
        cache_denominator = max(0, int(total["input_tokens_total"]))
        rates = {
            "duration_seconds": duration_seconds,
            "requests_per_hour": (float(total["requests"]) / hours) if hours else 0.0,
            "tokens_per_hour": (float(total["total_tokens"]) / hours) if hours else 0.0,
            "cache_read_ratio": (
                float(total["cache_read_input_tokens"]) / cache_denominator
                if cache_denominator
                else 0.0
            ),
        }
        projected_groups = []
        for raw in groups:
            group = dict(raw)
            group["total_tokens"] = group["input_tokens_total"] + group["output_tokens"]
            projected_groups.append(group)
        return {
            "period": {"from": _utc_text(start), "to": _utc_text(end), "from_epoch": start, "to_epoch": end},
            "workspace_id": self.workspace_id,
            "totals": total,
            "rates": rates,
            "groups": projected_groups,
        }

    def delivery_cursor(self, key: str, endpoint_id: str, *, tail: bool, now: float) -> dict[str, Any]:
        self._ensure()
        with self._lock, self._database() as db:
            row = db.execute(
                "SELECT * FROM usage_delivery_cursors WHERE destination_key = ?", (key,)
            ).fetchone()
            if row is not None:
                return dict(row)
            cursor = self.max_sequence() if tail else 0
            db.execute(
                "INSERT INTO usage_delivery_cursors VALUES (?,?,?,?,?,?)",
                (key, endpoint_id, cursor, now, now, now),
            )
        return {"destination_key": key, "endpoint_id": endpoint_id, "event_cursor": cursor,
                "audit_start": now, "audit_end": now, "updated_at": now}

    def update_delivery_cursor(self, key: str, *, event_cursor: int | None = None,
                               audit_start: float | None = None, audit_end: float | None = None) -> None:
        self._ensure()
        assignments = ["updated_at = ?"]
        values: list[Any] = [self.clock()]
        for column, value in (("event_cursor", event_cursor), ("audit_start", audit_start), ("audit_end", audit_end)):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        values.append(key)
        with self._lock, self._database() as db:
            db.execute("UPDATE usage_delivery_cursors SET " + ",".join(assignments) + " WHERE destination_key = ?", values)

    def import_cursor(self, source_path: str) -> dict[str, Any] | None:
        self._ensure()
        with self._lock, self._database() as db:
            row = db.execute(
                "SELECT * FROM usage_import_cursors WHERE source_path = ?",
                (source_path,),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_import_cursor(self, source_path: str, head_sha256: str, byte_offset: int) -> None:
        self._ensure()
        with self._lock, self._database() as db:
            db.execute(
                """INSERT INTO usage_import_cursors
                   (source_path,source_head_sha256,byte_offset,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(source_path) DO UPDATE SET
                   source_head_sha256=excluded.source_head_sha256,
                   byte_offset=excluded.byte_offset,updated_at=excluded.updated_at""",
                (source_path, head_sha256, max(0, int(byte_offset)), self.clock()),
            )


class LegacyUsageBackfillService:
    """Incrementally import complete legacy usage JSONL records into the ledger."""

    def __init__(self, ledger: SqliteUsageLedger, log: Callable[[str, str], None]) -> None:
        self.ledger = ledger
        self.log = log

    @staticmethod
    def discover(config_dir: Path, workspace_state_dir: Path, workspace_id: str,
                 current_path: Path) -> list[Path]:
        candidates = {current_path, current_path.with_suffix(".jsonl.1")}
        roots = (
            config_dir / "router-instances",
            workspace_state_dir / "router-instances",
        )
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.glob("*/usage-events.jsonl*"):
                parent = path.parent.name
                if root == config_dir / "router-instances" and not parent.endswith("-" + workspace_id):
                    continue
                candidates.add(path)
        return sorted((path for path in candidates if path.is_file()), key=lambda item: str(item))

    def run(self, paths: list[Path]) -> dict[str, int]:
        result = {"files": 0, "records": 0, "skipped": 0}
        for path in paths:
            imported, skipped = self._import_file(path)
            result["files"] += 1
            result["records"] += imported
            result["skipped"] += skipped
        if result["files"]:
            self.log(
                "INFO",
                "usage_backfill_complete "
                f"files={result['files']} records={result['records']} skipped={result['skipped']}",
            )
        return result

    def _import_file(self, path: Path) -> tuple[int, int]:
        try:
            resolved = path.resolve()
            size = resolved.stat().st_size
            with resolved.open("rb") as stream:
                head = stream.readline(4096)
        except OSError:
            return 0, 0
        source = str(resolved)
        head_digest = hashlib.sha256(head).hexdigest()
        cursor = self.ledger.import_cursor(source)
        offset = int(cursor.get("byte_offset") or 0) if cursor else 0
        if cursor and (str(cursor.get("source_head_sha256") or "") != head_digest or size < offset):
            offset = 0
        imported = 0
        skipped = 0
        committed_offset = offset
        try:
            with resolved.open("rb") as stream:
                stream.seek(offset)
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        break
                    committed_offset = stream.tell()
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("usage row is not an object")
                        event = self._event(raw)
                        before = self.ledger.max_sequence()
                        self.ledger.record(event)
                        imported += int(self.ledger.max_sequence() > before)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        skipped += 1
        except OSError as exc:
            self.log("WARN", f"usage_backfill_read_failed path={resolved} error={type(exc).__name__}: {exc}")
            return imported, skipped
        self.ledger.update_import_cursor(source, head_digest, committed_offset)
        return imported, skipped

    def _event(self, raw: dict[str, Any]) -> UsageEvent:
        event_id = str(raw.get("event_id") or "").strip()
        if not event_id:
            canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            event_id = "legacy_" + hashlib.sha256(canonical).hexdigest()
        timestamp = float(raw.get("timestamp") or 0)
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        cache_write = int(raw.get("cache_write_input_tokens") or 0)
        input_tokens = int(raw.get("input_tokens") or 0)
        detailed = any(
            key in raw
            for key in (
                "cache_read_input_tokens", "cache_write_input_tokens",
                "reasoning_output_tokens", "input_tokens_total",
            )
        )
        return UsageEvent(
            provider=str(raw.get("provider") or ""), model=str(raw.get("model") or ""),
            input_tokens=input_tokens, output_tokens=int(raw.get("output_tokens") or 0),
            request_id=str(raw.get("request_id") or ""), protocol=str(raw.get("protocol") or ""),
            status=str(raw.get("status") or "completed"), timestamp=timestamp, event_id=event_id,
            runtime=str(raw.get("runtime") or ""),
            workspace_id=str(raw.get("workspace_id") or self.ledger.workspace_id),
            session_id=str(raw.get("session_id") or ""), turn_id=str(raw.get("turn_id") or ""),
            cache_read_input_tokens=cache_read, cache_write_input_tokens=cache_write,
            input_tokens_total=int(raw.get("input_tokens_total") or (input_tokens + cache_read + cache_write)),
            reasoning_output_tokens=int(raw.get("reasoning_output_tokens") or 0),
            request_started_at=float(raw.get("request_started_at") or timestamp),
            request_completed_at=float(raw.get("request_completed_at") or timestamp),
            duration_ms=float(raw.get("duration_ms") or 0),
            usage_source=str(raw.get("usage_source") or "legacy_ciel_jsonl"),
            is_estimated=bool(raw.get("is_estimated", False)),
            is_incomplete=bool(raw.get("is_incomplete", not detailed)),
        )


class UsageApiKeyRepository:
    def __init__(self, ledger: SqliteUsageLedger, pepper_path: Path, environ: Mapping[str, str] = os.environ,
                 clock: Callable[[], float] = time.time) -> None:
        self.ledger = ledger
        self.pepper_path = pepper_path
        self.environ = environ
        self.clock = clock
        self._lock = threading.Lock()

    def _pepper(self) -> bytes:
        try:
            return self.pepper_path.read_bytes()
        except OSError:
            with self._lock:
                try:
                    return self.pepper_path.read_bytes()
                except OSError:
                    self.pepper_path.parent.mkdir(parents=True, exist_ok=True)
                    value = secrets.token_bytes(32)
                    temporary = self.pepper_path.with_name(f"{self.pepper_path.name}.{os.getpid()}.tmp")
                    temporary.write_bytes(value)
                    os.chmod(temporary, 0o600)
                    temporary.replace(self.pepper_path)
                    return value

    def _hash(self, secret: str) -> str:
        return hmac.new(self._pepper(), secret.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, name: str, scopes: list[str] | tuple[str, ...] = ("usage:read", "usage:stream"),
              expires_at: float = 0.0, *, secret: str = "", key_id: str = "") -> dict[str, Any]:
        self.ledger._ensure()
        value = secret or f"cu_{secrets.token_urlsafe(32)}"
        identifier = key_id or f"uk_{secrets.token_hex(8)}"
        normalized_scopes = sorted({scope for scope in scopes if scope in {"usage:read", "usage:stream"}})
        if not normalized_scopes:
            raise ValueError("at least one usage scope is required")
        now = self.clock()
        with self.ledger._lock, self.ledger._database() as db:
            db.execute(
                """INSERT INTO usage_api_keys
                   (key_id,name,secret_hash,scopes,created_at,expires_at,revoked_at,last_used_at)
                   VALUES (?,?,?,?,?,?,0,0)
                   ON CONFLICT(key_id) DO UPDATE SET name=excluded.name,
                   secret_hash=excluded.secret_hash,scopes=excluded.scopes,
                   expires_at=excluded.expires_at,revoked_at=0""",
                (identifier, name or identifier, self._hash(value), json.dumps(normalized_scopes), now, max(0.0, expires_at)),
            )
        return {"key_id": identifier, "name": name or identifier, "api_key": value,
                "scopes": normalized_scopes, "created_at": now, "expires_at": max(0.0, expires_at)}

    def authenticate(self, handler: Any, required_scope: str) -> dict[str, Any] | None:
        supplied = router_request_bearer_token(handler)
        if not supplied:
            try:
                supplied = str(handler.headers.get("x-ciel-usage-key") or "").strip()
            except Exception:
                supplied = ""
        if not supplied:
            return None
        digest = self._hash(supplied)
        self.ledger._ensure()
        now = self.clock()
        with self.ledger._lock, self.ledger._database() as db:
            row = db.execute("SELECT * FROM usage_api_keys WHERE secret_hash = ?", (digest,)).fetchone()
            if row is None or float(row["revoked_at"]) > 0 or (float(row["expires_at"]) > 0 and float(row["expires_at"]) <= now):
                return None
            scopes = json.loads(str(row["scopes"]))
            if required_scope not in scopes:
                return None
            db.execute("UPDATE usage_api_keys SET last_used_at = ? WHERE key_id = ?", (now, row["key_id"]))
        return {"key_id": row["key_id"], "name": row["name"], "scopes": scopes}

    def list(self) -> list[dict[str, Any]]:
        self.ledger._ensure()
        with self.ledger._lock, self.ledger._database() as db:
            rows = db.execute("SELECT key_id,name,scopes,created_at,expires_at,revoked_at,last_used_at FROM usage_api_keys ORDER BY created_at").fetchall()
        return [{**dict(row), "scopes": json.loads(str(row["scopes"]))} for row in rows]

    def revoke(self, key_id: str) -> bool:
        self.ledger._ensure()
        with self.ledger._lock, self.ledger._database() as db:
            result = db.execute("UPDATE usage_api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at = 0", (self.clock(), key_id))
        return result.rowcount > 0

    def bootstrap_environment(self) -> int:
        raw = str(self.environ.get("CIEL_RUNTIME_USAGE_API_KEYS") or "").strip()
        single = str(self.environ.get("CIEL_RUNTIME_USAGE_API_KEY") or "").strip()
        entries: list[dict[str, Any]] = []
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    entries = [{"id": key, "key": value} for key, value in parsed.items()]
                elif isinstance(parsed, list):
                    entries = [item for item in parsed if isinstance(item, dict)]
            except json.JSONDecodeError:
                for part in raw.split(","):
                    key_id, separator, value = part.partition("=")
                    if separator and key_id.strip() and value.strip():
                        entries.append({"id": key_id.strip(), "key": value.strip()})
        if single:
            entries.append({"id": "environment", "key": single})
        count = 0
        for item in entries:
            secret = str(item.get("key") or item.get("secret") or "").strip()
            if not secret:
                continue
            scopes = item.get("scopes") or ["usage:read", "usage:stream"]
            self.issue(str(item.get("name") or item.get("id") or "environment"), list(scopes),
                       float(item.get("expires_at") or 0), secret=secret,
                       key_id=str(item.get("id") or f"env_{hashlib.sha256(secret.encode()).hexdigest()[:12]}"))
            count += 1
        return count


@dataclass(frozen=True, slots=True)
class UsagePushEndpoint:
    endpoint_id: str
    url: str
    authorization: str
    timeout_seconds: float = 5.0
    poll_interval_seconds: float = 1.0
    start_mode: str = "tail"
    audit_interval_seconds: float = 86400.0
    audit_emit_on_start: bool = True


def usage_push_endpoints(config: dict[str, Any], environ: Mapping[str, str]) -> list[UsagePushEndpoint]:
    usage = config.get("usage") if isinstance(config.get("usage"), dict) else {}
    raw_items = usage.get("push_endpoints") if isinstance(usage, dict) else []
    items = list(raw_items) if isinstance(raw_items, list) else []
    environment_json = str(environ.get("CIEL_RUNTIME_USAGE_PUSH_ENDPOINTS") or "").strip()
    if environment_json:
        parsed = json.loads(environment_json)
        items = parsed if isinstance(parsed, list) else [parsed]
    elif str(environ.get("CIEL_RUNTIME_USAGE_PUSH_URL") or "").strip():
        items = [{
            "id": str(environ.get("CIEL_RUNTIME_USAGE_PUSH_ID") or "environment"),
            "url": str(environ.get("CIEL_RUNTIME_USAGE_PUSH_URL") or ""),
            "authorization": str(environ.get("CIEL_RUNTIME_USAGE_PUSH_AUTHORIZATION") or ""),
            "api_key": str(environ.get("CIEL_RUNTIME_USAGE_PUSH_API_KEY") or ""),
            "audit_interval_seconds": str(environ.get("CIEL_RUNTIME_USAGE_AUDIT_INTERVAL_SECONDS") or "86400"),
        }]
    endpoints: list[UsagePushEndpoint] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        authorization = str(raw.get("authorization") or "").strip()
        if not authorization and str(raw.get("api_key") or "").strip():
            authorization = "Bearer " + str(raw["api_key"]).strip()
        endpoints.append(UsagePushEndpoint(
            endpoint_id=str(raw.get("id") or f"endpoint-{index + 1}"), url=url,
            authorization=authorization,
            timeout_seconds=max(0.1, min(30.0, float(raw.get("timeout_seconds") or 5))),
            poll_interval_seconds=max(0.1, min(60.0, float(raw.get("poll_interval_seconds") or 1))),
            start_mode="beginning" if str(raw.get("start_mode") or "tail").lower() == "beginning" else "tail",
            audit_interval_seconds=max(1.0, float(raw.get("audit_interval_seconds") or 86400)),
            audit_emit_on_start=bool(raw.get("audit_emit_on_start", True)),
        ))
    return endpoints


def usage_jsonl_enabled(config: dict[str, Any], environ: Mapping[str, str]) -> bool:
    usage = config.get("usage") if isinstance(config.get("usage"), dict) else {}
    configured = bool(usage.get("jsonl_enabled", True))
    raw = str(environ.get("CIEL_RUNTIME_USAGE_LOG", str(configured))).strip().lower()
    return raw not in {"0", "false", "off", "no", ""}


class UsagePushDeliveryService:
    def __init__(self, ledger: SqliteUsageLedger, load_config: Callable[[], dict[str, Any]],
                 environ: Mapping[str, str], log: Callable[[str, str], None],
                 clock: Callable[[], float] = time.time) -> None:
        self.ledger = ledger
        self.load_config = load_config
        self.environ = environ
        self.log = log
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ciel-usage-push", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(max(0.0, timeout))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                endpoints = usage_push_endpoints(self.load_config(), self.environ)
                self.poll_once(endpoints)
                delay = min((endpoint.poll_interval_seconds for endpoint in endpoints), default=1.0)
            except Exception as exc:
                self.log("WARN", f"usage_push_poll_failed error={type(exc).__name__}: {exc}")
                delay = 1.0
            self._stop.wait(delay)

    def poll_once(self, endpoints: list[UsagePushEndpoint] | None = None) -> int:
        delivered = 0
        for endpoint in endpoints if endpoints is not None else usage_push_endpoints(self.load_config(), self.environ):
            authorization, missing = expand_environment_references(endpoint.authorization)
            if missing:
                self.log("WARN", "usage_push_authorization_missing " + ",".join(missing))
                continue
            key = hashlib.sha256(f"{endpoint.endpoint_id}\0{endpoint.url}\0{authorization}".encode()).hexdigest()
            now = self.clock()
            cursor = self.ledger.delivery_cursor(key, endpoint.endpoint_id, tail=endpoint.start_mode == "tail", now=now)
            events = self.ledger.events(after=int(cursor["event_cursor"]), limit=1)
            if events:
                event = events[0]
                cloud = self._cloud_event("ai.oneciel.ciel-runtime.usage.recorded", event["event_id"], event)
                if self._post(endpoint, authorization, cloud):
                    self.ledger.update_delivery_cursor(key, event_cursor=int(event["seq"]))
                    delivered += 1
            audit_start = float(cursor["audit_start"])
            audit_end = float(cursor["audit_end"])
            if endpoint.audit_emit_on_start and audit_end == audit_start:
                audit_end = now - endpoint.audit_interval_seconds
                audit_start = audit_end - endpoint.audit_interval_seconds
                self.ledger.update_delivery_cursor(
                    key,
                    audit_start=audit_start,
                    audit_end=audit_end,
                )
            due = now - audit_end >= endpoint.audit_interval_seconds
            if due:
                start = audit_end
                end = audit_end + endpoint.audit_interval_seconds
                snapshot = self.ledger.summary(start, end)
                audit_id = hashlib.sha256(f"{key}\0{start:.6f}\0{end:.6f}".encode()).hexdigest()
                cloud = self._cloud_event("ai.oneciel.ciel-runtime.usage.audit", audit_id, snapshot)
                if self._post(endpoint, authorization, cloud):
                    self.ledger.update_delivery_cursor(key, audit_start=start, audit_end=end)
                    delivered += 1
        return delivered

    def _cloud_event(self, event_type: str, event_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"specversion": "1.0", "id": event_id, "source": f"urn:ciel-runtime:workspace:{self.ledger.workspace_id}",
                "type": event_type, "time": _utc_text(self.clock()), "datacontenttype": "application/json", "data": data}

    def _post(self, endpoint: UsagePushEndpoint, authorization: str, event: dict[str, Any]) -> bool:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/cloudevents+json", "Accept": "application/json",
                   "User-Agent": "ciel-runtime-usage-delivery/1", "Idempotency-Key": str(event["id"])}
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(endpoint.url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=endpoint.timeout_seconds) as response:
                status = int(getattr(response, "status", 200) or 200)
                response.read(4096)
        except urllib.error.HTTPError as exc:
            self.log("WARN", f"usage_push_failed endpoint={endpoint.endpoint_id} status={exc.code}")
            return False
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            self.log("WARN", f"usage_push_failed endpoint={endpoint.endpoint_id} error={type(exc).__name__}: {exc}")
            return False
        return 200 <= status < 300


@dataclass(slots=True)
class UsageRuntimeServices:
    ledger: SqliteUsageLedger
    keys: UsageApiKeyRepository
    push: UsagePushDeliveryService
    config_dir: Path
    workspace_state_dir: Path
    workspace_id: str
    current_jsonl: Path
    log: Callable[[str, str], None]
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def start(self) -> None:
        backfill = LegacyUsageBackfillService(self.ledger, self.log)
        paths = backfill.discover(
            self.config_dir,
            self.workspace_state_dir,
            self.workspace_id,
            self.current_jsonl,
        )
        config = self.push.load_config()
        usage_config = config.get("usage") if isinstance(config.get("usage"), dict) else {}
        configured_paths = usage_config.get("backfill_paths") if isinstance(usage_config, dict) else []
        if isinstance(configured_paths, list):
            paths = sorted({*paths, *(Path(str(value)) for value in configured_paths if str(value).strip())}, key=lambda item: str(item))
        configured = str(self.environ.get("CIEL_RUNTIME_USAGE_BACKFILL_PATHS") or "").strip()
        if configured:
            try:
                raw_paths = json.loads(configured)
                values = raw_paths if isinstance(raw_paths, list) else [raw_paths]
            except json.JSONDecodeError:
                values = configured.split(os.pathsep)
            paths = sorted({*paths, *(Path(str(value)) for value in values if str(value).strip())}, key=lambda item: str(item))
        backfill.run(paths)
        try:
            imported = self.keys.bootstrap_environment()
            if imported:
                self.log("INFO", f"usage_api_keys_bootstrapped count={imported}")
        except Exception as exc:
            self.log("ERROR", f"usage_api_key_bootstrap_failed error={type(exc).__name__}: {exc}")
        self.push.start()

    def stop(self) -> None:
        self.push.stop()


class UsageHttpAdapter:
    def __init__(self, ledger: SqliteUsageLedger, keys: UsageApiKeyRepository,
                 write_json: Callable[..., Any], admin_reject: Callable[[Any, dict[str, Any] | None], bool],
                 load_config: Callable[[], dict[str, Any]], log: Callable[[str, str], None]) -> None:
        self.ledger = ledger
        self.keys = keys
        self.write_json = write_json
        self.admin_reject = admin_reject
        self.load_config = load_config
        self.log = log

    def _unauthorized(self, handler: Any) -> None:
        self.write_json(handler, {"ok": False, "error": "usage_api_key_required"}, 401)

    def handle_get(self, handler: Any, path: str, query: dict[str, list[str]]) -> bool:
        if path not in {"/ca/usage/events", "/ca/usage/snapshot", "/ca/usage/stream", "/ca/usage/keys"}:
            return False
        if path == "/ca/usage/keys":
            if self.admin_reject(handler, self.load_config()):
                return True
            self.write_json(handler, {"ok": True, "keys": self.keys.list()})
            return True
        scope = "usage:stream" if path == "/ca/usage/stream" else "usage:read"
        identity = self.keys.authenticate(handler, scope)
        if identity is None:
            self._unauthorized(handler)
            return True
        try:
            now = time.time()
            start = _parse_time((query.get("from") or [""])[0], now - 86400)
            end = _parse_time((query.get("to") or [""])[0], now)
            if end < start:
                raise ValueError("to must be greater than or equal to from")
            after = int((query.get("after") or ["0"])[0])
            limit = int((query.get("limit") or ["200"])[0])
        except ValueError as exc:
            self.write_json(handler, {"ok": False, "error": "invalid_usage_query", "message": str(exc)}, 400)
            return True
        if path == "/ca/usage/snapshot":
            self.write_json(handler, {"ok": True, "consumer_key_id": identity["key_id"], "snapshot": self.ledger.summary(start, end)})
            return True
        if path == "/ca/usage/events":
            events = self.ledger.events(start=start, end=end, after=after, limit=limit,
                                        runtime=(query.get("runtime") or [""])[0],
                                        provider=(query.get("provider") or [""])[0],
                                        model=(query.get("model") or [""])[0])
            self.write_json(handler, {"ok": True, "consumer_key_id": identity["key_id"], "events": events,
                                      "next_after": events[-1]["seq"] if events else after})
            return True
        return self._stream(handler, after)

    def _stream(self, handler: Any, after: int) -> bool:
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        try:
            while True:
                events = self.ledger.wait_after(after, 15.0)
                if not events:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                    continue
                for event in events:
                    after = max(after, int(event["seq"]))
                    handler.wfile.write(f"id: {after}\nevent: usage\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return True
        except Exception as exc:
            self.log("DEBUG", f"usage stream closed: {type(exc).__name__}: {exc}")
            return True

    def handle_post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        if path != "/ca/usage/keys":
            return False
        action = str(body.get("action") or "issue").strip().lower()
        if action == "revoke":
            key_id = str(body.get("key_id") or "").strip()
            if not key_id:
                self.write_json(handler, {"ok": False, "error": "key_id_required"}, 400)
            else:
                self.write_json(handler, {"ok": self.keys.revoke(key_id), "key_id": key_id})
            return True
        try:
            result = self.keys.issue(str(body.get("name") or "usage-consumer"), list(body.get("scopes") or ["usage:read", "usage:stream"]),
                                     _parse_time(str(body.get("expires_at") or ""), 0.0) if body.get("expires_at") else 0.0)
        except (TypeError, ValueError) as exc:
            self.write_json(handler, {"ok": False, "error": "invalid_usage_key", "message": str(exc)}, 400)
            return True
        self.write_json(handler, {"ok": True, **result}, 201)
        return True


__all__ = [
    "LegacyUsageBackfillService", "SqliteUsageLedger", "UsageApiKeyRepository", "UsageHttpAdapter",
    "UsagePushDeliveryService", "UsagePushEndpoint", "UsageRuntimeServices", "usage_jsonl_enabled", "usage_push_endpoints",
]
