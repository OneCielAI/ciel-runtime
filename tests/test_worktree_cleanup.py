import subprocess
import tempfile
import unittest
from pathlib import Path
from ciel_runtime_support.worktree_cleanup import cleanup_worktree


class WorktreeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="work-cleanup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.owned = self.root / "owned"
        self.owned.mkdir()
        self.remote = self.root / "remote.git"
        self.git(self.root, "init", "--bare", str(self.remote))
        self.git(self.root, "init", str(self.repo))
        self.git(self.repo, "config", "user.name", "Cleanup Test")
        self.git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "file").write_text("a")
        self.git(self.repo, "add", "file")
        self.git(self.repo, "commit", "-m", "fixture")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.target = self.owned / "worker"
        self.git(self.repo, "worktree", "add", "-b", "worker", str(self.target))
        self.head = self.git(self.target, "rev-parse", "HEAD")

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def clean(self, **kwargs):
        return cleanup_worktree(self.repo, self.target, owned_root=self.owned,
                                expected_head=self.head, active=False, **kwargs)

    def test_unpushed_without_approval_retained(self):
        with self.assertRaisesRegex(ValueError, "push or explicit"):
            self.clean()
        self.assertTrue(self.target.exists())

    def test_verified_remote_push_removes_and_confirms(self):
        self.git(self.repo, "push", "origin", "worker")
        result = self.clean(remote_ref="refs/heads/worker")
        self.assertTrue(result["pushed"])
        self.assertFalse(self.target.exists())

    def test_exact_explicit_approval_can_remove_unpushed(self):
        result = self.clean(approved_target=str(self.target))
        self.assertTrue(result["explicit_user_approval"])
        self.assertFalse(self.target.exists())

    def test_dirty_is_not_removed_even_with_approval(self):
        (self.target / "untracked").write_text("keep")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.clean(approved_target=str(self.target))
        self.assertTrue(self.target.exists())

    def test_approval_for_other_path_does_not_apply(self):
        with self.assertRaises(ValueError):
            self.clean(approved_target=str(self.repo))


if __name__ == "__main__":
    unittest.main()
