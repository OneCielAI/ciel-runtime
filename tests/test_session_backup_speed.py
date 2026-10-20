"""Parallel chunk storing: same snapshot as before, chunks in order, failures stop the backup."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from ciel_runtime_support import session_backup_collect
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


class UploadProgressTests(unittest.TestCase):
    def test_progress_keeps_coming_while_the_last_chunks_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cwd = base / "work"
            cwd.mkdir()
            (cwd / "big.bin").write_bytes(os.urandom(CHUNK_SIZE * 3))
            roots = default_roots(cwd, environ={}, home=base / "home", asset_home=base / "home", config_dir=base / "ciel")
            states: list[dict] = []
            target = MemoryTarget(delay=1.6)
            create_snapshot(roots, [target], CollectOptions(progress=states.append), key=None, versions={},
                            host="h", user="u", jobs=4)
        uploading = [state for state in states if state.get("phase") == "uploading"]
        self.assertTrue(uploading, states)
        self.assertGreater(uploading[0]["in_flight"], 0)
        self.assertEqual(3, len(target.list("blobs/")))  # the manifest is the fourth put


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


def make_tree(cwd: Path, count: int = 40) -> None:
    (cwd / "small").mkdir(parents=True)
    for index in range(count):
        (cwd / "small" / f"f{index:03d}.txt").write_text(f"file {index}\n" * 50, encoding="utf-8")
    (cwd / "big.bin").write_bytes(os.urandom(CHUNK_SIZE + 77))
    (cwd / "log.jsonl").write_bytes(b'{"a": 1}\n' * 1000 + b'{"partial')
    age(cwd)


def age(cwd: Path, seconds: float = 120) -> None:
    """Files old enough that a snapshot taken now may skip them next time."""

    past = time.time() - seconds
    for path in cwd.rglob("*"):
        if path.is_file():
            os.utime(path, (past, past))


class UnchangedFileTests(unittest.TestCase):
    def snapshot(self, roots, targets, **kwargs):
        return create_snapshot(roots, targets, CollectOptions(), key=None, versions={}, host="h", user="u",
                               jobs=kwargs.pop("jobs", 8), **kwargs)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.cwd = self.base / "work"
        make_tree(self.cwd)
        self.roots = default_roots(self.cwd, environ={}, home=self.base / "home", asset_home=self.base / "home",
                                   config_dir=self.base / "ciel")

    def tearDown(self):
        self.directory.cleanup()

    def files(self, manifest):
        return {(entry["root"], entry["path"]): (entry["size"], entry["chunks"]) for entry in manifest["entries"]}

    def test_unchanged_files_are_not_read_again_and_the_snapshot_is_the_same(self):
        target = LocalTarget(self.base / "target")
        first = self.snapshot(self.roots, [target])
        reads: list[str] = []
        real_stream = session_backup_collect._file_stream

        def counting(path):
            reads.append(Path(path).name)
            return real_stream(path)

        with mock.patch.object(session_backup_collect, "_file_stream", counting):
            second = self.snapshot(self.roots, [target])
        self.assertEqual([], reads)
        self.assertEqual(self.files(first.manifest), self.files(second.manifest))
        self.assertEqual(42, second.stats["reused_files"])
        self.assertEqual(0, second.stats["uploaded_chunks"])
        self.assertEqual(first.stats["bytes_total"], second.stats["bytes_total"])
        self.assertEqual([], verify_snapshot(target, second.manifest))
        full = self.snapshot(self.roots, [target], full=True)
        self.assertEqual(0, full.stats["reused_files"])
        self.assertEqual(self.files(first.manifest), self.files(full.manifest))

    def test_a_changed_or_replaced_file_is_read_again(self):
        target = LocalTarget(self.base / "target")
        self.snapshot(self.roots, [target])
        changed = self.cwd / "small" / "f001.txt"
        same_size = changed.read_bytes().replace(b"file 1", b"FILE 1", 1)
        changed.write_bytes(same_size)  # same size, new time
        replaced = self.cwd / "small" / "f002.txt"
        stat = replaced.stat()
        fresh = replaced.with_name("new.tmp-x")
        fresh.write_text(replaced.read_text(encoding="utf-8").upper(), encoding="utf-8")
        os.replace(fresh, replaced)
        os.utime(replaced, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same size and time, new file id
        (self.cwd / "log.jsonl").write_bytes(b'{"a": 1}\n' * 1001)
        second = self.snapshot(self.roots, [target])
        entries = {entry["path"]: entry for entry in second.manifest["entries"] if entry["root"] == "cwd"}
        self.assertEqual(same_size, b"".join(read_chunks(target, entries["small/f001.txt"]["chunks"])))
        self.assertTrue(b"".join(read_chunks(target, entries["small/f002.txt"]["chunks"])).startswith(b"FILE 2"))
        self.assertEqual(9 * 1001, entries["log.jsonl"]["size"])
        self.assertEqual(39, second.stats["reused_files"])

    def test_a_file_modified_just_before_the_last_snapshot_is_read_again(self):
        target = LocalTarget(self.base / "target")
        age(self.cwd / "small", 0.5)
        self.snapshot(self.roots, [target])
        second = self.snapshot(self.roots, [target])
        self.assertEqual(2, second.stats["reused_files"])  # only big.bin and log.jsonl were old enough

    def test_a_target_without_the_files_means_every_file_is_read(self):
        old = LocalTarget(self.base / "old")
        self.snapshot(self.roots, [old])
        new = LocalTarget(self.base / "new")
        both = self.snapshot(self.roots, [old, new])
        self.assertEqual(0, both.stats["reused_files"])
        self.assertEqual([], verify_snapshot(new, both.manifest))
        again = self.snapshot(self.roots, [old, new])
        self.assertEqual(42, again.stats["reused_files"])

    def test_cli_full_flag_and_menu_row(self):
        from ciel_runtime_support import session_backup_menu
        from ciel_runtime_support.session_backup_service import backup_command

        self.assertIn("--full", backup_command(self.cwd, "menu", {}, full=True))
        self.assertNotIn("--full", backup_command(self.cwd, "menu", {}))
        rows, values = session_backup_menu.panel_rows(self.base / "ciel", self.cwd)
        self.assertIn("Full backup now", rows[values.index("now-full")])
        line = session_backup_menu._progress_line({"files": 10, "unchanged": 7, "read": 0, "uploaded": 0}, 3)
        self.assertIn("10 files (7 unchanged)", line)


class ParallelReadTests(unittest.TestCase):
    def test_small_files_are_opened_together_and_entries_keep_the_walk_order(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cwd = base / "work"
            make_tree(cwd, count=60)
            roots = default_roots(cwd, environ={}, home=base / "home", asset_home=base / "home", config_dir=base / "ciel")
            active = [0, 0]
            lock = threading.Lock()
            real_stream = session_backup_collect._file_stream

            def slow(path):
                with lock:
                    active[0] += 1
                    active[1] = max(active[1], active[0])
                try:
                    time.sleep(0.01)
                    yield from real_stream(path)
                finally:
                    with lock:
                        active[0] -= 1

            orders = {}
            for jobs in (1, 8):
                with mock.patch.object(session_backup_collect, "_file_stream", slow):
                    result = create_snapshot(roots, [LocalTarget(base / f"t{jobs}")], CollectOptions(), key=None,
                                             versions={}, host="h", user="u", jobs=jobs)
                orders[jobs] = [(entry["path"], entry["chunks"]) for entry in result.manifest["entries"]]
                if jobs == 1:
                    self.assertEqual(1, active[1])
                    active[1] = 0
        self.assertGreater(active[1], 1)
        self.assertEqual(orders[1], orders[8])


if __name__ == "__main__":
    unittest.main()
