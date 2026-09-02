"""Provider-neutral CloudEvents receivers over Webhooks and SSE."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable


MAX_EVENT_BYTES = 1024 * 1024
WEBHOOK_TOLERANCE_SECONDS = 300
CLOUDEVENTS_SSE_ACCEPT = "text/event-stream, application/cloudevents+json"
ENVIRONMENT_REFERENCE_RE = re.compile(
    r"^(?:%([A-Za-z_][A-Za-z0-9_]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\{([A-Za-z_][A-Za-z0-9_]*)\})$"
)


class MissingEnvironmentReference(RuntimeError):
    """A configured environment reference is absent from the router process."""


def environment_reference_name(value: Any) -> str:
    """Return an exact environment reference name without expanding templates."""

    match = ENVIRONMENT_REFERENCE_RE.fullmatch(str(value or "").strip())
    if match is None:
        return ""
    return next((name for name in match.groups() if name), "")


def resolve_environment_reference(
    value: Any,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve %NAME%, ${NAME}, or {NAME}; preserve every other value literally."""

    text = str(value or "").strip()
    name = environment_reference_name(text)
    if not name:
        return text
    source = os.environ if environment is None else environment
    resolved = str(source.get(name) or "")
    if not resolved:
        raise MissingEnvironmentReference(f"environment variable {name} is not set")
    return resolved


