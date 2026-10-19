"""Exact native identity for fresh and continued launches (no live sessions).

Published-session fixtures copy the field set Claude Code 2.1.303 writes to
~/.claude/sessions/<pid>.json; values are synthetic.
"""

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
from unittest import mock

from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.codex_desktop_injection import CodexAppServerChannelInjector, CodexDesktopChannelPorts
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

ROOT = Path(__file__).resolve().parents[1]
INHERITED = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC")


def stamp(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)
    return process.pid


class IdentityHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        # The fixture home, never the developer's Claude config directory.
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.logs = []
        self.log = lambda *args: self.logs.append(args)
        artifact = SecureJsonRepository(self.root / "command.json", SecureJsonEffects(self.log))
        self.requests = ChannelCompactRequestRepository(
            artifact.path, threading.Lock(), artifact.save, lambda text, limit: text[:limit], self.log, lambda: 600)
        # 20000 context - 4000 output - 1000 reserve = 15000 input budget.
        self.budget = SimpleNamespace(
            context_limit=lambda *_: 20000, positive_int=lambda value: int(value or 0),
            configured_output=lambda *_: 4000, cap_output_ratio=lambda *_: 4000,
            reserve=lambda *_: 1000, compact_kind=lambda body: body.get("native"))
        self.binding = self.make()
        self.socket = str(self.root / "owned.sock")
        self.path = self.home / ".claude" / "projects" / "project" / "exact.jsonl"

    def make(self):
        return RuntimeCompactionBinding(
            self.root, self.home, self.requests, lambda: SimpleNamespace(budget=self.budget),
            lambda body: body.get("tokens", 100), lambda: ("other", {}), self.log)

    def transcript(self, tokens=16000):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = [{"type": "user", "sessionId": "exact", "cwd": str(self.root), "timestamp": stamp(1)},
                   {"type": "assistant", "sessionId": "exact", "timestamp": stamp(time.time()),
                    "message": {"usage": {"input_tokens": tokens}}}]
        self.path.write_text("".join(json.dumps(record) + chr(10) for record in records), encoding="utf-8")

    def published(self, session="exact", directory=None, **changes):
        directory = directory or self.home / ".claude" / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        record = {"pid": 12345, "sessionId": session, "cwd": str(self.root),
                  "startedAt": int(time.time() * 1000), "procStart": "134369156140544048",
                  "version": "2.1.303", "kind": "interactive", "entrypoint": "cli",
                  "pidDomain": "test", "messagingSocketPath": self.socket}
        record.update(changes)
        (directory / f"{record['pid']}.json").write_text(json.dumps(record), encoding="utf-8")

    def state(self):
        return self.binding.control.ports.load()


