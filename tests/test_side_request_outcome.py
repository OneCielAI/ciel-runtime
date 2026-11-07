import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_request_outcome import record, take


class SideRequestOutcomeTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
