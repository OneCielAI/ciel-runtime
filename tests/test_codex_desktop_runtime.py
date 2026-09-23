from __future__ import annotations

import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

from ciel_runtime_support.codex_app_server_websocket import (
    CodexWebSocketConnection,
    CodexWebSocketError,
    encode_client_frame,
    ws_endpoint,
)
from ciel_runtime_support.codex_desktop_runtime import (
    CodexDesktopLaunchError,
    CodexDesktopPorts,
    CodexDesktopSession,
    codex_desktop_paths,
    desktop_app_command,
    desktop_app_env,
    find_codex_desktop_app,
    listen_url_from_command,
    prepare_codex_home,
    readyz_url,
    wait_until_ready,
)
from ciel_runtime_support.launch_state import last_launch_runtime


class _Proc:
    def __init__(self, pid: int, *, exit_code: int = 0, alive: bool = True) -> None:
        self.pid = pid
        self._exit_code = exit_code
        self._alive = alive

    def poll(self):
        return None if self._alive else self._exit_code

    def wait(self):
        self._alive = False
        return self._exit_code


class CodexDesktopHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "user-codex"
        self.source.mkdir()
        (self.source / "config.toml").write_text('model = "gpt-6"\n', encoding="utf-8")
        (self.source / "auth.json").write_text('{"v": 1}', encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_paths_are_per_workspace_under_the_config_dir(self):
        paths = codex_desktop_paths(self.root / "cfg", "abc123")
        self.assertEqual(self.root / "cfg" / "codex-desktop" / "abc123" / "codex-home", paths.codex_home)
        self.assertEqual(self.root / "cfg" / "codex-desktop" / "abc123" / "electron-user-data", paths.user_data)

    def test_config_is_seeded_once_and_auth_follows_the_source(self):
        paths = codex_desktop_paths(self.root / "cfg", "ws")
        self.assertEqual(["config.toml", "auth.json"], prepare_codex_home(paths, self.source))
        (paths.codex_home / "config.toml").write_text('model = "edited-in-app"\n', encoding="utf-8")
        (self.source / "auth.json").write_text('{"v": 2}', encoding="utf-8")
        later = (paths.codex_home / "auth.json").stat().st_mtime + 10
        os.utime(self.source / "auth.json", (later, later))
        self.assertEqual(["auth.json"], prepare_codex_home(paths, self.source))
        self.assertEqual('model = "edited-in-app"\n', (paths.codex_home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual('{"v": 2}', (paths.codex_home / "auth.json").read_text(encoding="utf-8"))
        self.assertEqual('model = "gpt-6"\n', (self.source / "config.toml").read_text(encoding="utf-8"))
        self.assertTrue(paths.user_data.is_dir())
        self.assertTrue(paths.logs.is_dir())


class CodexDesktopLaunchPieceTests(unittest.TestCase):
    def test_listen_url_is_read_from_either_flag_form(self):
        self.assertEqual("ws://127.0.0.1:9489", listen_url_from_command(["codex", "app-server", "--listen", "ws://127.0.0.1:9489"]))
        self.assertEqual("ws://127.0.0.1:1", listen_url_from_command(["codex", "app-server", "--listen=ws://127.0.0.1:1"]))
        self.assertEqual("", listen_url_from_command(["codex", "app-server"]))

    def test_readyz_needs_a_websocket_listener(self):
        self.assertEqual("http://127.0.0.1:9489/readyz", readyz_url("ws://127.0.0.1:9489"))
        with self.assertRaises(CodexDesktopLaunchError):
            readyz_url("stdio://")

    def test_app_env_isolates_home_profile_and_backend_without_touching_the_input(self):
        paths = codex_desktop_paths(Path("C:/cfg"), "ws")
        base = {"PATH": "x", "CODEX_APP_SERVER_FORCE_CLI": "1", "CODEX_CLI_PATH": "c:/other/codex.exe"}
        env = desktop_app_env(base, paths, "ws://127.0.0.1:9489")
        self.assertEqual(str(paths.codex_home), env["CODEX_HOME"])
        self.assertEqual(str(paths.user_data), env["CODEX_ELECTRON_USER_DATA_PATH"])
        self.assertEqual("ws://127.0.0.1:9489", env["CODEX_APP_SERVER_WS_URL"])
        self.assertNotIn("CODEX_APP_SERVER_FORCE_CLI", env)
        self.assertNotIn("CODEX_CLI_PATH", env)
        self.assertIn("CODEX_APP_SERVER_FORCE_CLI", base)

    def test_app_command_passes_the_chromium_profile_switch(self):
        paths = codex_desktop_paths(Path("C:/cfg"), "ws")
        exe = Path("C:/app/ChatGPT.exe")
        self.assertEqual([str(exe), f"--user-data-dir={paths.user_data}"], desktop_app_command(exe, paths))

    def test_wait_until_ready_polls_until_200_and_stops_when_the_server_dies(self):
        attempts: list[str] = []

        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def urlopen(url, timeout):
            attempts.append(url)
            if len(attempts) < 3:
                raise OSError("refused")
            return _Response()

        self.assertTrue(wait_until_ready("http://h/readyz", urlopen=urlopen, sleep=lambda _s: None))
        self.assertEqual(3, len(attempts))
        self.assertFalse(wait_until_ready("http://h/readyz", alive=lambda: False, urlopen=urlopen, sleep=lambda _s: None))

    def test_find_app_prefers_the_override_then_the_store_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "app" / "ChatGPT.exe"
            exe.parent.mkdir()
            exe.write_text("", encoding="utf-8")
            self.assertEqual(exe, find_codex_desktop_app({"CIEL_RUNTIME_CODEX_DESKTOP_EXE": str(exe)}))
            self.assertIsNone(find_codex_desktop_app({"CIEL_RUNTIME_CODEX_DESKTOP_EXE": str(exe) + ".missing"}))

            class _Done:
                stdout = tmp + "\r\n"

            self.assertEqual(exe, find_codex_desktop_app({}, run=lambda *_a, **_k: _Done(), platform_name="nt"))
        self.assertIsNone(find_codex_desktop_app({}, run=lambda *_a, **_k: None, platform_name="posix"))

    def test_last_runtime_remembers_the_desktop_app(self):
        class _Repo:
            def read(self):
                return {}

            def previous_for_cwd(self, _key):
                return {"mode": "codex-desktop-router"}

        self.assertEqual("codex-desktop", last_launch_runtime(_Repo(), "k"))


class CodexDesktopSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []
        self.terminated: list[int] = []
        self.ready_checks: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _session(self, *, app: Path | None, ready: bool = True) -> CodexDesktopSession:
        def popen(cmd, env, cwd, stdout, stderr):
            kind = "app" if str(cmd[0]).endswith("ChatGPT.exe") else "server"
            self.calls.append((kind, list(cmd), dict(env)))
            return _Proc(100 if kind == "server" else 200)

        def wait_ready(url, alive):
            self.ready_checks.append(url)
            self.assertEqual(["server"], [kind for kind, _cmd, _env in self.calls])
            return ready

        return CodexDesktopSession(
            CodexDesktopPorts(
                config_dir=self.root / "cfg",
                workspace_id="ws1",
                source_codex_home=self.root / "missing-home",
                version="test",
                log=lambda _level, _message: None,
                terminate_tree=self.terminated.append,
                channel=None,
                find_app=lambda _env: app,
                popen=popen,
                wait_ready=wait_ready,
            )
        )

    def test_server_starts_in_the_isolated_home_before_the_app_attaches(self):
        cmd = ["codex.exe", "app-server", "-c", 'model_provider="ciel-runtime"', "--listen", "ws://127.0.0.1:9489"]
        rc = self._session(app=Path("C:/app/ChatGPT.exe"))(cmd, {"PATH": "p"}, self.root)
        self.assertEqual(0, rc)
        self.assertEqual(["server", "app"], [kind for kind, _cmd, _env in self.calls])
        home = str(self.root / "cfg" / "codex-desktop" / "ws1" / "codex-home")
        self.assertEqual(cmd, self.calls[0][1])
        self.assertEqual(home, self.calls[0][2]["CODEX_HOME"])
        self.assertEqual("ws://127.0.0.1:9489", self.calls[1][2]["CODEX_APP_SERVER_WS_URL"])
        self.assertEqual(["http://127.0.0.1:9489/readyz"], self.ready_checks)
        self.assertEqual([100], self.terminated)

    def test_missing_app_starts_nothing(self):
        self.assertEqual(2, self._session(app=None)(["codex", "app-server", "--listen", "ws://127.0.0.1:1"], {}, self.root))
        self.assertEqual([], self.calls)

    def test_unready_server_is_stopped_and_the_app_never_starts(self):
        rc = self._session(app=Path("C:/app/ChatGPT.exe"), ready=False)(["codex", "app-server", "--listen", "ws://127.0.0.1:1"], {}, self.root)
        self.assertEqual(1, rc)
        self.assertEqual(["server"], [kind for kind, _cmd, _env in self.calls])
        self.assertEqual([100], self.terminated)


class CodexWebSocketTests(unittest.TestCase):
    def test_endpoint_parsing_accepts_only_plain_ws(self):
        self.assertEqual(("127.0.0.1", 9489, "/"), ws_endpoint("ws://127.0.0.1:9489"))
        with self.assertRaises(CodexWebSocketError):
            ws_endpoint("wss://127.0.0.1:9489")

    def test_client_frames_are_masked_with_extended_lengths(self):
        mask = b"\x01\x02\x03\x04"
        short = encode_client_frame(0x1, b"hi", mask)
        self.assertEqual(bytes([0x81, 0x82]) + mask + bytes([ord("h") ^ 1, ord("i") ^ 2]), short)
        medium = encode_client_frame(0x1, b"x" * 300, mask)
        self.assertEqual((0x81, 0xFE, 300), (medium[0], medium[1], struct.unpack("!H", medium[2:4])[0]))

    def test_receive_handles_fragments_ping_and_close(self):
        client_sock, server_sock = socket.socketpair()
        connection = CodexWebSocketConnection(client_sock, random_bytes=lambda n: b"\x00" * n)
        server_sock.sendall(bytes([0x01, 3]) + b'{"a' + bytes([0x89, 1]) + b"p" + bytes([0x80, 3]) + b'":1' + bytes([0x81, 1]) + b"}")
        self.assertEqual('{"a":1', connection.receive_text())
        pong = server_sock.recv(64)
        self.assertEqual(0x8A, pong[0])
        self.assertEqual("}", connection.receive_text())
        server_sock.sendall(bytes([0x88, 0]))
        self.assertIsNone(connection.receive_text())
        self.assertTrue(connection.closed)
        connection.close()
        server_sock.close()

    def test_handshake_sends_no_origin_header(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        seen: list[bytes] = []

        def serve():
            conn, _addr = listener.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            seen.append(data)
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            conn.sendall(bytes([0x81, 2]) + b"ok")
            conn.recv(64)
            conn.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        connection = CodexWebSocketConnection.connect(f"ws://127.0.0.1:{port}")
        self.assertEqual("ok", connection.receive_text())
        connection.close()
        thread.join(5)
        listener.close()
        self.assertNotIn(b"origin:", seen[0].lower())
        self.assertIn(b"sec-websocket-version: 13", seen[0].lower())


if __name__ == "__main__":
    unittest.main()
