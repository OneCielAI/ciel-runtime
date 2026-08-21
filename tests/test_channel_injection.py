import unittest

from ciel_runtime_support.channel_injection import (
    ChannelPromptInjector,
    PromptInjection,
    RuntimeInjectionPolicy,
)
from ciel_runtime_support.channel_terminal_proxy import (
    windows_wake_requires_body_fallback,
)


class FakeWindowsTransport:
    separate_input_stages = True

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.drains = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def wait_until_input_consumed(self, _timeout_seconds: float = 2.0) -> bool:
        self.drains += 1
        return True

    @staticmethod
    def normalize_prompt(prompt: str) -> str:
        return " ".join(prompt.splitlines())

    def pending_input_events(self) -> int:
        return 0

    def input_snapshot(self) -> str | None:
        return None


class ChannelPromptInjectorTests(unittest.TestCase):
    def test_windows_stages_clear_body_and_submit_and_flattens_newlines(self) -> None:
        transport = FakeWindowsTransport()
        logs: list[str] = []
        injector = ChannelPromptInjector(
            sleep=lambda _seconds: None,
            retry_delay_seconds=lambda: 0.0,
            snapshot=lambda: None,
            log=lambda _level, message: logs.append(message),
        )

        injector.inject(
            transport,
            PromptInjection(
                prompt="first line\nsecond line",
                policy=RuntimeInjectionPolicy(
                    runtime="claude",
                    clear_input=b"\x15",
                    submit_input=b"\r",
                    submit_delay_seconds=0.0,
                    submit_attempts=4,
                    confirm_submission=True,
                ),
            ),
        )

        self.assertEqual(
            [b"\x15", b"first line second line", b"\r"], transport.writes
        )
        self.assertEqual(3, transport.drains)
        self.assertTrue(any("stage=clear" in line for line in logs))
        self.assertTrue(any("stage=body" in line for line in logs))
        self.assertTrue(any("stage=submit-1" in line for line in logs))

    def test_windows_full_prompt_attempts_have_a_hard_body_fallback_boundary(self) -> None:
        self.assertFalse(windows_wake_requires_body_fallback("unseen_retry", 1, 2))
        self.assertTrue(windows_wake_requires_body_fallback("unseen_retry", 2, 2))
        self.assertTrue(windows_wake_requires_body_fallback("stale", 2, 2))
        self.assertFalse(windows_wake_requires_body_fallback("completed", 99, 2))

    def test_missing_prompt_head_is_cleared_and_fully_rewritten_before_submit(self) -> None:
        transport = FakeWindowsTransport()
        snapshots = iter(("tail without prompt head", "first line second line"))
        transport.input_snapshot = lambda: next(snapshots)
        logs: list[str] = []
        injector = ChannelPromptInjector(
            sleep=lambda _seconds: None,
            retry_delay_seconds=lambda: 0.0,
            snapshot=lambda: None,
            log=lambda _level, message: logs.append(message),
        )

        injector.inject(
            transport,
            PromptInjection(
                prompt="first line\nsecond line",
                policy=RuntimeInjectionPolicy(
                    runtime="claude",
                    clear_input=b"\x15",
                    submit_input=b"\r",
                    submit_delay_seconds=0.0,
                    submit_attempts=1,
                    confirm_submission=True,
                ),
            ),
        )

        self.assertEqual(
            [b"\x15", b"first line second line", b"\x15", b"first line second line", b"\r"],
            transport.writes,
        )
        self.assertTrue(any("action=rewrite-full-prompt" in line for line in logs))

    def test_transport_snapshot_retries_submit_without_rewriting_body(self) -> None:
        transport = FakeWindowsTransport()
        transport.supports_input_snapshot = False
        snapshots = iter(("prompt-ready", "prompt-ready", "turn-started"))
        transport.input_snapshot = lambda: next(snapshots)
        logs: list[str] = []
        injector = ChannelPromptInjector(
            sleep=lambda _seconds: None,
            retry_delay_seconds=lambda: 0.0,
            snapshot=lambda: None,
            log=lambda _level, message: logs.append(message),
        )

        injector.inject(
            transport,
            PromptInjection(
                prompt="visible external message",
                policy=RuntimeInjectionPolicy(
                    runtime="codex",
                    clear_input=b"\x15",
                    submit_input=b"\r",
                    submit_delay_seconds=0.0,
                    submit_attempts=4,
                    confirm_submission=True,
                ),
            ),
        )

        self.assertEqual(
            [b"\x15", b"visible external message", b"\r", b"\r"],
            transport.writes,
        )
        self.assertEqual(1, transport.writes.count(b"visible external message"))
        self.assertTrue(
            any("channel_stdin_proxy_submit_confirmed attempt=2" in line for line in logs)
        )


if __name__ == "__main__":
    unittest.main()
