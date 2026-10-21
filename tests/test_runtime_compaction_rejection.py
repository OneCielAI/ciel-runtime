"""Truthful local rejection for Claude reactive native compaction; no live calls."""
import copy
import json
import re
import tempfile
import threading
import time
import unittest
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import claude_router as claude
from ciel_runtime_support import router_request_assembly as assembly
from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONTEXT_ATTRIBUTE
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
from ciel_runtime_support.runtime_compaction_control import CompactionScope
from ciel_runtime_support.runtime_compaction_rejection import (
    REACTIVE_MESSAGE,
    ReactiveCompactionRejection,
    ReactiveRejectionPorts,
)
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

MAIN = {"X-Claude-Code-Session-Id": "exact"}
AGENT = {**MAIN, "X-Claude-Code-Agent-Id": "reactive-compact"}
SCOPE = CompactionScope("claude", "exact", "epoch")
# Message verified against Claude Code 2.1.303 (local-pressure/run-01).
VERIFIED = ("prompt is too long: the unmodified client request exceeds the configured Runtime input budget "
            "(local JSON-size estimate; not a provider token count). Native compaction is required.")
# Claude Code 2.1.303 reactive triggers and its token-pair parser.
TRIGGERS = ("prompt is too long", "input is too long for requested model", "prompt_too_long")
TOKEN_PAIR = re.compile(r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)", re.I)


def ports(kind, **overrides):
    return kind(**{field.name: overrides.get(field.name, mock.Mock()) for field in fields(kind)})


