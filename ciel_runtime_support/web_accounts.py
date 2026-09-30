"""Email/password accounts and sessions for the router's web interface and API.

Accounts are machine level (``CONFIG_DIR/web-access``): one sign-in works for
every workspace router on the host. Passwords are stored only as scrypt
hashes; sessions only as SHA-256 digests of their random tokens, so neither
file lets anyone sign in. Changing a password or removing an account revokes
that account's sessions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support.channel_message_repository import exclusive_file_lock

ACCOUNTS_FILE = "accounts.json"
SESSIONS_FILE = "sessions.json"
LOCK_FILE = "web-access"  # exclusive_file_lock adds ".lock"
SESSION_TTL_SECONDS = 12 * 3600.0
MIN_PASSWORD_LENGTH = 8
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WebAccountError(ValueError):
    """A request the account store refuses (bad email, weak password, unknown account)."""


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    encode = lambda data: base64.urlsafe_b64encode(data).decode("ascii")  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.urlsafe_b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_email(email: str) -> str:
    value = str(email or "").strip().lower()
    if not _EMAIL.match(value):
        raise WebAccountError(f"Not an email address: {email!r}")
    return value


def _session_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WebAccountStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory)
        self.clock = clock

    # -- persistence -------------------------------------------------------
    def _read(self, name: str) -> dict[str, Any]:
        try:
            value = json.loads((self.directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, name: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        temporary = path.with_name(f"{name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _lock(self):
        return exclusive_file_lock(self.directory / LOCK_FILE)

    def _accounts(self) -> dict[str, dict[str, Any]]:
        accounts = self._read(ACCOUNTS_FILE).get("accounts")
        return accounts if isinstance(accounts, dict) else {}

    def _sessions(self) -> dict[str, dict[str, Any]]:
        sessions = self._read(SESSIONS_FILE).get("sessions")
        return sessions if isinstance(sessions, dict) else {}

    # -- accounts ----------------------------------------------------------
    def list_accounts(self) -> list[dict[str, Any]]:
        now = self.clock()
        sessions = self._sessions()
        rows = []
        for email, record in sorted(self._accounts().items()):
            active = sum(
                1
                for session in sessions.values()
                if session.get("email") == email and float(session.get("expires_at") or 0) > now
            )
            rows.append(
                {
                    "email": email,
                    "created_at": record.get("created_at"),
                    "password_changed_at": record.get("password_changed_at"),
                    "active_sessions": active,
                }
            )
        return rows

    def has_accounts(self) -> bool:
        return bool(self._accounts())

    def _check_password(self, password: str) -> None:
        if len(str(password or "")) < MIN_PASSWORD_LENGTH:
            raise WebAccountError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    def add(self, email: str, password: str) -> str:
        email = normalize_email(email)
        self._check_password(password)
        with self._lock():
            accounts = self._accounts()
            if email in accounts:
                raise WebAccountError(f"Account {email} already exists; reset its password instead.")
            now = self.clock()
            accounts[email] = {"password": hash_password(password), "created_at": now, "password_changed_at": now}
            self._write(ACCOUNTS_FILE, {"accounts": accounts})
        return email

    def set_password(self, email: str, password: str) -> str:
        email = normalize_email(email)
        self._check_password(password)
        with self._lock():
            accounts = self._accounts()
            if email not in accounts:
                raise WebAccountError(f"No account {email}.")
            accounts[email]["password"] = hash_password(password)
            accounts[email]["password_changed_at"] = self.clock()
            self._write(ACCOUNTS_FILE, {"accounts": accounts})
            self._drop_sessions(lambda session: session.get("email") == email)
        return email

    def remove(self, email: str) -> str:
        email = normalize_email(email)
        with self._lock():
            accounts = self._accounts()
            if accounts.pop(email, None) is None:
                raise WebAccountError(f"No account {email}.")
            self._write(ACCOUNTS_FILE, {"accounts": accounts})
            self._drop_sessions(lambda session: session.get("email") == email)
        return email

    def verify(self, email: str, password: str) -> str | None:
        try:
            email = normalize_email(email)
        except WebAccountError:
            return None
        record = self._accounts().get(email)
        if record is None:
            # Spend the same work as a real check so timing does not reveal accounts.
            verify_password(str(password or ""), hash_password("unused-password"))
            return None
        return email if verify_password(str(password or ""), str(record.get("password") or "")) else None

    # -- sessions ----------------------------------------------------------
    def _drop_sessions(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        sessions = self._sessions()
        now = self.clock()
        kept = {
            key: value
            for key, value in sessions.items()
            if not predicate(value) and float(value.get("expires_at") or 0) > now
        }
        if kept != sessions:
            self._write(SESSIONS_FILE, {"sessions": kept})
        return len(sessions) - len(kept)

    def create_session(self, email: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock():
            sessions = self._sessions()
            now = self.clock()
            sessions = {k: v for k, v in sessions.items() if float(v.get("expires_at") or 0) > now}
            sessions[_session_key(token)] = {"email": email, "created_at": now, "expires_at": now + SESSION_TTL_SECONDS}
            self._write(SESSIONS_FILE, {"sessions": sessions})
        return token

    def session_email(self, token: str) -> str | None:
        if not token:
            return None
        session = self._sessions().get(_session_key(token))
        if not isinstance(session, dict) or float(session.get("expires_at") or 0) <= self.clock():
            return None
        email = str(session.get("email") or "")
        return email if email in self._accounts() else None

    def revoke_session(self, token: str) -> None:
        key = _session_key(token)
        with self._lock():
            sessions = self._sessions()
            if sessions.pop(key, None) is not None:
                self._write(SESSIONS_FILE, {"sessions": sessions})

    def revoke_all_sessions(self) -> int:
        with self._lock():
            return self._drop_sessions(lambda _session: True)


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "SESSION_TTL_SECONDS",
    "WebAccountError",
    "WebAccountStore",
    "hash_password",
    "normalize_email",
    "verify_password",
]
