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
    def test_default_side_is_connector_neutral_and_only_creates_new_work(self):
        text = DEFAULT_SIDE_INSTRUCTION
        for phrase in ('ACK + 메모', '새 작업 요청', '원문', 'ask_main', 'completed'):
            self.assertIn(phrase, text)
        self.assertNotIn('walkie_call_tool', text)
        self.assertNotIn('순서와 긴급도는 prioritize', text)

    def test_no_instruction_claims_an_inherited_conversation_for_every_mode(self):
        for name, text in INSTRUCTIONS.items():
            with self.subTest(name=name):
                self.assertNotIn("그대로 물려받습니다", text)
                self.assertNotIn("그대로 물려받은 포크", text)
                self.assertNotIn("이미 컨텍스트에 있습니다", text)
                self.assertNotIn("물려받은 대화 기록에서", text)
                if name != "side":
                    self.assertIn("fresh", text)

    def test_the_actor_instructions_name_the_session_mode_and_the_background_reads(self):
        for name in ("gate_actor",):
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
        self.assertIn('Work 직접 변경', INSTRUCTIONS['side'])
        for name in ('gate_actor', 'gate_recheck'):
            text = INSTRUCTIONS[name]
            with self.subTest(name=name):
                self.assertIn("범위 전체", text)
                self.assertIn("범위를 좁히거나", text)

    def test_a_stalled_fork_is_rechecked_and_told_to_main_once(self):
        # User 2026-11-24: a fork or main only waiting, or stopped without an
        # answer, is caught and told to main.
        for name in ("gate_actor", "gate_recheck"):
            text = INSTRUCTIONS[name]
            with self.subTest(name=name):
                self.assertIn("오래 멈춘 포크", text)
                self.assertIn("stall_notices", text)
                self.assertIn('"checked"', text)
                self.assertIn('"needed"', text)
                self.assertIn("조회하지 않고 넣지 마십시오", text)

    def test_a_silent_running_fork_is_told_to_main_once(self):
        # User 2026-11-24 (Prody n1200): a running fork whose transcript stopped
        # growing is caught and told to main through stall_notices kind silent.
        for name in ("gate_actor", "gate_recheck"):
            text = INSTRUCTIONS[name]
            with self.subTest(name=name):
                self.assertIn("응답 없이 멈춘 포크", text)
                self.assertIn('"silent"', text)
                self.assertIn("포크에 메시지를 보내거나 중지하지 마십시오", text)

    def test_a_status_or_spec_question_on_a_running_item_goes_to_its_fork(self):
        # User 2026-11-24: a status question or a spec change about an item a
        # fork is running goes to that fork, which answers or acts on it.
        text = INSTRUCTIONS["side"]
        self.assertNotIn("그 항목에 대한 단순한 상태 질문이나 상황 보고는 전달하지 않습니다", text)
        self.assertIn("담당 포크의 실행이 확인되면", text)
        self.assertIn('relay=true, no_reply=true', text)
        self.assertIn('메인에 메시지 ID와 핵심 요약', text)
        relay = DEFAULTS["side_running_fork_relay"]
        self.assertIn("상태 질문", relay)
        self.assertIn("사양 변경", relay)

    def test_the_side_default_is_still_the_fork_prompt_byte_for_byte(self):
        source = SIDE_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn("T('mod_side_default_instruction')", source)
        from ciel_runtime_support.work_texts import TEXTS
        self.assertEqual(TEXTS['mod_side_default_instruction'], DEFAULT_SIDE_INSTRUCTION)
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
