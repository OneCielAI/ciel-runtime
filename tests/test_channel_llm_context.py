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

    def services(self, messages, *, wake=False, wake_ids=None, plan=False, stdin_reason=""):
        self.committed = []
        self.logs = []
        return ChannelLlmContextServices(
            policy=ChannelLlmContextPolicy(
                wake_request=lambda body: wake,
                wake_message_ids=lambda body: set(wake_ids or ({12} if wake else set())),
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

    def test_claimed_router_message_is_consumed_only_by_its_wake_request(self):
        message = {
            "id": 12,
            "channel": "web",
            "message": "router body",
            "meta": {"input_transport": "router"},
        }
        ordinary = {"messages": [{"role": "user", "content": "ordinary"}]}
        self.assertIs(
            ordinary,
            inject_pending_channel_context(
                ordinary,
                self.services([message], stdin_reason="stdin_wake_claimed"),
            ),
        )

        out = inject_pending_channel_context(
            {"messages": [{"role": "user", "content": "wake"}]},
            self.services([message], wake=True, stdin_reason="stdin_wake_claimed"),
        )
        self.assertEqual("channel:router body", out["messages"][-1]["content"][0]["text"])
        self.assertTrue(out["wake_removed"])

    def test_tty_message_is_not_consumed_or_overtaken_by_router(self):
        body = {"messages": []}
        messages = [
            {"id": 11, "message": "tty first", "meta": {"input_transport": "tty"}},
            {"id": 12, "message": "router later", "meta": {"input_transport": "router"}},
        ]

        out = inject_pending_channel_context(body, self.services(messages, wake=True))

        self.assertEqual({"messages": [], "wake_removed": True}, out)
        self.assertEqual([], self.committed)
        self.assertTrue(any("reason=input_transport_tty" in message for _level, message in self.logs))

    def test_one_router_wake_injects_its_claimed_batch(self):
        messages = [
            {"id": 21, "channel": "web", "message": "first", "meta": {"input_transport": "router"}},
            {"id": 22, "channel": "web", "message": "second", "meta": {"input_transport": "router"}},
        ]
        services = self.services(
            messages,
            wake=True,
            wake_ids={21, 22},
            stdin_reason="stdin_wake_claimed",
        )
        services = ChannelLlmContextServices(
            policy=services.policy,
            repository=services.repository,
            projection=ChannelLlmContextProjection(
                remove_wake_prompt=services.projection.remove_wake_prompt,
                format_prompt=lambda pending: ",".join(item["message"] for item in pending),
            ),
            log=services.log,
        )

        out = inject_pending_channel_context({"messages": []}, services)

        self.assertEqual("first,second", out["messages"][-1]["content"][0]["text"])
        self.assertEqual("21,22", out["metadata"]["ciel_runtime_channel_message_ids"])

    def test_plan_mode_requires_explicit_wake(self):
        body = {"messages": []}
        out = inject_pending_channel_context(body, self.services([{"id": 11}], plan=True))
        self.assertIs(body, out)
        self.assertIn("reason=plan_mode_active", self.logs[0][1])


if __name__ == "__main__":
    unittest.main()