class EventReceiverSecretVault:
    """Small cross-platform authenticated local vault for receiver credentials."""

    def __init__(self, path: Path, key_path: Path | None = None) -> None:
        self.path = path
        self.key_path = key_path or path.with_suffix(".key")
        self._lock = threading.Lock()
        self._master: bytes | None = None

    @staticmethod
    def _private(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _key(self) -> bytes:
        if self._master is not None:
            return self._master
        configured = str(os.environ.get("CIEL_RUNTIME_SECRET_MASTER_KEY") or "").strip()
        if configured:
            key = base64.urlsafe_b64decode(configured.encode("ascii"))
            if len(key) != 32:
                raise RuntimeError("CIEL_RUNTIME_SECRET_MASTER_KEY must decode to 32 bytes")
            self._master = key
            return key
        try:
            key = base64.urlsafe_b64decode(self.key_path.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            key = secrets.token_bytes(32)
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.key_path.with_suffix(self.key_path.suffix + ".tmp")
            temporary.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
            self._private(temporary)
            os.replace(temporary, self.key_path)
        if len(key) != 32:
            raise RuntimeError("event receiver vault key has an invalid length")
        self._master = key
        return key

    def _derive(self, purpose: bytes) -> bytes:
        return hmac.new(self._key(), b"ciel-runtime-events-v1:" + purpose, hashlib.sha256).digest()

    def _protect(self, plain_text: str) -> str:
        plain = plain_text.encode("utf-8")
        nonce = secrets.token_bytes(16)
        stream = bytearray()
        key = self._derive(b"encryption")
        for counter in range((len(plain) + 31) // 32):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        cipher = bytes(left ^ right for left, right in zip(plain, stream))
        body = b"CEV1" + nonce + cipher
        tag = hmac.new(self._derive(b"authentication"), body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + tag).decode("ascii")

    def _unprotect(self, encoded: str) -> str:
        payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(payload) < 52 or not payload.startswith(b"CEV1"):
            raise RuntimeError("event receiver secret has an invalid format")
        body, supplied = payload[:-32], payload[-32:]
        expected = hmac.new(self._derive(b"authentication"), body, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise RuntimeError("event receiver secret failed authentication")
        nonce, cipher = body[4:20], body[20:]
        stream = bytearray()
        key = self._derive(b"encryption")
        for counter in range((len(cipher) + 31) // 32):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        return bytes(left ^ right for left, right in zip(cipher, stream)).decode("utf-8")

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "receivers": {}}
        if not isinstance(value, dict) or not isinstance(value.get("receivers"), dict):
            raise RuntimeError("event receiver vault is invalid")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        self._private(temporary)
        os.replace(temporary, self.path)

    def update(self, receiver_id: str, values: dict[str, str]) -> None:
        admitted = {
            name: str(value)
            for name, value in values.items()
            if name in {"webhook_secret", "authorization"} and str(value)
        }
        if not admitted:
            return
        with self._lock:
            data = self._read()
            receivers = data.setdefault("receivers", {})
            current = receivers.get(receiver_id) if isinstance(receivers.get(receiver_id), dict) else {}
            current.update({name: self._protect(value) for name, value in admitted.items()})
            receivers[receiver_id] = current
            self._write(data)

    def load(self, receiver_id: str) -> dict[str, str]:
        with self._lock:
            values = self._read().get("receivers", {}).get(receiver_id, {})
            if not isinstance(values, dict):
                return {}
            return {name: self._unprotect(str(value)) for name, value in values.items() if str(value)}

    def status(self, receiver_id: str) -> dict[str, bool]:
        with self._lock:
            values = self._read().get("receivers", {}).get(receiver_id, {})
            return {
                "stored_webhook_secret": bool(isinstance(values, dict) and values.get("webhook_secret")),
                "stored_authorization": bool(isinstance(values, dict) and values.get("authorization")),
            }

    def clear(self, receiver_id: str) -> None:
        with self._lock:
            data = self._read()
            receivers = data.get("receivers", {})
            if not isinstance(receivers, dict) or receiver_id not in receivers:
                return
            del receivers[receiver_id]
            self._write(data)


def validate_cloud_event(raw_text: str) -> dict[str, str]:
    """Validate a structured CloudEvents 1.0 body without changing its text."""

    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CloudEvent body must be JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("CloudEvent body must be a JSON object")
    if value.get("specversion") != "1.0":
        raise ValueError("CloudEvent specversion must be 1.0")
    projected: dict[str, str] = {}
    for field_name in ("id", "source", "type"):
        field_value = value.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(f"CloudEvent {field_name} is required")
        projected[field_name] = field_value
    return projected


def verify_standard_webhook(
    raw: bytes,
    headers: Any,
    secret: str,
    *,
    now: float | None = None,
    tolerance_seconds: int = WEBHOOK_TOLERANCE_SECONDS,
) -> tuple[str, str]:
    """Verify the Standard Webhooks HMAC-SHA256 signature contract."""

    webhook_id = str(headers.get("webhook-id") or "").strip()
    timestamp = str(headers.get("webhook-timestamp") or "").strip()
    signature_header = str(headers.get("webhook-signature") or "").strip()
    if not webhook_id or not timestamp or not signature_header:
        raise ValueError("missing Standard Webhooks signature headers")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise ValueError("webhook-timestamp must be Unix seconds") from exc
    current = time.time() if now is None else now
    if abs(current - timestamp_value) > tolerance_seconds:
        raise ValueError("webhook timestamp is outside the replay window")
    encoded_secret = secret[6:] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(encoded_secret, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("webhook secret must be Standard Webhooks base64 (optionally whsec_ prefixed)") from exc
    signed = webhook_id.encode("utf-8") + b"." + timestamp.encode("ascii") + b"." + raw
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
    candidates: list[str] = []
    for token in signature_header.replace(",", " ").split():
        if token.startswith("v1="):
            candidates.append(token[3:])
        elif token != "v1":
            candidates.append(token)
    if not any(hmac.compare_digest(candidate, expected) for candidate in candidates):
        raise ValueError("webhook signature verification failed")
    return webhook_id, timestamp


def parse_sse_frames(lines: Any) -> Any:
    """Yield decoded SSE frames as (event_id, data), following data-line joining."""

    event_id = ""
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_id, "\n".join(data_lines)
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field == "id" and "\x00" not in value:
            event_id = value
    if data_lines:
        yield event_id, "\n".join(data_lines)


def json_pointer_value(value: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer without interpreting the event body."""

    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("cursor_json_pointer must be empty or start with /")
    current = value
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def cloud_event_cursor(raw_text: str, pointer: str) -> str:
    """Project an explicitly configured reconnect cursor from a CloudEvent."""

    if not pointer:
        return ""
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError:
        return ""
    cursor = json_pointer_value(value, pointer)
    if cursor is None or isinstance(cursor, (dict, list, bool)):
        return ""
    return str(cursor).strip()


def sse_reconnect_url(url: str, query_parameter: str, cursor: str) -> str:
    """Add a provider-neutral cursor query parameter while preserving the URL."""

    if not query_parameter or not cursor:
        return url
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(name, value) for name, value in query if name != query_parameter]
    query.append((query_parameter, cursor))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


@dataclass(slots=True)
class ExternalEventReceiverService:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    write_json: Callable[..., None]
    submit_event: Callable[..., dict[str, Any]]
    vault: EventReceiverSecretVault
    workspace_key: str
    log: Callable[[str, str], None]
    legacy_workspace_keys: tuple[str, ...] = ()
    cursor_path: Path | None = None
    urlopen: Callable[..., Any] = urllib.request.urlopen
    sleep: Callable[[float], None] = time.sleep
    _stop: threading.Event = field(default_factory=threading.Event)
    _threads: dict[str, threading.Thread] = field(default_factory=dict)
    _status: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cursor_lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _safe_id(value: Any) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 80 or any(not (char.isalnum() or char in "-_.") for char in text):
            raise ValueError("receiver id must use 1-80 letters, digits, dash, underscore, or dot")
        return text

    def receiver_configs(self) -> dict[str, dict[str, Any]]:
        root = self.load_config().get("external_event_receivers", {})
        workspace = root.get(self.workspace_key, {}) if isinstance(root, dict) else {}
        if not workspace and isinstance(root, dict):
            for legacy_key in self.legacy_workspace_keys:
                legacy = root.get(legacy_key, {})
                if isinstance(legacy, dict) and legacy:
                    workspace = legacy
                    break
        return {str(key): dict(value) for key, value in workspace.items() if isinstance(value, dict)} if isinstance(workspace, dict) else {}

    def migrate_legacy_config(self) -> bool:
        """Project one legacy port-scoped receiver config onto the workspace."""

        cfg = self.load_config()
        root = cfg.get("external_event_receivers", {})
        if not isinstance(root, dict) or isinstance(root.get(self.workspace_key), dict):
            return False
        keys = list(self.legacy_workspace_keys)
        keys.extend(
            key
            for key in sorted(root)
            if key not in keys and str(key).endswith(f"-{self.workspace_key}")
        )
        candidates = [
            (key, root.get(key))
            for key in keys
            if isinstance(root.get(key), dict) and root.get(key)
        ]
        if not candidates:
            return False
        _key, selected = max(
            candidates,
            key=lambda item: int(
                any(
                    isinstance(receiver, dict) and receiver.get("enabled")
                    for receiver in item[1].values()
                )
            ),
        )
        root[self.workspace_key] = json.loads(json.dumps(selected))
        self.save_config(cfg)
        return True

    def save_receiver(self, receiver_id: str, body: dict[str, Any]) -> dict[str, Any]:
        receiver_id = self._safe_id(receiver_id)
        transport = str(body.get("transport") or "webhook").strip().lower()
        if transport not in {"webhook", "sse"}:
            raise ValueError("transport must be webhook or sse")
        url = str(body.get("url") or "").strip()
        if transport == "sse" and not url:
            raise ValueError("SSE receiver requires url")
        event_types = body.get("event_types")
        if isinstance(event_types, str):
            event_types = [part.strip() for part in event_types.split(",") if part.strip()]
        if not isinstance(event_types, list):
            event_types = []
        cursor_json_pointer = str(body.get("cursor_json_pointer") or "").strip()
        if cursor_json_pointer and not cursor_json_pointer.startswith("/"):
            raise ValueError("cursor_json_pointer must be empty or start with /")
        if len(cursor_json_pointer) > 256:
            raise ValueError("cursor_json_pointer is too long")
        cursor_query_parameter = str(body.get("cursor_query_parameter") or "").strip()
        if len(cursor_query_parameter) > 80 or any(char in cursor_query_parameter for char in "&=?#/"):
            raise ValueError("cursor_query_parameter must be a single URL query parameter name")
        input_transport = str(body.get("input_transport") or "auto").strip().lower().replace("-", "_")
        input_transport = {
            "socket": "session_socket",
            "claude_socket": "session_socket",
            "messaging_socket": "session_socket",
            "terminal": "tty",
            "stdin": "tty",
            "llm": "router",
            "context": "router",
        }.get(input_transport, input_transport)
        if input_transport not in {"auto", "session_socket", "tty", "router"}:
            raise ValueError("input_transport must be auto, session_socket, tty, or router")
        cfg = self.load_config()
        root = cfg.setdefault("external_event_receivers", {})
        workspace = root.get(self.workspace_key)
        if not isinstance(workspace, dict):
            workspace = self.receiver_configs()
            root[self.workspace_key] = workspace
        current = workspace.get(receiver_id) if isinstance(workspace.get(receiver_id), dict) else {}
        current.update(
            {
                "enabled": bool(body.get("enabled", current.get("enabled", False))),
                "transport": transport,
                "url": url,
                "event_types": [str(value) for value in event_types if str(value)],
                "cursor_json_pointer": cursor_json_pointer,
                "cursor_query_parameter": cursor_query_parameter,
                "input_transport": input_transport,
            }
        )
        workspace[receiver_id] = current
        self.save_config(cfg)
        self.vault.update(
            receiver_id,
            {
                "webhook_secret": str(body.get("webhook_secret") or ""),
                "authorization": str(body.get("authorization") or ""),
            },
        )
        return self.public_receiver(receiver_id, current)

    def public_receiver(self, receiver_id: str, config: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status.get(receiver_id, {}))
        secrets_config = self.vault.load(receiver_id)
        references: dict[str, dict[str, Any]] = {}
        for field_name, raw_value in (
            ("url", config.get("url")),
            ("webhook_secret", secrets_config.get("webhook_secret")),
            ("authorization", secrets_config.get("authorization")),
        ):
            name = environment_reference_name(raw_value)
            if name:
                references[field_name] = {"name": name, "available": bool(os.environ.get(name))}
        return {
            "id": receiver_id,
            **config,
            **self.vault.status(receiver_id),
            "environment_references": references,
            "status": status,
        }

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_receiver(receiver_id, config) for receiver_id, config in sorted(self.receiver_configs().items())]

    def handle_get(self, handler: Any, path: str) -> bool:
        if path != "/ca/events/receivers":
            return False
        self.write_json(handler, {"ok": True, "receivers": self.list_public(), "external_events_visible_in_web_chat": False})
        return True

    def handle_config_post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        prefix = "/ca/events/receivers/"
        if not path.startswith(prefix):
            return False
        try:
            result = self.save_receiver(path[len(prefix):], body)
        except ValueError as exc:
            self.write_json(handler, {"ok": False, "error": str(exc)}, 400)
            return True
        self.start()
        self.write_json(handler, {"ok": True, "receiver": result, "restart_required": False})
        return True

    def handle_raw_post(self, handler: Any, path: str, raw: bytes) -> bool:
        prefix = "/ca/events/webhooks/"
        if not path.startswith(prefix):
            return False
        try:
            receiver_id = self._safe_id(path[len(prefix):])
            config = self.receiver_configs().get(receiver_id)
            if not config or not config.get("enabled") or config.get("transport") != "webhook":
                self.write_json(handler, {"ok": False, "error": "receiver_not_available"}, 404)
                return True
            if len(raw) > MAX_EVENT_BYTES:
                self.write_json(handler, {"ok": False, "error": "event_too_large"}, 413)
                return True
            secret = resolve_environment_reference(
                self.vault.load(receiver_id).get("webhook_secret", "")
            )
            if not secret:
                self.write_json(handler, {"ok": False, "error": "webhook_secret_not_configured"}, 503)
                return True
            transport_event_id, _timestamp = verify_standard_webhook(raw, handler.headers, secret)
            text = raw.decode("utf-8")
            event = validate_cloud_event(text)
            admitted = self._admit(receiver_id, "webhook", text, event, transport_event_id)
        except UnicodeDecodeError:
            self.write_json(handler, {"ok": False, "error": "CloudEvent must be UTF-8"}, 400)
            return True
        except MissingEnvironmentReference as exc:
            self.write_json(handler, {"ok": False, "error": str(exc)}, 503)
            return True
        except ValueError as exc:
            self.write_json(handler, {"ok": False, "error": str(exc)}, 400)
            return True
        self.write_json(handler, {"ok": True, "accepted": True, "event_id": event["id"], "duplicate": bool(admitted.get("_ciel_runtime_duplicate"))}, 202)
        return True

    def _admit(self, receiver_id: str, transport: str, raw_text: str, event: dict[str, str], transport_event_id: str = "") -> dict[str, Any]:
        receiver = self.receiver_configs().get(receiver_id, {})
        allowed = receiver.get("event_types", [])
        if allowed and event["type"] not in allowed:
            raise ValueError("CloudEvent type is not admitted by this receiver")
        result = self.submit_event(
            raw_text,
            receiver_id=receiver_id,
            transport=transport,
            event_id=event["id"],
            event_type=event["type"],
            event_source=event["source"],
            transport_event_id=transport_event_id,
            input_transport=(
                "" if str(receiver.get("input_transport") or "auto") == "auto"
                else str(receiver.get("input_transport") or "")
            ),
        )
        self._set_status(receiver_id, state="connected", last_event_id=event["id"], last_event_at=time.time(), error="")
        return result

    def _set_status(self, receiver_id: str, **values: Any) -> None:
        with self._lock:
            current = self._status.setdefault(receiver_id, {})
            current.update(values)

    def _read_sse_cursor(self, receiver_id: str) -> str:
        path = self.cursor_path
        if path is None:
            return ""
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return ""
        return str(value.get(receiver_id) or "") if isinstance(value, dict) else ""

    def _write_sse_cursor(self, receiver_id: str, event_id: str) -> None:
        path = self.cursor_path
        if path is None or not event_id:
            return
        with self._cursor_lock:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                value = {}
            if not isinstance(value, dict):
                value = {}
            value[receiver_id] = event_id
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)

    def start(self) -> None:
        self._stop.clear()
        for receiver_id, config in self.receiver_configs().items():
            existing = self._threads.get(receiver_id)
            if existing is not None and not existing.is_alive():
                self._threads.pop(receiver_id, None)
                existing = None
            if not config.get("enabled") or config.get("transport") != "sse" or existing is not None:
                continue
            thread = threading.Thread(target=self._run_sse, args=(receiver_id,), name=f"ciel-event-sse-{receiver_id}", daemon=True)
            self._threads[receiver_id] = thread
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in tuple(self._threads.values()):
            thread.join(timeout=2.0)
        self._threads.clear()

    def _run_sse(self, receiver_id: str) -> None:
        retry = 1.0
        last_event_id = self._read_sse_cursor(receiver_id)
        while not self._stop.is_set():
            config = self.receiver_configs().get(receiver_id, {})
            if not config.get("enabled") or config.get("transport") != "sse":
                break
            cursor_query_parameter = str(config.get("cursor_query_parameter") or "").strip()
            try:
                headers = {"Accept": CLOUDEVENTS_SSE_ACCEPT, "Cache-Control": "no-cache"}
                secret = resolve_environment_reference(
                    self.vault.load(receiver_id).get("authorization", "")
                )
                if secret:
                    headers["Authorization"] = (
                        secret if secret.lower().startswith("bearer ") else f"Bearer {secret}"
                    )
                if last_event_id and not cursor_query_parameter:
                    headers["Last-Event-ID"] = last_event_id
                request_url = sse_reconnect_url(
                    resolve_environment_reference(config.get("url")),
                    cursor_query_parameter,
                    last_event_id,
                )
                request = urllib.request.Request(request_url, headers=headers)
                self._set_status(receiver_id, state="connecting", error="")
                with self.urlopen(request, timeout=90) as response:
                    content_type = str(response.headers.get("content-type") or "").lower()
                    if "text/event-stream" not in content_type:
                        raise ValueError("SSE endpoint did not return text/event-stream")
                    self._set_status(receiver_id, state="connected", connected_at=time.time(), error="")
                    retry = 1.0
                    for transport_event_id, raw_text in parse_sse_frames(response):
                        if self._stop.is_set():
                            break
                        event = validate_cloud_event(raw_text)
                        self._admit(receiver_id, "sse", raw_text, event, transport_event_id)
                        projected_cursor = cloud_event_cursor(
                            raw_text,
                            str(config.get("cursor_json_pointer") or "").strip(),
                        )
                        next_cursor = projected_cursor or transport_event_id
                        if next_cursor:
                            last_event_id = next_cursor
                            self._write_sse_cursor(receiver_id, last_event_id)
            except Exception as exc:
                self._set_status(receiver_id, state="disconnected", error=f"{type(exc).__name__}: {exc}", retry_seconds=retry)
                self.log("WARN", f"external_event_sse_disconnected receiver={receiver_id} error={type(exc).__name__}: {exc}")
            if self._stop.wait(retry):
                break
            retry = min(30.0, retry * 2.0)
        self._set_status(receiver_id, state="stopped")


__all__ = [
    "CLOUDEVENTS_SSE_ACCEPT",
    "EventReceiverSecretVault",
    "MissingEnvironmentReference",
    "ExternalEventReceiverService",
    "cloud_event_cursor",
    "json_pointer_value",
    "parse_sse_frames",
    "environment_reference_name",
    "resolve_environment_reference",
    "sse_reconnect_url",
    "validate_cloud_event",
    "verify_standard_webhook",
]
