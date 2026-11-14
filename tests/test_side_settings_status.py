"""`/btw_with_mcp status` (MCP work_side_settings) reports what is stored.

Before: it always answered "type":"btw_with_mcp", so with a stored type of
"session" (side lane on, unmarked events injected as session input) the
console showed the durable inbox lane. It now reports the stored type and the
effective sse_injection_mode, the same value the shared settings path shows.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_setting_values import injection_mode

TOKEN = 'x' * 40


class SideSettingsStatusTests(unittest.TestCase):
    def call(self, config, **args):
        with tempfile.TemporaryDirectory() as root:
            answer = dispatch_work_tool(
                1, 'work_side_settings', {'session_id': 'claude:fixture', '_host': {'token': TOKEN}, **args},
                load_config=lambda: config, state_dir=Path(root),
                respond=lambda _id, text, error=False: {'text': text, 'error': error},
                save_config=lambda value: config.update(value))
        self.assertFalse(answer['error'], answer)
        return json.loads(answer['text'])

    def config(self, side):
        value = {'work_mode': True, 'work_parallel_host': {'token': TOKEN}}
        if side is not None:
            value['work_side_intake'] = side
        return value

    def test_status_reports_the_stored_type_and_the_effective_mode(self):
        cases = (
            ({'enabled': True, 'type': 'session'}, True, 'session', 'session'),
            ({'enabled': True, 'type': 'btw_with_mcp'}, True, 'btw_with_mcp', 'btw_with_mcp'),
            ({'enabled': True}, True, 'btw_with_mcp', 'btw_with_mcp'),
            ({'enabled': False, 'type': 'btw_with_mcp'}, False, 'btw_with_mcp', 'session'),
            ({'enabled': False, 'type': 'session'}, False, 'session', 'session'),
            (None, False, 'btw_with_mcp', 'session'),
        )
        for side, enabled, kind, mode in cases:
            with self.subTest(side=side):
                config = self.config(side)
                self.assertEqual({'enabled': enabled, 'type': kind, 'sse_injection_mode': mode}, self.call(config))
                # The same effective mode every other surface projects.
                self.assertEqual(mode, injection_mode(config))

    def test_on_and_off_report_what_they_wrote(self):
        config = self.config({'enabled': True, 'type': 'session'})
        self.assertEqual({'enabled': True, 'type': 'btw_with_mcp', 'sse_injection_mode': 'btw_with_mcp'},
                         self.call(config, enabled=True))
        self.assertEqual({'enabled': True, 'type': 'btw_with_mcp'}, config['work_side_intake'])
        self.assertEqual({'enabled': False, 'type': 'btw_with_mcp', 'sse_injection_mode': 'session'},
                         self.call(config, enabled=False))
        self.assertEqual('session', injection_mode(config))


if __name__ == '__main__':
    unittest.main()
