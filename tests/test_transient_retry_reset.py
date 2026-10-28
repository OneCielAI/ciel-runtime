import io
import unittest
import urllib.error
from ciel_runtime_support import transient_retry as retry

class ResetRecoveryTests(unittest.TestCase):
    def run_open(self, opener, config=None, budget=None):
        config = config if config is not None else {"gateway_retries": 2}
        with retry.request_scope(config, budget=budget or retry.TransientRetryBudget(2, 60)):
            return retry.open_with_transient_retry(opener, provider="cielairouter", config=config,
                log=lambda *_: None, sleep=lambda _: None)

    def test_direct_and_wrapped_reset_recover_before_response(self):
        for wrapped in (False, True):
            calls = []
            response = io.BytesIO(b"accepted")
            def opener():
                calls.append(1)
                if len(calls) == 1:
                    error = ConnectionResetError(10054, "fixture")
                    raise urllib.error.URLError(error) if wrapped else error
                return response
            self.assertEqual(b"accepted", self.run_open(opener).read())
            self.assertEqual(2, len(calls))

    def test_repeated_resets_stop_at_shared_budget(self):
        calls = []
        def opener():
            calls.append(1)
            raise ConnectionResetError(10054, "fixture")
        with self.assertRaises(ConnectionResetError):
            self.run_open(opener)
        self.assertEqual(3, len(calls))

    def test_disabled_retry_does_not_replay_reset(self):
        calls = []
        def opener():
            calls.append(1)
            raise ConnectionResetError(10054, "fixture")
        with self.assertRaises(ConnectionResetError):
            self.run_open(opener, {"gateway_retries": 0})
        self.assertEqual(1, len(calls))

    def test_unrelated_network_errors_are_not_replayed(self):
        calls = []
        def opener():
            calls.append(1)
            raise urllib.error.URLError("certificate verify failed")
        with self.assertRaises(urllib.error.URLError):
            self.run_open(opener)
        self.assertEqual(1, len(calls))

    def test_accepted_response_read_reset_does_not_reopen(self):
        calls = []
        class Response:
            headers = {}
            def read(self, size=-1):
                raise ConnectionResetError(10054, "read fixture")
        def opener():
            calls.append(1)
            return Response()
        response = self.run_open(opener)
        with self.assertRaises(ConnectionResetError):
            response.read()
        self.assertEqual(1, len(calls))

if __name__ == "__main__":
    unittest.main()
