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
