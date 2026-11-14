"""Fail-before contract for the retained-worktree content-baseline sidecar.

No live runtime state is used.  The tests define the approved D design before
product code: exact content, hard bounds, crash recovery, mixed versions and
cleanup.  Run only through the isolated wp-open-items runner.
"""
import gc
import json
import os
import stat
import subprocess
import sys
import threading
import time
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_workspace as ws


class ContentBaselineTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="work-content-baseline-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.config = self.root / "config"
        self.git(self.root, "init", str(self.repo))
        self.git(self.repo, "config", "user.name", "fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "delete.txt").write_text("delete me\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "base")
        self.prior = "lease-prior"
        self.replacement = "lease-replacement"
        self.record = ws.prepare(self.repo, self.prior, config_dir=self.config)
        self.worktree = Path(self.record["path"])

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def receipt(self, lease=None, generation="1"):
        return ws.salvage_observe(self.repo, lease or self.prior, generation=generation,
                                  terminal=True, slot=False, config_dir=self.config)

    def handoff(self, replacement=None, prior=None, prior_generation="1",
                replacement_generation="2", **kwargs):
        prior = prior or self.prior
        replacement = replacement or self.replacement
        receipt = self.receipt(prior, prior_generation)
        args = dict(prior_generation=prior_generation,
                    replacement_generation=replacement_generation,
                    receipt_digest=receipt["receipt_digest"], expected_path=receipt["path"],
                    expected_branch=receipt["branch"], expected_head=receipt["head"],
                    expected_dirty=receipt["dirty"], prior_terminal=True, prior_slot=False,
                    config_dir=self.config)
        args.update(kwargs)
        return ws.handoff_rebind(self.repo, prior, replacement, **args)

    def baseline_paths(self, replacement=None):
        return ws.content_baseline_paths(self.repo, replacement or self.replacement,
                                         config_dir=self.config)

    def drift(self, replacement=None):
        return ws.handoff_drift(self.repo, replacement or self.replacement,
                                config_dir=self.config)

    def assert_changed(self, mutate):
        self.handoff()
        self.assertEqual(False, self.drift()["changed_before_bind"])
        mutate()
        self.assertEqual(True, self.drift()["changed_before_bind"])

    # 1. Exact content classes -------------------------------------------------

    def test_01_content_only_staged_unstaged_untracked_ignored_and_deletion_change_digest(self):
        cases = {
            "unstaged": lambda: (self.worktree / "tracked.txt").write_text("unstaged\n", encoding="utf-8"),
            "staged": lambda: ((self.worktree / "tracked.txt").write_text("staged\n", encoding="utf-8"),
                               self.git(self.worktree, "add", "tracked.txt")),
            "untracked": lambda: (self.worktree / "new.bin").write_bytes(b"untracked\x00bytes"),
            "ignored": lambda: (self.worktree / "ignored.bin").write_bytes(b"ignored\x00bytes"),
            "deletion": lambda: (self.worktree / "delete.txt").unlink(),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                self.setUp()
                self.assert_changed(mutate)

    def test_02_unchanged_handoff_is_false_and_first_generation_is_not_applicable(self):
        self.assertEqual("not_applicable", ws.handoff_drift(
            self.repo, self.prior, config_dir=self.config)["changed_before_bind"])
        self.handoff()
        answer = self.drift()
        self.assertEqual(False, answer["changed_before_bind"])
        self.assertRegex(answer["baseline_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(answer["baseline_digest"], answer["current_digest"])

    def test_03_symlink_target_bytes_are_hashed_without_following(self):
        link = self.worktree / "link"
        try:
            os.symlink("tracked.txt", link)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        self.handoff()
        self.assertEqual(False, self.drift()["changed_before_bind"])
        link.unlink()
        os.symlink("delete.txt", link)
        self.assertEqual(True, self.drift()["changed_before_bind"])

    # 2. Provenance and crash points ------------------------------------------

    def test_04_old_build_missing_marker_is_unknown_but_intended_missing_sidecar_refuses(self):
        self.handoff()
        paths = self.baseline_paths()
        paths["intent"].unlink()
        paths["sidecar"].unlink()
        self.assertEqual("unknown", self.drift()["changed_before_bind"])
        paths["intent"].write_text(json.dumps({"schema": 1, "producer": ws.CONTENT_BASELINE_PRODUCER}),
                                   encoding="utf-8")
        with self.assertRaisesRegex(ws.WorkspaceError, "baseline incomplete"):
            self.drift()

    def test_05_corrupt_or_mismatched_intended_sidecar_refuses_never_false(self):
        self.handoff()
        paths = self.baseline_paths()
        paths["sidecar"].write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ws.WorkspaceError, "baseline incomplete"):
            self.drift()
        paths["sidecar"].write_text(json.dumps({"schema": 1, "producer": "other"}), encoding="utf-8")
        with self.assertRaisesRegex(ws.WorkspaceError, "baseline incomplete"):
            self.drift()

    def test_06_intent_only_failure_writes_no_replacement_manifest_and_retry_repairs(self):
        real = ws._save_content_record
        calls = []
        def fail_sidecar(path, value):
            calls.append(path)
            if str(path).endswith(".content-baseline-v1.json"):
                raise OSError("injected sidecar failure")
            return real(path, value)
        with patch.object(ws, "_save_content_record", side_effect=fail_sidecar):
            with self.assertRaisesRegex(ws.WorkspaceError, "baseline persistence failed"):
                self.handoff()
        paths = self.baseline_paths()
        self.assertTrue(paths["intent"].is_file())
        self.assertFalse(paths["sidecar"].exists())
        self.assertFalse(paths["manifest"].exists())
        self.assertEqual("ready", ws.observe(self.repo, self.prior, config_dir=self.config)["status"])
        self.handoff()
        self.assertEqual(False, self.drift()["changed_before_bind"])

    def test_07_sidecar_orphan_is_authoritative_and_retry_does_not_recapture(self):
        real_save = ws._save
        replacement_manifest = self.baseline_paths()["manifest"]
        def fail_manifest(path, value):
            if path == replacement_manifest:
                raise OSError("injected manifest failure")
            return real_save(path, value)
        with patch.object(ws, "_save", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.handoff()
        paths = self.baseline_paths()
        original = json.loads(paths["sidecar"].read_text(encoding="utf-8"))["digest"]
        (self.worktree / "tracked.txt").write_text("operator changed after crash\n", encoding="utf-8")
        self.handoff()
        self.assertEqual(original, json.loads(paths["sidecar"].read_text(encoding="utf-8"))["digest"])
        self.assertEqual(True, self.drift()["changed_before_bind"])

    def test_08_new_manifest_without_old_tombstone_replays_exact_handoff(self):
        real_save = ws._save
        old_manifest = ws._identity(self.repo, self.prior, self.config)[2]
        def fail_old(path, value):
            if path == old_manifest and value.get("status") == "rebound":
                raise OSError("injected tombstone failure")
            return real_save(path, value)
        with patch.object(ws, "_save", side_effect=fail_old):
            with self.assertRaises(OSError):
                self.handoff()
        self.assertTrue(self.baseline_paths()["manifest"].is_file())
        self.assertEqual("ready", ws.observe(self.repo, self.prior, config_dir=self.config)["status"])
        self.handoff()
        self.assertEqual("rebound", ws.observe(self.repo, self.prior,
                                                config_dir=self.config)["status"])

    # 3. Bounds and platform byte semantics -----------------------------------

    def test_09_entry_single_total_and_deadline_bounds_are_named_without_paths(self):
        checks = [
            ("MAX_CONTENT_ENTRIES", 1, "entry bound"),
            ("MAX_CONTENT_SINGLE_FILE_BYTES", 1, "single file byte bound"),
            ("MAX_CONTENT_TOTAL_BYTES", 1, "total byte bound"),
            ("CONTENT_CAPTURE_DEADLINE_SECONDS", 0, "deadline exceeded"),
        ]
        for constant, value, message in checks:
            with self.subTest(constant=constant):
                with patch.object(ws, constant, value):
                    with self.assertRaisesRegex(ws.WorkspaceError, message) as refused:
                        self.handoff(replacement="replacement-" + constant)
                self.assertNotIn("tracked.txt", str(refused.exception))
                self.assertNotIn(str(self.worktree), str(refused.exception))

    def bounded_enumeration(self, child_code, limit=8.0):
        """Run the streaming enumeration with ``git`` replaced by an owned child.

        The test, not the product, bounds the wait: if the enumeration is still
        blocked after ``limit`` seconds the test kills its own children (so a
        pre-fix build fails instead of hanging the run) and reports it.
        """
        real = subprocess.Popen
        children = []
        def fake_git(argv, *args, **kwargs):
            # Only the two streaming ``ls-files --others`` calls are replaced;
            # subprocess.run (the index read) goes to real Git.
            if "--others" not in argv:
                return real(argv, *args, **kwargs)
            child = real([sys.executable, "-c", child_code], *args, **kwargs)
            children.append(child)
            return child
        outcome = {}
        def target():
            try:
                ws._enumerate_content_paths(self.worktree, time.monotonic())
                outcome["result"] = "returned"
            except BaseException as error:  # noqa: BLE001 - reported to the test thread
                outcome["error"] = error
        started = time.monotonic()
        with (patch.object(ws, "CONTENT_CAPTURE_DEADLINE_SECONDS", .5),
              patch.object(ws.subprocess, "Popen", side_effect=fake_git)):
            worker = threading.Thread(target=target, daemon=True)
            worker.start()
            worker.join(limit)
            hung = worker.is_alive()
            if hung:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                worker.join(30)
        elapsed = time.monotonic() - started
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
            for stream in (child.stdout, child.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        self.assertFalse(hung, "streaming enumeration still blocked after %.1fs" % limit)
        self.assertTrue(children, "the streaming path never started its child")
        return outcome, elapsed

    def test_09a_silent_streaming_git_is_killed_at_the_deadline(self):
        """A child that writes nothing must not hold the blocking read past the deadline."""
        outcome, elapsed = self.bounded_enumeration("import time; time.sleep(60)")
        self.assertIsInstance(outcome.get("error"), ws.WorkspaceError, outcome)
        self.assertIn("deadline exceeded", str(outcome["error"]))
        self.assertNotIn(str(self.worktree), str(outcome["error"]))
        self.assertLess(elapsed, 5)

    def test_09b_streaming_git_that_floods_stderr_never_deadlocks(self):
        """An unread stderr pipe must not stall the child and the stdout read."""
        outcome, elapsed = self.bounded_enumeration(
            "import os; block = b'x' * 65536; [os.write(2, block) for _ in iter(int, 1)]")
        self.assertIsInstance(outcome.get("error"), ws.WorkspaceError, outcome)
        self.assertIn("deadline exceeded", str(outcome["error"]))
        self.assertLess(elapsed, 5)

    def test_09c_streaming_enumeration_closes_every_pipe_under_resourcewarning_error(self):
        """Real Git enumeration: no pipe is left for the collector to warn about."""
        real = subprocess.Popen
        children = []
        def recording(argv, *args, **kwargs):
            child = real(argv, *args, **kwargs)
            if "--others" in argv:
                children.append(child)
            return child
        unraisable = []
        previous = sys.unraisablehook
        sys.unraisablehook = unraisable.append
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", ResourceWarning)
                with patch.object(ws.subprocess, "Popen", side_effect=recording):
                    ws._enumerate_content_paths(self.worktree, time.monotonic())
                gc.collect()
        finally:
            sys.unraisablehook = previous
        self.assertEqual(2, len(children))
        for child in children:
            self.assertTrue(all(stream is None or stream.closed
                                for stream in (child.stdin, child.stdout, child.stderr)))
        self.assertEqual([], [repr(row.exc_value) for row in unraisable
                              if isinstance(row.exc_value, ResourceWarning)])

    def test_10_binary_non_ascii_and_newline_paths_are_stable(self):
        names = ["한글.txt"]
        # Windows rejects newline path components; POSIX must preserve them.
        if os.name != "nt":
            names.append("line\nbreak.txt")
        for name in names:
            (self.worktree / name).write_bytes(name.encode("utf-8"))
        self.handoff()
        first = self.drift()["current_digest"]
        self.assertEqual(False, self.drift()["changed_before_bind"])
        (self.worktree / names[-1]).write_bytes(b"changed")
        self.assertEqual(True, self.drift()["changed_before_bind"])
        self.assertNotEqual(first, self.drift()["current_digest"])

    def test_11_unsupported_windows_reparse_is_refused_without_opening(self):
        attrs = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        row = type("ReparseStat", (), {"st_file_attributes": attrs})()
        with patch.object(ws, "_is_unsupported_reparse", return_value=True) as checked:
            with self.assertRaisesRegex(ws.WorkspaceError, "reparse"):
                self.handoff()
        self.assertTrue(checked.called)
        self.assertTrue(ws._is_unsupported_reparse(row, type("NotSymlink", (), {
            "is_symlink": lambda self: False})()))

    def test_12_gitlink_hashes_recorded_object_current_head_and_dirty_without_walk(self):
        sub = self.root / "sub"
        self.git(self.root, "init", str(sub))
        self.git(sub, "config", "user.name", "fixture")
        self.git(sub, "config", "user.email", "fixture@example.invalid")
        (sub / "s").write_text("one", encoding="utf-8")
        self.git(sub, "add", "s")
        self.git(sub, "commit", "-m", "one")
        self.git(self.worktree, "-c", "protocol.file.allow=always", "submodule", "add", str(sub), "module")
        self.handoff()
        self.assertEqual(False, self.drift()["changed_before_bind"])
        module = self.worktree / "module"
        (module / "s").write_text("dirty", encoding="utf-8")
        self.assertEqual(True, self.drift()["changed_before_bind"])

    # 4. Durability, lifecycle, mixed version ---------------------------------

    def test_13_parent_directory_fsync_unsupported_does_not_undo_replaced_sidecar(self):
        with patch.object(ws, "_sync_parent_directory", return_value="unsupported"):
            self.handoff()
        self.assertTrue(self.baseline_paths()["sidecar"].is_file())
        self.assertEqual(False, self.drift()["changed_before_bind"])

    def test_14_sidecar_has_exact_versioned_bounded_metadata_and_no_content_or_paths(self):
        secret = "SECRET-CONTENT-NOT-PERSISTED"
        (self.worktree / "tracked.txt").write_text(secret, encoding="utf-8")
        self.handoff()
        row = json.loads(self.baseline_paths()["sidecar"].read_text(encoding="utf-8"))
        self.assertEqual((1, ws.CONTENT_BASELINE_PRODUCER), (row["schema"], row["producer"]))
        encoded = json.dumps(row, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("tracked.txt", encoded)
        self.assertLess(len(encoded), 8192)

    def test_15_repeated_generations_use_distinct_sidecars_and_old_one_cannot_satisfy_new(self):
        self.handoff()
        first_paths = self.baseline_paths()
        second_lease = "lease-third"
        receipt = ws.salvage_observe(self.repo, self.replacement, generation="2",
                                     terminal=True, slot=False, config_dir=self.config)
        ws.handoff_rebind(self.repo, self.replacement, second_lease,
                          prior_generation="2", replacement_generation="3",
                          receipt_digest=receipt["receipt_digest"], expected_path=receipt["path"],
                          expected_branch=receipt["branch"], expected_head=receipt["head"],
                          expected_dirty=receipt["dirty"], prior_terminal=True, prior_slot=False,
                          config_dir=self.config)
        second_paths = self.baseline_paths(second_lease)
        self.assertNotEqual(first_paths["sidecar"], second_paths["sidecar"])
        second_paths["sidecar"].write_bytes(first_paths["sidecar"].read_bytes())
        with self.assertRaisesRegex(ws.WorkspaceError, "baseline incomplete"):
            self.drift(second_lease)

    def test_16_successful_cleanup_removes_sidecars_and_unlink_failure_is_retryable(self):
        self.handoff()
        ws.bind(self.repo, self.replacement, "host", config_dir=self.config)
        ws.set_activity(self.repo, self.replacement, "host", idle=True, proof="idle",
                        config_dir=self.config)
        paths = self.baseline_paths()
        real_unlink = Path.unlink
        def fail_sidecar(path, *args, **kwargs):
            if path in (paths["intent"], paths["sidecar"]):
                raise OSError("injected unlink failure")
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_sidecar):
            result = ws.remove(self.repo, self.replacement, host_id="host", active=False,
                               idle_proof="idle", expected_head=self.record["head"],
                               approved_target=str(self.worktree), config_dir=self.config)
        self.assertTrue(result["path_absent"])
        self.assertTrue(result["content_baseline_cleanup_pending"])
        retried = ws.remove(self.repo, self.replacement, host_id="host", active=False,
                            idle_proof="idle", expected_head=self.record["head"],
                            approved_target=str(self.worktree), config_dir=self.config)
        self.assertFalse(retried["content_baseline_cleanup_pending"])
        self.assertFalse(paths["intent"].exists() or paths["sidecar"].exists())


    # 5. Audit findings: strict records and exact-key cleanup ------------------

    def test_17_corrupt_sidecar_values_are_refused_never_trusted(self):
        self.handoff()
        path = self.baseline_paths()["sidecar"]
        good = json.loads(path.read_text(encoding="utf-8"))
        corrupt = {
            "negative entries": {"entries": -1},
            "entries over bound": {"entries": ws.MAX_CONTENT_ENTRIES + 1},
            "bool entries": {"entries": True},
            "negative bytes": {"bytes_read": -5},
            "bytes over bound": {"bytes_read": ws.MAX_CONTENT_TOTAL_BYTES + 1},
            "zero time": {"captured_at": 0},
            "bool time": {"captured_at": True},
            "string time": {"captured_at": "1"},
            "bad head": {"head": "not-an-object-id"},
            "oversized head": {"head": "a" * 200},
        }
        for label, change in corrupt.items():
            with self.subTest(label=label):
                path.write_text(json.dumps({**good, **change}), encoding="utf-8")
                with self.assertRaisesRegex(ws.WorkspaceError, "baseline incomplete"):
                    self.drift()
        for label, literal in (("nan time", "NaN"), ("inf time", "Infinity")):
            with self.subTest(label=label):
                raw = json.dumps({**good, "captured_at": 1.0}).replace('"captured_at": 1.0',
                                                                      '"captured_at": ' + literal)
                path.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(ws.WorkspaceError, "baseline incomplete"):
                    self.drift()
        path.write_text(json.dumps(good), encoding="utf-8")
        self.assertEqual(False, self.drift()["changed_before_bind"])

    def bind_idle(self, lease):
        ws.bind(self.repo, lease, "host", config_dir=self.config)
        ws.set_activity(self.repo, lease, "host", idle=True, proof="idle", config_dir=self.config)

    def remove_replacement(self):
        return ws.remove(self.repo, self.replacement, host_id="host", active=False,
                         idle_proof="idle", expected_head=self.record["head"],
                         approved_target=str(self.worktree), config_dir=self.config)

    def test_18_cleanup_removes_corrupt_matching_files_by_exact_manifest_key(self):
        for corrupt in ("intent", "sidecar"):
            with self.subTest(corrupt=corrupt):
                self.setUp()
                self.handoff()
                self.bind_idle(self.replacement)
                paths = self.baseline_paths()
                paths[corrupt].write_text("{not json", encoding="utf-8")
                result = self.remove_replacement()
                self.assertFalse(result["content_baseline_cleanup_pending"])
                self.assertFalse(paths["intent"].exists() or paths["sidecar"].exists())

    def test_19_cleanup_never_touches_another_ownerships_files_and_converges(self):
        self.handoff()
        self.bind_idle(self.replacement)
        stranger = ws.prepare(self.repo, "other-owner", config_dir=self.config)
        foreign = self.baseline_paths("other-replacement")
        foreign["intent"].write_text("{corrupt foreign", encoding="utf-8")
        foreign["sidecar"].write_text("{corrupt foreign", encoding="utf-8")
        mine = self.baseline_paths()
        first = self.remove_replacement()
        second = self.remove_replacement()
        self.assertFalse(first["content_baseline_cleanup_pending"])
        self.assertFalse(second["content_baseline_cleanup_pending"])
        self.assertFalse(mine["intent"].exists() or mine["sidecar"].exists())
        self.assertTrue(foreign["intent"].exists() and foreign["sidecar"].exists())
        self.assertTrue(Path(stranger["path"]).is_dir())

    def test_20_cleanup_covers_every_generation_of_the_same_physical_worktree(self):
        self.handoff()
        receipt = ws.salvage_observe(self.repo, self.replacement, generation="2",
                                     terminal=True, slot=False, config_dir=self.config)
        ws.handoff_rebind(self.repo, self.replacement, "lease-third",
                          prior_generation="2", replacement_generation="3",
                          receipt_digest=receipt["receipt_digest"], expected_path=receipt["path"],
                          expected_branch=receipt["branch"], expected_head=receipt["head"],
                          expected_dirty=receipt["dirty"], prior_terminal=True, prior_slot=False,
                          config_dir=self.config)
        older, newer = self.baseline_paths(), self.baseline_paths("lease-third")
        older["sidecar"].write_text("{corrupt older", encoding="utf-8")
        self.bind_idle("lease-third")
        result = ws.remove(self.repo, "lease-third", host_id="host", active=False,
                           idle_proof="idle", expected_head=self.record["head"],
                           approved_target=str(self.worktree), config_dir=self.config)
        self.assertFalse(result["content_baseline_cleanup_pending"])
        for paths in (older, newer):
            self.assertFalse(paths["intent"].exists() or paths["sidecar"].exists())


if __name__ == "__main__":
    unittest.main()
