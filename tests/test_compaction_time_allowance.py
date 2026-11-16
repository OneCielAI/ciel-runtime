"""Native compaction gets enough time (user requirement 2026-11-16).

Users saw compactions run past 10 minutes with errors and retries. Measured on
claude.exe 2.1.321 (journal 2026/11/16 fixes/compaction-time): with its default
watchdogs it abandoned a held compaction stream 750 s after dispatch; with the
raised launch env it waited 1200 s and the compaction succeeded. These tests pin
the router and launch values that follow from that.
"""
import unittest
from unittest import mock

from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.claude_environment import COMPACTION_CLIENT_TIMEOUT_MS
from ciel_runtime_support.context_summary_policy import (CLAUDE_COMPACT_END, CLAUDE_COMPACT_TASK,
                                                        CLAUDE_COMPACT_TEXT_PREFIX, native_claude_compact_request)
from ciel_runtime_support.runtime_compaction_control import RuntimeCompactionPolicy

# The native auto-compact instruction shape compact_instruction_kind recognizes.
COMPACT_TEXT = "\n\n".join([
    CLAUDE_COMPACT_TEXT_PREFIX,
    CLAUDE_COMPACT_TASK + ", paying close attention to the user's explicit requests.",
    "Your summary should include the following sections:\n1. Primary Request and Intent",
    CLAUDE_COMPACT_END,
])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CompactionScopeTests(unittest.TestCase):
    def test_bounds_order(self):
        self.assertGreater(retry.COMPACTION_RETRY_WINDOW_SECONDS, retry.RETRY_WINDOW_SECONDS)
        self.assertGreater(retry.COMPACTION_HARD_PREOUTPUT_SECONDS, retry.COMPACTION_RETRY_WINDOW_SECONDS)
        self.assertGreater(retry.COMPACTION_HARD_PREOUTPUT_SECONDS, 600.0)  # past 10 minutes
        # Claude Code's own watchdogs stay outside the router's compaction bound,
        # and input admission is held past it.
        self.assertGreater(COMPACTION_CLIENT_TIMEOUT_MS / 1000, retry.COMPACTION_HARD_PREOUTPUT_SECONDS)
        self.assertGreater(RuntimeCompactionPolicy().confirmation_timeout_seconds,
                           retry.COMPACTION_HARD_PREOUTPUT_SECONDS)

    def test_compaction_scope_gets_the_long_window_and_ceiling(self):
        with retry.request_scope({}, "cielairouter", compaction=True) as budget:
            self.assertTrue(budget.window_mode)
            self.assertTrue(budget.compaction)
            self.assertEqual(retry.COMPACTION_RETRY_WINDOW_SECONDS, budget.deadline_seconds)
            self.assertEqual(retry.COMPACTION_HARD_PREOUTPUT_SECONDS, budget.hard_preoutput_seconds)
            self.assertEqual(retry.COMPACTION_RETRY_CEILING, budget.limit)
        with retry.request_scope({}, "cielairouter") as budget:
            self.assertFalse(budget.compaction)
            self.assertEqual(retry.RETRY_WINDOW_SECONDS, budget.deadline_seconds)
            self.assertEqual(retry.HARD_PREOUTPUT_SECONDS, budget.hard_preoutput_seconds)
            self.assertEqual(retry.WINDOW_RETRY_CEILING, budget.limit)

    def test_an_explicit_retry_count_keeps_its_policy(self):
        with retry.request_scope({"gateway_retries": 2}, "cielairouter", compaction=True) as budget:
            self.assertFalse(budget.window_mode)

    def test_one_compaction_attempt_may_wait_15_minutes_for_its_first_byte(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(retry.COMPACTION_RETRY_CEILING, retry.COMPACTION_RETRY_WINDOW_SECONDS,
                                            clock=clock, window_mode=True, compaction=True,
                                            hard_preoutput_seconds=retry.COMPACTION_HARD_PREOUTPUT_SECONDS)
        with retry.request_scope({}, "cielairouter", budget=budget):
            self.assertEqual(retry.COMPACTION_ATTEMPT_READ_SECONDS, retry.bounded_timeout({}, "cielairouter", 300.0))
            clock.now += retry.COMPACTION_RETRY_WINDOW_SECONDS - 100.0
            # The compaction window still caps the attempt.
            self.assertAlmostEqual(100.0, retry.bounded_timeout({}, "cielairouter", 300.0))
        ordinary = retry.TransientRetryBudget(retry.WINDOW_RETRY_CEILING, retry.RETRY_WINDOW_SECONDS,
                                              clock=Clock(), window_mode=True)
        with retry.request_scope({}, "cielairouter", budget=ordinary):
            self.assertEqual(300.0, retry.bounded_timeout({}, "cielairouter", 300.0))

    def test_a_recovery_child_keeps_the_compaction_allowance(self):
        with retry.request_scope({}, "cielairouter", compaction=True) as parent:
            child = retry._recovery_child(parent)
            self.assertTrue(child.compaction)
            self.assertEqual(parent.limit, child.limit)
            self.assertEqual(parent.hard_preoutput_seconds, child.hard_preoutput_seconds)

    def test_compaction_retries_continue_past_the_ordinary_ceiling(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(retry.COMPACTION_RETRY_CEILING, retry.COMPACTION_RETRY_WINDOW_SECONDS,
                                            clock=clock, window_mode=True, compaction=True, rand=lambda: 0.0,
                                            hard_preoutput_seconds=retry.COMPACTION_HARD_PREOUTPUT_SECONDS)
        self.assertEqual(retry.COMPACTION_RETRY_CEILING, budget.limit)
        self.assertGreater(budget.limit, retry.MAX_WINDOW_RETRIES)


class NativeCompactRequestTests(unittest.TestCase):
    def test_newest_user_text_is_the_native_compact_instruction(self):
        body = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"},
                             {"role": "user", "content": [{"type": "text", "text": COMPACT_TEXT}]}]}
        self.assertTrue(native_claude_compact_request(body))
        command = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}
        self.assertTrue(native_claude_compact_request(command))

    def test_ordinary_turns_and_quotes_are_not_compaction(self):
        self.assertFalse(native_claude_compact_request({"messages": [{"role": "user", "content": "hi"}]}))
        quoted = {"messages": [{"role": "user", "content": "please explain: " + COMPACT_TEXT}]}
        self.assertFalse(native_claude_compact_request(quoted))
        after_tool = {"messages": [{"role": "user", "content": [{"type": "text", "text": COMPACT_TEXT}]},
                                   {"role": "assistant", "content": "x"},
                                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                                                 "content": "out"}]}]}
        # The newest user message has no text: the earlier instruction is the
        # newest text-bearing user message, as in compact_request_kind.
        self.assertTrue(native_claude_compact_request(after_tool))
        self.assertFalse(native_claude_compact_request({}))
        self.assertFalse(native_claude_compact_request(None))


