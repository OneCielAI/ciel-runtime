"""R1 amendment 2: semantic recovery shares the dispatch's allowance.

The approved design in lead-rulings-independent-review-sse-retry-20261113.md:
- a recovery scope with gateway_retries=0 gets a bounded child of the active
  parent: same window/count deadline and start, same arrival bound, and only the
  parent's remaining retry attempts;
- it ignores ``parent.stopped`` from the terminal context 400, but never resets
  time or attempts; its attempts write back even on success/error/exception;
- an exposed parent gets one semantic recovery open but no transport replay;
- final notes state the request total (parent + child) and the actual stop;
- the explicitly marked committed compaction final open stays exempt.
"""
import io
import unittest
import urllib.error

from ciel_runtime_support import transient_retry as retry
from transient_retry_arrival_helpers import ArrivalClock, assert_answer_before_bound, default_scopes_on, window_on


def parent(clock, *, limit=3, deadline=420.0, attempts=0, window_mode=True,
           arrival=True, exposed=False):
    return retry.TransientRetryBudget(
        limit, deadline, clock=clock, started=clock(), attempts=attempts,
        window_mode=window_mode,
        arrival_started=clock() if arrival else None,
        hard_preoutput_seconds=retry.HARD_PREOUTPUT_SECONDS if arrival else None,
        stopped=True,  # the terminal semantic 400 stopped the original executor
        exposed=exposed, rand=lambda: 0.0,
    )


class Answer(io.BytesIO):
    """A non-SSE upstream answer the executor accepts as is."""

    def __init__(self):
        super().__init__(b'{"id":"ok"}')
        self.headers = {"content-type": "application/json"}
        self.status = 200


def recovery(clock, outer, open_once, *, inherit_arrival=True):
    config = {"gateway_retries": 0}
    seen = {}
    with retry.request_scope(config, "cielairouter", inherit_arrival=inherit_arrival) as child:
        seen["child"] = child
        try:
            seen["result"] = retry.open_with_transient_retry(
                open_once, provider="cielairouter", config=config,
                log=lambda *_: None, sleep=clock.sleep)
        except BaseException as error:  # the error and note are contract evidence
            seen["error"] = error
    return seen


