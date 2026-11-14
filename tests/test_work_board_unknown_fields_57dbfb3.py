"""R2 option B measurement: how a real 57dbfb3 Work board handles added fields.

Design evidence only, no product change. A real 57dbfb3 WorkPlanStore runs in
a separate process from a git-archive extract. On a board this build wrote, the
test adds one unknown root-level field and one unknown field on a node. It then
measures:
- whether the 57dbfb3 read rejects the document, drops the fields or keeps them;
- whether one real 57dbfb3 board write preserves them in the raw file.
The measured values are printed first; the assertions state what was measured.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import WorkPlanStore

ROOT_EXTRA = "r2_session_attachments"
NODE_EXTRA = "r2_lease_owner"

BETA_DRIVER = r"""
import json, sys
from pathlib import Path
root, board, item = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(root))
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import WorkPlanStore
ROOT_EXTRA, NODE_EXTRA = "r2_session_attachments", "r2_lease_owner"

def node_of(doc):
    return next(row for row in doc["nodes"] if row["id"] == item)

def has_root(doc):
    return ROOT_EXTRA in doc

def has_node(doc):
    return NODE_EXTRA in node_of(doc)

store = WorkPlanStore(board)
out = {"beta_tree": str(Path(tree.__file__).resolve())}
raw_before = json.loads(board.read_text(encoding="utf-8"))
out.update(root_raw_before=has_root(raw_before), node_raw_before=has_node(raw_before))
try:
    read_before = store.read()
except Exception as error:
    out["read_error"] = type(error).__name__ + ": " + str(error)[:300]
    print(json.dumps(out))
    raise SystemExit(0)
out.update(root_read_before=has_root(read_before), node_read_before=has_node(read_before),
           version_before=read_before["version"])

def mutate(doc):
    node_of(doc)["progress"] = "beta wrote"

try:
    written, _ = store.transaction(mutate, expected_version=read_before["version"])
except Exception as error:
    out["write_error"] = type(error).__name__ + ": " + str(error)[:300]
    print(json.dumps(out))
    raise SystemExit(0)
raw_after = json.loads(board.read_text(encoding="utf-8"))
out.update(root_raw_after=has_root(raw_after), node_raw_after=has_node(raw_after),
           version_after=written["version"], progress_after=node_of(raw_after).get("progress"))
print(json.dumps(out))
"""


class BetaBoardUnknownFieldMeasurement(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "work-plan.json"
        store = WorkPlanStore(self.path)
        doc = store.read()
        doc, root = store.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []}
            ]}, None), expected_version=doc["version"])
        self.item = next(row["id"] for row in doc["nodes"] if row["parent_id"] == root)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        # Shapes are illustrative of option B facts; values are not real processes.
        raw[ROOT_EXTRA] = {"owner": {"launcher_pid": 1234, "creation_identity": "identity-a"},
                           "heartbeat_at": 12.5, "fork_attempt_names": ["work-abc-a1"]}
        next(row for row in raw["nodes"] if row["id"] == self.item)[NODE_EXTRA] = {
            "launcher_pid": 1234, "creation_identity": "identity-a"}
        self.path.write_text(json.dumps(raw), encoding="utf-8")

    def beta_process(self):
        import test_work_parallel_rework_worktree as rw
        done = subprocess.run([sys.executable, "-I", "-B", "-c", BETA_DRIVER,
                               str(rw.beta_tree()), str(self.path), self.item],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(0, done.returncode, done.stderr[-1500:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_measure_57dbfb3_board_read_and_write_of_added_fields(self):
        result = self.beta_process()
        print("\n[R2-option-B] 57dbfb3 board added-field measurement:", json.dumps(result, sort_keys=True))
        self.assertIn("wp-beta-57dbfb3", result["beta_tree"])
        self.assertTrue(result["root_raw_before"] and result["node_raw_before"])
        self.assertNotIn("read_error", result)
        self.assertEqual((False, False), (result["root_read_before"], result["node_read_before"]))
        self.assertNotIn("write_error", result)
        self.assertEqual((False, False), (result["root_raw_after"], result["node_raw_after"]))
        self.assertEqual(result["version_before"] + 1, result["version_after"])
        self.assertEqual("beta wrote", result["progress_after"])


if __name__ == "__main__":
    unittest.main()
