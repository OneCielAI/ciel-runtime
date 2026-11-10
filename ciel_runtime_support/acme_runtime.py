"""Adapter-driven ACME certificate lifecycle and HTTP-01 challenge primitives.

This module deliberately defines a small adapter contract rather than binding the
runtime to an external ACME client's unstable API.  An integration adapter owns
all protocol/JWS behavior; this module owns validation, persistence, concurrency,
and safe activation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import urllib.error
import urllib.request
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from ciel_runtime_support.tls_runtime import (
    ActivatedCertificate,
    CertificateMetadata,
    TLSBundleStore,
    TLSConfigurationError,
    TLSRuntimeError,
    normalize_server_names,
)


_HTTP01_PREFIX = "/.well-known/acme-challenge/"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22,}")
_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ACMERuntimeError(RuntimeError):
    """Base error with an operator-safe, non-secret message."""


class ACMEConfigurationError(ACMERuntimeError):
    """ACME configuration or advertised capabilities are invalid."""


class ACMEOperationError(ACMERuntimeError):
    """Account registration or issuance failed without exposing adapter data."""


@dataclass(frozen=True, slots=True)
class ACMEConfig:
    directory_url: str
    identifiers: tuple[str, ...]
    profile: str | None = None
    contacts: tuple[str, ...] = ()

    def validated(self) -> "ACMEConfig":
        directory_url = _https_url(self.directory_url, "ACME directory")
        try:
            identifiers = normalize_server_names(self.identifiers)
        except TLSConfigurationError as error:
            raise ACMEConfigurationError(str(error)) from error
        if not identifiers:
            raise ACMEConfigurationError("ACME requires at least one identifier")
        profile = str(self.profile or "").strip() or None
        if profile is not None and not _PROFILE_RE.fullmatch(profile):
            raise ACMEConfigurationError("invalid ACME profile name")
        contacts: list[str] = []
        for raw in self.contacts:
            value = str(raw or "").strip()
            if not value or any(character.isspace() for character in value):
                raise ACMEConfigurationError("invalid ACME account contact")
            scheme = urlsplit(value).scheme.lower()
            if scheme not in {"mailto", "tel"}:
                raise ACMEConfigurationError("ACME contacts must use mailto or tel URLs")
            if value not in contacts:
                contacts.append(value)
        return ACMEConfig(directory_url, identifiers, profile, tuple(contacts))

    def redacted_status(self) -> dict[str, Any]:
        config = self.validated()
        return {
            "directory_origin": _origin(config.directory_url),
            "identifiers": list(config.identifiers),
            "profile": config.profile or "default",
            "contact_count": len(config.contacts),
        }


@dataclass(frozen=True, slots=True)
class ACMEDirectory:
    new_nonce: str
    new_account: str
    new_order: str
    profiles: tuple[str, ...] = ()
    renewal_info: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], requested_profile: str | None = None) -> "ACMEDirectory":
        try:
            new_nonce = _https_url(value.get("newNonce"), "newNonce endpoint")
            new_account = _https_url(value.get("newAccount"), "newAccount endpoint")
            new_order = _https_url(value.get("newOrder"), "newOrder endpoint")
        except AttributeError as error:
            raise ACMEConfigurationError("ACME directory response is invalid") from error
        metadata = value.get("meta") or {}
        if not isinstance(metadata, Mapping):
            raise ACMEConfigurationError("ACME directory metadata is invalid")
        advertised = metadata.get("profiles") or {}
        if not isinstance(advertised, Mapping):
            raise ACMEConfigurationError("ACME profile metadata is invalid")
        profiles: list[str] = []
        for name in advertised:
            normalized = str(name)
            if not _PROFILE_RE.fullmatch(normalized):
                raise ACMEConfigurationError("ACME directory advertises an invalid profile name")
            profiles.append(normalized)
        if requested_profile is not None and requested_profile not in profiles:
            raise ACMEConfigurationError("configured ACME profile is not advertised by the directory")
        renewal_value = value.get("renewalInfo")
        renewal_info = _https_url(renewal_value, "renewalInfo endpoint") if renewal_value else None
        return cls(new_nonce, new_account, new_order, tuple(sorted(profiles)), renewal_info)


@dataclass(frozen=True, slots=True)
class ACMEAccount:
    account_url: str
    private_key_pem: str = field(repr=False)
    contacts: tuple[str, ...] = ()

    def validated(self) -> "ACMEAccount":
        url = _https_url(self.account_url, "ACME account")
        key = str(self.private_key_pem or "")
        if "-----BEGIN" not in key or "PRIVATE KEY-----" not in key:
            raise ACMEOperationError("ACME adapter returned an invalid account")
        return ACMEAccount(url, key, tuple(self.contacts))


@dataclass(frozen=True, slots=True)
class ACMEOrderRequest:
    directory: ACMEDirectory
    directory_url: str
    identifiers: tuple[str, ...]
    profile: str | None


@dataclass(frozen=True, slots=True)
class IssuedCertificate:
    certificate_pem: bytes = field(repr=False)
    private_key_pem: bytes = field(repr=False)


class ACMEAdapter(Protocol):
    """Integration port; implementations may wrap any conforming ACME client."""

    def get_directory(self, directory_url: str) -> Mapping[str, Any]: ...

    def register_account(self, directory: ACMEDirectory, contacts: tuple[str, ...]) -> ACMEAccount: ...

    def issue_certificate(
        self,
        request: ACMEOrderRequest,
        account: ACMEAccount,
        challenges: "HTTP01TokenStore",
    ) -> IssuedCertificate: ...


@dataclass(frozen=True, slots=True)
class HTTP01Response:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


class HTTP01TokenStore:
    """In-memory, explicitly provisioned HTTP-01 tokens."""

    def __init__(self) -> None:
        self._values: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def provision(self, token: str, key_authorization: str) -> None:
        normalized_token = str(token or "")
        authorization = str(key_authorization or "")
        if not _TOKEN_RE.fullmatch(normalized_token):
            raise ACMEConfigurationError("invalid HTTP-01 challenge token")
        if not authorization.startswith(f"{normalized_token}."):
            raise ACMEConfigurationError("HTTP-01 key authorization does not match its token")
        try:
            encoded = authorization.encode("ascii")
        except UnicodeEncodeError as error:
            raise ACMEConfigurationError("HTTP-01 key authorization must be ASCII") from error
        with self._lock:
            self._values[normalized_token] = encoded

    def remove(self, token: str) -> None:
        with self._lock:
            self._values.pop(str(token), None)

    def resolve(self, token: str) -> bytes | None:
        with self._lock:
            return self._values.get(str(token))

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class HTTP01ChallengeHandler:
    """Composable handler for ``/.well-known/acme-challenge/`` GETs."""

    def __init__(self, tokens: HTTP01TokenStore) -> None:
        self.tokens = tokens

    def handle(self, method: str, path: str) -> HTTP01Response | None:
        clean_path = str(path or "").split("?", 1)[0]
        if not clean_path.startswith(_HTTP01_PREFIX):
            return None
        if method.upper() != "GET":
            return HTTP01Response(405, headers={"Allow": "GET", "Cache-Control": "no-store"})
        token = clean_path[len(_HTTP01_PREFIX) :]
        if "/" in token or not _TOKEN_RE.fullmatch(token):
            return HTTP01Response(404, headers={"Cache-Control": "no-store"})
        value = self.tokens.resolve(token)
        if value is None:
            return HTTP01Response(404, headers={"Cache-Control": "no-store"})
        return HTTP01Response(
            200,
            value,
            {"Content-Type": "application/octet-stream", "Cache-Control": "no-store"},
        )


def _https_url(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise ACMEConfigurationError(f"{label} must be a valid HTTPS URL") from error
    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or any(character.isspace() or ord(character) < 32 for character in text)
        or port is not None and not 1 <= port <= 65535
    ):
        raise ACMEConfigurationError(f"{label} must be an HTTPS URL without credentials or fragments")
    return text


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme.lower()}://{parts.hostname}{port}"


def _system_resolver(host: str) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ACMEConfigurationError("ACME identifier DNS resolution failed") from error
    return tuple(dict.fromkeys(str(result[4][0]) for result in results))


def validate_public_identifiers(
    identifiers: Sequence[str],
    resolver: Callable[[str], Sequence[str]] = _system_resolver,
) -> dict[str, tuple[str, ...]]:
    """Resolve every DNS identifier and reject any non-global address."""

    try:
        normalized = normalize_server_names(identifiers)
    except TLSConfigurationError as error:
        raise ACMEConfigurationError(str(error)) from error
    resolved: dict[str, tuple[str, ...]] = {}
    for identifier in normalized:
        try:
            literal = ipaddress.ip_address(identifier)
        except ValueError:
            try:
                addresses = tuple(str(address) for address in resolver(identifier))
            except ACMEConfigurationError:
                raise
            except Exception as error:
                raise ACMEConfigurationError("ACME identifier DNS resolution failed") from error
        else:
            addresses = (str(literal),)
        if not addresses:
            raise ACMEConfigurationError("ACME identifier did not resolve to an address")
        canonical: list[str] = []
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as error:
                raise ACMEConfigurationError("ACME identifier resolver returned an invalid address") from error
            if not parsed.is_global or parsed.is_multicast or parsed.is_unspecified:
                raise ACMEConfigurationError("ACME identifiers must resolve only to public IP addresses")
            if isinstance(parsed, ipaddress.IPv6Address) and parsed.sixtofour is not None and not parsed.sixtofour.is_global:
                raise ACMEConfigurationError("ACME identifiers must resolve only to public IP addresses")
            text = str(parsed)
            if text not in canonical:
                canonical.append(text)
        resolved[identifier] = tuple(canonical)
    return resolved


def renewal_due(metadata: CertificateMetadata, now: datetime | None = None) -> bool:
    """Use LE's ARI backstop: halfway under 10 days, otherwise 1/3 left."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    lifetime = metadata.lifetime_seconds
    if lifetime <= 0:
        return True
    remaining = (metadata.not_after - current).total_seconds()
    threshold = lifetime * (0.5 if lifetime < 10 * 24 * 60 * 60 else 1 / 3)
    return remaining <= threshold


