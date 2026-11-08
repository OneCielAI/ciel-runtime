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

from .event_delivery_ack import RECEIVER_FEATURES, delivery_ack_request
from .event_receiver_connection import ReceiverWorker, SseConnection, idle_timeout_seconds
from .event_receiver_diagnostics import receiver_failure
from .runtime_input_delivery_health import delivery_health_for_callback


MAX_WORK_DISCOVERY_INSTRUCTION_CHARS = 8192
TEST_WORK_DISCOVERY_INSTRUCTION = (
    "작업큐에 여유가 생기면 이 세션에서 사용할 수 있는 검색 수단으로 최근 미결 아이템이 있는지 확인하고 스스로 할 일을 찾아라. "
    "우선순위 P1→P5, 같은 우선순위에서는 최신순으로 검색해서 픽업하라."
)
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


def _discovery_fields(config: Mapping[str, Any]) -> dict[str, Any]:
    fields = {}
    for name in ("discovery_enabled", "test_default_override"):
        value = config.get(name, False)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        fields[name] = value
    instruction = config.get("discovery_instruction", "")
    if not isinstance(instruction, str):
        raise ValueError("discovery_instruction must be a string")
    if len(instruction) > MAX_WORK_DISCOVERY_INSTRUCTION_CHARS:
        raise ValueError("discovery_instruction is too long")
    fields["discovery_instruction"] = instruction.strip()
    file_path = config.get("discovery_file_path", "")
    if file_path is None:
        file_path = ""
    if not isinstance(file_path, str):
        raise ValueError("discovery_file_path must be a string")
    if len(file_path) > 1024:
        raise ValueError("discovery_file_path is too long")
    fields["discovery_file_path"] = file_path.strip()
    return fields


