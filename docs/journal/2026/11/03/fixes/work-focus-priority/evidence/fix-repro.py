"""Deterministic reproduction of the Kobi focus stall, run against the fixed tree.

Isolated temporary Work board and parallel ledger; no router, session, network or
real config is touched. Prints one JSON document.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

for key in [k for k in os.environ if k.startswith("CIEL_RUNTIME_")]:
    os.environ.pop(key, None)

ROOT = Path(__file__).resolve()
while not (ROOT / "ciel_runtime_support" / "work_plan_tree.py").is_file():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support import runtime_paths  # noqa: E402
from ciel_runtime_support.work_discovery import discovery_hint  # noqa: E402
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore  # noqa: E402
from ciel_runtime_support.work_plan_store import WorkPlanStore  # noqa: E402


def candidate(name):
    return dict(item_id=name, priority="P2", created_at=10, approved=True, focused=True,
                dependencies_done=True, plan_version=1, usage=.05, role="worker", mode="fork")


def main():
    with tempfile.TemporaryDirectory(prefix="focus-priority-repro-") as tmp:
        tmp = Path(tmp)
        (tmp / "cfg").mkdir()
        board = WorkPlanStore(tmp / "board.json")

        def plan(doc):
            focused = tree.add_subtree(doc, None, {"title": "Focused question", "children": [
                {"title": "Phase", "children": [{"title": "Needs approval"}]}]})
            other = tree.add_subtree(doc, None, {"title": "Unrelated approved goal", "children": [
                {"title": "Phase", "children": [{"title": "runnable research"}]}]})
            tree.set_phase(doc, "executing", focused)
            tree.set_phase(doc, "executing", other)
            return focused, other

        focused, other = board.transaction(plan)[1]
        ids = {n["title"]: n["id"] for n in board.read()["nodes"]}

        # Kobi's shape: the model focuses a question item; its only step waits on a person,
        # while an unrelated approved goal still has runnable work.
        board.transaction(lambda d: tree.set_focus(d, [focused], user_requested=True))
        board.transaction(lambda d: tree.update_node(d, ids["Needs approval"], waiting_on="user approval"))
        doc = board.read()
        resume = tree.continuation(doc)

        with patch.object(runtime_paths, "CONFIG_DIR", tmp / "cfg"):
            hint = discovery_hint({"work_discovery": {"enabled": True}}, tree.snapshot(doc))
            parallel = WorkParallelStore(tmp / "cfg" / "work-parallel.json")
            policy = parallel.read()["policy"]
            parallel.reserve([candidate("p2-a")], scope="claude:repro", expected_version=0)
            parallel.reserve([candidate("p2-b")], scope="claude:repro",
                             expected_version=parallel.read()["version"])
            try:
                parallel.reserve([candidate("p2-c")], scope="claude:repro",
                                 expected_version=parallel.read()["version"])
                third_refused = False
            except WorkParallelError:
                third_refused = True

        # Finishing the focused step releases focus without an explicit clear.
        board.transaction(lambda d: tree.update_node(d, ids["Needs approval"], status="done",
                                                    evidence="verified", waiting_on=""))
        released = board.read()

        print(json.dumps({
            "focus_node": focused,
            "unrelated_runnable_leaf": ids["runnable research"],
            "after_focus": {
                "actionable": resume["actionable"],
                "next": resume["next"]["title"] if resume["next"] else None,
                "next_root": resume["next"]["root_id"] if resume["next"] else None,
                "held": resume["held"],
                "waiting": [row["id"] for row in resume["waiting"]],
            },
            "discovery_with_focus": {"enabled": hint.get("enabled", False),
                                     "reason": hint.get("reason", "")},
            "parallel_default": {"enabled": policy["enabled"], "max_active": policy["max_active"],
                                 "reserved_without_usage_observation": bool(parallel.read()["leases"]),
                                 "third_non_p1_refused": third_refused,
                                 "active_slots": sum(r["slot"] for r in parallel.read()["leases"].values())},
            "focus_auto_released": released["focus_ids"] == [],
            "after_release_next": (tree.continuation(released)["next"] or {}).get("title"),
        }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
