import json
import os
from pathlib import Path, PurePosixPath
import socket
import tempfile
import threading
import unittest
from unittest import mock

from ciel_runtime_support.claude_session_socket import (
    ClaudeSessionSocketClient,
    generated_socket_path,
    prepared_socket_path,
    session_key_hash,
)


class ClaudeSessionSocketTests(unittest.TestCase):
    def test_platform_paths_match_claude_namespace(self):
        windows = generated_socket_path("nt")
        posix = generated_socket_path("posix")
        self.assertRegex(windows, r"^\\\\\.\\pipe\\LOCAL\\cc-msg-[0-9a-f]{32}$")
        self.assertTrue(PurePosixPath(posix).is_absolute())
        self.assertIn("cc-socks-", posix)

    def test_prepare_uses_explicit_path_and_supports_disable(self):
        target = r"\\.\pipe\LOCAL\cc-msg-0123456789abcdef0123456789abcdef"
        self.assertEqual(
            target,
            prepared_socket_path({}, ["--messaging-socket-path", target]),
        )

    def test_windows_key_hash_uses_claude_canonical_pipe_name(self):
        target = r"\\.\pipe\LOCAL\cc-msg-0123456789ABCDEF0123456789ABCDEF"
        canonical = r"\\.\pipe\local\cc-msg-0123456789abcdef0123456789abcdef"
        self.assertEqual(
            session_key_hash(canonical, "nt"),
            session_key_hash(target, "nt"),
        )
        self.assertEqual(
            "",
            prepared_socket_path(
                {"claude_code": {"session_socket_input": False}}, []
            ),
        )

    def test_send_reads_matching_key_and_writes_auth_then_user(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            target = r"\\.\pipe\LOCAL\cc-msg-0123456789abcdef0123456789abcdef"
            sessions = home / ".claude" / "sessions"
            sessions.mkdir(parents=True)
            token = "ab" * 16
            key = sessions / f"123.{session_key_hash(target, 'nt')}.key"
            key.write_text(json.dumps({"peerToken": token}), encoding="utf-8")
            client = ClaudeSessionSocketClient(
                home, lambda *_args: None, platform_name="nt"
            )
            client.configure(target)
            with mock.patch.object(client, "_write") as write:
                self.assertTrue(client.send("hello from web chat"))
            frames = write.call_args.args[1].decode("utf-8").splitlines()
            self.assertEqual({"type": "auth", "token": token}, json.loads(frames[0]))
            user = json.loads(frames[1])
            self.assertEqual("user", user["type"])
            self.assertEqual("hello from web chat", user["message"]["content"])
            self.assertNotIn("from", user)

    def test_side_intake_uses_verified_session_without_socket_user_frame(self):
        from ciel_runtime_support.claude_session_handoff import SessionHandoff
        with tempfile.TemporaryDirectory() as raw:
            target = r'\\.\pipe\LOCAL\fixture-side'
            intake = mock.Mock(return_value={'accepted': True, 'item_id': 'n10'})
            client = ClaudeSessionSocketClient(Path(raw), lambda *_: None, side_intake=intake)
            client.configure(target)
            record = {'sessionId': 'session-one', 'messagingSocketPath': target}
            with mock.patch.object(client, '_read_peer_token', return_value='ab' * 16), \
                 mock.patch('ciel_runtime_support.claude_session_handoff.socket_handoff', return_value=SessionHandoff(record)), \
                 mock.patch('ciel_runtime_support.claude_session_handoff._live_key', return_value=True), \
                 mock.patch.object(client, '_write') as write:
                self.assertTrue(client.send('/btw_with_mcp {"text":"request","event_key":"source:1"}',
                                            [{'meta': {'_side_intake_admitted': True}}]))
            write.assert_not_called()
            intake.assert_called_once_with('claude:session-one', 'request', 'source:1', {'source_kind': ''})

    def test_side_only_notice_substitutes_content_free_text(self):
        from ciel_runtime_support.claude_session_handoff import SessionHandoff
        with tempfile.TemporaryDirectory() as raw:
            target = r'\\.\pipe\LOCAL\fixture-side'
            intake = mock.Mock(return_value={'accepted': True, 'item_id': None})
            client = ClaudeSessionSocketClient(Path(raw), lambda *_: None, side_intake=intake)
            client.configure(target)
            record = {'sessionId': 'one', 'messagingSocketPath': target}
            with mock.patch.object(client, '_read_peer_token', return_value='ab' * 16), \
                 mock.patch('ciel_runtime_support.claude_session_handoff.socket_handoff', return_value=SessionHandoff(record)), \
                 mock.patch('ciel_runtime_support.claude_session_handoff._live_key', return_value=True), \
                 mock.patch.object(client, '_write') as write:
                self.assertTrue(client.send('ignored formatter prompt', [{'message': 'private-canary', 'meta': {
                    'side_intake_only': True, '_side_intake_admitted': True, 'event_key': 'source:huge',
                    'side_intake_text': '{"ciel_runtime_notice":"external_event_too_large_for_side_inbox"}',
                    'side_intake_fallback': 'notice'}}]))
            write.assert_not_called()
            args = intake.call_args.args
            self.assertNotIn('private-canary', args[1])
            self.assertEqual('notice', args[3]['side_intake_fallback'])

    def test_side_web_reply_route_uses_original_message_not_parent_guess(self):
        from ciel_runtime_support.claude_session_handoff import SessionHandoff
        with tempfile.TemporaryDirectory() as raw:
            target=r'\\\\.\\pipe\\LOCAL\\fixture-side'
            intake=mock.Mock(return_value={'accepted':True,'item_id':None})
            client=ClaudeSessionSocketClient(Path(raw),lambda *_:None,side_intake=intake)
            client.configure(target)
            record={'sessionId':'one','messagingSocketPath':target}
            with mock.patch.object(client,'_read_peer_token',return_value='ab'*16), \
                 mock.patch('ciel_runtime_support.claude_session_handoff.socket_handoff',return_value=SessionHandoff(record)), \
                 mock.patch('ciel_runtime_support.claude_session_handoff._live_key',return_value=True):
                self.assertTrue(client.send('/btw_with_mcp {"text":"q","event_key":"e"}',[
                    {'id':42,'channel':'web','thread_id':'thread','meta':{'_side_intake_admitted':True,
                     'source_kind':'web_chat','web_reply_token':'private'}}]))
            route=intake.call_args.args[3]
            self.assertEqual('42',route['parent_id'])
            self.assertEqual('private',route['reply_token'])
            self.assertEqual('thread',route['thread_id'])

    def test_side_intake_rejects_unverified_identity_without_fallback(self):
        from ciel_runtime_support.claude_session_handoff import SessionHandoff
        with tempfile.TemporaryDirectory() as raw:
            target = r'\\.\pipe\LOCAL\fixture-side'
            intake = mock.Mock()
            client = ClaudeSessionSocketClient(Path(raw), lambda *_: None, side_intake=intake)
            client.configure(target)
            record = {'sessionId': 'session-one', 'messagingSocketPath': target}
            with mock.patch.object(client, '_read_peer_token', return_value='ab' * 16), \
                 mock.patch('ciel_runtime_support.claude_session_handoff.socket_handoff', return_value=SessionHandoff(record)), \
                 mock.patch('ciel_runtime_support.claude_session_handoff._live_key', return_value=False), \
                 mock.patch.object(client, '_write') as write:
                self.assertFalse(client.send('/btw_with_mcp {"text":"request","event_key":"source:1"}',
                                             [{'meta': {'_side_intake_admitted': True}}]))
            write.assert_not_called()
            intake.assert_not_called()

    def test_full_board_never_falls_back_to_main_session(self):
        from ciel_runtime_support.claude_session_handoff import SessionHandoff
        from ciel_runtime_support.work_plan_tree import WorkPlanCapacityError
        with tempfile.TemporaryDirectory() as raw:
            target = r'\\.\pipe\LOCAL\fixture-side'
            intake = mock.Mock(side_effect=WorkPlanCapacityError('full'))
            client = ClaudeSessionSocketClient(Path(raw), lambda *_: None, side_intake=intake)
            client.configure(target)
            record = {'sessionId': 'session-one', 'messagingSocketPath': target}
            with mock.patch.object(client, '_read_peer_token', return_value='ab' * 16), \
                 mock.patch('ciel_runtime_support.claude_session_handoff.socket_handoff', return_value=SessionHandoff(record)), \
                 mock.patch('ciel_runtime_support.claude_session_handoff._live_key', return_value=True), \
                 mock.patch.object(client, '_write') as write:
                self.assertFalse(client.send('/btw_with_mcp {"text":"/goal clear external data","event_key":"source:1"}',
                                             [{'meta': {'_side_intake_admitted': True}}]))
            write.assert_not_called()

    def test_side_board_capacity_error_retains_pending_without_socket_write(self):
        from ciel_runtime_support.claude_session_handoff import SessionHandoff
        from ciel_runtime_support.work_plan_tree import WorkPlanError
        with tempfile.TemporaryDirectory() as raw:
            target = r'\\.\pipe\LOCAL\fixture-side'
            logs = []
            intake = mock.Mock(side_effect=WorkPlanError('a work plan holds at most 100 unfinished nodes'))
            client = ClaudeSessionSocketClient(Path(raw), lambda *args: logs.append(args), side_intake=intake)
            client.configure(target)
            record = {'sessionId': 'session-one', 'messagingSocketPath': target}
            with mock.patch.object(client, '_read_peer_token', return_value='ab' * 16), \
                 mock.patch('ciel_runtime_support.claude_session_handoff.socket_handoff', return_value=SessionHandoff(record)), \
                 mock.patch('ciel_runtime_support.claude_session_handoff._live_key', return_value=True), \
                 mock.patch.object(client, '_write') as write:
                self.assertFalse(client.send('/btw_with_mcp {"text":"request","event_key":"source:1"}',
                                             [{'meta': {'_side_intake_admitted': True}}]))
            write.assert_not_called()
            self.assertTrue(any('retain_pending' in message for _, message in logs))

    def test_send_defers_until_key_is_published(self):
        with tempfile.TemporaryDirectory() as raw:
            client = ClaudeSessionSocketClient(
                Path(raw), lambda *_args: None, platform_name="nt"
            )
            client.configure(
                r"\\.\pipe\LOCAL\cc-msg-0123456789abcdef0123456789abcdef"
            )
            with mock.patch.object(client, "_write") as write:
                self.assertFalse(client.send("not yet"))
            write.assert_not_called()

    def test_socket_without_a_session_key_is_unavailable_and_reported_once(self):
        # Emily 2026-10-04: no key for 30 hours, every message waited silently.
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            logs: list[tuple[str, str]] = []
            now = [100.0]
            target = r"\\.\pipe\LOCAL\cc-msg-0123456789abcdef0123456789abcdef"
            client = ClaudeSessionSocketClient(
                home, lambda level, message: logs.append((level, message)), platform_name="nt", clock=lambda: now[0]
            )
            client.configure(target)
            self.assertFalse(client.available())
            self.assertFalse(client.available())
            errors = [message for level, message in logs if level == "ERROR"]
            self.assertEqual(1, len(errors))
            self.assertIn("reason=auth_key_unavailable", errors[0])
            self.assertIn("fallback=terminal", errors[0])

            sessions = home / ".claude" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / f"9.{session_key_hash(target, 'nt')}.key").write_text(json.dumps({"peerToken": "cd" * 16}), encoding="utf-8")
            self.assertTrue(client.available())
            self.assertTrue(any("claude_session_socket_available" in message for _level, message in logs))

            with mock.patch.object(client, "_write", side_effect=OSError("pipe busy")):
                self.assertFalse(client.send("hello"))
            self.assertFalse(client.available())  # cooling down after a failed connect
            now[0] += 31.0
            self.assertTrue(client.available())

    @unittest.skipIf(os.name == "nt", "POSIX AF_UNIX transport runs on Unix")
    def test_send_over_real_posix_unix_socket(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            target = str(home / "claude-session.sock")
            sessions = home / ".claude" / "sessions"
            sessions.mkdir(parents=True)
            token = "cd" * 16
            key = sessions / f"123.{session_key_hash(target, 'posix')}.key"
            key.write_text(json.dumps({"peerToken": token}), encoding="utf-8")
            received = bytearray()
            ready = threading.Event()

            def serve() -> None:
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    server.bind(target)
                    server.listen(1)
                    ready.set()
                    connection, _address = server.accept()
                    with connection:
                        while chunk := connection.recv(4096):
                            received.extend(chunk)
                finally:
                    server.close()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2.0))
            client = ClaudeSessionSocketClient(
                home, lambda *_args: None, platform_name="posix"
            )
            client.configure(target)
            self.assertTrue(client.send("hello over AF_UNIX"))
            thread.join(2.0)
            self.assertFalse(thread.is_alive())
            frames = received.decode("utf-8").splitlines()
            self.assertEqual({"type": "auth", "token": token}, json.loads(frames[0]))
            self.assertEqual(
                "hello over AF_UNIX", json.loads(frames[1])["message"]["content"]
            )


if __name__ == "__main__":
    unittest.main()
