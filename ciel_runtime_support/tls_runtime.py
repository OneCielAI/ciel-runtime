"""Validated TLS configuration, certificate inspection, and atomic activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import threading
import time
from typing import Any, Sequence


_CERTIFICATE_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+.+?\s+-----END CERTIFICATE-----",
    re.DOTALL,
)


class TLSRuntimeError(RuntimeError):
    """Base error whose message is safe to expose in runtime status."""


class TLSConfigurationError(TLSRuntimeError):
    """TLS mode or path configuration is invalid."""


class TLSCertificateError(TLSRuntimeError):
    """A certificate bundle cannot safely be activated."""


class TLSMode(str, Enum):
    DISABLED = "disabled"
    MANUAL = "manual"
    ACME = "acme"

    @classmethod
    def parse(cls, value: "TLSMode | str") -> "TLSMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as error:
            raise TLSConfigurationError("unsupported TLS mode") from error


@dataclass(frozen=True, slots=True)
class TLSConfig:
    mode: TLSMode | str = TLSMode.DISABLED
    cert_file: Path | str | None = None
    key_file: Path | str | None = None
    server_names: tuple[str, ...] = ()

    def validated(self) -> "TLSConfig":
        mode = TLSMode.parse(self.mode)
        names = normalize_server_names(self.server_names)
        cert = Path(self.cert_file).expanduser().resolve() if self.cert_file else None
        key = Path(self.key_file).expanduser().resolve() if self.key_file else None
        if mode is TLSMode.DISABLED:
            if cert is not None or key is not None:
                raise TLSConfigurationError("disabled TLS mode cannot specify certificate files")
        elif mode is TLSMode.MANUAL:
            if cert is None or key is None:
                raise TLSConfigurationError("manual TLS mode requires a certificate and private key")
            if cert == key:
                raise TLSConfigurationError("certificate and private key must be separate files")
        elif cert is not None or key is not None:
            raise TLSConfigurationError("ACME TLS mode uses the managed certificate store")
        return TLSConfig(mode=mode, cert_file=cert, key_file=key, server_names=names)

    def redacted_status(self) -> dict[str, Any]:
        config = self.validated()
        return {
            "mode": TLSMode.parse(config.mode).value,
            "configured": bool(config.cert_file and config.key_file),
            "server_names": list(config.server_names),
        }


@dataclass(frozen=True, slots=True)
class CertificateMetadata:
    sha256_fingerprint: str
    subject_alt_names: tuple[str, ...]
    not_before: datetime
    not_after: datetime

    @property
    def lifetime_seconds(self) -> float:
        return max(0.0, (self.not_after - self.not_before).total_seconds())

    def redacted_status(self) -> dict[str, Any]:
        return {
            "fingerprint": self.sha256_fingerprint,
            "subject_alt_names": list(self.subject_alt_names),
            "not_after": self.not_after.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ActivatedCertificate:
    generation: str
    cert_file: Path
    key_file: Path
    metadata: CertificateMetadata

    def redacted_status(self) -> dict[str, Any]:
        return {"generation": self.generation, **self.metadata.redacted_status()}


def normalize_server_names(values: Sequence[str]) -> tuple[str, ...]:
    names: list[str] = []
    for raw in values:
        value = str(raw or "").strip().rstrip(".")
        if not value or any(character.isspace() for character in value):
            raise TLSConfigurationError("invalid TLS server name")
        if value.startswith("*.") or "*" in value:
            raise TLSConfigurationError("wildcard TLS server names are not supported")
        try:
            normalized = str(ipaddress.ip_address(value))
        except ValueError:
            try:
                normalized = value.encode("idna").decode("ascii").lower()
            except UnicodeError as error:
                raise TLSConfigurationError("invalid TLS server name") from error
            labels = normalized.split(".")
            if len(normalized) > 253 or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not re.fullmatch(r"[a-z0-9-]+", label)
                for label in labels
            ):
                raise TLSConfigurationError("invalid TLS server name")
        if normalized not in names:
            names.append(normalized)
    return tuple(names)


def _require_regular_file(path: Path, label: str) -> None:
    try:
        details = path.lstat()
    except (FileNotFoundError, OSError) as error:
        raise TLSConfigurationError(f"{label} file is missing or unreadable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise TLSConfigurationError(f"{label} must be a regular, non-symlink file")


def _require_restricted_private_key(path: Path) -> None:
    _require_regular_file(path, "private key")
    if os.name != "nt":
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & (stat.S_IRWXG | stat.S_IRWXO):
            raise TLSConfigurationError("private key permissions allow group or other access")


def _first_certificate_pem(data: bytes) -> bytes:
    match = _CERTIFICATE_RE.search(data)
    if match is None:
        raise TLSCertificateError("certificate file does not contain a PEM certificate")
    return match.group(0) + b"\n"


def _der_item(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    if offset + 2 > len(data):
        raise TLSCertificateError("certificate DER encoding is invalid")
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise TLSCertificateError("certificate DER encoding is invalid")
    first_length = data[offset + 1]
    position = offset + 2
    if first_length < 0x80:
        length = first_length
    else:
        width = first_length & 0x7F
        if width == 0 or width > 4 or position + width > len(data) or data[position] == 0:
            raise TLSCertificateError("certificate DER encoding is invalid")
        length = int.from_bytes(data[position : position + width], "big")
        if length < 0x80:
            raise TLSCertificateError("certificate DER encoding is invalid")
        position += width
    end = position + length
    if end > len(data):
        raise TLSCertificateError("certificate DER encoding is invalid")
    return tag, data[position:end], end


def _der_children(data: bytes) -> list[tuple[int, bytes]]:
    children: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(data):
        tag, value, offset = _der_item(data, offset)
        children.append((tag, value))
    return children


def _der_oid(value: bytes) -> str:
    if not value or value[-1] & 0x80:
        raise TLSCertificateError("certificate object identifier is invalid")
    first = value[0]
    parts = [min(first // 40, 2), first - 40 * min(first // 40, 2)]
    number = 0
    for byte in value[1:]:
        number = (number << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(number)
            number = 0
    return ".".join(str(part) for part in parts)


def _der_time(tag: int, value: bytes, label: str) -> datetime:
    try:
        text = value.decode("ascii")
        if tag == 0x17 and re.fullmatch(r"\d{12}Z", text):
            parsed = datetime.strptime(text, "%y%m%d%H%M%SZ")
            if parsed.year >= 2050:
                parsed = parsed.replace(year=parsed.year - 100)
        elif tag == 0x18 and re.fullmatch(r"\d{14}Z", text):
            parsed = datetime.strptime(text, "%Y%m%d%H%M%SZ")
        else:
            raise ValueError(label)
    except (UnicodeError, ValueError) as error:
        raise TLSCertificateError(f"certificate {label} is invalid") from error
    return parsed.replace(tzinfo=timezone.utc)


def _subject_alt_names(extensions: bytes) -> tuple[str, ...]:
    names: list[str] = []
    for tag, extension in _der_children(extensions):
        if tag != 0x30:
            raise TLSCertificateError("certificate extensions are invalid")
        fields = _der_children(extension)
        if len(fields) < 2 or fields[0][0] != 0x06 or fields[-1][0] != 0x04:
            raise TLSCertificateError("certificate extensions are invalid")
        if _der_oid(fields[0][1]) != "2.5.29.17":
            continue
        tag, general_names, end = _der_item(fields[-1][1])
        if tag != 0x30 or end != len(fields[-1][1]):
            raise TLSCertificateError("certificate subject alternative names are invalid")
        for name_tag, name_value in _der_children(general_names):
            if name_tag == 0x82:
                try:
                    item = name_value.decode("ascii").rstrip(".").lower()
                except UnicodeError as error:
                    raise TLSCertificateError("certificate DNS name is invalid") from error
            elif name_tag == 0x87 and len(name_value) in {4, 16}:
                item = str(ipaddress.ip_address(name_value))
            else:
                continue
            if item and item not in names:
                names.append(item)
    return tuple(names)


def _certificate_fields(der: bytes) -> tuple[datetime, datetime, tuple[str, ...]]:
    tag, certificate, end = _der_item(der)
    if tag != 0x30 or end != len(der):
        raise TLSCertificateError("certificate DER encoding is invalid")
    parts = _der_children(certificate)
    if len(parts) != 3 or parts[0][0] != 0x30:
        raise TLSCertificateError("certificate DER encoding is invalid")
    tbs = _der_children(parts[0][1])
    index = 1 if tbs and tbs[0][0] == 0xA0 else 0
    if len(tbs) < index + 6 or tbs[index + 3][0] != 0x30:
        raise TLSCertificateError("certificate DER encoding is invalid")
    validity = _der_children(tbs[index + 3][1])
    if len(validity) != 2:
        raise TLSCertificateError("certificate validity is invalid")
    not_before = _der_time(validity[0][0], validity[0][1], "notBefore")
    not_after = _der_time(validity[1][0], validity[1][1], "notAfter")
    extensions = next((value for tag, value in tbs[index + 6 :] if tag == 0xA3), b"")
    if not extensions:
        return not_before, not_after, ()
    sequence_tag, sequence, sequence_end = _der_item(extensions)
    if sequence_tag != 0x30 or sequence_end != len(extensions):
        raise TLSCertificateError("certificate extensions are invalid")
    return not_before, not_after, _subject_alt_names(sequence)


def inspect_certificate_pem(certificate_pem: bytes | str) -> CertificateMetadata:
    raw = certificate_pem.encode("ascii") if isinstance(certificate_pem, str) else certificate_pem
    first = _first_certificate_pem(raw)
    try:
        der = ssl.PEM_cert_to_DER_cert(first.decode("ascii"))
    except (UnicodeError, ValueError) as error:
        raise TLSCertificateError("certificate PEM encoding is invalid") from error
    try:
        not_before, not_after, alt_names = _certificate_fields(der)
    except TLSCertificateError:
        raise
    except (IndexError, ValueError) as error:
        raise TLSCertificateError("certificate metadata is invalid") from error
    return CertificateMetadata(
        sha256_fingerprint=hashlib.sha256(der).hexdigest(),
        subject_alt_names=alt_names,
        not_before=not_before,
        not_after=not_after,
    )


def inspect_certificate(path: Path | str) -> CertificateMetadata:
    certificate = Path(path)
    _require_regular_file(certificate, "certificate")
    try:
        raw = certificate.read_bytes()
    except OSError as error:
        raise TLSConfigurationError("certificate file is missing or unreadable") from error
    return inspect_certificate_pem(raw)


def _matches_name(pattern: str, expected: str) -> bool:
    try:
        return ipaddress.ip_address(pattern) == ipaddress.ip_address(expected)
    except ValueError:
        pass
    pattern = pattern.lower().rstrip(".")
    expected = expected.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return expected.endswith(f".{suffix}") and expected.count(".") == suffix.count(".") + 1
    return pattern == expected


def validate_certificate_metadata(
    metadata: CertificateMetadata,
    server_names: Sequence[str] = (),
    *,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if metadata.not_before > current:
        raise TLSCertificateError("certificate is not yet valid")
    if metadata.not_after <= current:
        raise TLSCertificateError("certificate has expired")
    expected_names = normalize_server_names(server_names)
    for expected in expected_names:
        if not any(_matches_name(pattern, expected) for pattern in metadata.subject_alt_names):
            raise TLSCertificateError("certificate subject alternative names do not match the configured server names")


def load_server_ssl_context(
    cert_file: Path | str,
    key_file: Path | str,
    server_names: Sequence[str] = (),
    *,
    now: datetime | None = None,
) -> tuple[ssl.SSLContext, CertificateMetadata]:
    certificate = Path(cert_file)
    private_key = Path(key_file)
    _require_regular_file(certificate, "certificate")
    _require_restricted_private_key(private_key)
    if certificate.resolve() == private_key.resolve():
        raise TLSConfigurationError("certificate and private key must be separate files")
    metadata = inspect_certificate(certificate)
    validate_certificate_metadata(metadata, server_names, now=now)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(str(certificate), str(private_key))
    except (OSError, ssl.SSLError) as error:
        raise TLSCertificateError("certificate and private key cannot be loaded as a matching TLS bundle") from error
    return context, metadata


def _write_restricted(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TLSBundleStore:
    """Generation-based certificate store with one atomic active pointer."""

    def __init__(self, root: Path | str) -> None:
        configured = Path(root).expanduser()
        if configured.exists() and configured.is_symlink():
            raise TLSConfigurationError("TLS certificate store must not be a symbolic link")
        self.root = configured.resolve()
        self.bundles = self.root / "bundles"
        self.active_manifest = self.root / "active.json"
        self._lock = threading.RLock()
        self.bundles.mkdir(parents=True, exist_ok=True)
        if self.bundles.is_symlink():
            raise TLSConfigurationError("TLS certificate bundle directory must not be a symbolic link")
        if os.name != "nt":
            os.chmod(self.root, 0o700)
            os.chmod(self.bundles, 0o700)

    def activate(
        self,
        certificate_pem: bytes | str,
        private_key_pem: bytes | str,
        server_names: Sequence[str] = (),
        *,
        now: datetime | None = None,
    ) -> ActivatedCertificate:
        certificate = certificate_pem.encode("ascii") if isinstance(certificate_pem, str) else bytes(certificate_pem)
        private_key = private_key_pem.encode("ascii") if isinstance(private_key_pem, str) else bytes(private_key_pem)
        generation = f"{time.time_ns():x}-{os.urandom(6).hex()}"
        staging = self.bundles / f".{generation}.tmp"
        final = self.bundles / generation
        with self._lock:
            try:
                staging.mkdir(mode=0o700)
                cert_path = staging / "certificate.pem"
                key_path = staging / "private-key.pem"
                _write_restricted(cert_path, certificate)
                _write_restricted(key_path, private_key)
                _, metadata = load_server_ssl_context(cert_path, key_path, server_names, now=now)
                _fsync_directory(staging)
                os.replace(staging, final)
                _fsync_directory(self.bundles)
                manifest = json.dumps({"version": 1, "generation": generation}, separators=(",", ":")).encode("utf-8")
                temporary = self.root / f".{generation}.active.tmp"
                _write_restricted(temporary, manifest)
                os.replace(temporary, self.active_manifest)
                _fsync_directory(self.root)
                return ActivatedCertificate(
                    generation=generation,
                    cert_file=final / "certificate.pem",
                    key_file=final / "private-key.pem",
                    metadata=metadata,
                )
            except TLSRuntimeError:
                raise
            except Exception as error:
                raise TLSCertificateError("certificate bundle activation failed") from error
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                for temporary in self.root.glob(f".{generation}.*.tmp"):
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

    def current(self, *, now: datetime | None = None, validate_time: bool = True) -> ActivatedCertificate | None:
        with self._lock:
            try:
                manifest_details = self.active_manifest.lstat()
                if stat.S_ISLNK(manifest_details.st_mode) or not stat.S_ISREG(manifest_details.st_mode):
                    raise TLSCertificateError("active certificate metadata is unsafe")
                manifest = json.loads(self.active_manifest.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except TLSCertificateError:
                raise
            except (OSError, ValueError, TypeError) as error:
                raise TLSCertificateError("active certificate metadata is invalid") from error
            generation = str(manifest.get("generation") or "")
            if not re.fullmatch(r"[a-f0-9]+-[a-f0-9]{12}", generation):
                raise TLSCertificateError("active certificate metadata is invalid")
            directory = self.bundles / generation
            try:
                directory_details = directory.lstat()
            except OSError as error:
                raise TLSCertificateError("active certificate bundle is missing") from error
            if stat.S_ISLNK(directory_details.st_mode) or not stat.S_ISDIR(directory_details.st_mode):
                raise TLSCertificateError("active certificate bundle is unsafe")
            cert_path = directory / "certificate.pem"
            key_path = directory / "private-key.pem"
            metadata = inspect_certificate(cert_path)
            if validate_time:
                validate_certificate_metadata(metadata, now=now)
            _require_restricted_private_key(key_path)
            return ActivatedCertificate(generation, cert_path, key_path, metadata)

    def ssl_context(
        self,
        server_names: Sequence[str] = (),
        *,
        now: datetime | None = None,
    ) -> tuple[ssl.SSLContext, ActivatedCertificate]:
        active = self.current(now=now)
        if active is None:
            raise TLSCertificateError("no active certificate is available")
        context, metadata = load_server_ssl_context(active.cert_file, active.key_file, server_names, now=now)
        return context, ActivatedCertificate(active.generation, active.cert_file, active.key_file, metadata)

    def redacted_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        try:
            active = self.current(now=now, validate_time=False)
        except TLSRuntimeError as error:
            return {"active": False, "error": str(error)}
        if active is None:
            return {"active": False}
        return {"active": True, **active.redacted_status()}


__all__ = [
    "ActivatedCertificate",
    "CertificateMetadata",
    "TLSBundleStore",
    "TLSCertificateError",
    "TLSConfig",
    "TLSConfigurationError",
    "TLSMode",
    "TLSRuntimeError",
    "inspect_certificate",
    "inspect_certificate_pem",
    "load_server_ssl_context",
    "normalize_server_names",
    "validate_certificate_metadata",
]
