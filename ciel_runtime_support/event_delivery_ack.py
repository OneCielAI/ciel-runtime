"""Per-event delivery acknowledgements for CloudEvents that ask for one.

A provider that wants to know each event reached the session marks it with
``data.delivery_ack`` (Walkie 49269 §3.4, re-requested in 56190)::

    "delivery_ack": {"required": true, "version": 1,
                     "url": "<same origin>/api/notification-delivery/event-ack",
                     "stream_id": "20807", "decision": "approval",
                     "redelivery_of": null | {"event_id", "stream_id", "attempt"}}

The SSE receiver says it can (``Ciel-Receiver-Features: event-ack/1``) only
when this service is attached.  For every such event this service

* acknowledges ``stage: queued`` once the event has a row in the runtime input
  queue (outcome ``queued``), or names it ``already_queued`` when the same
  CloudEvent id or the re-sent event's first id is already queued, or
  ``rejected`` when the queue kept another event's row instead;
* acknowledges ``stage: injected`` once the runtime input status store records
  the row ``submitted`` (written into the session socket / tty) or ``replied``;
* posts only to the SSE URL's own scheme, host and port, with the SSE
  receiver's Authorization, and never follows a redirect;
* retries a failed post after 1, 2, 4, 8 and 16 s (10 s timeout each), keeps an
  unsent acknowledgement on disk and sends it again after a router restart or
  the next stream reconnect, until 24 h after the event arrived;
* writes every step to ``event-acks.jsonl`` (no event text, no credentials),
  kept 14 days, whatever the router log level is.

Events without ``delivery_ack`` and receivers of other providers are untouched.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


RECEIVER_FEATURES = "event-ack/1"
ACK_TIMEOUT_SECONDS = 10.0
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0)
ACK_EXPIRE_SECONDS = 24 * 3600.0
LEDGER_RETENTION_SECONDS = 14 * 86400.0
MAX_ACKS_PER_REQUEST = 100
RETRY_STATUSES = frozenset({408, 425, 429})
LEDGER_FILE_NAME = "event-acks.jsonl"
# Runtime input states that prove the row reached the session.
INJECTED_STATES = frozenset({"submitted", "replied"})
_RECENT_STATUS_LIMIT = 5000


def iso_utc(epoch: float | None) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def receiver_client_header(version: str) -> str:
    """``Walkie-Client`` value: ``ciel-runtime/<version> (<os>) runtime=python/<version>``."""

    system = " ".join(part for part in (platform.system(), platform.release()) if part) or "unknown"
    system = "".join(char for char in system if char.isprintable() and char not in "()")[:120]
    return f"ciel-runtime/{version} ({system}) runtime=python/{platform.python_version()}"


@dataclass(frozen=True, slots=True)
class DeliveryAckRequest:
    url: str
    stream_id: str
    decision: str
    redelivery_event_id: str


def delivery_ack_request(raw_text: str) -> DeliveryAckRequest | None:
    """The event's ``data.delivery_ack`` when it asks for an acknowledgement, else None."""

    try:
        value = json.loads(raw_text)
    except (TypeError, ValueError):
        return None
    data = value.get("data") if isinstance(value, dict) else None
    ack = data.get("delivery_ack") if isinstance(data, dict) else None
    if not isinstance(ack, dict) or ack.get("required") is not True or ack.get("version") != 1:
        return None
    url = str(ack.get("url") or "").strip()
    stream_id = str(ack.get("stream_id") or "").strip()
    if not url or not stream_id or len(stream_id) > 64:
        return None
    redelivery = ack.get("redelivery_of")
    redelivery_event_id = str(redelivery.get("event_id") or "").strip() if isinstance(redelivery, dict) else ""
    return DeliveryAckRequest(url, stream_id, str(ack.get("decision") or ""), redelivery_event_id)


