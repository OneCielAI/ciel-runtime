import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from ciel_runtime_support.claude_session_handoff import socket_handoff
from ciel_runtime_support.claude_session_socket import ClaudeSessionSocketClient, SessionHandoffPending, session_key_hash

class HandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.directory = self.home / ".claude" / "sessions"
        self.directory.mkdir(parents=True)
        self.original = dict(pid=111, sessionId="original", cwd=str(self.home), procStart="1111", startedAt=1000,
                             pidDomain="test", messagingSocketPath=str(self.home / "original.sock"),
                             kind="interactive", parkedJobId="12345678")
        self.child = dict(pid=222, sessionId="12345678-1234-4234-8234-123456789abc", cwd=str(self.home),
                          procStart="2222", startedAt=2000, pidDomain="test", kind="bg", jobId="12345678",
                          messagingSocketPath=str(self.home / "bg.sock"))
        self.publish(self.original)
        self.publish(self.child)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        patch = mock.patch("ciel_runtime_support.claude_session_handoff._process_identity", return_value="2222")
        patch.start()
        self.addCleanup(patch.stop)

    def publish(self, record):
        pid = record["pid"]
        (self.directory / (str(pid) + ".json")).write_text(json.dumps(record), encoding="utf-8")
        digest = session_key_hash(record["messagingSocketPath"])
        key = self.directory / (str(pid) + "." + digest + ".key")
        key.write_text(json.dumps(dict(peerToken="ab" * 16, procStartFt=record["procStart"], pidDomain=record["pidDomain"])))

    def result(self):
        return socket_handoff(self.directory, self.original["messagingSocketPath"])

    def test_exact_peer_not_latest(self):
        self.publish(dict(self.child, pid=333, jobId="other", sessionId="other"))
        self.assertEqual(self.child, self.result().record)
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        with mock.patch.object(client, "_write") as write:
            self.assertTrue(client.send("owned prompt"))
        self.assertEqual(self.child["messagingSocketPath"], write.call_args.args[0])

    def test_missing_wrong_or_ambiguous_peer_holds(self):
        for changes in (dict(kind="interactive"), dict(jobId="other"), dict(cwd="other"),
                        dict(pidDomain="other"), dict(sessionId="other"), dict(startedAt=1)):
            self.publish(dict(self.child, **changes))
            self.assertTrue(self.result().pending, changes)
        self.publish(self.child)
        self.publish(dict(self.child, pid=333))
        self.assertTrue(self.result().pending)

    def test_dead_unknown_or_reused_pid_holds(self):
        for identity in (None, "", "reused"):
            with mock.patch("ciel_runtime_support.claude_session_handoff._process_identity", return_value=identity):
                self.assertTrue(self.result().pending)

    def test_no_handoff_keeps_original(self):
        self.original.pop("parkedJobId")
        self.publish(self.original)
        self.assertEqual(self.original, self.result().record)

    def test_pending_reserves_transport_without_write(self):
        (self.directory / "222.json").unlink()
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        self.assertTrue(client.available())
        with mock.patch.object(client, "_write") as write:
            with self.assertRaises(SessionHandoffPending):
                client.send("hold")
        write.assert_not_called()

    def test_pending_send_requeues_only_unwritten_attempt(self):
        import dataclasses
        from test_channel_pending_injection import _InjectionHarness, _message
        from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages
        from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
        (self.directory / "222.json").unlink()
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        message = _message(41)
        message["meta"] = dict(input_transport="session_socket")
        harness = _InjectionHarness([message])
        base = harness.services()
        import threading
        status = RuntimeInputStatusRepository(self.home / "status.jsonl", lambda **_: None, lambda *_: None, threading.RLock())
        status.transition(41, "queued")
        harness.delivery.status = status
        harness.delivery.bind_child_epoch("owned-epoch")
        services = dataclasses.replace(base, io=dataclasses.replace(base.io, write_session_socket=client.send,
                                                                   session_socket_available=client.available))
        self.assertEqual(40, inject_pending_channel_messages(99, 40, services=services))
        self.assertEqual("queued", status.get(41)["status"])
        self.assertEqual("native_handoff_pending", status.get(41)["reason"])
        self.assertNotIn("attempt_id", status.get(41).get("data", {}))
        self.assertFalse(harness.delivery.is_delivered(41))
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)
        self.publish(self.child)
        with mock.patch.object(client, "_write"):
            self.assertEqual(41, inject_pending_channel_messages(99, 40, services=services))
        self.assertEqual("submitted", status.get(41)["status"])

    def test_key_identity_mismatch_holds(self):
        digest = session_key_hash(self.child["messagingSocketPath"])
        key = self.directory / ("222." + digest + ".key")
        key.write_text(json.dumps(dict(peerToken="ab" * 16, procStartFt="wrong", pidDomain="test")))
        self.assertTrue(self.result().pending)

    def test_cycles_and_overlong_chains_hold(self):
        self.child["parkedJobId"] = "12345678"
        self.publish(self.child)
        self.assertTrue(self.result().pending)

    def test_inherited_config_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home / ".claude")
        client = ClaudeSessionSocketClient(self.home / "other", lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        with mock.patch.object(client, "_write") as write:
            self.assertTrue(client.send("config-dir"))
        self.assertEqual(self.child["messagingSocketPath"], write.call_args.args[0])

    def test_background_write_failure_never_falls_back_to_original(self):
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        with mock.patch.object(client, "_write", side_effect=OSError("busy")):
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                client.send("no tty replay")
        self.assertTrue(client.available())

    def test_defer_does_not_release_different_or_terminal_owner(self):
        import threading
        from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
        status = RuntimeInputStatusRepository(self.home / "owner-status.jsonl", lambda **_: None, lambda *_: None, threading.RLock())
        status.transition(1, "queued")
        attempt = status.begin_attempt([1], "owner")
        self.assertFalse(status.defer_unsubmitted(1, "other"))
        self.assertEqual("submitted", status.get(1)["status"])
        status.transition(1, "replied", expected_attempt=attempt)
        self.assertFalse(status.defer_unsubmitted(1, attempt))
        self.assertEqual("replied", status.get(1)["status"])

    def test_wake_reader_uses_same_successor_identity_without_new_epoch(self):
        from types import SimpleNamespace
        from ciel_runtime_support.channel_wake_context import ChannelWakeContext
        from ciel_runtime_support.channel_transcript_repository import ChannelTranscriptRepository
        path = self.home / "successor.jsonl"
        native = SimpleNamespace(runtime="claude", session_id=self.child["sessionId"])
        binding = SimpleNamespace(transcript_path=lambda:path, scope=lambda:native)
        transcript = SimpleNamespace(scope=dict(runtime="claude", child_epoch="launch-epoch", session_id="original"), cache={}, now=lambda:100.0)
        repository = ChannelTranscriptRepository(self.home, transcript.cache, transcript.scope, transcript.now)
        context = SimpleNamespace(pending_io=SimpleNamespace(compaction=binding), transcript=transcript, transcript_repository=lambda:repository)
        self.assertEqual(path, ChannelWakeContext.latest_transcript_path(context))
        self.assertEqual(native.session_id, transcript.scope["session_id"])
        self.assertEqual("launch-epoch", transcript.scope["child_epoch"])
        self.assertEqual(path, transcript.scope["bound_path"])

    def test_seen_published_record_never_falls_back_during_read_gap(self):
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        self.assertEqual(self.child["messagingSocketPath"], client.target)
        (self.directory / "111.json").write_text("{", encoding="utf-8")
        with mock.patch.object(client, "_write") as write:
            with self.assertRaises(SessionHandoffPending):
                client.send("held during rewrite")
        write.assert_not_called()
        self.publish(self.original)
        self.assertEqual(self.child["messagingSocketPath"], client.target)

    def test_background_connect_failure_is_definitely_unwritten(self):
        from ciel_runtime_support.claude_session_socket import SocketConnectFailed
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(self.original["messagingSocketPath"])
        with mock.patch.object(client, "_write", side_effect=SocketConnectFailed("busy")):
            with self.assertRaises(SessionHandoffPending):
                client.send("retry only unwritten")

    def test_socket_connect_error_classifies_before_write(self):
        from ciel_runtime_support.claude_session_socket import SocketConnectFailed
        client = ClaudeSessionSocketClient(self.home, lambda *_: None, platform_name="nt")
        with mock.patch("builtins.open", side_effect=OSError("busy")):
            with self.assertRaises(SocketConnectFailed):
                client._write("unused", b"not written")

    def test_canonical_original_coordinate_keeps_key_missing_fallback(self):
        self.original.pop("parkedJobId")
        self.publish(self.original)
        client = ClaudeSessionSocketClient(self.home, lambda *_: None)
        client.configure(str(self.home / "sub" / ".." / "original.sock"))
        with mock.patch.object(client, "_read_peer_token", return_value=""):
            self.assertFalse(client.available())
            self.assertFalse(client.send("legacy fallback"))

    def test_first_resolution_of_unreadable_key_owner_holds(self):
        (self.directory / "111.json").write_text("{", encoding="utf-8")
        self.assertTrue(self.result().pending)
