import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support.side_code_read import read_code, search_code
from ciel_runtime_support.work_plan_tree import WorkPlanError


class SideCodeReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'app.py').write_text('one\nAPI_KEY=abcdefghijk\nneedle here\n', encoding='utf8')
        (self.root / '.env').write_text('SECRET=value', encoding='utf8')
        self.env = patch.dict(os.environ, {'CIEL_RUNTIME_LAUNCH_CWD': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_reads_and_searches_bounded_redacted_workspace_text(self):
        result = read_code('src/app.py', offset=2, limit=2)
        self.assertEqual('src/app.py', result['path'])
        self.assertIn('API_KEY=‹hidden›', result['text'])
        self.assertIn('needle here', result['text'])
        found = search_code('needle', glob='*.py')
        self.assertEqual([('src/app.py', 3)], [(row['path'], row['line']) for row in found['matches']])

    def test_rejects_escape_absolute_secret_binary_and_symlink_paths(self):
        outside = self.root.parent / 'side-code-outside.txt'
        outside.write_text('outside', encoding='utf8')
        self.addCleanup(outside.unlink)
        (self.root / 'binary.bin').write_bytes(b'\xff')
        for value in ('../side-code-outside.txt', str(outside), '.env', '//server/share'):
            with self.subTest(value=value), self.assertRaises(WorkPlanError):
                read_code(value)
        with self.assertRaises(WorkPlanError):
            read_code('binary.bin')
        try:
            (self.root / 'escape').symlink_to(outside)
        except OSError:
            pass
        else:
            with self.assertRaises(WorkPlanError):
                read_code('escape')


if __name__ == '__main__':
    unittest.main()
