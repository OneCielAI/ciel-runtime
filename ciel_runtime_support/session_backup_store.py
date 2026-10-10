"""Content-addressed snapshot storage for session backups.

A snapshot is one manifest (JSON) plus the chunks it names.  Files are cut
into fixed 8 MiB chunks and each chunk is stored once under its SHA-256, so a
transcript that only grew since the last snapshot uploads just its tail chunk.
Targets (local folder, SSH, S3, rclone) only need to store named byte blobs:
``blobs/<aa>/<sha>`` and ``snapshots/<workspace>/<id>.json``.  A snapshot is
complete once its manifest exists; the manifest is always written last.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

FORMAT_VERSION = 1
CHUNK_SIZE = 8 * 1024 * 1024


class BackupTarget(Protocol):
    name: str

    def has(self, key: str) -> bool: ...

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def list(self, prefix: str) -> list[str]: ...

    def delete(self, key: str) -> None: ...


def blob_key(digest: str) -> str:
    return f"blobs/{digest[:2]}/{digest}"


def manifest_key(workspace: str, snapshot_id: str) -> str:
    return f"snapshots/{workspace}/{snapshot_id}.json"


def iter_chunks(data_source: Iterable[bytes]) -> Iterator[bytes]:
    """Re-cut a byte stream into CHUNK_SIZE pieces (the last may be shorter)."""

    pending = bytearray()
    for piece in data_source:
        pending.extend(piece)
        while len(pending) >= CHUNK_SIZE:
            yield bytes(pending[:CHUNK_SIZE])
            del pending[:CHUNK_SIZE]
    if pending:
        yield bytes(pending)


@dataclass
class UploadStats:
    chunks: int = 0
    uploaded_chunks: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "chunks": self.chunks,
            "uploaded_chunks": self.uploaded_chunks,
            "bytes_total": self.bytes_total,
            "bytes_uploaded": self.bytes_uploaded,
        }


class ChunkWriter:
    """Stores chunks on every target that lacks them; returns their digests."""

    def __init__(self, targets: list[BackupTarget]) -> None:
        self.targets = targets
        self.stats = UploadStats()
        self._known: set[tuple[str, str]] = set()

    def write(self, chunk: bytes) -> str:
        digest = hashlib.sha256(chunk).hexdigest()
        self.stats.chunks += 1
        self.stats.bytes_total += len(chunk)
        packed: bytes | None = None
        for target in self.targets:
            marker = (target.name, digest)
            if marker in self._known:
                continue
            key = blob_key(digest)
            if not target.has(key):
                packed = packed if packed is not None else zlib.compress(chunk, 6)
                target.put(key, packed)
                self.stats.uploaded_chunks += 1
                self.stats.bytes_uploaded += len(packed)
            self._known.add(marker)
        return digest

    def write_stream(self, data_source: Iterable[bytes]) -> list[str]:
        return [self.write(chunk) for chunk in iter_chunks(data_source)]


def read_chunks(target: BackupTarget, digests: list[str]) -> Iterator[bytes]:
    for digest in digests:
        chunk = zlib.decompress(target.get(blob_key(digest)))
        if hashlib.sha256(chunk).hexdigest() != digest:
            raise ValueError(f"chunk {digest} failed its checksum")
        yield chunk


def encode_manifest(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")


def decode_manifest(data: bytes) -> dict[str, Any]:
    manifest = json.loads(data.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION:
        raise ValueError("not a Ciel session backup manifest")
    return manifest


def list_snapshots(target: BackupTarget, workspace: str = "") -> list[tuple[str, str]]:
    """(workspace, snapshot id) pairs, oldest first."""

    prefix = f"snapshots/{workspace}/" if workspace else "snapshots/"
    rows = []
    for key in target.list(prefix):
        parts = key.split("/")
        if len(parts) == 3 and parts[2].endswith(".json"):
            rows.append((parts[1], parts[2][: -len(".json")]))
    return sorted(rows, key=lambda row: (row[1], row[0]))


def find_snapshot(target: BackupTarget, snapshot_id: str) -> tuple[str, dict[str, Any]]:
    matches = [row for row in list_snapshots(target) if row[1] == snapshot_id or row[1].startswith(snapshot_id)]
    if not matches:
        raise LookupError(f"no snapshot {snapshot_id!r} on {target.name}")
    if len({row[1] for row in matches}) > 1:
        raise LookupError(f"snapshot id {snapshot_id!r} is ambiguous on {target.name}")
    workspace, full_id = matches[-1]
    return workspace, decode_manifest(target.get(manifest_key(workspace, full_id)))


class LocalTarget:
    """A folder (local disk or a mounted/UNC share)."""

    def __init__(self, root: Path, name: str = "") -> None:
        self.root = Path(root)
        self.name = name or f"local:{self.root}"

    def _path(self, key: str) -> Path:
        if ".." in key.split("/"):
            raise ValueError(f"unsafe key {key!r}")
        return self.root.joinpath(*key.split("/"))

    def has(self, key: str) -> bool:
        return self._path(key).is_file()

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".part-", dir=str(path.parent))
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix.rstrip("/") else self.root
        if not base.is_dir():
            return []
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in base.rglob("*")
            if path.is_file() and not path.name.startswith(".part-")
        )

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


__all__ = [
    "CHUNK_SIZE",
    "FORMAT_VERSION",
    "BackupTarget",
    "ChunkWriter",
    "LocalTarget",
    "UploadStats",
    "blob_key",
    "decode_manifest",
    "encode_manifest",
    "find_snapshot",
    "iter_chunks",
    "list_snapshots",
    "manifest_key",
    "read_chunks",
]