class PublishedSocketIdentityTests(IdentityHarness):
    def setUp(self):
        super().setUp()
        self.binding.bind("claude", cwd=self.root, messaging_socket_path=self.socket)

    def test_fresh_launch_holds_until_exact_socket_record_then_shares_epoch(self):
        self.assertIsNone(self.binding.scope())
        self.assertTrue(self.binding.external_admission_paused())
        self.assertEqual("identity_pending", self.binding.context_pause_reason({}))
        self.published()
        other = self.make()
        self.assertEqual("exact", other.scope().session_id)
        self.assertEqual(self.binding.scope(), other.scope())
        self.assertFalse(other.external_admission_paused())

    def test_continue_seeds_resumed_pressure_once_transcript_is_selected(self):
        self.published()
        self.assertFalse(self.binding.external_admission_paused())  # no transcript yet
        self.transcript(16000)
        self.binding.cache["checked_at"] = 0.0
        self.assertTrue(self.binding.external_admission_paused())
        self.assertTrue(self.requests.read()["automatic"])
        self.assertEqual("queued", self.state()["phase"])

    def test_wrong_socket_cwd_earlier_process_or_identity_never_select_latest(self):
        self.transcript()
        for changes in ({"messagingSocketPath": str(self.root / "other.sock")}, {"cwd": str(self.root / "other")},
                        {"startedAt": int((time.time() - 60) * 1000)}, {"procStart": ""}, {"sessionId": ""}):
            self.published(**changes)
            self.assertIsNone(self.binding.scope(), changes)
            self.assertTrue(self.binding.external_admission_paused(), changes)

    def test_owned_pid_rejects_another_process_with_same_socket(self):
        self.binding.bind("claude", cwd=self.root, messaging_socket_path=self.socket, owned_pid=111)
        self.published()
        self.assertIsNone(self.binding.scope())
        self.published(pid=111)
        self.assertEqual("exact", self.binding.scope().session_id)

    def test_ambiguous_records_fail_closed(self):
        self.published()
        self.published("other", pid=12346)
        self.assertIsNone(self.binding.scope())
        self.assertTrue(self.binding.external_admission_paused())

    def test_identity_precedes_transcript_and_raw_pressure_is_observed(self):
        self.published()
        self.assertIsNone(self.binding.transcript_path())
        self.binding.observe_request("other", {}, {"tokens": 16000}, "claude", {"x-claude-code-session-id": "exact"})
        self.assertEqual("queued", self.state()["phase"])
        self.assertTrue(self.binding.external_admission_paused())

    def test_request_header_cannot_override_declared_socket_ownership(self):
        self.binding.observe_request("other", {}, {"tokens": 16000}, "claude", {"x-claude-code-session-id": "other"})
        self.assertIsNone(self.binding.scope())
        self.assertFalse(self.state())
        self.published()
        self.binding.observe_request("other", {}, {"tokens": 16000}, "claude", {"x-claude-code-session-id": "other"})
        self.assertFalse(self.state())

    def test_published_session_switch_follows_within_epoch(self):
        # Claude Code 2.1.303 rewrites sessionId in its pid file on /clear.
        self.published()
        self.transcript(16000)
        epoch = self.binding.scope().epoch
        self.assertTrue(self.binding.external_admission_paused())
        self.published("cleared")
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude", {"x-claude-code-session-id": "cleared"})
        scope = self.make().scope()
        self.assertEqual(("cleared", epoch), (scope.session_id, scope.epoch))
        self.assertIsNone(self.binding.transcript_path())
        self.assertFalse(self.binding.external_admission_paused())
        self.assertTrue(any("via=socket" in entry[1] for entry in self.logs))

    def test_vanished_transcript_keeps_exact_identity_and_drops_proof_path(self):
        self.published()
        self.transcript(100)
        self.assertEqual(self.path, self.binding.transcript_path())
        self.path.unlink()
        self.binding.cache["checked_at"] = 0.0
        self.assertEqual("exact", self.binding.scope().session_id)
        self.assertIsNone(self.binding.transcript_path())
        self.assertEqual("", self.binding.repository.load("compaction_scope")["path"])

    def test_inherited_claude_config_dir_locates_published_record(self):
        config = self.root / "sandbox-claude"
        os.environ["CLAUDE_CONFIG_DIR"] = str(config)
        self.binding.bind("claude", cwd=self.root, messaging_socket_path=self.socket)
        self.published()
        self.assertIsNone(self.binding.scope())
        self.published(directory=config / "sessions")
        self.assertEqual("exact", self.make().scope().session_id)
        self.path = config / "projects" / "project" / "exact.jsonl"
        self.transcript()
        peer = self.make()
        self.assertEqual(self.path, peer.transcript_path())
        self.assertTrue(peer.external_admission_paused())
        self.assertEqual("queued", peer.control.ports.load()["phase"])

    def test_separate_process_resolves_launcher_socket_identity(self):
        self.published()
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
        self.assertEqual(self.binding.scope().payload(), json.loads(result.stdout))
        self.assertEqual("exact", json.loads(result.stdout)["session_id"])