class RouterScopeTests(unittest.TestCase):
    """The router's request scope (routed and bridge paths) for a real POST."""

    def drive(self, body, bridge_request):
        import json
        from test_router_bridge_arrival_bound import Handler, services_for
        seen = []

        def runtime(_handler, _cfg, _name, _pcfg, _path, _body):
            budget = retry._SCOPE.get()
            seen.append((budget.compaction, budget.deadline_seconds, budget.hard_preoutput_seconds))
            return True
        with mock.patch("ciel_runtime_support.provider_failover.failover_service",
                        return_value=mock.Mock(effective=lambda _c, name, config, **_k: (name, config))),                 mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.gateway_overlay",
                           return_value=mock.Mock(apply=lambda _n, config, **_k: config)):
            Handler(services_for(runtime, bridge_request), json.dumps(body).encode()).do_POST()
        return seen

    def test_compaction_bodies_get_the_compaction_scope_on_both_paths(self):
        compact = {"model": "m", "messages": [{"role": "user", "content": "hi"},
                                              {"role": "assistant", "content": "ok"},
                                              {"role": "user", "content": COMPACT_TEXT}]}
        ordinary = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        for bridge in (False, True):
            with self.subTest(bridge=bridge):
                self.assertEqual([(True, retry.COMPACTION_RETRY_WINDOW_SECONDS,
                                   retry.COMPACTION_HARD_PREOUTPUT_SECONDS)], self.drive(compact, bridge))
                self.assertEqual([(False, retry.RETRY_WINDOW_SECONDS, retry.HARD_PREOUTPUT_SECONDS)],
                                 self.drive(ordinary, bridge))


if __name__ == "__main__":
    unittest.main()
