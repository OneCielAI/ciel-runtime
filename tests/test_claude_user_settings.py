import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import claude_user_settings as cus
from ciel_runtime_support.work_plan_tree import WorkPlanError


class ClaudeUserSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.env = {}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_defaults_when_files_are_absent(self):
        self.assertEqual({cus.LEFT_ARROW: True, cus.BACKGROUND: True}, cus.values(self.env, self.root))

    def test_left_arrow_keeps_unknown_keys_and_claude_format_and_leaves_no_lock(self):
        target = self.root / '.claude.json'
        target.write_text('{"projects":{"C:/a":{"keep":1},"c:/a":{"keep":2}},"이름":"값"}', encoding='utf8')
        self.assertFalse(cus.set_value(cus.LEFT_ARROW, 'off', env=self.env, home=self.root)[cus.LEFT_ARROW])
        text = target.read_text(encoding='utf8')
        stored = json.loads(text)
        self.assertEqual({'C:/a': {'keep': 1}, 'c:/a': {'keep': 2}}, stored['projects'])
        self.assertEqual('값', stored['이름'])
        self.assertIs(False, stored['leftArrowOpensAgents'])
        self.assertTrue(text.startswith('{\n  "projects"'))
        self.assertEqual(['.claude.json'], sorted(p.name for p in self.root.iterdir()))
        cus.set_value(cus.LEFT_ARROW, 'on', env=self.env, home=self.root)
        self.assertIs(True, json.loads(target.read_text(encoding='utf8'))['leftArrowOpensAgents'])

    def test_busy_claude_lock_is_never_taken_over(self):
        target = self.root / '.claude.json'
        target.write_text('{"a":1}', encoding='utf8')
        (self.root / '.claude.json.lock').mkdir()
        with patch.object(cus, 'LOCK_WAIT_SECONDS', 0.1), self.assertRaises(WorkPlanError):
            cus.set_value(cus.LEFT_ARROW, 'off', env=self.env, home=self.root)
        self.assertEqual('{"a":1}', target.read_text(encoding='utf8'))
        self.assertTrue((self.root / '.claude.json.lock').is_dir())

    def test_background_toggle_uses_user_settings_and_keeps_hooks(self):
        settings = self.root / '.claude' / 'settings.json'
        settings.parent.mkdir()
        settings.write_text('{"hooks":{"Stop":[{"hooks":[]}]}}', encoding='utf8')
        self.assertFalse(cus.set_value(cus.BACKGROUND, 'off', env=self.env, home=self.root)[cus.BACKGROUND])
        stored = json.loads(settings.read_text(encoding='utf8'))
        self.assertEqual({'Stop': [{'hooks': []}]}, stored['hooks'])
        self.assertIs(True, stored['disableAgentView'])
        self.assertFalse((self.root / '.claude.json').exists())
        self.assertTrue(cus.set_value(cus.BACKGROUND, 'on', env=self.env, home=self.root)[cus.BACKGROUND])
        self.assertNotIn('disableAgentView', json.loads(settings.read_text(encoding='utf8')))

    def test_unreadable_file_is_reported_and_never_overwritten(self):
        target = self.root / '.claude.json'
        target.write_text('{broken', encoding='utf8')
        self.assertIsNone(cus.values(self.env, self.root)[cus.LEFT_ARROW])
        with self.assertRaises(WorkPlanError):
            cus.set_value(cus.LEFT_ARROW, 'off', env=self.env, home=self.root)
        self.assertEqual('{broken', target.read_text(encoding='utf8'))
        self.assertFalse((self.root / '.claude.json.lock').exists())

    def test_paths_follow_claude_config_dir_and_legacy_rule(self):
        custom = self.root / 'custom'
        env = {'CLAUDE_CONFIG_DIR': str(custom)}
        self.assertEqual(custom / '.claude.json', cus.global_config_path(env, self.root))
        self.assertEqual(custom / 'settings.json', cus.user_settings_path(env, self.root))
        legacy = self.root / '.claude' / '.config.json'
        legacy.parent.mkdir()
        legacy.write_text('{"x":1}', encoding='utf8')
        self.assertEqual(legacy, cus.global_config_path({}, self.root))
        other = self.root / 'other-home'
        self.assertEqual(other / '.claude-custom-oauth.json',
                         cus.global_config_path({'CLAUDE_CODE_CUSTOM_OAUTH_URL': 'x'}, other))

    def test_rejects_unknown_keys_and_values(self):
        with self.assertRaises(WorkPlanError):
            cus.set_value('claude_theme', 'on', env=self.env, home=self.root)
        with self.assertRaises(WorkPlanError):
            cus.set_value(cus.LEFT_ARROW, 'maybe', env=self.env, home=self.root)


if __name__ == '__main__':
    unittest.main()
