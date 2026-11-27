"""An event about a waiting Work item releases it or reports the check of its condition.

Andy 2026-11-27 (#162105, n2892): Eric reported in IT Hotline 162796 that SSH
card 162105 was already approved. n2892 waited on exactly that approval and
carried a release_hint lookup, but the side actor answered "update" without
checking it (the host's correction had told it "Do not call tools"), so the
item stayed blocked all day. The prompt now names the waiting items with the
rule, an answer that neither releases nor checks them is corrected once with
each item's lookup, and the generic correction keeps read-only lookups allowed.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks'
WAITING_ON = 'Daniel Yun SSH passkey approval on IT Hotline 162105 (vmi3610452 oneciel, req d353d85a)'


class SideWaitCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests' / 'side_wait_check_harness.mjs', path / 'test.mjs')
            for name in ('side-intake.js', 'texts.generated.js'):
                shutil.copyfile(HOOKS / name, path / name)
            done = subprocess.run(['node', str(path / 'test.mjs')], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        cls.result = json.loads(done.stdout)

    def test_the_prompt_names_the_waiting_item_the_event_is_about_with_its_lookup(self):
        row = self.result['unchecked_then_checked']
        self.assertEqual(['n2892'], [item['id'] for item in row['waitItems']])
        self.assertEqual(WAITING_ON, row['waitItems'][0]['waiting_on'])
        self.assertIn('ssh_access.status', row['waitItems'][0]['lookup'])
        self.assertTrue(row['waitRule'])

    def test_an_unchecked_answer_is_corrected_with_the_items_lookup_then_commits(self):
        row = self.result['unchecked_then_checked']
        self.assertEqual([{'to': 'actor-unchecked_then_checked', 'waitCheck': True, 'namesItem': True,
                           'namesLookup': True, 'namesOther': False, 'forbidsTools': False, 'allowsReads': True}],
                         row['sends'])
        self.assertEqual((1, []), (row['resolves'], row['failures']))
        # wait_checks is the host's evidence, never a router decision field.
        self.assertNotIn('wait_checks', row['decisionKeys'][0])

    def test_a_second_unchecked_answer_fails_and_names_the_item(self):
        row = self.result['unchecked_twice']
        self.assertEqual((1, 0), (len(row['sends']), row['resolves']))
        self.assertEqual(['invalid_answer'], [failure['category'] for failure in row['failures']])
        self.assertIn('side wait check required: n2892', row['failures'][0]['reason'])

    def test_a_met_condition_must_be_released(self):
        row = self.result['met_not_released']
        self.assertEqual(1, len(row['sends']))
        self.assertTrue(row['sends'][0]['waitCheck'])
        self.assertEqual([[{'id': 'n2892', 'waiting_on': WAITING_ON}]], row['released'])

    def test_a_release_or_a_reported_check_commits_without_correction(self):
        for label in ('released', 'checked'):
            row = self.result[label]
            self.assertEqual(([], 1, []), (row['sends'], row['resolves'], row['failures']), label)

    def test_a_shared_conversation_alone_names_no_waiting_item(self):
        row = self.result['other_thread']
        self.assertEqual(([], [], 1), (row['waitItems'], row['sends'], row['resolves']))

    def test_a_room_or_company_id_in_the_event_names_no_waiting_item(self):
        # Andy 2026-11-27 12:19Z: a pending reminder with no source message
        # matched 8 items, 5 of them only through the room or company id.
        self.assertEqual(['nRoot', 'nRequest'], self.result['reminder_targets'])

    def test_the_correction_for_a_missing_decision_json_keeps_read_only_lookups_allowed(self):
        row = self.result['prose_first']
        self.assertEqual(1, len(row['sends']))
        self.assertEqual((False, True), (row['sends'][0]['forbidsTools'], row['sends'][0]['allowsReads']))
        self.assertEqual((1, []), (row['resolves'], row['failures']))


if __name__ == '__main__':
    unittest.main()
