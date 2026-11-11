import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ciel_runtime_support import work_parallel_workspace as ws

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="parallel-workspace-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.config = self.root / "config"
        self.git(self.root, "init", str(self.repo))
        self.git(self.repo, "config", "user.name", "Workspace Test")
        self.git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "file").write_text("initial")
        (self.repo / ".gitignore").write_text("ignored" + chr(10))
        self.git(self.repo, "add", "file", ".gitignore")
        self.git(self.repo, "commit", "-m", "fixture")
        self.lease = "lease/exact"
        self.r = ws.prepare(self.repo, self.lease, config_dir=self.config)
        self.target = Path(self.r["path"])

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, check=True).stdout.strip()

    def observe(self):
        return ws.observe(self.repo, self.lease, config_dir=self.config)

    def idle(self):
        ws.bind(self.repo, self.lease, "host-1", config_dir=self.config)
        ws.set_activity(self.repo, self.lease, "host-1", idle=True,
                        proof="observed-exit-1", config_dir=self.config)

    def remove(self, **kwargs):
        args = dict(host_id="host-1", active=False, idle_proof="observed-exit-1",
                    expected_head=self.r["head"], config_dir=self.config)
        args.update(kwargs)
        return ws.remove(self.repo, self.lease, **args)

    def test_prepare_binding_durable_and_idempotent(self):
        self.assertEqual(ws.prepare(self.repo, self.lease, config_dir=self.config), self.r)
        self.assertEqual(self.observe(), self.r)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD"), self.r["head"])
        self.assertEqual(self.git(self.target, "branch", "--show-current"), self.r["branch"])
        self.assertFalse(self.r["idle"])
        self.assertTrue(self.target.is_relative_to(self.repo / ".claude" / "worktrees"))
        self.assertEqual((self.target / "file").read_text(), "initial")

    def test_exact_repo_and_lease_identity(self):
        with self.assertRaises(ws.WorkspaceError):
            ws.observe(self.repo, "other", config_dir=self.config)
        (self.repo / "subdir").mkdir()
        with self.assertRaises(ws.WorkspaceError):
            ws.prepare(self.repo / "subdir", self.lease, config_dir=self.config)
        other = ws.prepare(self.repo, "lease-other", config_dir=self.config)
        self.assertNotEqual(other["path"], self.r["path"])

    def test_unknown_active_wrong_host_stale_proof_retained(self):
        with self.assertRaises(ws.WorkspaceError):
            self.remove(approved_target=str(self.target))
        self.idle()
        for changes in ({"active": True}, {"active": None}, {"host_id": "other"},
                        {"idle_proof": "stale"}):
            with self.subTest(changes=changes), self.assertRaises(ws.WorkspaceError):
                self.remove(approved_target=str(self.target), **changes)
        with self.assertRaises(ws.WorkspaceError):
            ws.bind(self.repo, self.lease, "other", config_dir=self.config)
        self.assertTrue(self.target.exists())

    def test_resume_revokes_idle_permission(self):
        self.idle()
        ws.set_activity(self.repo, self.lease, "host-1", idle=False, config_dir=self.config)
        with self.assertRaises(ws.WorkspaceError):
            self.remove(approved_target=str(self.target))
        self.assertIsNone(self.observe()["idle_proof"])

    def test_unpushed_and_other_path_approval_retained(self):
        self.idle()
        for approval in (None, str(self.repo)):
            with self.assertRaisesRegex(ws.WorkspaceError, "push or explicit"):
                self.remove(approved_target=approval)
        self.assertTrue(self.target.exists())

    def test_approval_removes_retains_branch_and_tombstone(self):
        self.idle()
        result = self.remove(approved_target=str(self.target))
        self.assertTrue(result["path_absent"] and result["registration_absent"])
        self.assertFalse(self.target.exists())
        self.git(self.repo, "show-ref", "--verify", "refs/heads/" + self.r["branch"])
        manifest = next((self.config / "work-parallel-workspaces").glob("*.json"))
        self.assertEqual(json.loads(manifest.read_text())["status"], "removed")
        with self.assertRaises(ws.WorkspaceError):
            ws.prepare(self.repo, self.lease, config_dir=self.config)

    def test_local_bare_remote_push_allows_removal(self):
        remote = self.root / "remote.git"
        self.git(self.root, "init", "--bare", str(remote))
        self.git(self.repo, "remote", "add", "origin", str(remote))
        self.git(self.repo, "push", "origin", self.r["branch"])
        self.idle()
        self.assertTrue(self.remove(remote_ref="refs/heads/" + self.r["branch"])["pushed"])

    def test_tracked_untracked_ignored_always_retained(self):
        self.idle()
        for name in ("file", "untracked", "ignored"):
            with self.subTest(name=name):
                p = self.target / name
                p.write_text("keep")
                with self.assertRaisesRegex(ws.WorkspaceError, "dirty"):
                    self.remove(approved_target=str(self.target))
                self.assertTrue(p.exists())
                if name == "file":
                    self.git(self.target, "restore", "file")
                else:
                    p.unlink()

    def test_head_change_requires_fresh_review(self):
        self.idle()
        (self.target / "file").write_text("changed")
        self.git(self.target, "add", "file")
        self.git(self.target, "commit", "-m", "fixture change")
        with self.assertRaisesRegex(ws.WorkspaceError, "HEAD changed"):
            self.remove(approved_target=str(self.target))
        self.assertNotEqual(self.observe()["head"], self.r["head"])
        self.assertTrue(self.remove(expected_head=self.observe()["head"],
                                    approved_target=str(self.target))["path_absent"])

    def test_manifest_forgery_and_corruption_block(self):
        manifest = next((self.config / "work-parallel-workspaces").glob("*.json"))
        record = json.loads(manifest.read_text())
        record["path"] = str(self.repo)
        manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(ws.WorkspaceError, "corrupt"):
            self.observe()
        manifest.write_text("not-json")
        with self.assertRaisesRegex(ws.WorkspaceError, "corrupt"):
            self.observe()
        self.assertTrue(self.target.exists())

    def test_missing_manifest_never_adopts_existing_path(self):
        next((self.config / "work-parallel-workspaces").glob("*.json")).unlink()
        with self.assertRaisesRegex(ws.WorkspaceError, "no ownership"):
            ws.prepare(self.repo, self.lease, config_dir=self.config)

    def test_interrupted_creation_retry_blocked(self):
        real = ws._git
        def interrupted(repo, *args):
            if args[:2] == ("worktree", "add"):
                raise RuntimeError("simulated crash")
            return real(repo, *args)
        with patch.object(ws, "_git", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                ws.prepare(self.repo, "crash", config_dir=self.config)
        with self.assertRaisesRegex(ws.WorkspaceError, "incomplete"):
            ws.prepare(self.repo, "crash", config_dir=self.config)

    def test_concurrent_prepare_has_one_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        def create(_):
            return ws.prepare(self.repo, "concurrent", config_dir=self.config)
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows = list(pool.map(create, range(3)))
        self.assertEqual(rows, [rows[0]] * 3)

    def test_missing_required_manifest_field_blocks(self):
        manifest = next((self.config / "work-parallel-workspaces").glob("*.json"))
        record = json.loads(manifest.read_text())
        del record["git_dir"]
        manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(ws.WorkspaceError, "corrupt"):
            self.observe()

    def test_idle_without_evidence_is_rejected(self):
        ws.bind(self.repo, self.lease, "host-1", config_dir=self.config)
        with self.assertRaises(ws.WorkspaceError):
            ws.set_activity(self.repo, self.lease, "host-1", idle=True, config_dir=self.config)
        self.assertFalse(self.observe()["idle"])

    def test_owned_parent_symlink_rejected(self):
        # Windows symlink creation may require a privilege this process lacks.
        link = self.root / "repo-link"
        try:
            link.symlink_to(self.repo, target_is_directory=True)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        with self.assertRaisesRegex(ws.WorkspaceError, "symlink"):
            ws.prepare(link, "link-lease", config_dir=self.config)

    def test_branch_switch_blocks_ownership(self):
        self.git(self.target, "checkout", "--detach")
        with self.assertRaises(ws.WorkspaceError):
            self.observe()

    def test_salvage_observer_is_bounded_metadata_only(self):
        (self.target / "file").write_text("secret changed content")
        for index in range(ws._MAX_CHANGED_FILES + 7):
            (self.target / ("untracked-%03d" % index)).write_text("private payload")
        receipt = ws.salvage_observe(self.repo, self.lease, generation="generation-1",
                                     terminal=True, slot=False, config_dir=self.config)
        self.assertEqual(receipt["path"], str(self.target))
        self.assertEqual(receipt["branch"], self.r["branch"])
        self.assertEqual(receipt["head"], self.r["head"])
        self.assertTrue(receipt["dirty"])
        self.assertEqual(len(receipt["changed_files"]), ws._MAX_CHANGED_FILES)
        self.assertTrue(receipt["changed_files_truncated"])
        self.assertEqual(receipt["changed_files_total"], ws._MAX_CHANGED_FILES + 8)
        self.assertNotIn("secret changed content", json.dumps(receipt))
        self.assertNotIn("private payload", json.dumps(receipt))
        self.assertRegex(receipt["receipt_digest"], r"^[0-9a-f]{64}$")

    def test_salvage_requires_terminal_slot_free_host_fact(self):
        for terminal, slot in ((False, False), (True, True), (None, False)):
            with self.subTest(terminal=terminal, slot=slot), self.assertRaisesRegex(
                    ws.WorkspaceError, "terminal slot-free"):
                ws.salvage_observe(self.repo, self.lease, generation="generation-1",
                                   terminal=terminal, slot=slot, config_dir=self.config)

    def test_handoff_rebind_preserves_dirty_workspace(self):
        (self.target / "file").write_text("retained dirty work")
        receipt = ws.salvage_observe(self.repo, self.lease, generation="generation-1",
                                     terminal=True, slot=False, config_dir=self.config)
        replacement = ws.handoff_rebind(
            self.repo, self.lease, "lease-replacement", prior_generation="generation-1",
            replacement_generation="generation-2", receipt_digest=receipt["receipt_digest"],
            expected_path=receipt["path"], expected_branch=receipt["branch"],
            expected_head=receipt["head"], expected_dirty=receipt["dirty"],
            prior_terminal=True, prior_slot=False, config_dir=self.config)
        self.assertEqual(replacement["path"], str(self.target))
        self.assertEqual(replacement["lease_id"], "lease-replacement")
        self.assertFalse(replacement["idle"])
        self.assertEqual((self.target / "file").read_text(), "retained dirty work")
        self.assertEqual(ws.observe(self.repo, "lease-replacement", config_dir=self.config),
                         replacement)
        old = self.observe()
        self.assertEqual(old["status"], "rebound")
        self.assertEqual(old["handoff"]["replacement_lease_id"], "lease-replacement")
        # The prior generation's tombstone is observable but can never bind,
        # go idle, be salvaged again, or remove the replacement's workspace.
        with self.assertRaisesRegex(ws.WorkspaceError, "handed off"):
            ws.bind(self.repo, self.lease, "host-old", config_dir=self.config)
        with self.assertRaisesRegex(ws.WorkspaceError, "handed off"):
            ws.set_activity(self.repo, self.lease, "host-old", idle=True,
                            proof="stale-exit", config_dir=self.config)
        with self.assertRaisesRegex(ws.WorkspaceError, "handed off"):
            ws.salvage_observe(self.repo, self.lease, generation="generation-1",
                               terminal=True, slot=False, config_dir=self.config)
        with self.assertRaisesRegex(ws.WorkspaceError, "handed off"):
            self.remove(approved_target=str(self.target), expected_head=receipt["head"])
        self.assertTrue(self.target.is_dir())
        with self.assertRaisesRegex(ws.WorkspaceError, "already rebound"):
            ws.handoff_rebind(
                self.repo, self.lease, "lease-third", prior_generation="generation-1",
                replacement_generation="generation-3", receipt_digest=receipt["receipt_digest"],
                expected_path=receipt["path"], expected_branch=receipt["branch"],
                expected_head=receipt["head"], expected_dirty=True,
                prior_terminal=True, prior_slot=False, config_dir=self.config)
        # The replacement alone holds lifecycle authority, and dirty work still
        # blocks its cleanup exactly as before.
        ws.bind(self.repo, "lease-replacement", "host-2", config_dir=self.config)
        ws.set_activity(self.repo, "lease-replacement", "host-2", idle=True,
                        proof="observed-exit-2", config_dir=self.config)
        with self.assertRaisesRegex(ws.WorkspaceError, "dirty"):
            ws.remove(self.repo, "lease-replacement", host_id="host-2", active=False,
                      idle_proof="observed-exit-2", expected_head=receipt["head"],
                      approved_target=str(self.target), config_dir=self.config)
        self.assertEqual((self.target / "file").read_text(), "retained dirty work")

    def test_handoff_rebind_retry_is_idempotent(self):
        receipt = ws.salvage_observe(self.repo, self.lease, generation="generation-1",
                                     terminal=True, slot=False, config_dir=self.config)
        args = dict(prior_generation="generation-1", replacement_generation="generation-2",
                    receipt_digest=receipt["receipt_digest"], expected_path=receipt["path"],
                    expected_branch=receipt["branch"], expected_head=receipt["head"],
                    expected_dirty=receipt["dirty"], prior_terminal=True, prior_slot=False,
                    config_dir=self.config)
        first = ws.handoff_rebind(self.repo, self.lease, "lease-replacement", **args)
        self.assertEqual(ws.handoff_rebind(self.repo, self.lease, "lease-replacement", **args), first)
        with self.assertRaisesRegex(ws.WorkspaceError, "already rebound"):
            ws.handoff_rebind(self.repo, self.lease, "lease-replacement",
                              **{**args, "replacement_generation": "generation-other"})

    def test_handoff_rebind_refuses_stale_and_mismatched_receipts(self):
        receipt = ws.salvage_observe(self.repo, self.lease, generation="generation-1",
                                     terminal=True, slot=False, config_dir=self.config)
        base = dict(prior_generation="generation-1", replacement_generation="generation-2",
                    receipt_digest=receipt["receipt_digest"], expected_path=receipt["path"],
                    expected_branch=receipt["branch"], expected_head=receipt["head"],
                    expected_dirty=receipt["dirty"], prior_terminal=True, prior_slot=False,
                    config_dir=self.config)
        for field, value in (("receipt_digest", "0" * 64), ("expected_path", str(self.repo)),
                             ("expected_branch", "other"), ("expected_head", "1" * 40),
                             ("expected_dirty", True), ("prior_terminal", False),
                             ("prior_slot", True)):
            with self.subTest(field=field), self.assertRaises(ws.WorkspaceError):
                ws.handoff_rebind(self.repo, self.lease, "replacement-" + field,
                                  **{**base, field: value})
        (self.target / "later").write_text("changed after receipt")
        with self.assertRaisesRegex(ws.WorkspaceError, "stale or mismatched"):
            ws.handoff_rebind(self.repo, self.lease, "replacement-stale", **base)
        self.assertFalse(any(p.name.endswith(".json") and "replacement" in p.name
                             for p in (self.config / "work-parallel-workspaces").glob("*.json")))
        self.assertEqual(self.observe()["status"], "ready")

if __name__ == "__main__":
    unittest.main()