def _write_atomic_restricted(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{os.getpid()}.{time.time_ns()}.{os.urandom(4).hex()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class ACMEAccountStore:
    """Atomic local account storage with owner-only permissions where supported."""

    def __init__(self, root: Path | str) -> None:
        configured = Path(root).expanduser()
        if configured.exists() and configured.is_symlink():
            raise ACMEConfigurationError("ACME account storage must not be a symbolic link")
        self.root = configured.resolve()
        self.path = self.root / "account.json"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root, 0o700)

    def load(self) -> ACMEAccount | None:
        with self._lock:
            try:
                details = self.path.lstat()
                if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                    raise ACMEOperationError("ACME account storage is unsafe")
                value = json.loads(self.path.read_text(encoding="utf-8"))
                private_key = base64.b64decode(value["private_key"], validate=True).decode("ascii")
                contacts = tuple(str(contact) for contact in value.get("contacts") or ())
                return ACMEAccount(str(value["account_url"]), private_key, contacts).validated()
            except FileNotFoundError:
                return None
            except ACMEOperationError:
                raise
            except Exception as error:
                raise ACMEOperationError("ACME account storage is invalid") from error

    def save(self, account: ACMEAccount) -> None:
        valid = account.validated()
        try:
            current = self.path.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and (stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode)):
            raise ACMEOperationError("ACME account storage is unsafe")
        value = {
            "version": 1,
            "account_url": valid.account_url,
            "private_key": base64.b64encode(valid.private_key_pem.encode("ascii")).decode("ascii"),
            "contacts": list(valid.contacts),
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self._lock:
            _write_atomic_restricted(self.path, raw)

    def exists(self) -> bool:
        return self.path.is_file()


class ACMECertificateManager:
    """Single-flight issuance/renewal with last-known-good TLS retention."""

    def __init__(
        self,
        adapter: ACMEAdapter,
        config: ACMEConfig,
        state_root: Path | str,
        tls_store: TLSBundleStore,
        *,
        resolver: Callable[[str], Sequence[str]] = _system_resolver,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.adapter = adapter
        self.config = config.validated()
        self.account_store = ACMEAccountStore(state_root)
        self.tls_store = tls_store
        self.resolver = resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.challenges = HTTP01TokenStore()
        self.challenge_handler = HTTP01ChallengeHandler(self.challenges)
        self._condition = threading.Condition()
        self._inflight = False
        self._completed_result: ActivatedCertificate | None = None
        self._completed_error: ACMERuntimeError | None = None
        self._last_action = "idle"
        self._last_error: str | None = None

    def ensure_certificate(self, *, force: bool = False) -> ActivatedCertificate:
        with self._condition:
            if self._inflight:
                while self._inflight:
                    self._condition.wait()
                if self._completed_error is not None:
                    raise self._completed_error
                if self._completed_result is None:
                    raise ACMEOperationError("ACME certificate operation did not produce a result")
                return self._completed_result
            self._inflight = True
            self._completed_result = None
            self._completed_error = None
        try:
            result = self._ensure(force=force)
        except ACMERuntimeError as error:
            with self._condition:
                self._completed_error = error
                self._last_error = str(error)
            raise
        except Exception as error:
            safe = ACMEOperationError("ACME certificate operation failed")
            with self._condition:
                self._completed_error = safe
                self._last_error = str(safe)
            raise safe from error
        else:
            with self._condition:
                self._completed_result = result
                self._last_error = None
            return result
        finally:
            self.challenges.clear()
            with self._condition:
                self._inflight = False
                self._condition.notify_all()

    def _ensure(self, *, force: bool) -> ActivatedCertificate:
        now = self.clock()
        current = self.tls_store.current(now=now, validate_time=False)
        if current is not None and not force and not renewal_due(current.metadata, now):
            self._last_action = "unchanged"
            return current
        validate_public_identifiers(self.config.identifiers, self.resolver)
        try:
            raw_directory = self.adapter.get_directory(self.config.directory_url)
        except Exception as error:
            raise ACMEOperationError("ACME directory discovery failed") from error
        if not isinstance(raw_directory, Mapping):
            raise ACMEOperationError("ACME directory discovery failed")
        directory = ACMEDirectory.from_mapping(raw_directory, self.config.profile)
        account = self.account_store.load()
        if account is None:
            try:
                account = self.adapter.register_account(directory, self.config.contacts).validated()
            except Exception as error:
                raise ACMEOperationError("ACME account registration failed") from error
            self.account_store.save(account)
        request = ACMEOrderRequest(
            directory=directory,
            directory_url=self.config.directory_url,
            identifiers=self.config.identifiers,
            profile=self.config.profile,
        )
        try:
            issued = self.adapter.issue_certificate(request, account, self.challenges)
            if not isinstance(issued, IssuedCertificate):
                raise TypeError("invalid adapter result")
        except Exception as error:
            raise ACMEOperationError("ACME certificate issuance failed") from error
        try:
            activated = self.tls_store.activate(
                issued.certificate_pem,
                issued.private_key_pem,
                self.config.identifiers,
                now=now,
            )
        except TLSRuntimeError as error:
            raise ACMEOperationError("issued certificate failed TLS validation or activation") from error
        self._last_action = "renewed" if current is not None else "issued"
        return activated

    def status(self) -> dict[str, Any]:
        with self._condition:
            inflight = self._inflight
            action = self._last_action
            error = self._last_error
        status: dict[str, Any] = {
            **self.config.redacted_status(),
            "account_present": self.account_store.exists(),
            "inflight": inflight,
            "last_action": action,
            "certificate": self.tls_store.redacted_status(now=self.clock()),
        }
        if error:
            status["last_error"] = error
        return status


# Minimal stdlib ACME v2 adapter. RSA/JWS primitives stay private to this module;
# the manager above retains storage, validation, single-flight, and activation.
def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _json64(value: Mapping[str, Any]) -> str:
    return _b64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _der_length(size: int) -> bytes:
    if size < 128:
        return bytes([size])
    encoded = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([128 | len(encoded)]) + encoded


def _der_integer(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")
    return b"\x02" + _der_length(len(raw)) + raw


def _der_sequence(*parts: bytes) -> bytes:
    body = b"".join(parts)
    return b"\x30" + _der_length(len(body)) + body


def _pem(label: str, der: bytes) -> bytes:
    body = base64.encodebytes(der).replace(b"\n", b"")
    lines = b"\n".join(body[index:index + 64] for index in range(0, len(body), 64))
    return b"-----BEGIN " + label.encode() + b"-----\n" + lines + b"\n-----END " + label.encode() + b"-----\n"


@dataclass(frozen=True, slots=True)
class _RSAKey:
    n: int
    e: int
    d: int
    p: int
    q: int

    @property
    def size(self) -> int:
        return (self.n.bit_length() + 7) // 8

    def jwk(self) -> dict[str, str]:
        return {"e": _b64url(self.e.to_bytes((self.e.bit_length() + 7) // 8, "big")),
                "kty": "RSA", "n": _b64url(self.n.to_bytes(self.size, "big"))}

    def thumbprint(self) -> str:
        return _b64url(hashlib.sha256(json.dumps(self.jwk(), sort_keys=True,
            separators=(",", ":")).encode()).digest())

    def sign(self, value: bytes) -> bytes:
        digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(value).digest()
        encoded = b"\x00\x01" + b"\xff" * (self.size - len(digest) - 3) + b"\x00" + digest
        return pow(int.from_bytes(encoded, "big"), self.d, self.n).to_bytes(self.size, "big")

    def private_pem(self) -> bytes:
        return _pem("RSA PRIVATE KEY", _der_sequence(
            _der_integer(0), _der_integer(self.n), _der_integer(self.e), _der_integer(self.d),
            _der_integer(self.p), _der_integer(self.q), _der_integer(self.d % (self.p - 1)),
            _der_integer(self.d % (self.q - 1)), _der_integer(pow(self.q, -1, self.p))))


def _probably_prime(candidate: int) -> bool:
    small = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    if any(candidate % prime == 0 for prime in small):
        return candidate in small
    exponent, rounds = candidate - 1, 0
    while exponent % 2 == 0:
        exponent //= 2
        rounds += 1
    for base in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53):
        if base >= candidate:
            continue
        value = pow(base, exponent, candidate)
        if value in (1, candidate - 1):
            continue
        for _ in range(rounds - 1):
            value = pow(value, 2, candidate)
            if value == candidate - 1:
                break
        else:
            return False
    return True


def _generate_rsa(bits: int = 2048) -> _RSAKey:
    e = 65537
    def prime() -> int:
        while True:
            value = int.from_bytes(os.urandom(bits // 16), "big") | (1 << (bits // 2 - 1)) | 1
            if (value - 1) % e and _probably_prime(value):
                return value
    while True:
        p, q = prime(), prime()
        if p != q and (p * q).bit_length() == bits:
            break
    if p < q:
        p, q = q, p
    return _RSAKey(p * q, e, pow(e, -1, (p - 1) * (q - 1)), p, q)


def _acme_der_item(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    if offset + 2 > len(data):
        raise ACMEOperationError("ACME key DER encoding is invalid")
    tag, first = data[offset], data[offset + 1]
    position = offset + 2
    if first < 128:
        length = first
    else:
        width = first & 127
        if width == 0 or width > 4 or position + width > len(data):
            raise ACMEOperationError("ACME key DER encoding is invalid")
        length = int.from_bytes(data[position:position + width], "big")
        position += width
    end = position + length
    if end > len(data):
        raise ACMEOperationError("ACME key DER encoding is invalid")
    return tag, data[position:end], end


def _acme_der_children(data: bytes) -> list[tuple[int, bytes]]:
    values, offset = [], 0
    while offset < len(data):
        tag, value, offset = _acme_der_item(data, offset)
        values.append((tag, value))
    return values


def _parse_rsa_private(pem: str | bytes) -> _RSAKey:
    raw = pem.encode() if isinstance(pem, str) else pem
    try:
        body = b"".join(line for line in raw.splitlines() if not line.startswith(b"-----"))
        tag, sequence, end = _acme_der_item(base64.b64decode(body, validate=True))
        fields = _acme_der_children(sequence)
        values = [int.from_bytes(value, "big") for tag, value in fields if tag == 0x02]
    except Exception as error:
        raise ACMEOperationError("ACME RSA private key is invalid") from error
    if tag != 0x30 or end <= 0 or len(values) < 6:
        raise ACMEOperationError("ACME RSA private key is invalid")
    return _RSAKey(values[1], values[2], values[3], values[4], values[5])


def _csr(key: _RSAKey, identifiers: Sequence[str]) -> bytes:
    algorithm = _der_sequence(bytes.fromhex("06092a864886f70d010101"), b"\x05\x00")
    public = _der_sequence(_der_integer(key.n), _der_integer(key.e))
    spki = _der_sequence(algorithm, b"\x03" + _der_length(len(public) + 1) + b"\x00" + public)
    names = []
    for identifier in identifiers:
        try:
            packed = ipaddress.ip_address(identifier).packed
        except ValueError:
            names.append(b"\x82" + _der_length(len(identifier.encode("ascii"))) + identifier.encode("ascii"))
        else:
            names.append(b"\x87" + _der_length(len(packed)) + packed)
    sans = _der_sequence(*names)
    extension = _der_sequence(bytes.fromhex("0603551d11"), b"\x04" + _der_length(len(sans)) + sans)
    extensions = _der_sequence(extension)
    attribute = _der_sequence(bytes.fromhex("06092a864886f70d01090e"), b"\x31" + _der_length(len(extensions)) + extensions)
    info = _der_sequence(_der_integer(0), b"\x30\x00", spki,
                         b"\xa0" + _der_length(len(attribute)) + attribute)
    signature_algorithm = _der_sequence(bytes.fromhex("06092a864886f70d01010b"), b"\x05\x00")
    signature = key.sign(info)
    return _der_sequence(info, signature_algorithm, b"\x03" + _der_length(len(signature) + 1) + b"\x00" + signature)


class HTTP01ChallengeServer:
    """Dedicated port-80 listener used only while an ACME operation is active."""

    def __init__(self, challenges: HTTP01ChallengeHandler, host: str = "0.0.0.0", port: int = 80) -> None:
        self.challenges, self.host, self.port = challenges, host, port
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "HTTP01ChallengeServer":
        challenges = self.challenges
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                response = challenges.handle("GET", self.path)
                if response is None:
                    response = HTTP01Response(404, headers={"Cache-Control": "no-store"})
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                if response.body:
                    self.wfile.write(response.body)
            def log_message(self, _format: str, *_args: Any) -> None:
                return None
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as error:
            raise ACMEOperationError("ACME HTTP-01 listener could not bind to port 80") from error
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, _kind: Any, _value: Any, _traceback: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(5)


class StdlibACMEAdapter:
    """ACME v2 RSA adapter with HTTP-01 support and no optional dependency."""

    def __init__(self, *, timeout: float = 30.0, poll_seconds: float = 1.0,
                 poll_limit: int = 120, user_agent: str = "ciel-runtime-acme/1") -> None:
        self.timeout, self.poll_seconds, self.poll_limit = timeout, poll_seconds, poll_limit
        self.user_agent = user_agent
        self._nonce = ""
        self._directory_url = ""

    def _request(self, url: str, data: bytes | None = None, headers: Mapping[str, str] | None = None) -> tuple[int, Mapping[str, str], bytes]:
        request = urllib.request.Request(url, data=data, headers={"User-Agent": self.user_agent, **dict(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.headers, response.read(2_000_001)
        except urllib.error.HTTPError as error:
            error.read(65537)
            raise ACMEOperationError(f"ACME server rejected request with HTTP {error.code}") from error
        except (OSError, ssl.SSLError) as error:
            raise ACMEOperationError("ACME transport failed") from error

    def get_directory(self, directory_url: str) -> Mapping[str, Any]:
        self._directory_url = directory_url
        status, _headers, body = self._request(directory_url)
        if status != 200 or len(body) > 2_000_000:
            raise ACMEOperationError("ACME directory discovery failed")
        try:
            return json.loads(body)
        except ValueError as error:
            raise ACMEOperationError("ACME directory response is invalid") from error

    def _nonce_value(self, endpoint: str) -> str:
        if self._nonce:
            value, self._nonce = self._nonce, ""
            return value
        _status, headers, _body = self._request(endpoint, data=b"", headers={"Content-Length": "0"})
        value = str(headers.get("Replay-Nonce") or "")
        if not value:
            raise ACMEOperationError("ACME server omitted Replay-Nonce")
        return value

    def _signed(self, url: str, key: _RSAKey, payload: Mapping[str, Any] | str,
                *, kid: str | None = None) -> tuple[Mapping[str, str], Any]:
        protected: dict[str, Any] = {"alg": "RS256", "nonce": self._nonce_value(url), "url": url}
        protected["kid" if kid else "jwk"] = kid or key.jwk()
        payload64 = _b64url(payload.encode()) if isinstance(payload, str) else _json64(payload)
        protected64 = _json64(protected)
        body = json.dumps({"protected": protected64, "payload": payload64,
                           "signature": _b64url(key.sign(f"{protected64}.{payload64}".encode()))}).encode()
        _status, headers, raw = self._request(url, body, {"Content-Type": "application/jose+json"})
        self._nonce = str(headers.get("Replay-Nonce") or "")
        try:
            value = json.loads(raw) if raw else {}
        except ValueError as error:
            raise ACMEOperationError("ACME response is invalid") from error
        return headers, value

    def register_account(self, directory: ACMEDirectory, contacts: tuple[str, ...]) -> ACMEAccount:
        key = _generate_rsa()
        headers, _value = self._signed(directory.new_account, key,
            {"termsOfServiceAgreed": True, "contact": list(contacts)})
        account_url = str(headers.get("Location") or "")
        if not account_url:
            raise ACMEOperationError("ACME account response omitted its URL")
        return ACMEAccount(account_url, key.private_pem().decode("ascii"), contacts)

    def issue_certificate(self, request: ACMEOrderRequest, account: ACMEAccount,
                          challenges: HTTP01TokenStore) -> IssuedCertificate:
        key = _parse_rsa_private(account.private_key_pem)
        identifiers = [{"type": "ip" if _is_ip(value) else "dns", "value": value}
                       for value in request.identifiers]
        payload: dict[str, Any] = {"identifiers": identifiers}
        if request.profile:
            payload["profile"] = request.profile
        headers, order = self._signed(request.directory.new_order, key, payload, kid=account.account_url)
        order_url = str(headers.get("Location") or "")
        finalizer = str(order.get("finalize") or "")
        authorizations = order.get("authorizations") or []
        if not order_url or not finalizer or not isinstance(authorizations, list):
            raise ACMEOperationError("ACME order response is incomplete")
        challenge_rows = []
        for authorization_url in authorizations:
            _headers, authorization = self._signed(str(authorization_url), key, "", kid=account.account_url)
            challenge = next((row for row in authorization.get("challenges", [])
                              if row.get("type") == "http-01"), None)
            if not challenge:
                raise ACMEOperationError("ACME authorization has no HTTP-01 challenge")
            token = str(challenge.get("token") or "")
            challenges.provision(token, token + "." + key.thumbprint())
            challenge_rows.append((str(authorization_url), str(challenge.get("url") or ""), token))
        with HTTP01ChallengeServer(HTTP01ChallengeHandler(challenges)):
            for authorization_url, challenge_url, token in challenge_rows:
                self._signed(challenge_url, key, {}, kid=account.account_url)
                self._poll(authorization_url, key, account.account_url, "authorization")
                challenges.remove(token)
        certificate_key = _generate_rsa()
        csr = _csr(certificate_key, request.identifiers)
        self._signed(finalizer, key, {"csr": _b64url(csr)}, kid=account.account_url)
        order = self._poll(order_url, key, account.account_url, "order")
        certificate_url = str(order.get("certificate") or "")
        if not certificate_url:
            raise ACMEOperationError("ACME order completed without a certificate URL")
        # Certificate POST-as-GET is PEM rather than a JSON resource.
        protected = {"alg": "RS256", "kid": account.account_url,
                     "nonce": self._nonce_value(request.directory.new_nonce), "url": certificate_url}
        protected64, payload64 = _json64(protected), ""
        body = json.dumps({"protected": protected64, "payload": payload64,
            "signature": _b64url(key.sign(f"{protected64}.".encode()))}).encode()
        _status, response_headers, raw_certificate = self._request(
            certificate_url, body, {"Content-Type": "application/jose+json", "Accept": "application/pem-certificate-chain"})
        self._nonce = str(response_headers.get("Replay-Nonce") or "")
        if b"-----BEGIN CERTIFICATE-----" not in raw_certificate:
            raise ACMEOperationError("ACME certificate download is invalid")
        return IssuedCertificate(raw_certificate, certificate_key.private_pem())

    def _poll(self, url: str, key: _RSAKey, kid: str, label: str) -> Mapping[str, Any]:
        for _ in range(self.poll_limit):
            _headers, value = self._signed(url, key, "", kid=kid)
            state = value.get("status")
            if state == "valid":
                return value
            if state == "invalid":
                raise ACMEOperationError(f"ACME {label} became invalid")
            time.sleep(self.poll_seconds)
        raise ACMEOperationError(f"ACME {label} validation timed out")


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


__all__ = [
    "ACMEAccount",
    "ACMEAccountStore",
    "ACMEAdapter",
    "ACMECertificateManager",
    "ACMEConfig",
    "ACMEConfigurationError",
    "ACMEDirectory",
    "ACMEOperationError",
    "ACMEOrderRequest",
    "ACMERuntimeError",
    "HTTP01ChallengeHandler",
    "HTTP01ChallengeServer",
    "HTTP01Response",
    "HTTP01TokenStore",
    "IssuedCertificate",
    "StdlibACMEAdapter",
    "renewal_due",
    "validate_public_identifiers",
]
