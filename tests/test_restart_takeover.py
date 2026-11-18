"""Restart worktree snapshots are non-destructive and the takeover note names the work.

User requirement 2026-11-17: a restart backs up and records the worktrees being
worked on, and the relaunched session takes them over (decision: snapshot ref).
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import restart_takeover as rt

_GLOBAL = tempfile.TemporaryDirectory(prefix="ciel-takeover-gitconfig-")
(Path(_GLOBAL.name) / "gitconfig").write_text("", encoding="utf-8")
# Hermetic: GitHub runners trust every repository in /etc/gitconfig.
HERMETIC = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(Path(_GLOBAL.name) / "gitconfig")}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **HERMETIC}).stdout.strip()


def digest_tree(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.parts}


class RestartTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = patch.dict(os.environ, HERMETIC)
        self.env.start()
        self.addCleanup(self.env.stop)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.state = self.root / "state"

    def worktree(self, name, branch):
        path = self.repo / ".claude" / "worktrees" / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path))
        return path

    def test_snapshot_keeps_index_files_and_branches_and_captures_untracked(self):
        (self.repo / "a.txt").write_text("one\nedited\n", encoding="utf-8")
        (self.repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "staged.txt")
        (self.repo / "new.txt").write_text("untracked\n", encoding="utf-8")
        (self.repo / "ignored.log").write_text("noise\n", encoding="utf-8")
        work = self.worktree("feature", "feature")
        (work / "b.txt").write_text("fork work\n", encoding="utf-8")
        clean = self.worktree("clean", "clean")
        before = {path: (digest_tree(path), git(path, "diff", "--cached", "--name-status"),
                         git(path, "status", "--porcelain"), git(path, "rev-parse", "HEAD"))
                  for path in (self.repo, work, clean)}
        branches = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
        manifest = rt.snapshot_all(self.repo, "a" * 32, claude_config=None, state_dir=self.state)
        after = {path: (digest_tree(path), git(path, "diff", "--cached", "--name-status"),
                        git(path, "status", "--porcelain"), git(path, "rev-parse", "HEAD"))
                 for path in (self.repo, work, clean)}
        self.assertEqual(before, after)
        self.assertEqual(branches, git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"))
        rows = {Path(row["path"]).resolve(): row for row in manifest["worktrees"]}
        main, fork, idle = rows[self.repo.resolve()], rows[work.resolve()], rows[clean.resolve()]
        self.assertIsNone(idle["snapshot_ref"])
        self.assertEqual((2, 1), (main["modified"], main["untracked"]))
        files = git(self.repo, "ls-tree", "-r", "--name-only", main["snapshot_ref"]).splitlines()
        self.assertEqual(sorted([".gitignore", "a.txt", "new.txt", "staged.txt"]), sorted(files))
        self.assertEqual("one\nedited\n", git(self.repo, "show", main["snapshot_ref"] + ":a.txt") + "\n")
        self.assertEqual(main["head"], git(self.repo, "rev-parse", main["snapshot_ref"] + "^"))
        self.assertEqual("fork work", git(self.repo, "show", fork["snapshot_ref"] + ":b.txt"))
        self.assertTrue(main["snapshot_ref"].startswith(rt.REF_PREFIX + "a" * 32 + "/"))
        stored = json.loads((self.state / rt.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["worktrees"], stored["worktrees"])

    def test_a_repository_another_account_owns_is_snapshotted_with_exact_trust(self):
        work = self.worktree("feature", "feature")
        (work / "b.txt").write_text("x\n", encoding="utf-8")
        with patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
            with self.assertRaises(subprocess.CalledProcessError):
                git(work, "status")
            manifest = rt.snapshot_all(work, "b" * 32, claude_config=None, state_dir=self.state)
        fork = next(row for row in manifest["worktrees"] if Path(row["path"]).resolve() == work.resolve())
        self.assertNotIn("error", fork)
        self.assertTrue(fork["snapshot_ref"])

    def test_session_worktree_comes_from_the_newest_transcript_and_reaches_the_notice(self):
        work = self.worktree("feature", "feature")
        (work / "b.txt").write_text("x\n", encoding="utf-8")
        claude = self.root / "claude"
        project = claude / "projects" / rt.re.sub(r"[^A-Za-z0-9._-]", "-", str(self.repo.resolve()).rstrip("\\/"))
        project.mkdir(parents=True)
        (project / "old.jsonl").write_text(json.dumps({"cwd": str(self.repo)}) + "\n", encoding="utf-8")
        os.utime(project / "old.jsonl", (1, 1))
        (project / "new.jsonl").write_text(
            json.dumps({"cwd": str(self.repo)}) + "\n" + json.dumps({"cwd": str(work / "sub")}) + "\n{broken\n",
            encoding="utf-8")
        manifest = rt.snapshot_all(self.repo, "c" * 32, claude_config=claude, state_dir=self.state)
        self.assertEqual(str(work / "sub"), manifest["session_cwd"])
        self.assertEqual(work.resolve(), Path(manifest["session_worktree"]).resolve())
        line = rt.notice_suffix(manifest)
        self.assertIn("재시작 전 작업 위치: " + str(work / "sub"), line)
        self.assertIn("branch feature", line)
        self.assertIn("snapshot " + rt.REF_PREFIX + "c" * 32 + "/", line)
        self.assertIn(rt.MANIFEST, line)
        self.assertNotIn("\n", line)

    def test_the_notice_names_each_changed_worktree_when_the_session_was_at_the_root(self):
        # E2E 2026-11-17: a Bash cd is not in the transcript, so the session cwd is the
        # launch folder; the changed fork worktree must still be named, not only counted.
        work = self.worktree("feat", "feat")
        (work / "b.txt").write_text("x\n", encoding="utf-8")
        self.worktree("idle", "idle")
        claude = self.root / "claude"
        project = claude / "projects" / rt.re.sub(r"[^A-Za-z0-9._-]", "-", str(self.repo.resolve()).rstrip("\\/"))
        project.mkdir(parents=True)
        (project / "s.jsonl").write_text(json.dumps({"cwd": str(self.repo)}) + "\n", encoding="utf-8")
        manifest = rt.snapshot_all(self.repo, "9" * 32, claude_config=claude, state_dir=self.state,
                                   exclude=[self.repo / ".claude" / "projects"])
        line = rt.notice_suffix(manifest)
        fork = next(row for row in manifest["worktrees"] if Path(row["path"]).resolve() == work.resolve())
        self.assertIn("재시작 전 작업 위치: " + str(self.repo), line)
        self.assertIn("변경이 남은 워크트리: " + fork["path"] + " (branch feat, HEAD " + fork["head"][:10]
                      + ", snapshot " + fork["snapshot_ref"] + ")", line)
        self.assertNotIn("idle", line)
        self.assertNotIn("\n", line)

    def test_the_notice_marks_skipped_and_failed_worktrees_and_caps_the_list(self):
        rows = [{"path": f"G:/r/w{i}", "branch": f"b{i}", "head": "0123456789abcdef",
                 "snapshot_ref": rt.REF_PREFIX + f"x/w{i}"} for i in range(6)]
        rows.append({"path": "G:/r/big", "branch": "big", "head": "f" * 40, "snapshot_ref": None,
                     "skipped": "too large to snapshot (9000 untracked files, 1 bytes)"})
        line = rt.notice_suffix({"worktrees": [rows[-1], *rows[:6]], "session_cwd": None})
        self.assertIn("G:/r/big (branch big, HEAD ffffffffff, snapshot skipped: too large to snapshot)", line)
        self.assertIn(" 외 2곳", line)
        failed = rt.notice_suffix({"worktrees": [{"path": "G:/r/f", "branch": None, "head": "a" * 40,
                                                  "snapshot_ref": None, "error": "TakeoverError: boom"}]})
        self.assertIn("G:/r/f (detached HEAD, HEAD aaaaaaaaaa, snapshot failed)", failed)

    def test_only_the_newest_restarts_keep_snapshot_refs(self):
        (self.repo / "a.txt").write_text("dirty\n", encoding="utf-8")
        with patch.object(rt, "KEEP_RESTARTS", 2):
            for number in range(4):
                stamp = str(1_800_000_000 + number)
                with patch.dict(os.environ, {"GIT_COMMITTER_DATE": stamp + " +0000"}):
                    with patch.dict(rt._IDENTITY, {"GIT_COMMITTER_NAME": "ciel-runtime"}):
                        rt.snapshot_all(self.repo, str(number) * 32, claude_config=None, state_dir=self.state)
        refs = git(self.repo, "for-each-ref", "--format=%(refname)", rt.REF_PREFIX).splitlines()
        self.assertEqual({"2" * 32, "3" * 32}, {ref[len(rt.REF_PREFIX):].split("/")[0] for ref in refs})

    def test_cli_state_inside_the_repository_is_never_snapshotted(self):
        # Celly: the home folder is the repository; .claude and .claude.json are not ignored there.
        (self.repo / ".claude" / "projects").mkdir(parents=True)
        (self.repo / ".claude" / "projects" / "s.jsonl").write_text("{}\n", encoding="utf-8")
        (self.repo / ".claude.json").write_text("{}", encoding="utf-8")
        (self.repo / "work.txt").write_text("real work\n", encoding="utf-8")
        manifest = rt.snapshot_all(self.repo, "e" * 32, claude_config=self.repo / ".claude", state_dir=self.state,
                                   exclude=[self.repo / ".claude", self.repo / ".claude.json", self.root / "elsewhere"])
        main = manifest["worktrees"][0]
        self.assertEqual(1, main["untracked"])
        files = git(self.repo, "ls-tree", "-r", "--name-only", main["snapshot_ref"]).splitlines()
        self.assertEqual([".gitignore", "a.txt", "work.txt"], sorted(files))

    def test_an_oversized_untracked_set_is_recorded_not_snapshotted(self):
        for name in ("x1.bin", "x2.bin"):
            (self.repo / name).write_text("x", encoding="utf-8")
        with patch.object(rt, "MAX_UNTRACKED_FILES", 1):
            manifest = rt.snapshot_all(self.repo, "f" * 32, claude_config=None, state_dir=self.state)
        main = manifest["worktrees"][0]
        self.assertIsNone(main["snapshot_ref"])
        self.assertIn("too large to snapshot (2 untracked files", main["skipped"])
        self.assertEqual("", git(self.repo, "for-each-ref", rt.REF_PREFIX))

    def test_a_folder_outside_git_records_a_note_and_no_error(self):
        plain = self.root / "plain"
        plain.mkdir()
        with patch.object(rt, "_find_root", lambda _folder: None):
            manifest = rt.snapshot_all(plain, "d" * 32, claude_config=None, state_dir=self.state)
        self.assertIsNone(manifest["repository"])
        self.assertEqual([], manifest["worktrees"])
        self.assertEqual("", rt.notice_suffix({"worktrees": []}))


if __name__ == "__main__":
    unittest.main()
