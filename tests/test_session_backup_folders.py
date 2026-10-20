"""Session backup: first-level folders chosen in the menu, and git worktrees as an option (default off)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import session_backup_menu
from ciel_runtime_support.session_backup_cli import BackupContext, run_backup_command
from ciel_runtime_support.session_backup_collect import (
    is_linked_worktree,
    measure_tree,
)
from ciel_runtime_support.session_backup_service import load_settings
from ciel_runtime_support.session_backup_store import LocalTarget, find_snapshot


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.config, self.home, self.cwd = base / "ciel", base / "home", base / "work"
        for folder in (self.config, self.home, self.cwd):
            folder.mkdir()
        write(self.cwd / "notes.md", "plan\n")
        write(self.cwd / "data" / "big.bin", "x" * 1000)
        write(self.cwd / "src" / "data" / "keep.txt", "nested data folder stays\n")
        # A linked worktree (as git writes it) and a submodule (which is not a worktree).
        write(self.cwd / "wt-feature" / ".git", "gitdir: C:/repo/.git/worktrees/wt-feature\n")
        write(self.cwd / "wt-feature" / "feature.py", "print('feature')\n")
        write(self.cwd / "vendor" / "lib" / ".git", "gitdir: ../../.git/modules/vendor/lib\n")
        write(self.cwd / "vendor" / "lib" / "lib.py", "print('lib')\n")
        space = mock.patch("ciel_runtime_support.session_backup_cli.shutil.disk_usage", return_value=mock.Mock(free=1024 ** 4))
        space.start()
        self.addCleanup(space.stop)

    def context(self, outputs: list[str]) -> BackupContext:
        return BackupContext(self.config, self.home, self.home, {}, {"ciel_runtime": "test"}, cwd=lambda: self.cwd, output=outputs.append)

    def backup(self, *extra: str) -> dict:
        outputs: list[str] = []
        self.assertEqual(0, run_backup_command(["create", "--json", *extra], self.context(outputs)), outputs)
        return json.loads(outputs[-1])

    def snapshot_paths(self, result: dict) -> set[str]:
        _, manifest = find_snapshot(LocalTarget(self.config / "backups"), result["id"])
        return {f"{entry['root']}:{entry['path']}" for entry in manifest["entries"] if entry["root"] != "ciel" and entry["root"] != "ciel_ws"}


class DetectionTests(Base):
    def test_linked_worktree_is_told_apart_from_a_submodule_and_a_repo(self):
        self.assertTrue(is_linked_worktree(self.cwd / "wt-feature"))
        self.assertFalse(is_linked_worktree(self.cwd / "vendor" / "lib"))
        self.assertFalse(is_linked_worktree(self.cwd / "src"))

    def test_measure_follows_the_same_choices(self):
        full = measure_tree(self.cwd, ())[0]
        chosen = measure_tree(self.cwd, (), skip_top=("data",), skip_worktrees=True)[0]
        left_out = sum(path.stat().st_size for folder in ("data", "wt-feature") for path in (self.cwd / folder).rglob("*") if path.is_file())
        self.assertEqual(full - left_out, chosen)


class ChoiceTests(Base):
    def test_default_leaves_worktrees_out_and_keeps_every_folder(self):
        result = self.backup()
        paths = self.snapshot_paths(result)
        self.assertIn("cwd:data/big.bin", paths)
        self.assertIn("cwd:vendor/lib/lib.py", paths)  # submodules stay
        self.assertNotIn("cwd:wt-feature/feature.py", paths)
        self.assertEqual(["wt-feature"], result["left_out"]["worktrees"])
        self.assertEqual("off", result["left_out"]["worktree_mode"])
        self.assertEqual(0, result["not_backed_up_count"])  # a choice is not a missing file

    def test_menu_choices_drive_the_next_backup(self):
        rows, values = session_backup_menu.panel_rows(self.config, self.cwd)
        self.assertIn("folder:data", values)
        self.assertIn("  [x] data  [backed up]", rows)
        self.assertIn("  [ ] wt-feature  [git worktree · follows Git worktrees]", rows)
        self.assertEqual(["data is left out of this folder's backups (this first-level folder only, not same-named ones deeper)."],
                         session_backup_menu.apply(self.config, self.cwd, "folder:data"))
        lines = session_backup_menu.apply(self.config, self.cwd, "worktrees")
        self.assertTrue(lines[0].startswith("Git worktrees: inside"), lines)
        rows, _ = session_backup_menu.panel_rows(self.config, self.cwd)
        self.assertIn("  [ ] data  [left out]", rows)
        self.assertIn("  [x] wt-feature  [git worktree · follows Git worktrees]", rows)

        result = self.backup()
        paths = self.snapshot_paths(result)
        self.assertNotIn("cwd:data/big.bin", paths)
        self.assertIn("cwd:src/data/keep.txt", paths)  # only the first-level folder was chosen out
        self.assertIn("cwd:wt-feature/feature.py", paths)
        self.assertEqual(["data"], result["left_out"]["folders"])

        self.assertEqual(["data is backed up again."], session_backup_menu.apply(self.config, self.cwd, "folder:data"))
        self.assertIn("cwd:data/big.bin", self.snapshot_paths(self.backup()))

    def test_menu_shows_what_was_left_out_after_a_backup(self):
        session_backup_menu.apply(self.config, self.cwd, "folder:data")
        with mock.patch.object(session_backup_menu, "run_backup", return_value={"ok": True, **self.backup()}):
            lines = session_backup_menu.apply(self.config, self.cwd, "now", output=lambda _text: None)
        self.assertIn("Left out by your choice: 1 folder(s): data", lines)
        self.assertIn("Git worktrees: off; 1 left out", lines)

    def test_restore_replaces_read_only_files(self):
        import os
        import stat

        locked = self.cwd / "src" / "locked.txt"
        write(locked, "original\n")
        result = self.backup()
        os.chmod(locked, stat.S_IREAD)  # git keeps its object files like this
        self.addCleanup(lambda: os.chmod(locked, stat.S_IREAD | stat.S_IWRITE))
        outputs: list[str] = []
        self.assertEqual(0, run_backup_command(["restore", result["id"], "--json", "--no-safety"], self.context(outputs)), outputs)
        self.assertEqual("original\n", locked.read_text(encoding="utf-8"))

    def test_measure_folder_sizes_from_the_menu(self):
        lines = session_backup_menu.apply(self.config, self.cwd, "measure-folders", output=lambda _text: None)
        self.assertTrue(lines[0].startswith("Measured "), lines)
        rows, _ = session_backup_menu.panel_rows(self.config, self.cwd)
        self.assertTrue(any(row.startswith("  [x] data  [backed up · 0.0 MB") for row in rows), rows)
        self.assertTrue(any(row.startswith("Measure folder sizes  [measured ") for row in rows))


@unittest.skipUnless(shutil.which("git"), "needs git")
class OutsideWorktreeTests(Base):
    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd, check=True,
                       capture_output=True, text=True)

    def test_all_backs_up_worktrees_elsewhere_and_restores_them_in_place(self):
        shutil.rmtree(self.cwd / "wt-feature")
        self.git("init", "-q", cwd=self.cwd)
        self.git("add", "notes.md", cwd=self.cwd)
        self.git("commit", "-q", "-m", "init", cwd=self.cwd)
        outside = Path(self.tmp.name) / "outside-wt"
        self.git("worktree", "add", "-q", str(outside), cwd=self.cwd)
        write(outside / "work-in-progress.txt", "elsewhere\n")

        paths = self.snapshot_paths(self.backup())
        self.assertFalse(any(path.startswith("worktree-") for path in paths))  # default off

        result = self.backup("--worktrees", "all")
        paths = self.snapshot_paths(result)
        self.assertIn("worktree-1:work-in-progress.txt", paths)
        self.assertEqual([str(outside)], [str(Path(p)) for p in result["left_out"]["outside_worktrees"]])

        (outside / "work-in-progress.txt").unlink()
        outputs: list[str] = []
        self.assertEqual(0, run_backup_command(["restore", result["id"], "--json", "--no-safety"], self.context(outputs)), outputs)
        self.assertEqual("elsewhere\n", (outside / "work-in-progress.txt").read_text(encoding="utf-8"))
        self.assertIn("worktree-1", json.loads(outputs[-1])["roots"])
        settings = load_settings(self.config)
        self.assertNotIn("folders", settings)  # a CLI flag does not change the saved choice


if __name__ == "__main__":
    unittest.main()