class RequestIdentityTests(IdentityHarness):
    def test_first_main_request_binds_once_without_socket(self):
        self.binding.bind("claude", cwd=self.root)
        self.assertTrue(self.binding.external_admission_paused())
        for headers in (None, {}, {"x-claude-code-agent-id": "agent", "x-claude-code-session-id": "agent-parent"}):
            self.binding.observe_request("other", {}, {"tokens": 16000}, "claude", headers)
            self.assertIsNone(self.binding.scope(), headers)
        self.binding.observe_request("other", {}, {"tokens": 16000}, "claude", {"x-claude-code-session-id": "exact"})
        self.assertEqual("exact", self.make().scope().session_id)
        self.assertEqual("queued", self.state()["phase"])
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude", {"x-claude-code-session-id": "other"})
        self.assertEqual("exact", self.binding.scope().session_id)

    def test_codex_fresh_request_binds_from_native_session_key(self):
        self.binding.bind("codex", cwd=self.root, codex_home=self.root / "codex")
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude", {"x-claude-code-session-id": "x"})
        self.assertIsNone(self.binding.scope())
        self.binding.observe_request("other", {}, {"tokens": 100}, "codex", {"session_id": "thread"})
        self.assertEqual("thread", self.binding.scope().session_id)

    def test_launch_without_any_exact_source_stays_ungated(self):
        self.binding.bind("claude", cwd=self.root, request_identity=False)
        self.assertFalse(self.binding.external_admission_paused())
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude", {"x-claude-code-session-id": "exact"})
        self.assertIsNone(self.binding.scope())
        self.assertTrue(any("identity_unavailable" in entry[1] for entry in self.logs))

    def test_stale_launcher_cannot_hold_later_consumers(self):
        self.binding.bind("claude", cwd=self.root, messaging_socket_path=self.socket)
        value = self.binding.repository.load("compaction_scope")
        value["launcher_pid"] = dead_pid()
        self.binding.repository.save(value, "compaction_scope")
        self.assertFalse(self.make().external_admission_paused())

    def test_unsupported_runtime_is_not_held(self):
        self.binding.bind("muse", cwd=self.root)
        self.assertFalse(self.binding.external_admission_paused())


class AppServerThreadIdentityTests(IdentityHarness):
    def test_only_the_binding_process_epoch_can_follow_threads(self):
        self.binding.bind("codex", cwd=self.root, codex_home=self.root / "codex")
        self.make().bind_native_session("codex", "foreign")
        self.assertIsNone(self.binding.scope())
        self.binding.bind_native_session("claude", "wrong-runtime")
        self.assertIsNone(self.binding.scope())
        self.binding.bind_native_session("codex", "thread-1")
        self.assertEqual("thread-1", self.make().scope().session_id)
        stale_epoch = self.binding.bound_epoch
        later = self.make()
        later.bind("codex", cwd=self.root)
        self.binding.bound_epoch = stale_epoch
        self.binding.bind_native_session("codex", "thread-2")
        self.assertNotEqual(stale_epoch, later.scope() and later.scope().epoch)
        self.assertIsNone(later.scope())

    def test_injector_lifecycle_thread_started_binds_exact_user_thread(self):
        self.binding.bind("codex", cwd=self.root, codex_home=self.root / "codex")
        notifications = [
            {"method": "thread/started", "params": {"thread": {"id": "sub", "parentThreadId": "x"}}},
            {"method": "thread/started", "params": {"thread": {"id": "user-thread"}}},
        ]
        client = SimpleNamespace(next_notification=lambda timeout=0.0: notifications.pop(0) if notifications else None,
                                 resume_thread=lambda *_args, **_kw: {}, request=lambda *_args, **_kw: {"data": []})
        injector = CodexAppServerChannelInjector(
            lambda: client, CodexDesktopChannelPorts(
                read_messages=lambda *_: [], read_cursor=lambda: 0, commit_cursor=lambda *_: None,
                status=None, log=self.log, compaction=self.binding),
            cwd=str(self.root), version="test", start_own_thread=False)
        injector.client = client
        injector.poll_once()
        self.assertEqual("user-thread", self.binding.scope().session_id)
        self.assertFalse(self.binding.external_admission_paused())

    def test_injector_retries_thread_bind_until_its_launch_binds(self):
        # A resumed remote TUI opens the injector before the launch binds.
        client = SimpleNamespace(next_notification=lambda timeout=0.0: None, initialize=lambda **_kw: {},
                                 resume_thread=lambda *_args, **_kw: {}, request=lambda *_args, **_kw: {"data": []})
        injector = CodexAppServerChannelInjector(
            lambda: client, CodexDesktopChannelPorts(
                read_messages=lambda *_: [], read_cursor=lambda: 0, commit_cursor=lambda *_: None,
                status=None, log=self.log, compaction=self.binding),
            cwd=str(self.root), version="test", start_own_thread=False, initial_thread_id="resumed")
        injector.open()
        self.binding.bind("codex", cwd=self.root, codex_home=self.root / "codex")
        self.assertIsNone(self.binding.scope())
        injector.poll_once()
        self.assertEqual("resumed", self.binding.scope().session_id)


if __name__ == "__main__":
    unittest.main()
