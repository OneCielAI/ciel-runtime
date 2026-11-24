"""Side and blocker-check instructions (Andy 2026-11-23).

A fresh side actor starts with no conversation, yet every instruction told it
that it inherited main's; it judged waits without main's recorded decisions,
answered "미조회" for most gates, and released n2582 on an approval narrower
than the wait. The instructions must hold for both session modes, send the
actor to the item records and main's transcript, ask one verdict per gate, and
keep a partial approval from releasing a wait.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_settings import DEFAULT_SIDE_INSTRUCTION
from ciel_runtime_support.work_workflow_prompts import DEFAULTS

SIDE_JS = Path(__file__).resolve().parents[1] / "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js"
INSTRUCTIONS = {"side": DEFAULT_SIDE_INSTRUCTION, "gate_actor": DEFAULTS["gate_actor"],
                "gate_recheck": DEFAULTS["gate_recheck"]}


class SideBackgroundPromptTests(unittest.TestCase):
    def test_no_instruction_claims_an_inherited_conversation_for_every_mode(self):
        for name, text in INSTRUCTIONS.items():
            with self.subTest(name=name):
                self.assertNotIn("그대로 물려받습니다", text)
                self.assertNotIn("그대로 물려받은 포크", text)
                self.assertNotIn("이미 컨텍스트에 있습니다", text)
                self.assertNotIn("물려받은 대화 기록에서", text)
                self.assertIn("fresh", text)

    def test_the_actor_instructions_name_the_session_mode_and_the_background_reads(self):
        for name in ("side", "gate_actor"):
            text = INSTRUCTIONS[name]
            with self.subTest(name=name):
                self.assertIn('session_mode가 "fork"이면', text)
                self.assertIn("main_transcript", text)
                self.assertIn("output_mode count", text)
                self.assertIn('".{0,300}<id>.{0,300}"', text)
                self.assertIn("Read로 통째로 읽지 마십시오", text)
                self.assertIn("root_id=<", text)
                self.assertIn("detail=true", text)
                self.assertIn("progress", text)

    def test_every_gate_gets_one_verdict_and_unqueried_is_not_one(self):
        for name in ("gate_actor", "gate_recheck"):
            text = INSTRUCTIONS[name]
            with self.subTest(name=name):
                self.assertIn("게이트마다 판정 하나", text)
                for verdict in ("해제:", "유지:", "확인 불가:"):
                    self.assertIn(verdict, text)
                self.assertIn('"미조회"는 판정이 아닙니다', text)
                self.assertNotIn('"미조회"라고 적고', text)

    def test_a_partial_approval_or_mains_narrowing_never_releases_a_wait(self):
        for name, text in INSTRUCTIONS.items():
            with self.subTest(name=name):
                self.assertIn("범위 전체", text)
                self.assertIn("범위를 좁히거나", text)

    def test_the_side_default_is_still_the_fork_prompt_byte_for_byte(self):
        source = SIDE_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(re.search(r"const PROMPT = `([^`]*)`", source).group(1), DEFAULT_SIDE_INSTRUCTION)
        self.assertNotIn("${", DEFAULT_SIDE_INSTRUCTION)


class SideWorkloadRecordsTests(unittest.TestCase):
    def test_the_claimed_workload_carries_mains_progress_records(self):
        # Real-engine run 2026-11-23: the linked leaf reached the actor with its
        # refs and note but no progress, where main records its decisions; the
        # router's projection never carried progress.
        from ciel_runtime_support import work_plan_tree as tree
        from ciel_runtime_support.side_workload import project

        doc = tree.empty_document()
        root = tree.add_node(doc, None, "goal")
        leaf = tree.add_node(doc, root, "leaf", refs=["ref-77"])
        tree.update_node(doc, leaf, progress="MAIN DECISION: only 120px is approved", note="scope note")
        node = next(row for row in project(doc)["nodes"] if row["id"] == leaf)
        self.assertEqual(("MAIN DECISION: only 120px is approved", "scope note", ["ref-77"]),
                         (node.get("progress"), node.get("note"), node.get("refs")))


if __name__ == "__main__":
    unittest.main()
