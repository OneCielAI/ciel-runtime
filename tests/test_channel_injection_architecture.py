import unittest

from ciel_runtime_support.channel_injection import ChannelPromptInjector, PromptInjection, RuntimeInjectionPolicy


class MemoryTransport:
    def __init__(self, consumed=True):
        self.writes = []
        self.consumed = consumed
        self.waits = []

    def write(self, data):
        self.writes.append(data)

    def wait_until_input_consumed(self, timeout_seconds=2.0):
        self.waits.append(timeout_seconds)
        return self.consumed


class ChannelInjectionArchitectureTests(unittest.TestCase):
    def make_injector(self, snapshots=()):
        sleeps = []
        logs = []
        values = iter(snapshots)
        injector = ChannelPromptInjector(
            sleep=sleeps.append,
            retry_delay_seconds=lambda: 0.5,
            snapshot=lambda: next(values, None),
            log=lambda level, message: logs.append((level, message)),
        )
        return injector, sleeps, logs

    def test_waits_for_transport_before_runtime_submit_delay(self):
        transport = MemoryTransport()
        injector, sleeps, _ = self.make_injector()
        policy = RuntimeInjectionPolicy("codex", b"\x15", b"\r", 0.25)
        injector.inject(transport, PromptInjection("message", policy))
        self.assertEqual([b"\x15message", b"\r"], transport.writes)
        self.assertEqual([2.0], transport.waits)
        self.assertEqual([0.25], sleeps)

    def test_policy_owns_bracketed_paste_encoding(self):
        transport = MemoryTransport()
        injector, _, _ = self.make_injector()
        policy = RuntimeInjectionPolicy("claude", b"\x15", b"\r", 0, bracketed_paste=True)
        injector.inject(transport, PromptInjection("hello", policy))
        self.assertEqual(b"\x15\x1b[200~hello\x1b[201~", transport.writes[0])

    def test_reports_drain_timeout_but_still_submits(self):
        transport = MemoryTransport(consumed=False)
        injector, _, logs = self.make_injector()
        policy = RuntimeInjectionPolicy("codex", b"\x15", b"\r", 0)
        injector.inject(transport, PromptInjection("message", policy))
        self.assertEqual([("WARN", "channel_input_drain_timeout")], logs)
        self.assertEqual(b"\r", transport.writes[-1])

    def test_submission_confirmation_stops_retries(self):
        transport = MemoryTransport()
        injector, sleeps, logs = self.make_injector(("before", "after"))
        policy = RuntimeInjectionPolicy("claude", b"\x15", b"\r", 0, 4, True)
        injector.inject(transport, PromptInjection("message", policy))
        self.assertEqual([b"\x15message", b"\r"], transport.writes)
        self.assertEqual([0.5], sleeps)
        self.assertEqual([("INFO", "channel_stdin_proxy_submit_confirmed attempt=1")], logs)


if __name__ == "__main__":
    unittest.main()