class RecoverySharedAllowanceTests(unittest.TestCase):
    def test_transport_failure_on_recovery_retries_with_time_and_attempts_left(self):
        clock = ArrivalClock()
        outer = parent(clock, limit=3, attempts=1)
        opens = []

        def open_once():
            opens.append(clock())
            if len(opens) == 1:
                raise TimeoutError("transport")
            return Answer()

        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, open_once)
        child = seen["child"]
        self.assertEqual(b'{"id":"ok"}', seen["result"].read())
        self.assertEqual(2, len(opens))
        self.assertEqual(2, child.limit, "parent limit 3 minus one spent retry")
        self.assertEqual(outer.started, child.started)
        self.assertEqual(outer.deadline_seconds, child.deadline_seconds)
        self.assertEqual(outer.arrival_started, child.arrival_started)
        self.assertFalse(child.start_on_first_open, "no fresh first-open re-base")
        self.assertEqual(2, outer.attempts, "child's one retry wrote back")

    def test_recovery_after_window_end_fails_before_open(self):
        clock = ArrivalClock()
        outer = parent(clock)
        clock.advance(421.0)
        opened = []
        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, lambda: opened.append(1))
        self.assertEqual([], opened)
        self.assertEqual("deadline", seen["child"].stop_reason)
        self.assertIn("0 attempts", str(seen["error"]))
        self.assertFalse(hasattr(seen["error"], "_ciel_transient_retry_note"))

    def test_recovery_after_arrival_bound_fails_before_open(self):
        clock = ArrivalClock()
        outer = parent(clock, deadline=900.0)
        clock.advance(retry.HARD_PREOUTPUT_SECONDS + 1.0)
        opened = []
        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, lambda: opened.append(1))
        self.assertEqual([], opened)
        self.assertEqual("preoutput_bound", seen["child"].stop_reason)
        self.assertIn("0 attempts", str(seen["error"]))

    def test_recovery_never_extends_past_parent_window_end(self):
        clock = ArrivalClock()
        outer = parent(clock)
        clock.advance(419.5)
        opened = []

        def fail():
            opened.append(clock())
            raise TimeoutError("transport")

        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, fail)
        self.assertEqual(1, len(opened), "the minimum wait cannot admit a second open")
        self.assertLessEqual(clock(), outer.started + outer.deadline_seconds)
        self.assertEqual("deadline", seen["child"].stop_reason)
        self.assertEqual(0, outer.attempts, "an unadmitted retry is not charged")

    def test_no_attempts_left_keeps_the_single_semantic_open_without_retry(self):
        # The recovery is one body change, not a retry: with time left it still
        # opens once (as before R1 amendment 2); a transport failure then ends
        # honestly at attempt_ceiling with the request's retries, not 0.
        clock = ArrivalClock()
        outer = parent(clock, limit=2, attempts=2)
        opened = []

        def fail():
            opened.append(clock())
            raise TimeoutError("transport")

        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, fail)
        self.assertEqual(1, len(opened))
        self.assertEqual(0, seen["child"].limit)
        self.assertEqual("attempt_ceiling", seen["child"].stop_reason)
        self.assertIn("retried this request 2 time(s)", seen["error"]._ciel_transient_retry_note)
        self.assertEqual(2, outer.attempts)

    def real_dispatch(self, context_400_at, recovery_open):
        """A real default dispatch scope reaches a context 400, then one recovery scope."""
        clock = ArrivalClock()
        arrival = clock()
        context_400 = b'{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long"}}'

        def first_open():
            retry.bounded_timeout({}, "cielairouter", 300.0)
            clock.advance(context_400_at)
            raise urllib.error.HTTPError("http://fixture/v1/messages", 400, "x",
                                         {"content-type": "application/json"}, io.BytesIO(context_400))

        with window_on(420.0), default_scopes_on(clock):
            with retry.request_scope({}, "cielairouter", arrival_started=arrival) as dispatch:
                try:
                    retry.open_with_transient_retry(first_open, provider="cielairouter", config={},
                                                    log=lambda *_: None, sleep=clock.sleep)
                except urllib.error.HTTPError as error:
                    error.close()
                seen = recovery(clock, dispatch, lambda: recovery_open(clock))
        return clock, arrival, seen

    def test_real_dispatch_recovery_is_clipped_to_the_window_end(self):
        timeouts = []

        def recovery_open(clock):
            timeout = retry.bounded_timeout({"gateway_retries": 0}, "cielairouter", 300.0)
            timeouts.append(timeout)
            clock.advance(timeout)
            raise TimeoutError("timed out")

        clock, arrival, seen = self.real_dispatch(300.0, recovery_open)
        assert_answer_before_bound(self, clock(), arrival)
        self.assertAlmostEqual(120.0, timeouts[0], msg="min(300 s requested, 420 s window end, 540 s bound)")
        self.assertEqual([120.0], timeouts, "the clipped open spent the window; no retry after it")
        self.assertEqual(retry.WINDOW_RETRY_CEILING, seen["child"].limit)
        self.assertEqual("deadline", seen["child"].stop_reason)
        self.assertLessEqual(clock() - seen["child"].started, 420.0)

    def test_reviewer_shape_context_400_after_the_window_end_fails_before_open(self):
        # R1's reviewer shape (context 400 at t=500 s). Approved design: remaining()
        # = min(parent window end, arrival bound), so no time is left at t=500.
        opened = []
        clock, arrival, seen = self.real_dispatch(500.0, lambda clock: opened.append(clock()))
        assert_answer_before_bound(self, clock(), arrival)
        self.assertEqual([], opened)
        self.assertIn("request deadline passed before the recovery open (0 attempts)", str(seen["error"]))
        self.assertTrue(retry.retry_handled(seen["error"]))

    def test_exposed_parent_allows_no_child_retry(self):
        clock = ArrivalClock()
        outer = parent(clock, limit=3, exposed=True)
        opens = []

        def fail():
            opens.append(clock())
            raise TimeoutError("transport")

        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, fail)
        self.assertEqual(1, len(opens), "semantic recovery open only; no replay after output")
        self.assertEqual(0, seen["child"].limit)
        self.assertEqual("attempt_ceiling", seen["child"].stop_reason)
        self.assertEqual(0, outer.attempts)

    def test_final_note_and_summary_include_parent_and_child_retries(self):
        clock = ArrivalClock()
        outer = parent(clock, limit=4, attempts=2)
        opens = []

        def fail():
            opens.append(clock())
            raise TimeoutError("transport")

        with retry.request_scope({}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, fail)
        error, child = seen["error"], seen["child"]
        self.assertEqual(3, len(opens))       # initial + two remaining retries
        self.assertEqual(2, child.attempts)
        self.assertEqual(4, outer.attempts)   # write-back on terminal error
        self.assertIn("retried this request 4 time(s)", error._ciel_transient_retry_note)
        self.assertIn("attempt_ceiling", error._ciel_transient_retry_note)
        self.assertEqual(4, error._ciel_transient_retry_summary["attempts"])
        self.assertEqual("attempt_ceiling", error._ciel_transient_retry_summary["stop"])

    def test_second_recovery_sees_attempts_written_back_by_first(self):
        clock = ArrivalClock()
        outer = parent(clock, limit=3, attempts=0)
        calls = []

        def succeeds_second():
            calls.append(1)
            if len(calls) % 2:
                raise TimeoutError("transport")
            return Answer()

        with retry.request_scope({}, "cielairouter", budget=outer):
            first = recovery(clock, outer, succeeds_second)
            second = recovery(clock, outer, succeeds_second)
        self.assertEqual(3, first["child"].limit)
        self.assertEqual(2, second["child"].limit)
        self.assertEqual(2, outer.attempts)

    def test_count_mode_parent_shares_its_count_deadline_and_attempts(self):
        clock = ArrivalClock()
        outer = parent(clock, limit=3, deadline=60.0, attempts=1,
                       window_mode=False, arrival=False)
        opens = []

        def open_once():
            opens.append(1)
            if len(opens) == 1:
                raise ConnectionRefusedError("refused")
            return Answer()

        with retry.request_scope({"gateway_retries": 3}, "cielairouter", budget=outer):
            seen = recovery(clock, outer, open_once)
        child = seen["child"]
        self.assertFalse(child.window_mode)
        self.assertIsNone(child.arrival_started)
        self.assertEqual(outer.started, child.started)
        self.assertEqual(outer.deadline_seconds, child.deadline_seconds)
        self.assertEqual(b'{"id":"ok"}', seen["result"].read())
        self.assertEqual(2, outer.attempts)

    def test_committed_final_open_marker_stays_exempt(self):
        clock = ArrivalClock()
        outer = parent(clock)
        opened = []
        config = {"gateway_retries": 0}
        with retry.request_scope({}, "cielairouter", budget=outer):
            with retry.request_scope(config, "cielairouter", inherit_arrival=False) as child:
                result = retry.open_with_transient_retry(
                    lambda: opened.append(1) or "answer", provider="cielairouter",
                    config=config, log=lambda *_: None)
        self.assertIsNone(child)
        self.assertEqual("answer", result)
        self.assertEqual([1], opened)

    def test_without_an_active_parent_nothing_changes(self):
        with window_on(420.0):
            with retry.request_scope({"gateway_retries": 0}, "cielairouter") as child:
                self.assertIsNone(child)
                self.assertEqual(300.0, retry.bounded_timeout(
                    {"gateway_retries": 0}, "cielairouter", 300.0))


if __name__ == "__main__":
    unittest.main()