def stamp(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


class RejectionDecisionTests(unittest.TestCase):
    def test_no_local_estimate_or_stale_state_can_reject_a_native_turn(self):
        effects = mock.Mock(side_effect=AssertionError("preflight must not infer token pressure"))
        rejection = ReactiveCompactionRejection(ReactiveRejectionPorts(
            main_scope=effects, compact_kind=effects, estimate=effects,
            input_budget=effects, state=effects, log=effects))
        for provider in ("other", "anthropic"):
            for body in ({"tokens": 10**9}, {"native": "claude", "tokens": 10**9}):
                for headers in (MAIN, AGENT, {}, None):
                    with self.subTest(provider=provider, body=body, headers=headers):
                        self.assertIsNone(rejection.reject_request(provider, {}, body, "claude", headers))
        effects.assert_not_called()


class RouterRejectionTests(unittest.TestCase):
    def services(self, events, rejection):
        def inject(body):
            events.append("inject")
            body["messages"].append({"role": "user", "content": "pending"})
            return body

        def filter_tools(_provider, _config, body):
            body = dict(body)
            body["tools"] = []
            return body
        return claude.ClaudeRouterServices(
            core=ports(claude.ClaudeRouterCore), count_tokens=ports(claude.ClaudeRouterCountTokens),
            pipeline=ports(claude.ClaudeRouterPipeline,
                router_event_message_preview=lambda *_args: {},
                filter_blocked_tools=filter_tools,
                normalize_tool_choice=lambda _p, _c, body: body,
                strip_advisor_tools=lambda _p, body: body,
                inject_channel_context=inject, inject_tool_result_context=lambda body: body),
            shortcuts=ports(claude.ClaudeRouterShortcuts, **{f.name: (lambda *_a, **_k: False) for f in fields(claude.ClaudeRouterShortcuts)}),
            delivery=ports(claude.ClaudeRouterDelivery, is_client_disconnect=lambda _e: False),
            routing=ports(claude.ClaudeRouterRouting,
                resolve_model=mock.Mock(return_value="model"), select_protocol=lambda *_a: "openai_responses",
                provider_labels={}, forward_responses=mock.Mock()),
            normalization=ports(claude.ClaudeRouterNativeNormalization),
            transport=ports(claude.ClaudeRouterTransport), response=ports(claude.ClaudeRouterResponse),
            context_recovery=ports(claude.ClaudeRouterContextRecovery,
                observe_request=mock.Mock(side_effect=lambda *_a: events.append("observe")),
                reject_request=mock.Mock(side_effect=lambda *_a: events.append("reject") or rejection)),
        )

    def post(self, services, handler=None):
        handler = handler or SimpleNamespace(path="/v1/messages", headers=MAIN)
        body = {"model": "model", "stream": True, "tools": [{"name": "t"}],
                "messages": [{"role": "user", "content": "original"}]}
        expected = copy.deepcopy(body)
        claude.handle_claude_messages_post(services, handler, {}, "other", {}, "/v1/messages", body)
        return handler, expected

    def test_rejection_is_written_before_pending_input_or_upstream(self):
        events = []
        payload = {"type": "error", "error": {"type": "invalid_request_error", "message": REACTIVE_MESSAGE}}
        services = self.services(events, payload)
        handler, expected = self.post(services)
        self.assertEqual(["observe", "reject"], events)
        services.context_recovery.reject_request.assert_called_once_with("other", {}, expected, "claude", MAIN)
        services.context_recovery.observe_request.assert_called_once_with("other", {}, expected, "claude", MAIN)
        services.routing.write_json.assert_called_once_with(handler, payload, 400)
        services.delivery.begin.assert_not_called()
        services.delivery.commit.assert_not_called()
        services.routing.resolve_model.assert_not_called()
        services.routing.forward_responses.assert_not_called()
        published = services.core.event_bus.publish.call_args_list[-1].kwargs
        self.assertEqual("context_recovery.local_rejection", published["category"])

    def test_no_rejection_continues_with_pending_input(self):
        for rejection in (None, mock.Mock()):  # a non-mapping is never an error payload
            with self.subTest(rejection=rejection):
                events = []
                services = self.services(events, rejection)
                self.post(services)
                self.assertEqual(["observe", "reject", "inject"], events)
                services.routing.write_json.assert_not_called()
                services.routing.forward_responses.assert_called_once()

    def test_remote_request_never_reaches_the_rejection(self):
        events = []
        services = self.services(events, {"type": "error"})
        handler = SimpleNamespace(path="/v1/messages", headers=MAIN)
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        self.post(services, handler)
        services.context_recovery.reject_request.assert_not_called()
        services.routing.write_json.assert_not_called()

    def test_default_and_assembly_fields_are_compatible(self):
        self.assertIsNone(claude.ClaudeRouterContextRecovery(lambda *_: None).reject_request("p", {}, {}, "claude", {}))
        reject = mock.Mock()
        recovery = assembly.ClaudeRouterContextRecoveryPorts(mock.Mock(), mock.Mock(), mock.Mock(), reject)
        built = assembly.ClaudeRouterAssembly(*([mock.Mock()] * 9), recovery).services()
        self.assertIs(reject, built.context_recovery.reject_request)
        self.assertIsNone(assembly.ClaudeRouterContextRecoveryPorts(mock.Mock()).reject_request())


class BindingCycleTests(unittest.TestCase):
    """Real binding and controller; the test wires the ports the facade must provide."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = []
        log = lambda *args: self.logs.append(args)  # noqa: E731
        artifact = SecureJsonRepository(self.root / "command.json", SecureJsonEffects(log))
        requests = ChannelCompactRequestRepository(
            artifact.path, threading.Lock(), artifact.save, lambda text, limit: text[:limit], log, lambda: 600)
        # Input budget 15000 = 20000 - 4000 output - 1000 reserve.
        budget = SimpleNamespace(
            context_limit=lambda *_: 20000, positive_int=lambda value: int(value or 0),
            configured_output=lambda *_: 4000, cap_output_ratio=lambda *_: 4000,
            reserve=lambda *_: 1000, compact_kind=lambda body: body.get("native"))
        self.binding = RuntimeCompactionBinding(
            self.root, self.root / "home", requests, lambda: SimpleNamespace(budget=budget),
            lambda body: body.get("tokens", 100), lambda: ("other", {}), log)
        self.path = self.root / "home" / ".claude" / "projects" / "project" / "exact.jsonl"
        self.path.parent.mkdir(parents=True)
        self.write({"type": "user", "sessionId": "exact", "cwd": str(self.root), "timestamp": stamp(1)})
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.rejection = ReactiveCompactionRejection(ReactiveRejectionPorts(
            main_scope=self.binding._main_session, compact_kind=self.binding._compact_kind,
            estimate=self.binding.estimate, input_budget=self.binding.input_budget,
            state=lambda _scope: self.binding.control.ports.load(), log=log))

    def write(self, record):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + chr(10))

    def checkpoint(self):
        self.write({"type": "system", "subtype": "compact_boundary", "sessionId": "exact",
                    "timestamp": stamp(time.time() + 1), "uuid": f"boundary-{time.time_ns()}",
                    "compactMetadata": {"preTokens": 16000, "postTokens": 900}})

    def request(self, body, headers=MAIN):
        # Router order: observe the raw request, then decide.
        self.binding.observe_request("other", {}, body, "claude", headers)
        result = self.rejection.reject_request("other", {}, body, "claude", headers)
        return result["error"]["message"] if result else None

    def phase(self):
        return (self.binding.control.ports.load() or {}).get("phase")

    def test_non_shrinking_native_compactions_stop_after_the_bound(self):
        self.assertIsNone(self.request({"tokens": 16000}))
        self.assertIsNone(self.phase())
        self.assertIsNone(self.request({"tokens": 16000, "native": "claude"}))
        self.assertEqual("compacting", self.phase())
        self.checkpoint()
        self.assertIsNone(self.request({"tokens": 15500}))
        self.assertIsNone(self.request({"tokens": 16000, "native": "claude"}))
        self.checkpoint()
        self.assertIsNone(self.request({"tokens": 15500}))
        self.assertIsNone(self.request({"tokens": 15500}))

    def test_shrunk_retry_passes_and_verifies(self):
        self.assertIsNone(self.request({"tokens": 16000}))
        self.assertIsNone(self.request({"tokens": 16000, "native": "claude"}))
        self.checkpoint()
        self.assertIsNone(self.request({"tokens": 900}))
        self.assertEqual("verified", self.phase())

    def test_subagent_and_side_queries_are_not_rejected(self):
        self.assertIsNone(self.request({"tokens": 16000}, AGENT))
        self.assertIsNone(self.request({"tokens": 16000}, {**MAIN, "x-claude-code-request-class": "auxiliary"}))
        self.assertIsNone(self.request({"tokens": 16000}, {"X-Claude-Code-Session-Id": "other"}))
        self.assertIsNone(self.request({"tokens": 16000, "native": "claude"}, AGENT))

    def test_controller_blocked_state_refuses_reactive(self):
        scope = self.binding.scope()
        self.binding.control.ports.save({"scope": scope.payload(), "phase": "blocked", "attempts": 2})
        self.assertIsNone(self.request({"tokens": 16000}))


if __name__ == "__main__":
    unittest.main()
