"""Turn ends pushed straight to Walkie's ``/api/agent-runtime-events``.

Walkie learned that an agent's turn ended from a status observer installed on
each agent PC, which relays the router's ``/ca/tui`` ``agent.turn_ended``.  The
installed copies never update, so an old observer silently loses turn ends
(Walkie 48926 F3).  The router now sends each turn end itself, as Walkie's
endpoint takes it (Walkie server/src/agent-runtime-events.ts, Daniel 34963):

* CloudEvents 1.0, one event as ``application/cloudevents+json``;
* type ``ai.oneciel.ciel-runtime.agent.turn_ended``, source
  ``urn:ciel-runtime:workspace:<id>``, data = the turn fields only (no text);
* the agent's own service credential as ``Authorization: Bearer``: the one the
  Runtime already keeps, encrypted, for the Walkie notification stream receiver
  (``external-event-secrets.vault.json``); nothing new is stored and the value
  is never logged;
* the event id is the turn's own, so a resend, a retry or the observer's report
  of the same turn is handled once by Walkie; ``Idempotency-Key`` is that id.

Delivery runs on its own thread: the router's request thread only queues.  A
network error, 408, 425, 429 or 5xx is retried with backoff up to
``max_attempts`` times; any other 4xx is final.

Configuration (workspace config, all optional)::

    "agent_runtime_events": {
      "enabled": true,                 # false: push nothing
      "url": "https://.../api/agent-runtime-events",   # else derived, below
      "credential_receiver": "default", # the receiver whose credential is sent
      "timeout_seconds": 5
    }

Without ``url``, each enabled SSE receiver whose URL is Walkie's notification
stream (``/api/notification-delivery/stream``) gives one endpoint: the same
origin's ``/api/agent-runtime-events`` with that receiver's credential.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .external_event_receiver import MissingEnvironmentReference, resolve_environment_reference

TURN_ENDED_TYPE = "ai.oneciel.ciel-runtime.agent.turn_ended"
WALKIE_STREAM_PATH = "/api/notification-delivery/stream"
WALKIE_EVENTS_PATH = "/api/agent-runtime-events"
TURN_FIELDS = ("turn_id", "runtime", "ended_at", "reason", "by_user_input", "stop_check_blocked")
RETRY_STATUSES = frozenset({408, 425, 429})
MAX_ATTEMPTS = 6
MAX_BACKOFF_SECONDS = 30.0
MAX_PENDING = 1_000
_SEEN_LIMIT = 5_000


@dataclass(frozen=True, slots=True)
class RuntimeEventEndpoint:
    url: str
    # The full Authorization header value.  Never logged.
    authorization: str = ""
    timeout_seconds: float = 5.0

    def label(self) -> str:
        """The endpoint as logs show it: scheme, host and port only."""

        parts = urllib.parse.urlsplit(self.url)
        return f"{parts.scheme}://{parts.hostname or ''}{':' + str(parts.port) if parts.port else ''}"


def _bearer(secret: str) -> str:
    secret = str(secret or "").strip()
    if not secret:
        return ""
    return secret if secret.lower().startswith("bearer ") else f"Bearer {secret}"


def runtime_event_endpoints(
    config: Mapping[str, Any],
    receivers: Mapping[str, Mapping[str, Any]],
    load_receiver_secrets: Callable[[str], Mapping[str, Any]],
) -> list[RuntimeEventEndpoint]:
    """Where turn ends go, from the workspace config and its event receivers."""

    raw = config.get("agent_runtime_events")
    section = raw if isinstance(raw, Mapping) else {}
    if section.get("enabled", True) is False:
        return []
    try:
        timeout = max(0.5, min(30.0, float(section.get("timeout_seconds") or 5.0)))
    except (TypeError, ValueError):
        timeout = 5.0
    named = str(section.get("credential_receiver") or "").strip()

    def credential(receiver_id: str) -> str | None:
        try:
            secret = resolve_environment_reference(load_receiver_secrets(receiver_id).get("authorization", ""))
        except (MissingEnvironmentReference, OSError, RuntimeError, ValueError):
            return None
        return _bearer(secret) or None

    explicit = str(section.get("url") or "").strip()
    if explicit:
        authorization = credential(named) if named else ""
        if authorization is None:
            return []
        return [RuntimeEventEndpoint(explicit, authorization, timeout)]
    endpoints: dict[str, RuntimeEventEndpoint] = {}
    for receiver_id, receiver in sorted(receivers.items()):
        if named and receiver_id != named:
            continue
        if not isinstance(receiver, Mapping) or not receiver.get("enabled") or receiver.get("transport") != "sse":
            continue
        try:
            parts = urllib.parse.urlsplit(resolve_environment_reference(receiver.get("url")))
        except (MissingEnvironmentReference, ValueError):
            continue
        if parts.scheme not in {"http", "https"} or parts.path.rstrip("/") != WALKIE_STREAM_PATH:
            continue
        authorization = credential(receiver_id)
        if not authorization:
            continue  # Walkie takes no event without the agent's credential.
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, WALKIE_EVENTS_PATH, "", ""))
        endpoints.setdefault(url, RuntimeEventEndpoint(url, authorization, timeout))
    return list(endpoints.values())


def turn_ended_cloud_event(fields: Mapping[str, Any], workspace_id: str, now: float) -> dict[str, Any]:
    """One ``agent.turn_ended`` as a CloudEvent; its id depends on the turn only."""

    source = f"urn:ciel-runtime:workspace:{workspace_id}"
    turn_id = str(fields.get("turn_id") or "")
    event_id = hashlib.sha256(f"{source}\0{TURN_ENDED_TYPE}\0{turn_id}".encode("utf-8")).hexdigest()
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": source,
        "type": TURN_ENDED_TYPE,
        "time": datetime.fromtimestamp(float(now), timezone.utc).isoformat(timespec="milliseconds"),
        "datacontenttype": "application/json",
        "data": {name: fields[name] for name in TURN_FIELDS if name in fields},
    }


@dataclass(slots=True)
class _Pending:
    endpoint: RuntimeEventEndpoint
    event: dict[str, Any]
    attempts: int = 0
    due: float = 0.0


class AgentRuntimeEventPusher:
    """Queues each turn end once per endpoint and delivers it with bounded retries."""

    def __init__(
        self,
        workspace_id: str,
        endpoints: Callable[[], list[RuntimeEventEndpoint]],
        log: Callable[[str, str], Any],
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.time,
        max_attempts: int = MAX_ATTEMPTS,
        max_pending: int = MAX_PENDING,
    ) -> None:
        self.workspace_id = str(workspace_id or "workspace")
        self.endpoints = endpoints
        self.log = log
        self.urlopen = urlopen
        self.clock = clock
        self.max_attempts = max(1, int(max_attempts))
        self.max_pending = max(1, int(max_pending))
        self._pending: list[_Pending] = []
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the router's turn-end listener -------------------------------------
    def submit(self, fields: Mapping[str, Any]) -> None:
        """Queue one turn end; never blocks on the network."""

        if not str(fields.get("turn_id") or ""):
            return
        try:
            endpoints = self.endpoints()
        except Exception as exc:  # noqa: BLE001 - a broken config never breaks turn reporting
            self.log("WARN", f"agent_runtime_event_endpoints_failed error={type(exc).__name__}")
            return
        if not endpoints:
            return
        now = self.clock()
        event = turn_ended_cloud_event(fields, self.workspace_id, now)
        with self._lock:
            for endpoint in endpoints:
                key = (endpoint.url, event["id"])
                if key in self._seen:
                    continue
                self._seen[key] = None
                while len(self._seen) > _SEEN_LIMIT:
                    self._seen.popitem(last=False)
                if len(self._pending) >= self.max_pending:
                    dropped = self._pending.pop(0)
                    self._dropped(dropped, "queue_full")
                self._pending.append(_Pending(endpoint, event, 0, now))
        self._wake.set()

    def attach(self) -> None:
        from . import tui_observation

        if self.submit not in tui_observation.TURN_ENDED_LISTENERS:
            tui_observation.TURN_ENDED_LISTENERS.append(self.submit)

    def detach(self) -> None:
        from . import tui_observation

        if self.submit in tui_observation.TURN_ENDED_LISTENERS:
            tui_observation.TURN_ENDED_LISTENERS.remove(self.submit)

    # -- delivery ------------------------------------------------------------
    def flush(self) -> int:
        """Send every queued event that is due once; the number delivered."""

        now = self.clock()
        with self._lock:
            due = [item for item in self._pending if item.due <= now]
            self._pending = [item for item in self._pending if item.due > now]
        delivered = 0
        retry: list[_Pending] = []
        for item in due:
            outcome, detail = self._send(item)
            item.attempts += 1
            if outcome == "delivered":
                delivered += 1
                self.log(
                    "INFO",
                    f"agent_runtime_event_delivered turn={item.event['data'].get('turn_id')} "
                    f"endpoint={item.endpoint.label()} attempts={item.attempts}",
                )
            elif outcome == "retry" and item.attempts < self.max_attempts:
                item.due = self.clock() + min(MAX_BACKOFF_SECONDS, 2.0 ** (item.attempts - 1))
                self.log(
                    "WARN",
                    f"agent_runtime_event_retry turn={item.event['data'].get('turn_id')} "
                    f"endpoint={item.endpoint.label()} {detail} attempt={item.attempts}/{self.max_attempts}",
                )
                retry.append(item)
            else:
                self._dropped(item, detail if outcome == "rejected" else f"{detail} attempts={item.attempts}")
        if retry:
            with self._lock:
                self._pending.extend(retry)
        return delivered

    def _dropped(self, item: _Pending, detail: str) -> None:
        self.log(
            "WARN",
            f"agent_runtime_event_dropped turn={item.event['data'].get('turn_id')} "
            f"endpoint={item.endpoint.label()} {detail}",
        )

    def _send(self, item: _Pending) -> tuple[str, str]:
        body = json.dumps(item.event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/cloudevents+json",
            "Accept": "application/json",
            "User-Agent": "ciel-runtime-agent-events/1",
            "Idempotency-Key": str(item.event["id"]),
        }
        if item.endpoint.authorization:
            headers["Authorization"] = item.endpoint.authorization
        request = urllib.request.Request(item.endpoint.url, data=body, headers=headers, method="POST")
        try:
            with self.urlopen(request, timeout=item.endpoint.timeout_seconds) as response:
                status = int(getattr(response, "status", 200) or 200)
                response.read(4096)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            return "retry", f"error={type(exc).__name__}"
        if 200 <= status < 300:
            return "delivered", f"status={status}"
        if status in RETRY_STATUSES or status >= 500:
            return "retry", f"status={status}"
        return "rejected", f"status={status}"

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    # -- thread --------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.attach()
        self._thread = threading.Thread(target=self._run, name="ciel-agent-runtime-events", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self.detach()
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001 - the router must keep running
                self.log("WARN", f"agent_runtime_event_push_failed error={type(exc).__name__}")
            with self._lock:
                next_due = min((item.due for item in self._pending), default=None)
            wait = 60.0 if next_due is None else max(0.05, min(60.0, next_due - self.clock()))
            self._wake.wait(wait)
            self._wake.clear()


_ROUTER_PUSHER: AgentRuntimeEventPusher | None = None


def start_router_pusher(
    workspace_id: str,
    endpoints: Callable[[], list[RuntimeEventEndpoint]],
    log: Callable[[str, str], Any],
) -> None:
    """Started with the router's services: every turn end the router publishes is pushed."""

    global _ROUTER_PUSHER
    if _ROUTER_PUSHER is None:
        _ROUTER_PUSHER = AgentRuntimeEventPusher(workspace_id, endpoints, log)
        _ROUTER_PUSHER.start()


def stop_router_pusher() -> None:
    global _ROUTER_PUSHER
    if _ROUTER_PUSHER is not None:
        _ROUTER_PUSHER.stop()
        _ROUTER_PUSHER = None


__all__ = [
    "AgentRuntimeEventPusher",
    "RuntimeEventEndpoint",
    "TURN_ENDED_TYPE",
    "WALKIE_EVENTS_PATH",
    "WALKIE_STREAM_PATH",
    "runtime_event_endpoints",
    "start_router_pusher",
    "stop_router_pusher",
    "turn_ended_cloud_event",
]
