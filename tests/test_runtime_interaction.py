import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.runtime_interaction import (
    RuntimeInteractionDisplayState,
    RuntimeInteractionEvent,
    RuntimeInteractionRepository,
    poll_runtime_interaction,
)


class RuntimeInteractionTests(unittest.TestCase):
    def test_repository_publishes_pending_and_terminal_states_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [100.0]
            path = Path(directory) / "runtime-interaction.json"
            repository = RuntimeInteractionRepository(path, clock=lambda: now[0])

            pending = repository.publish_pending(
                request_id="request-1",
                kind="zai-start-plan-captcha",
                url="http://example.test/captcha?state=one",
                timeout_seconds=180,
            )
            self.assertEqual(pending, repository.read())
            self.assertEqual("pending", json.loads(path.read_text())["status"])

            now[0] = 101.0
            completed = repository.publish_status(pending, "completed")
            self.assertEqual(completed, repository.read())
            self.assertEqual("completed", completed.status)
            self.assertEqual(pending.created_at, completed.created_at)

    def test_pending_notice_is_visible_immediately_and_reminded(self):
        event = RuntimeInteractionEvent(
            request_id="request-1",
            kind="zai-start-plan-captcha",
            status="pending",
            created_at=100.0,
            updated_at=100.0,
            expires_at=280.0,
            url="http://100.95.132.58:42121/zai-start-plan-captcha?state=one",
        )
        displayed = []
        state = RuntimeInteractionDisplayState()

        poll_runtime_interaction(100.0, state, lambda: event, displayed.append)
        poll_runtime_interaction(110.0, state, lambda: event, displayed.append)
        poll_runtime_interaction(131.0, state, lambda: event, displayed.append)

        self.assertEqual(2, len(displayed))
        self.assertIn(event.url, displayed[0])
        self.assertIn("continue automatically", displayed[0])

    def test_expired_pending_notice_is_not_displayed(self):
        event = RuntimeInteractionEvent(
            request_id="request-1",
            kind="zai-start-plan-captcha",
            status="pending",
            created_at=100.0,
            updated_at=100.0,
            expires_at=120.0,
            url="http://example.test/captcha",
        )
        displayed = []

        poll_runtime_interaction(
            121.0,
            RuntimeInteractionDisplayState(),
            lambda: event,
            displayed.append,
        )

        self.assertEqual([], displayed)

    def test_completion_is_displayed_once_without_repeating_url(self):
        event = RuntimeInteractionEvent(
            request_id="request-1",
            kind="zai-start-plan-captcha",
            status="completed",
            created_at=100.0,
            updated_at=105.0,
            expires_at=280.0,
            url="http://example.test/captcha?state=secret",
        )
        displayed = []
        state = RuntimeInteractionDisplayState()

        poll_runtime_interaction(105.0, state, lambda: event, displayed.append)
        poll_runtime_interaction(106.0, state, lambda: event, displayed.append)

        self.assertEqual(1, len(displayed))
        self.assertIn("verification received", displayed[0])
        self.assertNotIn("secret", displayed[0])


if __name__ == "__main__":
    unittest.main()
