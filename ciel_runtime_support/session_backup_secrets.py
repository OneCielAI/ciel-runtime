"""Credentials in a session backup: split out, then encrypted with a backup key.

Secrets never go into a snapshot as plain chunks.  Whole secret files (CLI
sign-ins, Ciel vaults together with their ``.key`` files) and the credential
fields of JSON settings are gathered into one bundle that is encrypted with a
key the operator supplies (``CIEL_RUNTIME_BACKUP_KEY`` or ``--key-file``) and
that is never stored in the backup.  Without a key the bundle is left out and
the snapshot records only which secrets were skipped (names, never values).

The cipher is stdlib only, in the scheme of the OAuth token vault: scrypt key
derivation, an HMAC-SHA256 counter keystream and an HMAC-SHA256 tag.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping

from ciel_runtime_support.config_repository import is_shared_credential_field

KEY_ENV = "CIEL_RUNTIME_BACKUP_KEY"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_EXTRA_SECRET_WORDS = ("apikey", "api_key", "token", "secret", "password", "credential", "cookie")


def secret_field(name: Any) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    return is_shared_credential_field(lowered) or any(word in lowered for word in _EXTRA_SECRET_WORDS)


def split_secret_fields(value: Any) -> tuple[Any, Any]:
    """(public part, secret part) of a JSON value; the secret part keeps the nesting."""

    if isinstance(value, dict):
        public: dict[str, Any] = {}
        hidden: dict[str, Any] = {}
        for key, item in value.items():
            if secret_field(key) and item not in (None, "", [], {}):
                hidden[key] = item
                continue
            kept, nested = split_secret_fields(item)
            public[key] = kept
            if nested not in (None, {}, []):
                hidden[key] = nested
        return public, hidden
    if isinstance(value, list):
        pairs = [split_secret_fields(item) for item in value]
        hidden_list = [nested for _, nested in pairs]
        return [kept for kept, _ in pairs], (hidden_list if any(item not in (None, {}, []) for item in hidden_list) else None)
    return value, None


def merge_secret_fields(public: Any, hidden: Any) -> Any:
    if isinstance(public, dict) and isinstance(hidden, dict):
        merged = dict(public)
        for key, item in hidden.items():
            merged[key] = merge_secret_fields(merged.get(key), item) if key in merged else item
        return merged
    if isinstance(public, list) and isinstance(hidden, list):
        return [
            merge_secret_fields(item, hidden[index]) if index < len(hidden) and hidden[index] is not None else item
            for index, item in enumerate(public)
        ]
    return public if hidden is None else hidden


def backup_key(environ: Mapping[str, str], key_file: str | os.PathLike[str] | None = None) -> bytes | None:
    """The operator's backup key (raw passphrase bytes), or None when there is none."""

    if key_file:
        data = Path(key_file).read_bytes().strip()
        if not data:
            raise ValueError(f"backup key file {key_file} is empty")
        return data
    value = str(environ.get(KEY_ENV) or "").strip()
    return value.encode("utf-8") if value else None


def _derive(passphrase: bytes, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.scrypt(passphrase, salt=salt, dklen=64, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return material[:32], material[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def seal(plain: bytes, passphrase: bytes) -> bytes:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    enc_key, mac_key = _derive(passphrase, salt)
    body = bytes(a ^ b for a, b in zip(plain, _keystream(enc_key, nonce, len(plain))))
    tag = hmac.new(mac_key, salt + nonce + body, hashlib.sha256).digest()
    envelope = {
        "v": 1,
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "body": base64.b64encode(body).decode(),
        "tag": base64.b64encode(tag).decode(),
    }
    return json.dumps(envelope).encode("utf-8")


def open_sealed(sealed: bytes, passphrase: bytes) -> bytes:
    envelope = json.loads(sealed.decode("utf-8"))
    salt = base64.b64decode(envelope["salt"])
    nonce = base64.b64decode(envelope["nonce"])
    body = base64.b64decode(envelope["body"])
    enc_key, mac_key = _derive(passphrase, salt)
    expected = hmac.new(mac_key, salt + nonce + body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, base64.b64decode(envelope["tag"])):
        raise ValueError("wrong backup key, or the secrets bundle was altered")
    return bytes(a ^ b for a, b in zip(body, _keystream(enc_key, nonce, len(body))))


class SecretBundle:
    """Secret file contents and JSON credential fields, keyed by snapshot path."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fields: dict[str, Any] = {}

    def __bool__(self) -> bool:
        return bool(self.files or self.fields)

    def names(self) -> list[str]:
        return sorted([*self.files, *(f"{path}#fields" for path in self.fields)])

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "files": {path: base64.b64encode(data).decode() for path, data in self.files.items()},
                "fields": self.fields,
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "SecretBundle":
        payload = json.loads(data.decode("utf-8"))
        bundle = cls()
        bundle.files = {path: base64.b64decode(text) for path, text in (payload.get("files") or {}).items()}
        bundle.fields = dict(payload.get("fields") or {})
        return bundle


__all__ = [
    "KEY_ENV",
    "SecretBundle",
    "backup_key",
    "merge_secret_fields",
    "open_sealed",
    "seal",
    "secret_field",
    "split_secret_fields",
]
