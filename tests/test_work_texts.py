"""Work texts are data: one source, generated for the mod, editable without a restart (2026-11-19)."""
import re
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_texts
from ciel_runtime_support import work_workflow_prompts as prompts

HOOKS = Path(__file__).resolve().parents[1] / "ciel_runtime_support/claude_mods/ciel-work/hooks"


class WorkTextsTests(unittest.TestCase):
    def test_the_mod_defaults_file_is_generated_from_the_registry(self):
        generated = (HOOKS / work_texts.MOD_TEXTS_FILE).read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(work_texts.mod_texts_js(), generated)

    def test_the_mod_keeps_no_literal_copy_of_a_registry_text(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(HOOKS.glob("*.js"))
                           if path.name != work_texts.MOD_TEXTS_FILE)
        for name, text in work_texts.mod_defaults().items():
            self.assertNotIn(text[:60], source, name)
            self.assertIn("T('" + name + "'", source, name)

    def test_main_waits_for_host_notifications_without_polling(self):
        main = work_texts.TEXTS["mod_instructions"]
        for phrase in ("알림을 기다리기 위해", "work_parallel_status", "셸로 반복 조회하지", "턴을 끝냅니다"):
            self.assertIn(phrase, main)
        self.assertNotIn("알림을 기다리기 위해", work_texts.TEXTS["mod_fork_rules"])

    def test_ack_and_worker_guidance_cover_observed_failures(self):
        from ciel_runtime_support.side_intake_settings import DEFAULT_SIDE_INSTRUCTION

        self.assertIn('등록된 연동과 그 스키마만 사용', DEFAULT_SIDE_INSTRUCTION)
        for text in (prompts.DEFAULTS['side_source_instructions'],):
            for field in ('messages.work.get', 'version', 'UUID client_id', 'message_id', 'memo'):
                self.assertIn(field, text)
        for tool in ('Agent', 'Explore', 'TaskStop', 'SendMessage', 'work_parallel_report'):
            self.assertIn(tool, work_texts.TEXTS['mod_fork_rules'])

    def test_fork_rules_carry_the_complete_question_wait_contract(self):
        # The contract is its own text, appended only for a router that
        # advertises question_wait; the base rules never name the new fields.
        self.assertNotIn("waiting_on", work_texts.TEXTS["mod_fork_rules"])
        text = work_texts.TEXTS["mod_fork_question_wait"]
        for phrase in (
                "question_wait detail is exactly",
                "blocking_todos:[<assigned leaf item_id>]",
                "waiting_on",
                "release_hint:{condition, lookup, prompt?}",
                "1..120",
                "1..600",
                "1..400",
                "Never place waiting_on or release_hint at top level",
                "never pass item_id, answer, approved",
                "writes no board state, receipt, or lease/state transition",
                "same lease remains running/slotted",
                "Only start a wait once per lease",
                "waiting_on/release_hint are never an answer, approval",
                "verified by side/gate and resume resumes that original lease/fork",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_external_ci_wait_prompt_connects_the_worker_and_sse_actor(self):
        from ciel_runtime_support.work_workflow_tools import side_instruction

        worker = work_texts.TEXTS['mod_fork_question_wait']
        for phrase in ('GitHub PR', 'head SHA', 'run ID', '외부 결과를 기다리기 전에', '할당된 리프',
                       '실패·취소', 'read-only'):
            self.assertIn(phrase, worker)
        self.assertIn('side_external_wait_routing', prompts.DEFAULTS)
        side = prompts.DEFAULTS['side_external_wait_routing']
        for phrase in ('head SHA', 'run ID', 'release_waits', 'relay', 'ask_main', 'work_status', '실행 승인'):
            self.assertIn(phrase, side)
        gate = prompts.DEFAULTS['gate_actor']
        for phrase in ('head SHA', 'run ID', 'release_hint.condition'):
            self.assertIn(phrase, gate)
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            combined = side_instruction({}, state, '')
            self.assertNotIn('외부 실행 결과 전달 (필수 규칙)', combined)
            self.assertNotIn('명시 ACK', combined)
            self.assertIn('원문', work_texts.TEXTS['mod_side_default_instruction'])
            self.assertIn('side_source_instructions', prompts.DEFAULTS)
            prompts.set_prompt(state, 'side_external_wait_routing', 'custom ci routing rule',
                               prompts.fingerprint(side))
            self.assertIn('custom ci routing rule', side_instruction({}, state, ''))

    def test_no_bundled_skill_takes_a_registered_command_name(self):
        # Claude Code 2.1.330 real load (2026-11-23): a skill named work-mode made
        # $.command.register('/work-mode') throw "it is the plugin's
        # /ciel-work:work-mode", which skipped the whole session.start hook.
        register = (HOOKS / "register.js").read_text(encoding="utf-8")
        commands = set(re.findall(r"command\.register\(\{\s*name:\s*'([^']+)'", register))
        self.assertIn("work-mode", commands)
        skills = set()
        for path in (HOOKS.parent / "skills").glob("*/SKILL.md"):
            skills.add(path.parent.name)
            named = re.search(r"^name:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.M)
            if named:
                skills.add(named.group(1))
        self.assertTrue(skills)
        self.assertEqual(set(), commands & skills)

    def test_bundled_work_mode_skill_has_the_operating_guardrails(self):
        skill_root = HOOKS.parent / "skills" / "work-operations"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
                "/work-mode help",
                "P0 is a separate capacity-excluded lane",
                "P1`-`P5` use per-priority",
                "Reuse an existing lease/fork",
                "Submitted means pending main review",
                "waiting_input",
                "question_wait detail is exactly",
                "blocking_todos:[<assigned leaf item_id>]",
                "release_hint.lookup",
                "never an answer, approval, or execution authorization",
                "Read `/work-mode help`, `work_status`, and `work_parallel_status`",
                "Do not poll `work_status`, `work_parallel_status`",
                "run the existing read-only diagnostic",
                "put deterministic checks in the host",
                "Before waiting on external results (GitHub PR, CI/CD, deployment)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill)
        register = (HOOKS / "register.js").read_text(encoding="utf-8")
        self.assertIn("ciel-work:work-operations", register)
        self.assertIn("submitted waits for main review", register)

    def test_every_work_text_is_a_managed_prompt(self):
        with tempfile.TemporaryDirectory() as raw:
            names = {row["name"] for row in prompts.listing(Path(raw))["prompts"]}
        self.assertTrue(set(work_texts.TEXTS) <= names)

    def test_a_stored_mod_text_reaches_the_mod_with_a_new_fingerprint(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            before = prompts.mod_overrides(root)
            self.assertEqual({}, before["texts"])
            prompts.set_prompt(root, "mod_planning", "계획 승인 전에는 수정하지 않습니다.",
                               prompts.fingerprint(prompts.effective(root, "mod_planning")))
            prompts.set_prompt(root, "gate_recheck", "게이트 규칙", prompts.fingerprint(prompts.effective(root, "gate_recheck")))
            after = prompts.mod_overrides(root)
            # Only the mod's own texts travel to the mod.
            self.assertEqual({"mod_planning": "계획 승인 전에는 수정하지 않습니다."}, after["texts"])
            self.assertNotEqual(before["fingerprint"], after["fingerprint"])


if __name__ == "__main__":
    unittest.main()
