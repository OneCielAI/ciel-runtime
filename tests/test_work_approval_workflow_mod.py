"""Approval workflow in side-intake.js (node harness, no Claude Code needed).

An approval event of a registered request is settled by the host through
work_side_workflow_event with the thread the host read itself; no side fork
starts, nothing is resolved by a model, and only an applied approval sends the
one start notice and wakes the fork pump.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SideWorkflowHarnessTests(unittest.TestCase):
    def run_harness(self, mode):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests/side_workflow_harness.mjs', path / 'test.mjs')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js', path / 'side-intake.js')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js', path / 'texts.generated.js')
            done = subprocess.run(['node', str(path / 'test.mjs'), mode], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        return json.loads(done.stdout)

    def test_a_verified_approval_is_settled_by_the_host_without_a_fork(self):
        row = self.run_harness('approved')
        self.assertEqual(0, row['spawns'])
        self.assertEqual(0, row['resolves'])
        self.assertEqual({'receipt': True, 'generation': True}, row['settleHost'])
        # The registered request message alone, read once (never the thread).
        self.assertEqual(('verified_request', 1), (row['sourceContext'], row['approvals']))
        self.assertEqual(['walkie_read_messages:9099'], row['walkie'])
        self.assertEqual(1, row['replies'])
        self.assertEqual(1, row['approvedCalls'])
        self.assertEqual(['Work workflow: n5 approved'], row['logs'])

    def test_a_refused_approval_sends_nothing_and_starts_nothing(self):
        row = self.run_harness('refused')
        self.assertEqual((0, 0, 0, 0), (row['spawns'], row['resolves'], row['replies'], row['approvedCalls']))
        self.assertIn('not applied', row['logs'][0])


class ModRegistryTests(unittest.TestCase):
    def test_host_only_workflow_tool_is_listed_everywhere(self):
        register = (ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js').read_text(encoding='utf-8')
        start = register.index('const HOST_ONLY_SIDE_TOOLS')
        self.assertIn("'work_side_workflow_event'", register[start:register.index('.map(', start)])
        from ciel_runtime_support.work_mode_launch import WORK_TOOL_NAMES
        for name in ('work_side_workflow_event', 'work_proposal_submit', 'work_approval_requested',
                     'work_proposal_status', 'work_workflow_resolve', 'work_workflow_delegate'):
            self.assertIn(name, WORK_TOOL_NAMES)
        for test in ('work.test.ts', 'workflow.test.ts'):
            text = (ROOT / 'ciel_runtime_support/claude_mods/ciel-work/tests' / test).read_text(encoding='utf-8')
            replies = text[text.index('const HOST_REPLIES'):text.index('\n}\n', text.index('const HOST_REPLIES'))]
            self.assertIn('work_side_workflow_event', replies, test)
        gate = (ROOT / 'ciel_runtime_support/claude_mods/ciel-work-gate/hooks/register.js').read_text(encoding='utf-8')
        self.assertIn("'work_proposal_status'", gate[gate.index('const ROUTER_ALLOWED'):gate.index('\n', gate.index(
            'const ROUTER_ALLOWED'))])
        self.assertNotIn("'work_proposal_submit'", gate[gate.index('const ROUTER_ALLOWED'):gate.index('\n', gate.index(
            'const ROUTER_ALLOWED'))])


if __name__ == '__main__':
    unittest.main()
