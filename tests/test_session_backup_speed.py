"""Parallel chunk storing: same snapshot as before, chunks in order, failures stop the backup."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots
from ciel_runtime_support.session_backup_ops import create_snapshot, verify_snapshot
from ciel_runtime_support.session_backup_store import (
    CHUNK_SIZE,
    ChunkWriter,
    LocalTarget,
    TargetWriteError,
    pack_chunk,
    read_chunks,
)


class MemoryTarget:
    def __init__(self, delay: float = 0.0, fail_after: int | None = None) -> None:
        self.name = "memory"
        self.blobs: dict[str, bytes] = {}
        self.delay = delay
        self.fail_after = fail_after
        self.active = 0
        self.most_active = 0
        self.puts = 0
        self._lock = threading.Lock()

    def has(self, key: str) -> bool:
        return key in self.blobs

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            self.active += 1
            self.most_active = max(self.most_active, self.active)
            self.puts += 1
            fail = self.fail_after is not None and self.puts > self.fail_after
        try:
            time.sleep(self.delay)
            if fail:
                raise OSError(28, "No space left on device")
            self.blobs[key] = data
        finally:
            with self._lock:
                self.active -= 1

    def get(self, key: str) -> bytes:
        return self.blobs[key]

    def list(self, prefix: str) -> list[str]:
        return sorted(key for key in self.blobs if key.startswith(prefix))

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)


def chunks(count: int) -> list[bytes]:
    return [os.urandom(1024) + bytes([index]) * 70000 for index in range(count)]


class PackTests(unittest.TestCase):
    def test_incompressible_chunks_are_stored_and_read_back(self):
        random_chunk = os.urandom(CHUNK_SIZE // 8)
        text_chunk = b"lorem ipsum " * 100000
        stored = pack_chunk(random_chunk)
        self.assertLess(len(stored), len(random_chunk) * 1.01)
        self.assertEqual(random_chunk, zlib.decompress(stored))
        self.assertLess(len(pack_chunk(text_chunk)), len(text_chunk) // 50)


class WriterTests(unittest.TestCase):
    def test_parallel_writes_keep_the_order_and_run_together(self):
        target = MemoryTarget(delay=0.05)
        writer = ChunkWriter([target], jobs=8)
        data = chunks(24)
        digests = [writer.write(chunk) for chunk in data]
        writer.drain()
        writer.close()
        self.assertEqual(data, list(read_chunks(target, digests)))
        self.assertGreater(target.most_active, 1)
        self.assertLessEqual(target.most_active, 8)
        self.assertEqual(24, writer.stats.uploaded_chunks)

    def test_the_same_chunk_in_flight_is_stored_once(self):
        target = MemoryTarget(delay=0.05)
        writer = ChunkWriter([target], jobs=8)
        chunk = chunks(1)[0]
        digests = [writer.write(chunk) for _ in range(10)]
        writer.drain()
        writer.close()
        self.assertEqual(1, len(set(digests)))
        self.assertEqual(1, target.puts)
        self.assertEqual(10, writer.stats.chunks)

    def test_a_failing_target_stops_the_backup(self):
        writer = ChunkWriter([MemoryTarget(delay=0.01, fail_after=3)], jobs=4)
        with self.assertRaises(TargetWriteError) as stopped:
            for chunk in chunks(40):
                writer.write(chunk)
            writer.drain()
        writer.close()
        self.assertTrue(stopped.exception.no_space)


class SnapshotTests(unittest.TestCase):
    def test_parallel_snapshot_equals_the_sequential_one(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cwd = base / "work"
            (cwd / "small").mkdir(parents=True)
            for index in range(50):
                (cwd / "small" / f"f{index}.txt").write_text(f"file {index}\n" * 100, encoding="utf-8")
            (cwd / "big.bin").write_bytes(os.urandom(CHUNK_SIZE * 2 + 123))
            (cwd / "log.jsonl").write_text('{"a": 1}\n' * 200000, encoding="utf-8")
            roots = default_roots(cwd, environ={}, home=base / "home", asset_home=base / "home", config_dir=base / "ciel")
            manifests = {}
            for jobs in (1, 8):
                target = LocalTarget(base / f"target-{jobs}")
                result = create_snapshot(roots, [target], CollectOptions(), key=None, versions={}, host="h", user="u", jobs=jobs)
                self.assertEqual([], verify_snapshot(target, result.manifest))
                manifests[jobs] = {(entry["root"], entry["path"]): entry["chunks"] for entry in result.manifest["entries"]}
                self.assertEqual(jobs, result.manifest["stats"]["jobs"])
            self.assertEqual(manifests[1], manifests[8])


if __name__ == "__main__":
    unittest.main()
