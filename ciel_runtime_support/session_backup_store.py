"""Content-addressed snapshot storage for session backups.

A snapshot is one manifest (JSON) plus the chunks it names.  Files are cut
into fixed 8 MiB chunks and each chunk is stored once under its SHA-256, so a
transcript that only grew since the last snapshot uploads just its tail chunk.
Targets (local folder, SSH, S3, rclone, Synology) only need to store named byte
blobs: ``blobs/<aa>/<sha>`` and ``snapshots/<workspace>/<id>.json``.  A snapshot
is complete once its manifest exists; the manifest is always written last.

Chunks are hashed in order on the caller's thread; compressing and storing them
runs on ``jobs`` worker threads (zlib, hashing, file and socket I/O release the
GIL), with at most ``2 * jobs`` chunks in flight.  A chunk that does not compress
(already-compressed data, binaries) is stored as a zlib "stored" stream: same
format, read back the same way, without the cost of a compression that saves
nothing (measured 2026-10-10: zlib level 6 on random data 46 MB/s, level 0 1234 MB/s).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import zlib
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

FORMAT_VERSION = 1
CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_JOBS = 8
MAX_JOBS = 16
_SAMPLE = 64 * 1024


class TargetWriteError(Exception):
    """A target could not store a chunk (full disk, lost connection): the whole backup stops.

    Not an OSError on purpose, so per-file read errors (skipped and listed) never hide it."""

    def __init__(self, target: str, error: BaseException) -> None:
        reason = "no space left on the target" if getattr(error, "errno", None) == 28 or getattr(error, "winerror", None) in (39, 112) else ""
        super().__init__(f"backup target {target} could not store data: {reason or type(error).__name__}: {error}")
        self.target = target
        self.no_space = bool(reason)


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
        if not pending and len(piece) == CHUNK_SIZE:
            yield bytes(piece)  # already a whole chunk (files are read in CHUNK_SIZE blocks)
            continue
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


def pack_chunk(chunk: bytes) -> bytes:
    """zlib data for a chunk: level 6, or stored (level 0) when a sample shows it does not compress."""

    if len(chunk) > _SAMPLE:
        sample = chunk[:_SAMPLE]
        if len(zlib.compress(sample, 1)) > len(sample) * 0.95:
            return zlib.compress(chunk, 0)
    return zlib.compress(chunk, 6)


class ChunkWriter:
    """Stores chunks on every target that lacks them; returns their digests.

    ``jobs`` > 1 compresses and stores on worker threads; call ``drain()``
    before writing anything that names the chunks (the manifest).  A target
    failure stops the writer: the next ``write``/``drain`` raises it.
    """

    def __init__(self, targets: list[BackupTarget], jobs: int = 1) -> None:
        self.targets = targets
        self.stats = UploadStats()
        self.jobs = max(1, min(MAX_JOBS, int(jobs or 1)))
        self._submitted: set[str] = set()
        self._lock = threading.Lock()
        self._pending: set[Future] = set()
        self._error: BaseException | None = None
        self._pool = ThreadPoolExecutor(self.jobs, thread_name_prefix="ciel-backup") if self.jobs > 1 else None

    def _store(self, digest: str, chunk: bytes) -> None:
        packed: bytes | None = None
        key = blob_key(digest)
        for target in self.targets:
            try:
                if not target.has(key):
                    packed = packed if packed is not None else pack_chunk(chunk)
                    target.put(key, packed)
                    with self._lock:
                        self.stats.uploaded_chunks += 1
                        self.stats.bytes_uploaded += len(packed)
            except OSError as error:
                raise TargetWriteError(target.name, error) from error

    def _collect(self, done: set[Future]) -> None:
        for future in done:
            self._pending.discard(future)
            error = future.exception()
            if error is not None and self._error is None:
                self._error = error
        if self._error is not None:
            raise self._error

    def write(self, chunk: bytes) -> str:
        digest = hashlib.sha256(chunk).hexdigest()
        self.stats.chunks += 1
        self.stats.bytes_total += len(chunk)
        if self._error is not None:
            raise self._error
        if digest in self._submitted:
            return digest
        self._submitted.add(digest)
        if self._pool is None:
            self._store(digest, chunk)
            return digest
        while len(self._pending) >= 2 * self.jobs:
            self._collect(wait(self._pending, return_when=FIRST_COMPLETED).done)
        self._pending.add(self._pool.submit(self._store, digest, chunk))
        return digest

    def write_stream(self, data_source: Iterable[bytes]) -> list[str]:
        return [self.write(chunk) for chunk in iter_chunks(data_source)]

    @property
    def in_flight(self) -> int:
        return len(self._pending)

    def bytes_sent(self) -> int:
        """Bytes the targets have sent so far, chunks still uploading included (targets that count them)."""

        return sum(int(getattr(target, "bytes_sent", 0) or 0) for target in self.targets)

    def drain(self, report: Callable[[], None] | None = None, interval: float = 1.0) -> None:
        """Wait until every chunk is stored; raise the first failure. ``report`` is called every ``interval`` s meanwhile."""

        while self._pending:
            done, _ = wait(self._pending, timeout=interval if report is not None else None)
            self._collect(done)
            if report is not None and self._pending:
                report()
        if self._error is not None:
            raise self._error

    def close(self) -> None:
        if self._pool is not None:
            for future in self._pending:
                future.cancel()
            self._pool.shutdown(wait=True)


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
    "DEFAULT_JOBS",
    "FORMAT_VERSION",
    "MAX_JOBS",
    "BackupTarget",
    "ChunkWriter",
    "LocalTarget",
    "TargetWriteError",
    "UploadStats",
    "blob_key",
    "decode_manifest",
    "encode_manifest",
    "find_snapshot",
    "iter_chunks",
    "list_snapshots",
    "manifest_key",
    "pack_chunk",
    "read_chunks",
]
