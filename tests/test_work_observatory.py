"""The /work-mode view Work & SSE pane, rendered from the real register.js under Node.

The host is scripted (tests/work_observatory_harness.mjs); no Claude Code, router or network.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_mode_launch as w

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("work_observatory_harness.mjs")


@unittest.skipUnless(NODE, "node is required to run the Work mod")
class WorkObservatoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="work-observatory-")
        cls.addClassCleanup(temp.cleanup)
        root = Path(temp.name)
        stub = root / "node_modules" / "claude-code"
        stub.mkdir(parents=True)
        (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        (stub / "package.json").write_text('{"name":"claude-code","type":"module","exports":"./index.js"}',
                                           encoding="utf-8")
        (stub / "index.js").write_text(
            "const store = new Map()\n"
            "export const atom = (key, init) => ({ key, init })\n"
            "export const read = async ($, a) => store.has(a.key.plugin + '/' + a.key.key)"
            " ? store.get(a.key.plugin + '/' + a.key.key) : (a.init ?? null)\n"
            "export const update = async ($, a, fn) => { const cur = store.has(a.key.plugin + '/' + a.key.key)"
            " ? store.get(a.key.plugin + '/' + a.key.key) : (a.init ?? null);"
            " store.set(a.key.plugin + '/' + a.key.key, fn(cur)) }\n", encoding="utf-8")
        for name in ("register.js", "side-intake.js"):
            shutil.copyfile(w.CLAUDE_MOD_DIR / "hooks" / name, root / name)
        shutil.copyfile(HARNESS, root / HARNESS.name)
        done = subprocess.run([NODE, HARNESS.name], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", timeout=60, check=False)
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.result = json.loads(done.stdout)

    def test_view_opens_work_and_sse_pane_with_colored_hierarchy(self):
        r = self.result
        self.assertIn('Work & SSE pane', r['view'])
        self.assertEqual('Work & SSE Observatory', r['opened']['title'])
        self.assertTrue(r['graphical'])
        work = '\n'.join(r['work']['texts'])
        self.assertIn('WORK 7 · executing · 1/3', work)
        self.assertIn('Slots 1/5', work)
        self.assertIn('P1', work)
        self.assertIn('  Mobile layout', ''.join(t for t in r['work']['texts'] if 'Mobile layout' in t).replace('[!] ', ''))
        self.assertIn('error', r['work']['colors'])      # blocked child
        self.assertIn('warning', r['work']['colors'])    # waiting child
        self.assertEqual(0, r['work']['hovers'])
        self.assertEqual('row', r['work']['headerDirection'])

    def test_sse_tab_shows_source_priority_state_and_p1_filter(self):
        side = '\n'.join(self.result['side']['texts'])
        self.assertIn('P1 side_mcp', side)
        self.assertIn('P3 quarantined', side)
        self.assertIn('Action: create → n9', side)
        self.assertIn('success', self.result['side']['colors'])
        self.assertIn('error', self.result['side']['colors'])
        p1 = '\n'.join(self.result['sideP1'])
        self.assertIn('request 1', p1)
        self.assertNotIn('request 3', p1)

    def test_every_setting_is_listed_across_pages_and_edited_by_kind(self):
        s = self.result['settings']
        self.assertEqual(10, len(s['first']))
        self.assertEqual(4, len(s['second']))
        self.assertEqual([['work_mode', 'off'], ['work_plan_approval_ask', 'ask'], ['work_parallel_max_active', '3']],
                         self.result['writes'])

    def test_side_model_picker_pages_catalog_and_applies_live(self):
        picker = self.result['picker']
        self.assertIn('✓ _Combo_0', picker['texts'])
        self.assertIn('  inherit — follow main model', picker['texts'])
        self.assertEqual('Models 1/2', picker['pages'])
        self.assertIn('  _Combo_11', picker['second'])
        picked = self.result['picked']
        self.assertEqual(['_Combo_11'], picked['write'])
        self.assertIn('next SSE', picked['toast'])
        self.assertTrue(picked['backToList'])

    def test_mobile_and_narrow_get_single_column_touch_layout(self):
        mobile = self.result['mobile']
        self.assertEqual({'Box', 'Text', 'Button'}, set(mobile['types']))
        self.assertEqual('column', mobile['headerDirection'])
        self.assertIn('Touch', mobile['hint'])
        self.assertEqual(6, mobile['settingsRows'])
        self.assertEqual(0, mobile['hovers'])
        self.assertIn('Touch', self.result['narrow']['hint'])

    def test_settings_command_opens_the_pane_settings_tab(self):
        self.assertIn('Settings tab', self.result['composer'])

    def test_view_opens_even_when_a_display_refresh_overlaps(self):
        race = self.result['race']
        self.assertTrue(race['opened'])
        self.assertIn('Work & SSE pane', race['text'])


if __name__ == '__main__':
    unittest.main()
