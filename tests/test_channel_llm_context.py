import contextlib
import unittest

from ciel_runtime_support.channel_llm_context import (
    ChannelLlmContextPolicy,
    ChannelLlmContextProjection,
    ChannelLlmContextRepository,
    ChannelLlmContextServices,
    inject_pending_channel_context,
    strip_internal_metadata,
)


class ChannelLlmContextTests(unittest.TestCase):
    def test_internal_metadata_projection_preserves_public_values(self):
        body = {
            "metadata": {
                "public": "keep",
                "ciel_runtime_channel_injected": True,
            }
        }

        projected = strip_internal_metadata(body)

        self.assertEqual({"public": "keep"}, projected["metadata"])
        self.assertIn("ciel_runtime_channel_injected", body["metadata"])

    def test_metadata_projection_returns_original_when_no_private_values_exist(self):
        body = {"metadata": {"public": "keep"}}

        self.assertIs(body, strip_internal_metadata(body))

    def services(self, messages, *, wake=False, plan=False, stdin_reason=""):
        self.committed = []
        self.logs = []
        return ChannelLlmContextServices(
            policy=ChannelLlmContextPolicy(
                wake_request=lambda body: wake,
                plan_mode_active=lambda body: plan,
                delivery_mode=lambda: "llm",
                ids_in_request=lambda body: set(),
                scan_limit=lambda: 100,
                skip_reason=lambda message: str(message.get("skip") or ""),
                stdin_skip_reason=lambda message_id: stdin_reason,
            ),
            repository=ChannelLlmContextRepository(
                lock=contextlib.nullcontext,
                read_cursor=lambda: 10,
                commit_cursor=self.committed.append,
                read_messages=lambda last_id, limit: messages,
                superseded_ids=lambda candidates: set(),
            ),
            projection=ChannelLlmContextProjection(
                remove_wake_prompt=lambda body: {**body, "wake_removed": True},
                format_prompt=lambda pending: f"channel:{pending[0]['message']}",
            ),
            log=lambda level, message: self.logs.append((level, message)),
        )

    def test_injects_first_eligible_message_and_metadata(self):
        out = inject_pending_channel_context(
            {"messages": [{"role": "user", "content": "hello"}]},
            self.services([{"id": 11, "channel": "web", "message": "answer"}]),
        )

        self.assertEqual("channel:answer", out["messages"][-1]["content"][0]["text"])
        self.assertEqual("11", out["metadata"]["ciel_runtime_channel_message_ids"])
        self.assertEqual([], self.committed)

    def test_skipped_messages_advance_cursor_but_stdin_claim_does_not(self):
        body = {"messages": []}
        inject_pending_channel_context(body, self.services([{"id": 11, "skip": "connection_noise"}]))
        self.assertEqual([11], self.committed)

        inject_pending_channel_context(body, self.services([{"id": 12}], stdin_reason="stdin_wake_claimed"))
        self.assertEqual([], self.committed)

    def test_plan_mode_requires_explicit_wake(self):
        body = {"messages": []}
        out = inject_pending_channel_context(body, self.services([{"id": 11}], plan=True))
        self.assertIs(body, out)
        self.assertIn("reason=plan_mode_active", self.logs[0][1])


if __name__ == "__main__":
    unittest.main()
