"""The terminal Observatory Client labels side receipts with their real state.

Walkie 2026-11-17: every row with a recorded side failure read "failed", although
the router was still retrying it or it was waiting for main's compaction. Only a
quarantined (terminal) row is "failed" now. tests/observatory_stage_harness.mjs
renders the packaged observatory.client.js under Node; no Claude Code.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_mode_launch as w

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("observatory_stage_harness.mjs")


@unittest.skipUnless(NODE, "node is required to run the Work mod")
class ObservatoryStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="observatory-stage-") as raw:
            root = Path(raw)
            (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
            shutil.copyfile(w.CLAUDE_MOD_DIR / "hooks" / "observatory.client.js", root / "observatory.client.js")
            shutil.copyfile(HARNESS, root / HARNESS.name)
            done = subprocess.run([NODE, HARNESS.name], cwd=root, capture_output=True, text=True,
                                  encoding="utf-8", timeout=60, check=False)
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.rows = json.loads(done.stdout)

    def test_only_a_quarantined_row_is_failed_at_every_width(self):
        for width in ("100", "40"):
            with self.subTest(width=width):
                rows = self.rows[width]
                self.assertIn("P3 retrying", rows["Retry row"])
                self.assertIn("P3 waiting", rows["Waiting row"])
                self.assertIn("P3 failed", rows["Terminal row"])
                self.assertIn("P3 received", rows["Fresh row"])
                for summary in ("Retry row", "Waiting row", "Fresh row"):
                    self.assertNotIn("failed", rows[summary])

    def test_an_answered_row_stays_answered_after_an_earlier_retry(self):
        for width in ("100", "40"):
            with self.subTest(width=width):
                self.assertIn("P3 answered", self.rows[width]["Answered row"])


if __name__ == "__main__":
    unittest.main()
