"""Stdlib-only RSA keys and self-signed X.509 certificates for TLS tests.

The keys are generated at test time, so no private key material is stored in
the repository. This is test scaffolding only, not production cryptography.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import functools
import hashlib
import ipaddress
import secrets
import textwrap
from dataclasses import dataclass
from typing import Sequence

_SMALL_PRIMES = tuple(
    number for number in range(3, 2000, 2) if all(number % divisor for divisor in range(3, int(number**0.5) + 1, 2))
)
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


@dataclass(frozen=True)
class RSAKey:
    n: int
    e: int
    d: int
    p: int
    q: int

    @property
    def size_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8

    def private_pem(self) -> bytes:
        der = _sequence(
            _integer(0),
            _integer(self.n),
            _integer(self.e),
            _integer(self.d),
            _integer(self.p),
            _integer(self.q),
            _integer(self.d % (self.p - 1)),
            _integer(self.d % (self.q - 1)),
            _integer(pow(self.q, -1, self.p)),
        )
        return _pem("RSA PRIVATE KEY", der)

    def sign_sha256(self, data: bytes) -> bytes:
        digest = _SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
        padding = b"\xff" * (self.size_bytes - len(digest) - 3)
        encoded = int.from_bytes(b"\x00\x01" + padding + b"\x00" + digest, "big")
        return pow(encoded, self.d, self.n).to_bytes(self.size_bytes, "big")


def _probably_prime(candidate: int) -> bool:
    if any(candidate % prime == 0 for prime in _SMALL_PRIMES):
        return candidate in _SMALL_PRIMES
    exponent, rounds = candidate - 1, 0
    while exponent % 2 == 0:
        exponent //= 2
        rounds += 1
    for _ in range(24):
        witness = secrets.randbelow(candidate - 3) + 2
        value = pow(witness, exponent, candidate)
        if value in (1, candidate - 1):
            continue
        for _ in range(rounds - 1):
            value = pow(value, 2, candidate)
            if value == candidate - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, exponent: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if (candidate - 1) % exponent and _probably_prime(candidate):
            return candidate


def generate_rsa_key(bits: int = 2048) -> RSAKey:
    exponent = 65537
    while True:
        p = _prime(bits // 2, exponent)
        q = _prime(bits // 2, exponent)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        d = pow(exponent, -1, (p - 1) * (q - 1))
        return RSAKey(n=n, e=exponent, d=d, p=max(p, q), q=min(p, q))


@functools.lru_cache(maxsize=None)
def cached_rsa_key(label: str, bits: int = 2048) -> RSAKey:
    """Generate once per test process and label; the label only keys the cache."""

    return generate_rsa_key(bits)


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    encoded = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _length(len(body)) + body


def _integer(value: int) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")
    return _tlv(0x02, body)


def _sequence(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _oid(dotted: str) -> bytes:
    parts = [int(part) for part in dotted.split(".")]
    body = bytearray([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.append(0x80 | (part & 0x7F))
            part >>= 7
        body.extend(reversed(chunk))
    return _tlv(0x06, bytes(body))


def _time(value: datetime) -> bytes:
    value = value.astimezone(timezone.utc)
    if value.year < 2050:
        return _tlv(0x17, value.strftime("%y%m%d%H%M%SZ").encode("ascii"))
    return _tlv(0x18, value.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


def _name(common_name: str) -> bytes:
    attribute = _sequence(_oid("2.5.4.3"), _tlv(0x0C, common_name.encode("utf-8")))
    return _sequence(_tlv(0x31, attribute))


def _pem(label: str, der: bytes) -> bytes:
    body = "\n".join(textwrap.wrap(base64.b64encode(der).decode("ascii"), 64))
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----\n".encode("ascii")


def make_certificate(
    key: RSAKey,
    names: Sequence[str],
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
    common_name: str = "ciel-runtime-test",
) -> bytes:
    """Return a self-signed PEM certificate carrying *names* as SAN entries."""

    start = not_before or datetime.now(timezone.utc) - timedelta(hours=1)
    end = not_after or start + timedelta(days=30)
    algorithm = _sequence(_oid("1.2.840.113549.1.1.11"), b"\x05\x00")
    public_key = _sequence(_integer(key.n), _integer(key.e))
    subject_public_key_info = _sequence(
        _sequence(_oid("1.2.840.113549.1.1.1"), b"\x05\x00"),
        _tlv(0x03, b"\x00" + public_key),
    )
    general_names = []
    for name in names:
        try:
            general_names.append(_tlv(0x87, ipaddress.ip_address(name).packed))
        except ValueError:
            general_names.append(_tlv(0x82, name.encode("ascii")))
    extensions = b""
    if general_names:
        san = _sequence(_oid("2.5.29.17"), _tlv(0x04, _sequence(*general_names)))
        extensions = _tlv(0xA3, _sequence(san))
    tbs = _sequence(
        _tlv(0xA0, _integer(2)),
        _integer(secrets.randbits(63) | 1),
        algorithm,
        _name(common_name),
        _sequence(_time(start), _time(end)),
        _name(common_name),
        subject_public_key_info,
        extensions,
    )
    certificate = _sequence(tbs, algorithm, _tlv(0x03, b"\x00" + key.sign_sha256(tbs)))
    return _pem("CERTIFICATE", certificate)
