import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding, native_input_tokens
from ciel_runtime_support.runtime_compaction_control import CompactionScope
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

ROOT = Path(__file__).resolve().parents[1]
INHERITED = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC")


def stamp(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


class BindingHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.logs = []
        self.log = lambda *args: self.logs.append(args)
        artifact = SecureJsonRepository(self.root / "command.json", SecureJsonEffects(self.log))
        self.requests = ChannelCompactRequestRepository(
            artifact.path, threading.Lock(), artifact.save, lambda text, limit: text[:limit], self.log, lambda: 600)
        # Same arithmetic as ProviderRequestBuilder: 20000 - 4000 output - 1000 reserve.
        self.context_limit = 20000
        self.budget = SimpleNamespace(
            context_limit=lambda *_: self.context_limit, positive_int=lambda value: int(value or 0),
            configured_output=lambda *_: 4000, cap_output_ratio=lambda *_: 4000,
            reserve=lambda *_: 1000, compact_kind=lambda body: body.get("native"))
        self.provider = "other"
        self.binding = self.make()
        self.path = self.home / ".claude" / "projects" / "project" / "exact.jsonl"
        self.path.parent.mkdir(parents=True)
        self.write({"type": "user", "sessionId": "exact", "cwd": str(self.root), "timestamp": stamp(1)})
        self.usage(16000)

    def make(self):
        return RuntimeCompactionBinding(
            self.root, self.home, self.requests, lambda: SimpleNamespace(budget=self.budget),
            lambda body: body.get("tokens", 100), lambda: (self.provider, {}), self.log)

    def write(self, record):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + chr(10))

    def usage(self, tokens, session="exact"):
        self.write({"type": "assistant", "sessionId": session, "timestamp": stamp(time.time()),
                    "message": {"usage": {"input_tokens": tokens}}})

    def checkpoint(self):
        self.write({"type": "system", "subtype": "compact_boundary", "sessionId": "exact",
                    "timestamp": stamp(time.time() + 1), "uuid": f"boundary-{time.time_ns()}",
                    "compactMetadata": {"preTokens": 16000, "postTokens": 900}})

    def state(self):
        return self.binding.control.ports.load()


class ScopeIdentityTests(BindingHarness):
    def test_exact_resumed_scope_and_epoch_shared_between_instances(self):
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        other = self.make()
        self.assertEqual(self.binding.scope(), other.scope())
        self.assertEqual(self.path, other.transcript_path())
        epoch = other.scope().epoch
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.assertNotEqual(epoch, other.scope().epoch)

    def test_separate_process_reads_launcher_epoch_and_session(self):
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        expected = self.binding.scope().payload()
        code = (
            "import json,sys; from pathlib import Path; from types import SimpleNamespace; "
            "from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding; "
            "requests=SimpleNamespace(queue_automatic=lambda *_: None, clear=lambda *_: None); "
            "b=RuntimeCompactionBinding(Path(sys.argv[1]),Path(sys.argv[2]),requests,None,None,None,lambda *_: None); "
            "print(json.dumps(b.scope().payload()))"
        )
        env = {key: value for key, value in os.environ.items() if not key.startswith(INHERITED)}
        result = subprocess.run([sys.executable, "-c", code, str(self.root), str(self.home)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, json.loads(result.stdout))

    def test_fresh_launch_without_explicit_id_never_binds_latest(self):
        # A same-cwd conversation written after launch is still not ours.
        self.binding.bind("claude", cwd=self.root, started_at=time.time() - 5)
        self.usage(90000)
        self.assertIsNone(self.binding.scope())
        self.assertIsNone(self.binding.transcript_path())
        self.assertTrue(self.binding.external_admission_paused())
        self.assertIsNone(self.requests.read())

    def test_wrong_resume_id_never_falls_back(self):
        self.binding.bind("claude", session_id="missing", cwd=self.root)
        self.assertEqual("missing", self.binding.scope().session_id)
        self.assertIsNone(self.binding.transcript_path())
        self.assertFalse(self.binding.external_admission_paused())

    def test_unreadable_scope_fails_closed_for_consumers_but_not_requests(self):
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.binding.repository.path.write_text("{", encoding="utf-8")
        self.assertTrue(self.binding.external_admission_paused())
        self.binding.observe_request("other", {}, {"tokens": 50000}, "claude")
        self.assertTrue(any("runtime_compaction_observe_failed" in entry[1] for entry in self.logs))


class ResumedPressureTests(BindingHarness):
    def test_resumed_usage_is_diagnostic_only_without_mutation(self):
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        original = self.path.read_bytes()
        self.assertEqual(15000, self.binding.input_budget("other", {}, {}))
        self.assertFalse(self.binding.external_admission_paused())
        self.assertFalse(self.make().external_admission_paused())
        self.assertEqual(original, self.path.read_bytes())
        self.assertIsNone(self.requests.read())

    def test_unknown_and_post_checkpoint_usage_stay_unknown(self):
        self.checkpoint()
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.assertIsNone(native_input_tokens(self.path, self.binding.scope()))
        self.assertFalse(self.binding.external_admission_paused())
        self.assertTrue(any("native_input_tokens=unknown" in entry[1] for entry in self.logs))

    def test_first_real_request_prevents_stale_tail_seeding(self):
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude")
        self.assertFalse(self.make().external_admission_paused())
        self.assertIsNone(self.requests.read())

    def test_sidechain_and_other_session_usage_are_ignored(self):
        scope = CompactionScope("claude", "exact", "launch")
        self.checkpoint()
        self.write({"type": "assistant", "sessionId": "exact", "isSidechain": True,
                    "message": {"usage": {"input_tokens": 90000}}})
        self.usage(90000, session="other")
        self.assertIsNone(native_input_tokens(self.path, scope))

    def test_codex_resume_uses_last_input_not_cumulative_usage(self):
        codex = self.root / "codex"
        path = codex / "sessions" / "rollout-native.jsonl"
        path.parent.mkdir(parents=True)
        lines = [
            {"type": "session_meta", "payload": {"id": "native", "cwd": str(self.root)}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 16000, "cached_input_tokens": 12000, "total_tokens": 18000},
                "total_token_usage": {"input_tokens": 90000}}}},
        ]
        path.write_text("".join(json.dumps(line) + chr(10) for line in lines), encoding="utf-8")
        self.binding.bind("codex", session_id="native", codex_home=codex, cwd=self.root)
        self.assertEqual(16000, native_input_tokens(path, self.binding.scope()))
        self.assertFalse(self.binding.external_admission_paused())


