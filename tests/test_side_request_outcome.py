import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_request_outcome import peek, record, take


class SideRequestOutcomeTests(unittest.TestCase):
    def test_peek_is_nondestructive_and_checks_generation_start_and_ttl(self):
        from unittest.mock import patch
        clock = 'ciel_runtime_support.side_request_outcome.time.time'
        with tempfile.TemporaryDirectory() as raw:
            args = (Path(raw), 'claude:parent', 'agent-1', 'receipt-1')
            with patch(clock, return_value=10000):
                record(*args, {'code':'billing_error'})
            with patch(clock, return_value=10001):
                self.assertIsNone(peek(*args, not_before=10001))
                self.assertEqual('billing_error', peek(*args, not_before=9999)['code'])
                self.assertEqual('billing_error', peek(*args, not_before=9999)['code'])
            with patch(clock, return_value=13601):
                self.assertIsNone(peek(*args, not_before=9999))
    def test_record_is_bounded_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            record(root,'claude:parent','agent-1','receipt-1',{'status':404,'code':'model_not_found',
                'terminal_reason':'no_executable_targets','pool_size':0,'attempted':0,
                'effective_model':'API KEYS - SSE','message':'x'*1000,'ignored':'secret'})
            row=take(root,'claude:parent','agent-1','receipt-1')
            self.assertEqual(404,row['status'])
            self.assertEqual('no_executable_targets',row['terminal_reason'])
            self.assertEqual(300,len(row['message']))
            self.assertNotIn('ignored',row)
            self.assertIsNone(take(root,'claude:parent','agent-1','receipt-1'))
            record(root,'claude:parent','agent-1','receipt-2',{'status':500})
            self.assertIsNone(take(root,'claude:parent','agent-1','receipt-1'))
            self.assertEqual(500,take(root,'claude:parent','agent-1','receipt-2')['status'])
            record(root,'claude:parent','http-agent','receipt-3',{'status':404,'terminal_reason':'no_executable_targets'})
            recovered=take(root,'claude:parent','','receipt-3')
            self.assertEqual('no_executable_targets',recovered['terminal_reason'])
            # Native hook agent ids and routed HTTP agent ids differ in the real
            # host. A non-empty hook spelling must still recover the exact
            # parent+receipt outcome when its direct hash does not exist.
            record(root,'claude:parent','http-agent','receipt-4',{'status':404,'terminal_reason':'no_executable_targets'})
            recovered=take(root,'claude:parent','hook-agent','receipt-4')
            self.assertEqual('no_executable_targets',recovered['terminal_reason'])


if __name__ == '__main__':
    unittest.main()