def effective_work_discovery(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Select from a workspace's receiver-id -> config map (receiver_configs()).

    Select the first eligible receiver by sorted id; invalid/missing instructions
    fail closed, without falling through to another source. Never inspect events,
    connect a receiver, or mutate configuration. Environment references use the
    same exact-reference contract as receiver URLs and authorization.
    """
    for receiver_id, receiver in sorted(config.items()):
        if not isinstance(receiver, dict):
            continue
        if (receiver.get("enabled") is not True or receiver.get("transport") != "sse"
                or receiver.get("discovery_enabled") is not True):
            continue
        try:
            fields = _discovery_fields(receiver)
            used_default = fields["test_default_override"]
            file_path = fields.get("discovery_file_path", "")
            if file_path:
                # The file body is selected remotely but read locally; the
                # path itself is shown in status, never the body. A missing
                # file fails closed like a missing instruction.
                from .work_discovery_file import load_file_instruction
                from .runtime_paths import WORKSPACE_STATE_DIR
                instruction = load_file_instruction(file_path, WORKSPACE_STATE_DIR)
                if instruction is None:
                    return None
                return {"receiver_id": receiver_id, "instruction": instruction,
                        "config": dict(receiver), "used_test_default": False,
                        "file_path": file_path}
            instruction = (TEST_WORK_DISCOVERY_INSTRUCTION if used_default else
                           resolve_environment_reference(fields["discovery_instruction"]))
            instruction = instruction.strip()
            if not instruction or len(instruction) > MAX_WORK_DISCOVERY_INSTRUCTION_CHARS:
                return None
        except (ValueError, MissingEnvironmentReference):
            return None
        return {"receiver_id": receiver_id, "instruction": instruction,
                "config": dict(receiver), "used_test_default": used_default}
    return None


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


class EventTypeNotAdmitted(ValueError):
    """A valid CloudEvent the receiver's operator allowlist filters out."""


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
    # event_delivery_ack.DeliveryAckService (Walkie 49269/56190): with it the SSE GET says
    # Ciel-Receiver-Features: event-ack/1 and data.delivery_ack events are acknowledged.
    delivery_ack: Any = None
    # Walkie-Client: ciel-runtime/<version> (<os>) runtime=python/<version>.
    client_header: str = ""
    _stop: threading.Event = field(default_factory=threading.Event)
    _running: bool = False
    _threads: dict[str, threading.Thread] = field(default_factory=dict)
    _workers: dict[str, ReceiverWorker] = field(default_factory=dict)
    _lifecycle_lock: Any = field(default_factory=threading.RLock)
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
        idle_timeout = idle_timeout_seconds(body.get("idle_timeout_seconds"))
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
        discovery = _discovery_fields({**current, **{
            key: body[key] for key in ("discovery_enabled", "discovery_instruction", "test_default_override",
                                       "discovery_file_path")
            if key in body
        }})
        current.update(
            {
                "enabled": bool(body.get("enabled", current.get("enabled", False))),
                **discovery,
                "transport": transport,
                "url": url,
                "event_types": [str(value) for value in event_types if str(value)],
                "cursor_json_pointer": cursor_json_pointer,
                "cursor_query_parameter": cursor_query_parameter,
                "input_transport": input_transport,
                **({"idle_timeout_seconds": idle_timeout} if idle_timeout is not None else {}),
            }
        )
        if "idle_timeout_seconds" in body and idle_timeout is None:
            current.pop("idle_timeout_seconds", None)
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
        with self._lifecycle_lock:
            thread = self._threads.get(receiver_id)
            status["worker_alive"] = bool(thread is not None and thread.is_alive())
        for key, default in {
            "state": "stopped", "attempt_count": 0, "last_attempt_at": None,
            "last_failure_at": None, "last_recovery_at": None, "consecutive_failures": 0,
            "retry_seconds": None, "next_retry_at": None, "diagnostic": None,
            "phase": "stopped", "recovery_count": 0, "last_failure": None,
        }.items():
            status.setdefault(key, default)
        status["delivery"] = delivery_health_for_callback(self.submit_event, receiver_id=receiver_id)
        status.setdefault("idle_timeout_seconds", config.get("idle_timeout_seconds"))
        status.setdefault("effective_idle_timeout_seconds", config.get("idle_timeout_seconds"))
        secrets_config = self.vault.load(receiver_id)
        references: dict[str, dict[str, Any]] = {}
        for field_name, raw_value in (
            ("url", config.get("url")),
            ("discovery_instruction", config.get("discovery_instruction")),
            ("webhook_secret", secrets_config.get("webhook_secret")),
            ("authorization", secrets_config.get("authorization")),
        ):
            name = environment_reference_name(raw_value)
            if name:
                references[field_name] = {"name": name, "available": bool(os.environ.get(name))}
        return {
            "id": receiver_id,
            **config,
            "discovery_enabled": config.get("discovery_enabled", False),
            "discovery_instruction": config.get("discovery_instruction", ""),
            "discovery_file_path": config.get("discovery_file_path", ""),
            "test_default_override": config.get("test_default_override", False),
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
        suffix = path[len(prefix):]
        try:
            if suffix.endswith("/reconnect"):
                receiver_id = self._safe_id(suffix[:-len("/reconnect")])
                accepted = self.reconnect(receiver_id)
                self.write_json(handler, {"ok": accepted, "reconnecting": accepted}, 200 if accepted else 409)
                return True
            receiver_id = self._safe_id(suffix)
            self.save_receiver(receiver_id, body)
            self.reconcile(receiver_id)
            result = self.public_receiver(receiver_id, self.receiver_configs()[receiver_id])
        except ValueError as exc:
            self.write_json(handler, {"ok": False, "error": str(exc)}, 400)
            return True
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

    # SSE-only control: an external sender dumps a local file's full stream as
    # the stop-time discovery message. The flag travels in the CloudEvent
    # data; the body never enters chat history as an instruction source.
    FILE_DUMP_TYPE = "ciel.work.discovery.file"
    MAX_FILE_DUMP_BYTES = 64 * 1024

    def _admit_file_dump(self, receiver_id: str, raw_text: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict) or value.get("type") != self.FILE_DUMP_TYPE:
            return None
        data = value.get("data")
        if not isinstance(data, dict):
            raise ValueError("file dump data must be an object")
        mode = str(data.get("mode") or "").strip()
        if mode == "set":
            file_path = data.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip() or len(file_path) > 1024:
                raise ValueError("file dump set requires file_path")
            stream = data.get("file_stream")
            if not isinstance(stream, str):
                raise ValueError("file dump set requires file_stream text")
            encoded = stream.encode("utf-8")
            if len(encoded) > self.MAX_FILE_DUMP_BYTES:
                raise ValueError("file dump stream is too large")
            from .runtime_paths import WORKSPACE_STATE_DIR
            from .work_discovery_file import resolve_instruction_path
            target = resolve_instruction_path(file_path.strip(), WORKSPACE_STATE_DIR)
            if target is None:
                raise ValueError("file dump path cannot be resolved")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".ciel-tmp")
            temporary.write_text(stream, encoding="utf-8")
            os.replace(temporary, target)
            body: dict[str, Any] = {"discovery_enabled": True,
                                    "discovery_file_path": file_path.strip()}
        elif mode == "clear":
            body = {"discovery_file_path": ""}
        else:
            raise ValueError("file dump mode must be set or clear")
        current = self.receiver_configs().get(receiver_id, {})
        merged = {**current, **body}
        saved = self.save_receiver(receiver_id, merged)
        return {"file_dump": mode, "file_path": saved.get("discovery_file_path", "")}

    def _admit(self, receiver_id: str, transport: str, raw_text: str, event: dict[str, str], transport_event_id: str = "") -> dict[str, Any]:
        receiver = self.receiver_configs().get(receiver_id, {})
        allowed = receiver.get("event_types", [])
        if allowed and event["type"] not in allowed:
            raise EventTypeNotAdmitted("CloudEvent type is not admitted by this receiver")
        if transport == "sse" and event["type"] == self.FILE_DUMP_TYPE:
            dumped = self._admit_file_dump(receiver_id, raw_text)
            if dumped is not None:
                self._set_status(receiver_id, state="connected", last_event_id=event["id"], last_event_at=time.time(), error="")
                return {"_ciel_file_dump": dumped}
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

    def _cursor_scope(self, receiver_id: str, config: dict[str, Any] | None = None) -> str:
        config = self.receiver_configs().get(receiver_id, {}) if config is None else config
        scope = [resolve_environment_reference(config.get("url")),
                 str(config.get("cursor_json_pointer") or ""), str(config.get("cursor_query_parameter") or "")]
        return hashlib.sha256(json.dumps(scope).encode("utf-8")).hexdigest()

    def _read_sse_cursor(self, receiver_id: str) -> str:
        path = self.cursor_path
        if path is None:
            return ""
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return ""
        if not isinstance(value, dict):
            return ""
        scopes = value.get("_receiver_scopes", {})
        if isinstance(scopes, dict) and scopes.get(receiver_id) not in (None, self._cursor_scope(receiver_id)):
            return ""
        return str(value.get(receiver_id) or "")

    def _write_sse_cursor(self, receiver_id: str, event_id: str, scope: str | None = None) -> None:
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
            scopes = value.setdefault("_receiver_scopes", {})
            if not isinstance(scopes, dict):
                scopes = value["_receiver_scopes"] = {}
            scopes[receiver_id] = scope or self._cursor_scope(receiver_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)

    def _ack_endpoint(self, receiver_id: str) -> tuple[str, str]:
        """The receiver's SSE URL and Authorization now, for its acknowledgements."""

        config = self.receiver_configs().get(receiver_id, {})
        if not config.get("enabled") or config.get("transport") != "sse":
            return "", ""
        secret = resolve_environment_reference(self.vault.load(receiver_id).get("authorization", ""))
        authorization = secret if secret.lower().startswith("bearer ") or not secret else f"Bearer {secret}"
        return resolve_environment_reference(config.get("url")), authorization

    def _admit_sse_frame(self, receiver_id: str, sse_url: str, raw_text: str, event: dict[str, str], transport_event_id: str) -> None:
        request = delivery_ack_request(raw_text) if self.delivery_ack is not None else None
        if request is None:
            self._admit(receiver_id, "sse", raw_text, event, transport_event_id)
            return
        received_at = self.delivery_ack.clock()
        # A re-sent event whose first event is already queued is not queued twice (49269 R4).
        original = self.delivery_ack.queued_original(
            receiver_id, request, event["id"], event_source=event.get("source", "")
        )
        admitted = None
        if original is None:
            admitted = self._admit(receiver_id, "sse", raw_text, event, transport_event_id)
        else:
            self._set_status(receiver_id, state="connected", last_event_id=event["id"], last_event_at=time.time(), error="")
        # Persist before the reconnect cursor advances. Failure reconnects from the old cursor.
        self._set_status(receiver_id, phase="ack")
        self.delivery_ack.observe(receiver_id, sse_url, event, request, received_at, admitted, original)

    def _admit_invalid_sse_frame(self, receiver_id: str, raw_text: str, transport_event_id: str, error: ValueError) -> None:
        """Queue a malformed frame with a transport id as untrusted side text.

        The transport id gives it a stable, provider-owned identity and cursor,
        so it no longer pins every later SSE event. Its body asks for nothing:
        no delivery acknowledgement and no injection lane is read from it. With
        side intake off it takes the ordinary untrusted external-event lane.
        """
        digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        event = {"id": "invalid:" + transport_event_id[:200] + ":" + digest,
                 "source": "urn:ciel-runtime:invalid-sse:" + receiver_id, "type": "ciel.invalid_cloud_event"}
        if not raw_text.strip():
            # Nothing to deliver; record it so the cursor may move past it.
            self._dead_letter(receiver_id, "", raw_text, event, transport_event_id, reason="empty_frame")
            return
        self.submit_event(
            raw_text,
            receiver_id=receiver_id,
            transport="sse",
            event_id=event["id"],
            event_type=event["type"],
            event_source=event["source"],
            transport_event_id=transport_event_id,
            input_transport="session_socket",
            side_intake_fallback="raw_text",
        )
        self.log("WARN", "external_event_invalid_frame_admitted_as_side_text "
                 f"receiver={receiver_id} transport_id={transport_event_id[:200]} "
                 f"chars={len(raw_text)} sha256={digest} error={type(error).__name__}")

    def _dead_letter(self, receiver_id: str, sse_url: str, raw_text: str, event: dict[str, str],
                     transport_event_id: str, *, reason: str = "event_type_not_admitted") -> None:
        """Durably record an undeliverable frame, without its content, before its cursor moves."""
        digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        entry = {"at": time.time(), "reason": reason, "receiver_id": receiver_id,
                 **{key: str(value)[:200] for key, value in (
                     ("event_source", event.get("source")), ("event_id", event.get("id")),
                     ("event_type", event.get("type")), ("transport_event_id", transport_event_id))},
                 "chars": len(raw_text), "sha256": digest}
        if self.cursor_path is None:
            raise OSError("SSE dead-letter storage is unavailable")
        path = self.cursor_path.parent / "external-event-dead-letters.jsonl"
        with self._cursor_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        request = delivery_ack_request(raw_text) if self.delivery_ack is not None and sse_url else None
        if request is not None:
            self._set_status(receiver_id, phase="ack")
            self.delivery_ack.observe(receiver_id, sse_url, event, request,
                                      self.delivery_ack.clock(), None, rejected=True)
        self.log("WARN", f"external_event_filtered_dead_letter receiver={receiver_id} reason={reason} "
                 f"type={entry['event_type'][:120]} transport_id={entry['transport_event_id']} "
                 f"chars={len(raw_text)} sha256={digest}")

    def _start_worker(self, receiver_id: str) -> None:
        existing = self._threads.get(receiver_id)
        if existing is not None and existing.is_alive():
            return
        worker = ReceiverWorker()
        self._workers[receiver_id] = worker
        thread = threading.Thread(target=self._worker_main, args=(receiver_id, worker), name=f"ciel-event-sse-{receiver_id}", daemon=True)
        self._threads[receiver_id] = thread
        thread.start()

    def start(self) -> None:
        with self._lifecycle_lock:
            self._running = True
            self._stop.clear()
            if self.delivery_ack is not None:
                self.delivery_ack.start(self._ack_endpoint)
            for receiver_id, config in self.receiver_configs().items():
                if config.get("enabled") and config.get("transport") == "sse":
                    self._start_worker(receiver_id)

    def reconcile(self, receiver_id: str) -> bool:
        """Apply a committed save without taking prelaunch startup ownership."""
        receiver_id = self._safe_id(receiver_id)
        with self._lifecycle_lock:
            config = self.receiver_configs().get(receiver_id, {})
            enabled = config.get("enabled") and config.get("transport") == "sse"
            worker = self._workers.get(receiver_id)
            thread = self._threads.get(receiver_id)
            if not enabled:
                if worker is not None:
                    worker.interrupt(stop=True)
                self._set_status(receiver_id, state="stopped", phase="stopped", error="", diagnostic=None,
                             retry_seconds=None, next_retry_at=None)
                return False
            if not self._running or self._stop.is_set():
                return False
            if worker is not None and thread is not None and thread.is_alive():
                worker.interrupt()
            else:
                self._start_worker(receiver_id)
            self._set_status(receiver_id, state="reconnecting", phase="reconnect", error="", retry_seconds=None, next_retry_at=None)
            return True

    def reconnect(self, receiver_id: str) -> bool:
        return self.reconcile(receiver_id)

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._running = False
            self._stop.set()
            workers = tuple(self._workers.values())
            threads = tuple(self._threads.values())
            for worker in workers:
                worker.interrupt(stop=True)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        # Retain live workers, including urlopen calls still waiting for headers.
        # Their permanent stop token must not be cleared by a later start().
        if self.delivery_ack is not None:
            with self._lifecycle_lock:
                if self._stop.is_set():
                    self.delivery_ack.stop()

    def _worker_main(self, receiver_id: str, worker: ReceiverWorker) -> None:
        try:
            retry = 1.0
            while not self._stop.is_set() and not worker.stop.is_set():
                try:
                    self._run_sse(receiver_id, worker)
                    break
                except Exception as exc:
                    diagnostic = receiver_failure(exc, "worker")
                    self._set_status(receiver_id, state="backoff", phase="worker", error=diagnostic["class"],
                                     diagnostic=diagnostic, last_failure=diagnostic, last_failure_at=time.time(),
                                     retry_seconds=retry, next_retry_at=time.time() + retry)
                    try:
                        self.log("WARN", f"external_event_sse_worker_recovering receiver={receiver_id} error={diagnostic['class']}")
                    except Exception:
                        pass
                    if worker.reconnect.wait(retry):
                        worker.reconnect.clear()
                    retry = min(30.0, retry * 2.0)
        finally:
            with self._lifecycle_lock:
                if self._workers.get(receiver_id) is worker:
                    self._workers.pop(receiver_id, None)
                    self._threads.pop(receiver_id, None)
                    if worker.stop.is_set() and self._running and not self._stop.is_set():
                        self.reconcile(receiver_id)

    def _run_sse(self, receiver_id: str, worker: ReceiverWorker | None = None) -> None:
        worker = worker or ReceiverWorker()
        retry = 1.0
        last_event_id = ""
        cursor_scope = None
        failures = 0
        attempts = 0
        while not self._stop.is_set() and not worker.stop.is_set():
            connection = None
            diagnostic = None
            phase = "config"
            recovered = False
            stream_started = time.monotonic()
            attempts += 1
            self._set_status(receiver_id, state="connecting", phase="config", attempt_count=attempts, last_attempt_at=time.time())

            def activity(metadata: dict[str, Any]) -> None:
                nonlocal recovered, retry, failures
                self._set_status(receiver_id, **{k: v for k, v in metadata.items() if k != "delivery_progress"})
                if (not recovered and metadata.get("delivery_progress", time.monotonic() - stream_started >= 30.0)
                        and not self._stop.is_set() and not worker.stop.is_set() and not worker.reconnect.is_set()):
                    recovered = True
                    values = dict(state="connected", error="", diagnostic=None, retry_seconds=None,
                                  next_retry_at=None, consecutive_failures=0)
                    if failures:
                        values["last_recovery_at"] = time.time()
                        with self._lock:
                            values["recovery_count"] = int(self._status.get(receiver_id, {}).get("recovery_count", 0)) + 1
                    failures = 0
                    retry = 1.0
                    self._set_status(receiver_id, **values)

            try:
                config = self.receiver_configs().get(receiver_id, {})
                if not config.get("enabled") or config.get("transport") != "sse":
                    break
                timeout = idle_timeout_seconds(config.get("idle_timeout_seconds"))
                self._set_status(receiver_id, idle_timeout_seconds=timeout, effective_idle_timeout_seconds=timeout,
                                 heartbeat_observed=False, last_activity_at=None)
                phase = "cursor"
                next_scope = self._cursor_scope(receiver_id, config)
                if cursor_scope is None:
                    last_event_id = self._read_sse_cursor(receiver_id)
                elif cursor_scope != next_scope:
                    last_event_id = ""
                cursor_scope = next_scope
                cursor_query_parameter = str(config.get("cursor_query_parameter") or "").strip()
                headers = {"Accept": CLOUDEVENTS_SSE_ACCEPT, "Cache-Control": "no-cache"}
                if self.client_header:
                    headers["Walkie-Client"] = self.client_header
                if self.delivery_ack is not None:
                    headers["Ciel-Receiver-Features"] = RECEIVER_FEATURES
                phase = "authorization"
                secret = resolve_environment_reference(self.vault.load(receiver_id).get("authorization", ""))
                if secret:
                    headers["Authorization"] = secret if secret.lower().startswith("bearer ") else f"Bearer {secret}"
                if last_event_id and not cursor_query_parameter:
                    headers["Last-Event-ID"] = last_event_id
                phase = "request"
                request_url = sse_reconnect_url(resolve_environment_reference(config.get("url")), cursor_query_parameter, last_event_id)
                request = urllib.request.Request(request_url, headers=headers)
                phase = "connect"
                self._set_status(receiver_id, phase=phase)
                with self.urlopen(request, timeout=90) as response:
                    stream_started = time.monotonic()
                    connection = SseConnection(response, timeout, on_activity=activity)
                    worker.bind(connection)
                    connection.start()
                    phase = "headers"
                    content_type = str(response.headers.get("content-type") or "").lower()
                    if "text/event-stream" not in content_type:
                        raise ValueError("SSE endpoint did not return text/event-stream")
                    self._set_status(receiver_id, state="connected", phase="read", connected_at=time.time(),
                                     error="", diagnostic=None, retry_seconds=None, next_retry_at=None)
                    if self.delivery_ack is not None:
                        self.delivery_ack.reconnected(receiver_id)
                    phase = "read"
                    for transport_event_id, raw_text in parse_sse_frames(connection.lines()):
                        if self._stop.is_set() or worker.stop.is_set() or worker.reconnect.is_set():
                            break
                        phase = "admission"
                        self._set_status(receiver_id, phase=phase)
                        try:
                            event = validate_cloud_event(raw_text)
                        except ValueError as error:
                            if not transport_event_id:
                                raise
                            self._admit_invalid_sse_frame(receiver_id, raw_text, transport_event_id, error)
                            event = None
                        else:
                            try:
                                self._admit_sse_frame(receiver_id, request_url, raw_text, event, transport_event_id)
                            except EventTypeNotAdmitted:
                                self._dead_letter(receiver_id, request_url, raw_text, event, transport_event_id)
                        # An invalid frame's body is untrusted; only its transport id is a cursor.
                        projected_cursor = "" if event is None else cloud_event_cursor(
                            raw_text, str(config.get("cursor_json_pointer") or "").strip())
                        next_cursor = projected_cursor or transport_event_id
                        if next_cursor:
                            phase = "cursor_write"
                            self._set_status(receiver_id, phase=phase)
                            self._write_sse_cursor(receiver_id, next_cursor, cursor_scope)
                            last_event_id = next_cursor
                        activity({"delivery_progress": True})
                        phase = "read"
                        self._set_status(receiver_id, phase=phase)
                    if connection.timed_out:
                        raise TimeoutError("SSE line activity timed out")
                    if not self._stop.is_set() and not worker.stop.is_set() and not worker.reconnect.is_set():
                        raise EOFError()
                    phase = "cleanup"
            except Exception as exc:
                if phase == "admission":
                    with self._lock:
                        phase = self._status.get(receiver_id, {}).get("phase", phase)
                diagnostic = receiver_failure(exc, phase)
            finally:
                try:
                    if connection is not None:
                        connection.close()
                except Exception as exc:
                    if diagnostic is None:
                        diagnostic = receiver_failure(exc, "cleanup")
                worker.bind(None)
            if self._stop.is_set() or worker.stop.is_set():
                break
            if worker.reconnect.is_set():
                worker.reconnect.clear()
                continue
            failures += 1
            diagnostic = diagnostic or {"class": "Exception", "category": "internal", "phase": phase}
            self._set_status(receiver_id, state="backoff", phase=diagnostic["phase"],
                             error=diagnostic["class"], diagnostic=diagnostic, last_failure=diagnostic,
                             last_failure_at=time.time(), consecutive_failures=failures,
                             retry_seconds=retry, next_retry_at=time.time() + retry)
            try:
                self.log("WARN", f"external_event_sse_disconnected receiver={receiver_id} error={diagnostic['class']} diagnostic={json.dumps(diagnostic, sort_keys=True)}")
            except Exception:
                pass
            if worker.reconnect.wait(retry):
                worker.reconnect.clear()
            if self._stop.is_set() or worker.stop.is_set():
                break
            retry = min(30.0, retry * 2.0)
        self._set_status(receiver_id, state="stopped", phase="stopped", error="", diagnostic=None,
                             retry_seconds=None, next_retry_at=None)


__all__ = [
    "CLOUDEVENTS_SSE_ACCEPT",
    "EventReceiverSecretVault",
    "MAX_WORK_DISCOVERY_INSTRUCTION_CHARS",
    "TEST_WORK_DISCOVERY_INSTRUCTION",
    "effective_work_discovery",
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
    "idle_timeout_seconds",
]
