"""Per-workspace store of Codex and Claude OAuth tokens for the routed modes.

Two files live in the workspace state directory:

- ``oauth-tokens.vault.json`` holds the credentials (access, refresh and id
  tokens) encrypted with a workspace key (``oauth-tokens.vault.key``, or
  ``CIEL_RUNTIME_SECRET_MASTER_KEY``).
- ``oauth-tokens.state.json`` holds everything that is not secret: labels,
  observed usage, exhaustion and refresh status, and which conversation is
  pinned to which token.

Every read-modify-write runs under an advisory file lock, because the router
and the ``ciel-runtime tokens`` command can touch the store at the same time.
Credential fields are kept out of ``config.json`` on purpose: the workspace
config repository moves every credential-named field into the shared config.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Iterator

from ciel_runtime_support.channel_message_repository import exclusive_file_lock

PROVIDERS = ("codex", "claude")
VAULT_FILE = "oauth-tokens.vault.json"
STATE_FILE = "oauth-tokens.state.json"

STATUS_ACTIVE = "active"
STATUS_REFRESH_FAILED = "refresh_failed"
STATUS_DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class OAuthCredential:
    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    # Codex: the ChatGPT account (workspace) id sent as ChatGPT-Account-ID.
    account_id: str = ""
    # Epoch seconds; 0 when unknown.
    expires_at: float = 0.0
    scopes: tuple[str, ...] = ()


@dataclass(slots=True)
class OAuthTokenState:
    token_id: str
    provider: str
    label: str = ""
    email: str = ""
    source: str = ""
    created_at: float = 0.0
    status: str = STATUS_ACTIVE
    # window name -> {"used_percent": float, "reset_at": float}
    usage: dict[str, dict[str, float]] = field(default_factory=dict)
    usage_observed_at: float = 0.0
    draining: bool = False
    exhausted_until: float = 0.0
    last_error: str = ""
    last_used_at: float = 0.0
    refreshed_at: float = 0.0
    expires_at: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OAuthTokenState":
        known = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        state = cls(**known)
        state.usage = {
            str(name): {str(key): float(number) for key, number in window.items()}
            for name, window in dict(state.usage or {}).items()
            if isinstance(window, dict)
        }
        return state


@dataclass(slots=True)
class OAuthStoreSnapshot:
    """The state file contents: token order is the fill order."""

    tokens: list[OAuthTokenState] = field(default_factory=list)
    # "<provider>:<session key>" -> {"token_id": str, "seen_at": float}
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, token_id: str) -> OAuthTokenState | None:
        return next((token for token in self.tokens if token.token_id == token_id), None)

    def for_provider(self, provider: str) -> list[OAuthTokenState]:
        return [token for token in self.tokens if token.provider == provider]


class OAuthTokenCipher:
    """Authenticated local encryption, the scheme of the event receiver vault."""

    PREFIX = b"COV1"

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self._master: bytes | None = None

    def _key(self) -> bytes:
        if self._master is not None:
            return self._master
        configured = str(os.environ.get("CIEL_RUNTIME_SECRET_MASTER_KEY") or "").strip()
        if configured:
            key = base64.urlsafe_b64decode(configured.encode("ascii"))
        else:
            try:
                key = base64.urlsafe_b64decode(self.key_path.read_text(encoding="ascii").strip())
            except FileNotFoundError:
                key = secrets.token_bytes(32)
                _atomic_write(self.key_path, base64.urlsafe_b64encode(key).decode("ascii"))
        if len(key) != 32:
            raise RuntimeError("oauth token vault key must be 32 bytes")
        self._master = key
        return key

    def _derive(self, purpose: bytes) -> bytes:
        return hmac.new(self._key(), b"ciel-runtime-oauth-v1:" + purpose, hashlib.sha256).digest()

    def _stream(self, nonce: bytes, length: int) -> bytes:
        key = self._derive(b"encryption")
        stream = bytearray()
        for counter in range((length + 31) // 32):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        return bytes(stream[:length])

    def protect(self, plain_text: str) -> str:
        plain = plain_text.encode("utf-8")
        nonce = secrets.token_bytes(16)
        body = self.PREFIX + nonce + bytes(a ^ b for a, b in zip(plain, self._stream(nonce, len(plain))))
        tag = hmac.new(self._derive(b"authentication"), body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + tag).decode("ascii")

    def unprotect(self, encoded: str) -> str:
        payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(payload) < 52 or not payload.startswith(self.PREFIX):
            raise RuntimeError("oauth token vault entry has an invalid format")
        body, supplied = payload[:-32], payload[-32:]
        expected = hmac.new(self._derive(b"authentication"), body, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise RuntimeError("oauth token vault entry failed authentication")
        nonce, cipher = body[4:20], body[20:]
        return bytes(a ^ b for a, b in zip(cipher, self._stream(nonce, len(cipher)))).decode("utf-8")


class OAuthTokenStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = directory
        self.vault_path = directory / VAULT_FILE
        self.state_path = directory / STATE_FILE
        self.cipher = OAuthTokenCipher(self.vault_path.with_suffix(".key"))
        self.clock = clock
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[OAuthStoreSnapshot]:
        """Read, let the caller change, then write the state file under the lock."""

        with self._lock, exclusive_file_lock(self.state_path):
            snapshot = self._read_state()
            before = _state_json(snapshot)
            yield snapshot
            if _state_json(snapshot) != before:
                _atomic_write(self.state_path, _state_json(snapshot))

    def snapshot(self) -> OAuthStoreSnapshot:
        with self._lock, exclusive_file_lock(self.state_path):
            return self._read_state()

    def has_tokens(self, provider: str) -> bool:
        try:
            return any(token.status != STATUS_DISABLED for token in self.snapshot().for_provider(provider))
        except (OSError, ValueError):
            return False

    def add(self, provider: str, credential: OAuthCredential, *, label: str = "", email: str = "", source: str = "") -> OAuthTokenState:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}")
        if not credential.access_token:
            raise ValueError("an access token is required")
        with self.transaction() as snapshot:
            token_id = _new_token_id(provider, {token.token_id for token in snapshot.tokens})
            state = OAuthTokenState(
                token_id=token_id,
                provider=provider,
                label=label or token_id,
                email=email,
                source=source,
                created_at=self.clock(),
                expires_at=credential.expires_at,
            )
            self._write_credential(token_id, credential)
            snapshot.tokens.append(state)
            return state

    def remove(self, token_id: str) -> bool:
        with self.transaction() as snapshot:
            before = len(snapshot.tokens)
            snapshot.tokens = [token for token in snapshot.tokens if token.token_id != token_id]
            snapshot.sessions = {
                key: pin for key, pin in snapshot.sessions.items() if pin.get("token_id") != token_id
            }
            vault = self._read_vault()
            vault["tokens"].pop(token_id, None)
            _atomic_write(self.vault_path, json.dumps(vault, indent=2))
            return len(snapshot.tokens) != before

    def credential(self, token_id: str) -> OAuthCredential | None:
        with self._lock:
            entry = self._read_vault()["tokens"].get(token_id)
        if not isinstance(entry, dict):
            return None
        value = json.loads(self.cipher.unprotect(str(entry.get("credential") or "")))
        value["scopes"] = tuple(value.get("scopes") or ())
        return OAuthCredential(**value)

    def replace_credential(self, token_id: str, credential: OAuthCredential) -> None:
        """Store refreshed credentials; the caller holds :meth:`transaction`."""

        self._write_credential(token_id, credential)

    def _write_credential(self, token_id: str, credential: OAuthCredential) -> None:
        with self._lock:
            vault = self._read_vault()
            payload = asdict(credential)
            payload["scopes"] = list(credential.scopes)
            vault["tokens"][token_id] = {"credential": self.cipher.protect(json.dumps(payload))}
            _atomic_write(self.vault_path, json.dumps(vault, indent=2))

    def _read_vault(self) -> dict[str, Any]:
        try:
            value = json.loads(self.vault_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "tokens": {}}
        if not isinstance(value, dict) or not isinstance(value.get("tokens"), dict):
            raise ValueError("oauth token vault is invalid")
        return value

    def _read_state(self) -> OAuthStoreSnapshot:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return OAuthStoreSnapshot()
        if not isinstance(value, dict):
            raise ValueError("oauth token state is invalid")
        return OAuthStoreSnapshot(
            tokens=[OAuthTokenState.from_dict(item) for item in value.get("tokens") or [] if isinstance(item, dict)],
            sessions={
                str(key): dict(pin) for key, pin in dict(value.get("sessions") or {}).items() if isinstance(pin, dict)
            },
        )


def _state_json(snapshot: OAuthStoreSnapshot) -> str:
    return json.dumps(
        {"version": 1, "tokens": [asdict(token) for token in snapshot.tokens], "sessions": snapshot.sessions},
        indent=2,
        sort_keys=True,
    )


def _new_token_id(provider: str, taken: set[str]) -> str:
    while True:
        candidate = f"{provider}-{secrets.token_hex(3)}"
        if candidate not in taken:
            return candidate


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


__all__ = [
    "OAuthCredential",
    "OAuthStoreSnapshot",
    "OAuthTokenCipher",
    "OAuthTokenState",
    "OAuthTokenStore",
    "PROVIDERS",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_REFRESH_FAILED",
]