class ObservationTests(BindingHarness):
    def setUp(self):
        super().setUp()
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude")  # resumed, small

    def test_raw_pressure_queues_once_and_router_append_stays_paused(self):
        self.binding.observe_failure("other", {}, {"tokens": 15000}, "prompt is too long: 21000 tokens > 20000 maximum", "claude")
        request = self.requests.read()
        self.binding.observe_request("other", {}, {"tokens": 16000}, "claude")
        self.assertEqual(request["id"], self.requests.read()["id"])
        self.assertEqual("context_recovery", self.binding.context_pause_reason({}))
        self.assertEqual("native_compaction", self.binding.context_pause_reason({"native": "claude"}))

    def test_remote_bridge_and_other_runtime_are_bypassed(self):
        self.binding.observe_request("other", {REMOTE_BRIDGE_CONFIG_MARKER: True}, {"tokens": 90000}, "claude")
        self.binding.observe_request("other", {}, {"tokens": 90000}, "codex")
        self.assertIsNone(self.requests.read())
        self.assertFalse(self.binding.external_admission_paused())

    def test_numeric_input_overflow_forces_recovery_output_only_does_not(self):
        self.binding.observe_failure(
            "other", {}, {}, "maximum context length is 20000 tokens requested 21000 tokens "
            "(14000 in the messages, 7000 in the completion", "claude")
        self.assertIsNone(self.requests.read())
        self.binding.observe_failure("other", {}, {}, "upstream said no", "claude")
        self.assertIsNone(self.requests.read())
        self.binding.observe_failure("other", {}, {}, "prompt is too long: 21000 tokens > 20000 maximum", "claude")
        self.assertTrue(self.requests.read()["automatic"])
        self.assertTrue(self.state()["pressure"])

    def test_checkpoint_alone_holds_consumers_until_real_shrink(self):
        self.binding.observe_failure("other", {}, {"tokens": 15000}, "prompt is too long: 21000 tokens > 20000 maximum", "claude")
        request = self.requests.read()
        self.assertTrue(self.binding.command_ready(request))
        self.binding.command_submitted(request)
        self.requests.clear(request["id"])
        self.assertTrue(self.binding.external_admission_paused())
        self.checkpoint()
        # Persisted checkpoint alone must not release external SSE as a probe.
        self.assertTrue(self.binding.external_admission_paused())
        self.assertEqual("context_recovery", self.binding.context_pause_reason({}))
        self.assertEqual("awaiting_shrink", self.state()["phase"])
        self.binding.observe_request("other", {}, {"tokens": 900}, "claude")
        self.assertEqual("verified", self.state()["phase"])
        self.assertEqual("", self.binding.context_pause_reason({}))
        self.assertFalse(self.binding.external_admission_paused())

    def test_non_shrinking_probe_closes_consumer_gate_without_fake_success(self):
        self.binding.observe_failure("other", {}, {"tokens": 15000}, "prompt is too long: 21000 tokens > 20000 maximum", "claude")
        request = self.requests.read()
        self.binding.command_ready(request)
        self.binding.command_submitted(request)
        self.requests.clear(request["id"])
        self.checkpoint()
        self.assertTrue(self.binding.external_admission_paused())
        self.binding.observe_request("other", {}, {"tokens": 15500}, "claude")
        self.assertEqual("retry_wait", self.state()["phase"])
        self.assertEqual("next_request_not_below_budget", self.state()["failure"])
        self.assertTrue(self.binding.external_admission_paused())

    def test_subagent_and_other_session_cannot_fake_shrink_or_pressure(self):
        main = {"X-Claude-Code-Session-Id": "exact"}
        agent = {**main, "X-Claude-Code-Agent-Id": "acb38bfde8cdac030"}
        self.binding.observe_request("other", {}, {"tokens": 90000}, "claude", agent)
        self.binding.observe_request("other", {}, {"tokens": 90000}, "claude", {"X-Claude-Code-Session-Id": "other"})
        self.binding.observe_request("other", {}, {"tokens": 90000}, "claude", {})
        self.assertIsNone(self.requests.read())
        self.binding.observe_failure("other", {}, {"tokens": 15000}, "prompt is too long: 21000 tokens > 20000 maximum", "claude", main)
        request = self.requests.read()
        self.binding.command_ready(request)
        self.binding.command_submitted(request)
        self.requests.clear(request["id"])
        self.checkpoint()
        self.binding.external_admission_paused()
        self.binding.observe_request("other", {}, {"tokens": 50}, "claude", agent)
        self.assertEqual("awaiting_shrink", self.state()["phase"])
        self.binding.observe_failure("other", {}, {}, "prompt is too long: 21000 tokens > 20000 maximum", "claude", agent)
        self.assertEqual("awaiting_shrink", self.state()["phase"])
        self.binding.observe_request("other", {}, {"tokens": 900}, "claude", main)
        self.assertEqual("verified", self.state()["phase"])

    def test_codex_identity_uses_prompt_cache_key_then_session_header(self):
        codex = self.root / "codex"
        path = codex / "sessions" / "rollout-thread.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread"}}) + chr(10), encoding="utf-8")
        self.binding.bind("codex", session_id="thread", codex_home=codex, cwd=self.root)
        self.binding.observe_request("other", {}, {"tokens": 90000, "prompt_cache_key": "subagent"}, "codex", {})
        self.binding.observe_request("other", {}, {"tokens": 90000}, "codex", {"session_id": "subagent"})
        self.assertIsNone(self.requests.read())
        self.binding.observe_request("other", {}, {"tokens": 90000, "prompt_cache_key": "thread"}, "codex", {})
        self.assertIsNone(self.requests.read())

    def test_router_callback_shapes_are_accepted(self):
        from ciel_runtime_support import claude_router, openai_responses_router

        recovery = claude_router.ClaudeRouterContextRecovery(lambda *_: None, self.binding.observe_request,
                                                             self.binding.observe_failure)
        recovery.observe_request("other", {}, {"tokens": 100}, "claude", {"X-Claude-Code-Session-Id": "exact"})
        recovery.observe_failure("other", {}, {}, b"not an overflow", "claude", None)
        self.assertIn("observe_context_request", {field.name for field in dataclasses.fields(
            openai_responses_router.OpenAIResponsesCore)})

    def test_anthropic_native_owns_pressure_but_observed_compact_cannot_deadlock(self):
        self.provider = "anthropic"
        self.binding.observe_request("anthropic", {}, {"tokens": 90000}, "claude")
        self.binding.observe_failure("anthropic", {}, {}, "prompt is too long: 21000 tokens > 20000 maximum", "claude")
        self.assertIsNone(self.requests.read())
        self.assertFalse(self.binding.external_admission_paused())
        self.context_limit = 0  # unknown capacity must not strand the cycle
        self.binding.observe_request("anthropic", {}, {"tokens": 15000, "native": "claude"}, "claude")
        self.assertEqual("compacting", self.state()["phase"])
        self.assertTrue(self.binding.external_admission_paused())
        self.checkpoint()
        self.assertTrue(self.binding.external_admission_paused())
        self.binding.observe_request("anthropic", {}, {"tokens": 900}, "claude")
        self.assertEqual("verified", self.state()["phase"])


class FacadeWiringTests(unittest.TestCase):
    def test_facade_ports_share_one_binding_without_live_launch(self):
        import ciel_runtime as runtime

        binding = runtime._RUNTIME_COMPACTION
        self.assertIs(binding, runtime.channel_wake_context().pending_io.compaction)
        self.assertIs(binding, runtime.channel_terminal_context().policy.compaction)
        self.assertIs(binding, runtime._CODEX_SESSIONS.ports.channel.compaction)
        self.assertEqual(binding.repository.path.parent, runtime.ROUTER_INSTANCE_DIR)
        self.assertLessEqual(len(Path(runtime.__file__).read_text(encoding="utf-8").splitlines()), 4980)


if __name__ == "__main__":
    unittest.main()