def origin_of(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return None
    return scheme, parts.hostname.lower(), port or (443 if scheme == "https" else 80)


def consumer_id_of(sse_url: str) -> str:
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(str(sse_url or "")).query)
    except ValueError:
        return ""
    return str((query.get("consumer_id") or [""])[-1]).strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib signature
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def _no_redirect_open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


class DeliveryAckLedger:
    """Append-only snapshots of each acknowledged event (``event-acks.jsonl``)."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock

    def load(self) -> dict[str, dict[str, Any]]:
        """The newest snapshot of each event; the file is rewritten without expired ones."""

        entries: dict[str, dict[str, Any]] = {}
        snapshots = []
        cutoff = self.clock() - LEDGER_RETENTION_SECONDS
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and item.get("key") and float(item.get("updated_at_epoch") or 0) >= cutoff:
                        entries[str(item["key"])] = item
                        snapshots.append(item)
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        cutoff = self.clock() - LEDGER_RETENTION_SECONDS
        kept = {key: item for key, item in entries.items() if float(item.get("updated_at_epoch") or 0) >= cutoff}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                for item in snapshots:
                    stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            os.replace(temporary, self.path)
        except OSError:
            pass
        return kept

    def write(self, entry: dict[str, Any]) -> None:
        entry["updated_at_epoch"] = self.clock()
        entry["updated_at"] = iso_utc(entry["updated_at_epoch"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def ledger_summary(path: Path, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Counts for the menu: read-only, from the newest snapshot of each event."""

    entries: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("key"):
                    entries[str(item["key"])] = item
    except OSError:
        pass
    cutoff = clock() - LEDGER_RETENTION_SECONDS
    recent = [item for item in entries.values() if float(item.get("updated_at_epoch") or 0) >= cutoff]
    summary = {"events": len(recent), "acked": 0, "waiting": 0, "failed": 0, "other": 0, "injected": 0, "last": None}
    for item in recent:
        state = str((item.get("queued_ack") or {}).get("status") or "")
        if state == "acked":
            summary["acked"] += 1
        elif state == "pending":
            summary["waiting"] += 1
        elif state == "failed":
            summary["failed"] += 1
        else:
            summary["other"] += 1
        if item.get("injected_at"):
            summary["injected"] += 1
    if recent:
        last = max(recent, key=lambda item: float(item.get("updated_at_epoch") or 0))
        summary["last"] = {
            "at": last.get("updated_at"),
            "outcome": last.get("outcome"),
            "queued_ack": (last.get("queued_ack") or {}).get("status"),
            "injected_ack": (last.get("injected_ack") or {}).get("status") if last.get("injected_ack") else None,
        }
    return summary


def _new_ack(now: float) -> dict[str, Any]:
    return {"status": "pending", "attempts": 0, "due": now, "detail": ""}


