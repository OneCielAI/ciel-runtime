"""The R2 fake-clock helpers measure what they claim (their own evidence before pty uses them)."""
import unittest
import urllib.error

from ciel_runtime_support import transient_retry as retry
from transient_retry_arrival_helpers import (ArrivalClock, Rejecting503, assert_answer_before_bound,
                                             default_scopes_on, window_on)


class ArrivalHelperTests(unittest.TestCase):
    def run_open(self, clock, opener, arrival):
        with window_on(420.0), default_scopes_on(clock):
            with retry.request_scope({}, "cielairouter", arrival_started=arrival):
                try:
                    retry.open_with_transient_retry(opener, provider="cielairouter", config={},
                                                    log=lambda *_: None, sleep=clock.sleep)
                except (urllib.error.HTTPError, TimeoutError, RuntimeError) as error:
                    getattr(error, "close", lambda: None)()

    def test_slow_pre_open_with_arrival_stamp_answers_before_the_bound(self):
        clock = ArrivalClock()
        arrival = clock()
        clock.advance(200.0)  # slow pre-open phase (bridge work before the first open)
        opener = Rejecting503(clock, seconds_per_attempt=88.0)
        self.run_open(clock, opener, arrival)
        assert_answer_before_bound(self, clock.now, arrival)
        self.assertGreaterEqual(len(opener.opens), 2)

    def test_the_helper_detects_a_scope_that_lost_the_arrival_stamp(self):
        # Fail-before shape of R2: without the stamp, the scope's arrival is its own
        # construction, so 200 s of pre-open work plus the window pass 540 s.
        clock = ArrivalClock()
        arrival = clock()
        clock.advance(200.0)
        opener = Rejecting503(clock, seconds_per_attempt=88.0)
        self.run_open(clock, opener, None)
        with self.assertRaises(AssertionError):
            assert_answer_before_bound(self, clock.now, arrival)

    def test_success_after_retries_is_supported(self):
        clock = ArrivalClock()

        class Response:
            def close(self):
                pass

        opener = Rejecting503(clock, seconds_per_attempt=10.0, succeed_after=2, response=Response)
        with window_on(420.0), default_scopes_on(clock):
            with retry.request_scope({}, "cielairouter", arrival_started=clock()):
                result = retry.open_with_transient_retry(opener, provider="cielairouter", config={},
                                                         log=lambda *_: None, sleep=clock.sleep)
        result.close()
        self.assertEqual(3, len(opener.opens))


if __name__ == "__main__":
    unittest.main()
