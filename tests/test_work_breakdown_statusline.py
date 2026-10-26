import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT

NL = chr(10)
ESC = chr(27)


class BreakdownStatuslineTests(unittest.TestCase):
    def render(self, directory, session_id="s1", config=None):
        directory = Path(directory)
        (directory / "config.json").write_text(json.dumps(config or {}), encoding="utf-8")
        script = directory / "statusline.py"
        script.write_text(STATUSLINE_SCRIPT, encoding="utf-8")
        env = {k:v for k,v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "CIEL_CODE_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
        # The external board fallback reads CIEL_CODE_CONFIG_DIR (else APPDATA);
        # pin it to the temp directory so a real user board is never rendered.
        env.update(CIEL_RUNTIME_CONFIG_DIR=str(directory), CIEL_RUNTIME_WORKSPACE_STATE_DIR=str(directory),
                   CIEL_RUNTIME_STATUSLINE_ANSI="0", CIEL_CODE_CONFIG_DIR=str(directory / "ciel-code"))
        payload = {} if session_id is None else {"session_id": session_id}
        result = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write_plan(self, directory, doc):
        digest = hashlib.sha256(b"claude:s1").hexdigest()
        p = Path(directory) / "work-sessions" / digest / "work-plan.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def plan(self, directory):
        doc = tree.empty_document()
        tree.set_plan(doc, {"title": "Three problems", "children": [
            {"title": "A Cache", "children": [{"title": "TTL", "children": [{"title": "Verify expiry"}]}]},
            {"title": "B DAG", "children": [{"title": "Cycles"}]},
            {"title": "C Ledger", "children": [{"title": "Duplicates"}]}]})
        doc["phase"] = "executing"
        tree.update_node(doc, "n4", status="in_progress")
        return doc, self.write_plan(directory, doc)

    def write_board(self, directory, board, session_id="s1"):
        p = Path(directory) / "ciel-code" / "boards" / "sessions" / session_id / "active.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(board), encoding="utf-8")
        return p

    def test_current_hierarchy_branch_counts_and_active_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.plan(tmp)
            out = self.render(tmp)
            self.assertIn("[work executing] 0/3 Three problems", out)
            for label in ["A Cache 0/1", "B DAG 0/1", "C Ledger 0/1", "in_progress n4", "A Cache > TTL > Verify expiry"]:
                self.assertIn(label, out)
            self.assertIn(NL + "  in_progress", out)

    def test_breakdown_renders_one_branch_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.plan(tmp)
            out = self.render(tmp)
            self.assertEqual(out.splitlines(), [
                "[work executing] 0/3 Three problems",
                "  - A Cache 0/1",
                "  - B DAG 0/1",
                "  - C Ledger 0/1",
                "  in_progress n4 Three problems > A Cache > TTL > Verify expiry",
            ])
            self.assertNotIn(" | ", out)

    def test_every_branch_is_listed_without_a_count_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            titles = [f"Branch {index:02d}" for index in range(1, 10)]
            doc = tree.empty_document()
            tree.set_plan(doc, {"title": "Nine problems", "children": [
                {"title": title, "children": [{"title": title + " step"}]} for title in titles]})
            doc["phase"] = "executing"
            self.write_plan(tmp, doc)
            lines = self.render(tmp).splitlines()
            self.assertEqual(lines[0], "[work executing] 0/9 Nine problems")
            self.assertEqual(lines[1:10], ["  - " + title + " 0/1" for title in titles])
            self.assertTrue(lines[10].startswith("  pending "), lines)
            self.assertEqual(len(lines), 11)
            self.assertFalse(any("branches" in line for line in lines), lines)

    def test_finished_goal_reports_complete_on_its_own_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path = self.plan(tmp)
            for node_id in ("n4", "n6", "n8"):
                tree.update_node(doc, node_id, status="done", evidence="verified")
            path.write_text(json.dumps(doc), encoding="utf-8")
            # Completing the active goal returns the tree to the planning phase.
            self.assertEqual(self.render(tmp).splitlines(), [
                "[work planning] 3/3 Three problems",
                "  - A Cache 1/1",
                "  - B DAG 1/1",
                "  - C Ledger 1/1",
                "  complete",
            ])

    def test_other_missing_invalid_session_never_reads_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.plan(tmp)
            for session in ["s2", None, "../s1"]:
                self.assertEqual(self.render(tmp, session), "")

    def test_disabled_mode_hides_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.plan(tmp)
            self.assertEqual(self.render(tmp, config={"work_mode": False}), "")

    def test_terminal_controls_are_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, path = self.plan(tmp)
            doc["nodes"][1]["title"] = "Cache" + ESC + "[31m" + NL + "unsafe"
            doc["nodes"][3]["id"] = "n4" + ESC + "[2J" + chr(0x9b)
            path.write_text(json.dumps(doc), encoding="utf-8")
            out = self.render(tmp)
            self.assertNotIn(ESC, out)
            self.assertNotIn(chr(0x9b), out)
            self.assertIn("Cache", out)
            lines = out.splitlines()
            # A newline inside a title must not add a status-line row.
            self.assertEqual(len(lines), 5, lines)
            self.assertEqual(lines[1], "  - Cache [31m unsafe 0/1")
            self.assertTrue(lines[4].startswith("  in_progress n4 [2J "), lines)

    def test_snapshot_exposes_all_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc, _ = self.plan(tmp)
            tree.update_node(doc, "n4", status="done", evidence="expiry test passed")
            state = tree.snapshot(doc)
            self.assertEqual([(b["title"], b["done"], b["total"]) for b in state["breakdown"]],
                             [("A Cache", 1, 1), ("B DAG", 0, 1), ("C Ledger", 0, 1)])

    def test_external_board_fallback_lists_one_item_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [{"id": "i" + str(index), "title": "Item " + str(index), "status": "planning"}
                     for index in range(1, 8)]
            items[0]["status"] = "running"
            items[1]["status"] = "done"
            self.write_board(tmp, {"activeItem": "i1", "items": items,
                                   "steps": {"i1": [{"state": "done"}, {"state": "todo"}]}})
            self.assertEqual(self.render(tmp).splitlines(), [
                "[work]",
                "  >1/2 Item 1 <-",
                "  xdone Item 2",
                "  ~ready Item 3",
                "  ~ready Item 4",
                "  ~ready Item 5",
                "  ~ready Item 6",
                "  ~ready Item 7",
            ])

    def test_external_board_titles_are_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_board(tmp, {"activeItem": "a", "items": [
                {"id": "a", "title": "Red" + ESC + "[31m" + NL + "line" + chr(0x9b) + "2J", "status": "running"},
                {"id": "b", "title": "Bell" + chr(7) + chr(13) + "tail", "status": "paused"}]})
            out = self.render(tmp)
            for control in (ESC, chr(7), chr(13), chr(0x9b)):
                self.assertNotIn(control, out)
            self.assertEqual(out.splitlines(), [
                "[work]",
                "  >ready Red [31m line 2J <-",
                "  " + chr(124) + "ready Bell tail",
            ])

    def test_plan_takes_precedence_over_external_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.plan(tmp)
            self.write_board(tmp, {"items": [{"id": "a", "title": "Board item", "status": "running"}]})
            out = self.render(tmp)
            self.assertIn("[work executing]", out)
            self.assertNotIn("Board item", out)

    def test_external_board_other_session_is_not_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_board(tmp, {"items": [{"id": "a", "title": "Board item", "status": "running"}]}, "s2")
            self.assertEqual(self.render(tmp), "")

if __name__ == "__main__":
    unittest.main()