class DeliveryAckService:
    """Queued / injected acknowledgements of one router's event receivers."""

    def __init__(
        self,
        state_dir: Path,
        status_path: Path,
        log: Callable[[str, str], Any],
        *,
        urlopen: Callable[..., Any] = _no_redirect_open,
        clock: Callable[[], float] = time.time,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ) -> None:
        self.ledger = DeliveryAckLedger(Path(state_dir) / LEDGER_FILE_NAME, clock)
        self.status_path = Path(status_path)
        self.log = log
        self.urlopen = urlopen
        self.clock = clock
        self.retry_delays = tuple(retry_delays)
        self.endpoint: Callable[[str], tuple[str, str]] = lambda _receiver_id: ("", "")
        self._entries: dict[str, dict[str, Any]] | None = None
        self._status_offset = 0
        self._status_remainder = b""
        self._recent_status: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pruned_at = 0.0

    # -- state ---------------------------------------------------------------
    def entries(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._entries is None:
                self._entries = self.ledger.load()
                self._pruned_at = self.clock()
            return self._entries

    def summary(self) -> dict[str, Any]:
        return ledger_summary(self.ledger.path, self.clock)

    def _save(self, entry: dict[str, Any]) -> None:
        try:
            self.ledger.write(entry)
        except OSError as exc:
            self.log("WARN", f"event_ack_ledger_write_failed error={type(exc).__name__}")
            raise

    @staticmethod
    def _key(receiver_id: str, event_id: str) -> str:
        return f"{receiver_id} {event_id}"

    # -- the receiver's side -------------------------------------------------
    def queued_original(self, receiver_id: str, request: DeliveryAckRequest, event_id: str = "") -> dict[str, Any] | None:
        """A re-sent event's first event when it is already in the queue."""

        with self._lock:
            for candidate in (event_id, request.redelivery_event_id):
                if not candidate:
                    continue
                original = self.entries().get(self._key(receiver_id, candidate))
                if original and original.get("outcome") in {"queued", "already_queued"} and int(original.get("request_id") or 0) > 0:
                    return dict(original)
        return None

    def observe(
        self,
        receiver_id: str,
        sse_url: str,
        event: dict[str, str],
        request: DeliveryAckRequest,
        received_at: float,
        admitted: dict[str, Any] | None,
        original: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one delivery_ack event after its queue admission and schedule its queued ack."""

        now = self.clock()
        event_id = str(event.get("id") or "")
        queued_at = 0.0
        request_id = 0
        if original is not None:
            outcome = "already_queued"
            request_id = int(original.get("request_id") or 0)
            queued_at = float(original.get("queued_at_epoch") or 0) or received_at
        elif admitted is not None and admitted.get("_ciel_runtime_duplicate"):
            meta = admitted.get("meta") if isinstance(admitted.get("meta"), dict) else {}
            kept_event_id = str(meta.get("event_id") or "")
            request_id = int(admitted.get("id") or 0)
            if kept_event_id in {event_id, request.redelivery_event_id}:
                outcome = "already_queued"
                queued_at = float(admitted.get("created_at_epoch") or 0) or received_at
            else:
                outcome = "rejected"
        else:
            outcome = "queued"
            request_id = int((admitted or {}).get("id") or 0)
            queued_at = now
        entry: dict[str, Any] = {
            "key": self._key(receiver_id, event_id),
            "receiver_id": receiver_id,
            "event_id": event_id,
            "stream_id": request.stream_id,
            "decision": request.decision,
            "redelivery_of": request.redelivery_event_id or None,
            "request_id": request_id,
            "outcome": outcome,
            "ack_url": request.url,
            "consumer_id": consumer_id_of(sse_url),
            "received_at": iso_utc(received_at),
            "received_at_epoch": received_at,
            "queued_at": iso_utc(queued_at),
            "queued_at_epoch": queued_at,
            "injected_at": "",
            "injected_at_epoch": 0.0,
            "session_status": "",
            "queued_ack": _new_ack(now),
            "injected_ack": None,
        }
        problem = self._endpoint_problem(entry, sse_url)
        if problem:
            entry["queued_ack"] = {"status": "dropped", "attempts": 0, "due": 0.0, "detail": problem}
        with self._lock:
            previous = self.entries().get(entry["key"])
            if previous:
                # Replays must not erase injection evidence or unsent injected acks.
                for name in ("injected_at", "injected_at_epoch", "session_status", "injected_ack"):
                    entry[name] = previous.get(name)
            self._save(entry)
            self.entries()[entry["key"]] = entry
        self.log(
            "INFO",
            f"event_ack_received receiver={receiver_id} event_id={event_id} stream_id={request.stream_id} "
            f"outcome={outcome} request_id={request_id or '-'}" + (f" ack={problem}" if problem else ""),
        )
        self._wake.set()
        return entry

    @staticmethod
    def _endpoint_problem(entry: dict[str, Any], sse_url: str) -> str:
        if not entry.get("consumer_id"):
            return "no_consumer_id"
        sse_origin = origin_of(sse_url)
        if sse_origin is None or origin_of(str(entry.get("ack_url") or "")) != sse_origin:
            return "cross_origin"
        return ""

    def reconnected(self, receiver_id: str) -> None:
        """The stream is back: acknowledgements that ran out of retries go again."""

        now = self.clock()
        woke = False
        with self._lock:
            for entry in self.entries().values():
                if entry.get("receiver_id") != receiver_id:
                    continue
                for stage in ("queued_ack", "injected_ack"):
                    ack = entry.get(stage)
                    if isinstance(ack, dict) and ack.get("status") == "failed":
                        ack.update(status="pending", attempts=0, due=now)
                        woke = True
        if woke:
            self._wake.set()

    # -- injection evidence --------------------------------------------------
    def _awaiting_injection(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [
            entry
            for entry in self.entries().values()
            if entry.get("outcome") in {"queued", "already_queued"}
            and int(entry.get("request_id") or 0) > 0
            and not entry.get("injected_at")
            and now - float(entry.get("received_at_epoch") or 0) < ACK_EXPIRE_SECONDS
        ]

    def _read_status_records(self) -> None:
        try:
            size = self.status_path.stat().st_size
        except OSError:
            return
        if size < self._status_offset:
            self._status_offset, self._status_remainder = 0, b""
        if size == self._status_offset:
            return
        try:
            with self.status_path.open("rb") as stream:
                stream.seek(self._status_offset)
                chunk = stream.read(size - self._status_offset)
        except OSError:
            return
        self._status_offset += len(chunk)
        data = self._status_remainder + chunk
        lines = data.split(b"\n")
        self._status_remainder = lines.pop()
        for raw in lines:
            try:
                record = json.loads(raw.decode("utf-8"))
                request_id = int(record.get("request_id") or 0)
            except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
                continue
            if request_id <= 0:
                continue
            seen = self._recent_status.pop(request_id, {})
            status = str(record.get("status") or "")
            at = float(record.get("updated_at_epoch") or 0)
            seen.update(status=status, reason=str(record.get("reason") or ""), at=at)
            if status == "queued":
                # A request id written again (a rotated queue) starts over.
                seen.pop("injected_at", None)
            elif status in INJECTED_STATES and not seen.get("injected_at"):
                seen["injected_at"] = at
            self._recent_status[request_id] = seen
            while len(self._recent_status) > _RECENT_STATUS_LIMIT:
                self._recent_status.popitem(last=False)

    def scan_injections(self) -> int:
        """Match queued events against the runtime input status store; the number injected now."""

        with self._lock:
            awaiting = self._awaiting_injection()
            if not awaiting:
                return 0
            self._read_status_records()
            injected = 0
            now = self.clock()
            for entry in awaiting:
                request_id = int(entry["request_id"])
                seen = self._recent_status.get(request_id)
                if not seen:
                    continue
                status = f"{seen.get('status')}{':' + seen['reason'] if seen.get('reason') else ''}"
                changed = status != entry.get("session_status")
                entry["session_status"] = status
                # Evidence older than this event's own queue row belongs to an earlier row of the same id.
                if seen.get("injected_at") and float(seen["injected_at"]) >= float(entry.get("queued_at_epoch") or 0) - 5.0:
                    entry["injected_at_epoch"] = float(seen["injected_at"])
                    entry["injected_at"] = iso_utc(entry["injected_at_epoch"])
                    entry["injected_ack"] = _new_ack(now)
                    if (entry.get("queued_ack") or {}).get("status") == "dropped":
                        entry["injected_ack"] = dict(entry["queued_ack"])
                    injected += 1
                    changed = True
                    self.log(
                        "INFO",
                        f"event_ack_injected receiver={entry['receiver_id']} event_id={entry['event_id']} "
                        f"stream_id={entry['stream_id']} request_id={request_id}",
                    )
                if changed:
                    self._save(entry)
        if injected:
            self._wake.set()
        return injected

    # -- sending -------------------------------------------------------------
    def _due(self, now: float) -> list[tuple[dict[str, Any], str]]:
        due: list[tuple[dict[str, Any], str]] = []
        for entry in self.entries().values():
            for stage in ("queued_ack", "injected_ack"):
                ack = entry.get(stage)
                if not isinstance(ack, dict) or ack.get("status") not in {"pending", "failed"}:
                    continue
                if now - float(entry.get("received_at_epoch") or 0) >= ACK_EXPIRE_SECONDS:
                    ack.update(status="expired", detail="older_than_24h")
                    self._save(entry)
                    continue
                if ack.get("status") == "pending" and float(ack.get("due") or 0) <= now:
                    due.append((entry, stage))
        return due

    @staticmethod
    def _ack_item(entry: dict[str, Any], stage: str) -> dict[str, Any]:
        item: dict[str, Any] = {"stream_id": entry["stream_id"], "event_id": entry["event_id"]}
        if stage == "injected_ack":
            item.update(stage="injected", injected_at=entry["injected_at"])
            return item
        item.update(stage="queued", outcome=entry["outcome"], received_at=entry["received_at"])
        if entry.get("outcome") != "rejected" and entry.get("queued_at"):
            item["queued_at"] = entry["queued_at"]
        return item

    def flush(self) -> int:
        """Post every due acknowledgement once; the number Walkie took."""

        with self._lock:
            due = self._due(self.clock())
        groups: dict[tuple[str, str, str], list[tuple[dict[str, Any], str]]] = {}
        for entry, stage in due:
            groups.setdefault((entry["receiver_id"], entry["ack_url"], entry["consumer_id"]), []).append((entry, stage))
        sent = 0
        for (receiver_id, url, consumer_id), items in groups.items():
            for start in range(0, len(items), MAX_ACKS_PER_REQUEST):
                sent += self._post(receiver_id, url, consumer_id, items[start:start + MAX_ACKS_PER_REQUEST])
        return sent

    def _post(self, receiver_id: str, url: str, consumer_id: str, items: list[tuple[dict[str, Any], str]]) -> int:
        try:
            sse_url, authorization = self.endpoint(receiver_id)
        except Exception as exc:  # noqa: BLE001 - a missing credential is a failed attempt, not a crash
            sse_url, authorization = "", ""
            self.log("WARN", f"event_ack_endpoint_failed receiver={receiver_id} error={type(exc).__name__}")
        problem = self._endpoint_problem({"ack_url": url, "consumer_id": consumer_id}, sse_url)
        if problem:
            self._finish(items, "dropped", problem)
            return 0
        body = json.dumps(
            {"consumer_id": consumer_id, "acks": [self._ack_item(entry, stage) for entry, stage in items]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with self.urlopen(request, timeout=ACK_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", 200) or 200)
                payload = response.read(256 * 1024)
        except urllib.error.HTTPError as exc:
            status, payload = int(exc.code), b""
            if 300 <= status < 400:
                self._finish(items, "dropped", f"redirect_refused status={status}")
                return 0
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            self._retry(items, f"error={type(exc).__name__}")
            return 0
        if not 200 <= status < 300:
            if status in RETRY_STATUSES or status >= 500:
                self._retry(items, f"status={status}")
            else:
                self._finish(items, "dropped", f"status={status}")
            return 0
        try:
            answer = json.loads(payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            answer = {}
        lists = {
            name: {str(value) for value in answer.get(name, [])} if isinstance(answer, dict) and isinstance(answer.get(name), list) else set()
            for name in ("acked", "unknown", "rejected")
        }
        taken = 0
        with self._lock:
            for entry, stage in items:
                stream_id = str(entry["stream_id"])
                result = next((name for name in ("acked", "rejected", "unknown") if stream_id in lists[name]), "unlisted")
                if result == "unlisted":
                    self._retry([(entry, stage)], "unconfirmed_response")
                    continue
                ack = entry[stage]
                ack.update(status=result, attempts=int(ack.get("attempts") or 0) + 1, detail=f"status={status}", sent_at=iso_utc(self.clock()))
                taken += result == "acked"
                self._save(entry)
                self.log(
                    "INFO",
                    f"event_ack_sent receiver={receiver_id} stage={'injected' if stage == 'injected_ack' else 'queued'} "
                    f"event_id={entry['event_id']} stream_id={stream_id} outcome={entry['outcome']} result={result} "
                    f"attempts={ack['attempts']}",
                )
        return taken

    def _retry(self, items: list[tuple[dict[str, Any], str]], detail: str) -> None:
        now = self.clock()
        with self._lock:
            for entry, stage in items:
                ack = entry[stage]
                attempts = int(ack.get("attempts") or 0) + 1
                if attempts <= len(self.retry_delays):
                    ack.update(status="pending", attempts=attempts, due=now + self.retry_delays[attempts - 1], detail=detail)
                    level = "INFO"
                else:
                    # Kept on disk: the next router start or stream reconnect sends it again.
                    ack.update(status="failed", attempts=attempts, due=0.0, detail=detail)
                    level = "WARN"
                self._save(entry)
                self.log(
                    level,
                    f"event_ack_{'retry' if ack['status'] == 'pending' else 'failed'} receiver={entry['receiver_id']} "
                    f"stage={'injected' if stage == 'injected_ack' else 'queued'} event_id={entry['event_id']} "
                    f"stream_id={entry['stream_id']} {detail} attempts={attempts}",
                )

    def _finish(self, items: list[tuple[dict[str, Any], str]], status: str, detail: str) -> None:
        with self._lock:
            for entry, stage in items:
                ack = entry[stage]
                ack.update(status=status, attempts=int(ack.get("attempts") or 0) + 1, detail=detail)
                self._save(entry)
                self.log(
                    "WARN",
                    f"event_ack_dropped receiver={entry['receiver_id']} stage={'injected' if stage == 'injected_ack' else 'queued'} "
                    f"event_id={entry['event_id']} stream_id={entry['stream_id']} {detail}",
                )

    # -- thread --------------------------------------------------------------
    def start(self, endpoint: Callable[[str], tuple[str, str]]) -> None:
        """Run in the router: ``endpoint(receiver_id)`` gives the SSE URL and Authorization now."""

        self.endpoint = endpoint
        if self._thread is not None and self._thread.is_alive():
            return
        now = self.clock()
        with self._lock:
            for entry in self.entries().values():
                for stage in ("queued_ack", "injected_ack"):
                    ack = entry.get(stage)
                    if isinstance(ack, dict) and ack.get("status") in {"pending", "failed"}:
                        ack.update(status="pending", attempts=0, due=now)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ciel-event-ack", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_injections()
                self.flush()
                if self.clock() - self._pruned_at >= 86400.0:
                    with self._lock:
                        self._entries = None
                        self.entries()
            except Exception as exc:  # noqa: BLE001 - the router must keep running
                self.log("WARN", f"event_ack_loop_failed error={type(exc).__name__}")
            with self._lock:
                entries = self.entries().values()
                pending = [
                    float(ack.get("due") or 0)
                    for entry in entries
                    for ack in (entry.get("queued_ack"), entry.get("injected_ack"))
                    if isinstance(ack, dict) and ack.get("status") == "pending"
                ]
                awaiting = bool(self._awaiting_injection())
            wait = 30.0
            if pending:
                wait = max(0.05, min(wait, min(pending) - self.clock()))
            if awaiting:
                wait = min(wait, 1.0)
            self._wake.wait(wait)
            self._wake.clear()


__all__ = [
    "ACK_EXPIRE_SECONDS",
    "ACK_TIMEOUT_SECONDS",
    "DeliveryAckLedger",
    "DeliveryAckRequest",
    "DeliveryAckService",
    "LEDGER_FILE_NAME",
    "LEDGER_RETENTION_SECONDS",
    "RECEIVER_FEATURES",
    "RETRY_DELAYS",
    "delivery_ack_request",
    "consumer_id_of",
    "iso_utc",
    "ledger_summary",
    "origin_of",
    "receiver_client_header",
]
