import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ciel_runtime_support.codex_app_server import (  # noqa: E402
    CodexAppServerClient,
    CodexAppServerError,
    CodexAppServerNotSent, CodexAppServerRejected, CodexAppServerUncertain,
    codex_app_server_launch_args,
    command_for_popen,
    responses_user_message_item,
    text_user_input,
)


class CapturingStdin:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        return None

    def close(self):
        self.closed = True
        return None


class FakeProcess:
    def __init__(self, stdout_text):
        self.stdin = CapturingStdin()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("")
        self.terminated = False
        self.waited = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True
        return None

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def kill(self):
        self.killed = True
        return None


class CodexAppServerSupportTests(unittest.TestCase):
    def test_missing_stdin_and_serialization_failure_are_proven_not_sent(self):
        process = FakeProcess("")
        client = CodexAppServerClient(process)
        process.stdin = None
        with self.assertRaises(CodexAppServerNotSent):
            client.request("turn/start", {})
        process.stdin = CapturingStdin()
        with self.assertRaises(CodexAppServerNotSent):
            client.request("turn/start", {"bad": object()})
        self.assertEqual([], process.stdin.writes)

    def test_write_flush_timeout_and_bad_response_are_uncertain(self):
        for boundary in ("write", "flush", "timeout", "response"):
            with self.subTest(boundary=boundary):
                text = '{"id":1,"result":null}' + chr(10) if boundary == "response" else ""
                process = FakeProcess(text)
                client = CodexAppServerClient(process)
                if boundary in {"write", "flush"}:
                    def fail(*args):
                        raise OSError("after attempted write")
                    setattr(process.stdin, boundary, fail)
                with self.assertRaises(CodexAppServerUncertain):
                    client.request("turn/start", {}, timeout=0.01)

    def test_explicit_rejection_retains_method_code_and_exact_no_active_proof(self):
        error = {"code": -32600, "message": "no active turn to steer"}
        client = CodexAppServerClient(FakeProcess(json.dumps({"id": 1, "error": error}) + chr(10)))
        with self.assertRaises(CodexAppServerRejected) as raised:
            client.turn_steer("thread", "turn", "message")
        self.assertEqual(error, raised.exception.error)
        self.assertTrue(raised.exception.no_active_turn)
        self.assertFalse(CodexAppServerRejected("turn/start", error).no_active_turn)
        self.assertFalse(CodexAppServerRejected("turn/steer", {**error, "code": -32603}).no_active_turn)
        self.assertFalse(CodexAppServerRejected("turn/steer", {**error, "message": "turn mismatch"}).no_active_turn)

    def test_windows_batch_launcher_preserves_embedded_config_quotes(self):
        with (
            mock.patch("ciel_runtime_support.codex_app_server.os.name", "nt"),
            mock.patch.dict("os.environ", {"COMSPEC": "C:\\Windows\\cmd.exe"}),
        ):
            command = command_for_popen(
                "C:\\Tools\\codex.cmd",
                ["-c", 'model_providers.ciel-runtime.name="Ciel Runtime Codex"'],
            )

        self.assertEqual(
            'C:\\Windows\\cmd.exe /d /s /c "C:\\Tools\\codex.cmd -c '
            '\"model_providers.ciel-runtime.name=\\\"Ciel Runtime Codex\\\"\""',
            command,
        )
        self.assertNotIn('\\\\\\"Ciel Runtime Codex', command)

    def test_close_reaps_process(self):
        process = FakeProcess("")
        client = CodexAppServerClient(process)  # type: ignore[arg-type]

        client.close()

        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertFalse(process.killed)

    def test_close_surfaces_cleanup_failure(self):
        process = FakeProcess("")
        process.stdin.close = lambda: (_ for _ in ()).throw(OSError("broken pipe"))
        client = CodexAppServerClient(process)  # type: ignore[arg-type]

        with self.assertRaisesRegex(CodexAppServerError, "stdin close failed"):
            client.close()

    def test_launch_args_add_default_listen_for_foreground_server(self):
        args = codex_app_server_launch_args(
            [],
            config_args=["-c", 'model_provider="ciel-runtime-codex"'],
            default_listen_url="ws://127.0.0.1:8899",
        )

        self.assertEqual(
            [
                "app-server",
                "-c",
                'model_provider="ciel-runtime-codex"',
                "--listen",
                "ws://127.0.0.1:8899",
            ],
            args,
        )

    def test_launch_args_do_not_add_listen_to_app_server_subcommand(self):
        args = codex_app_server_launch_args(
            ["daemon", "start"],
            config_args=["-c", 'model_provider="ciel-runtime-codex"'],
            default_listen_url="ws://127.0.0.1:8899",
        )

        self.assertEqual(["app-server", "-c", 'model_provider="ciel-runtime-codex"', "daemon", "start"], args)

    def test_text_payloads_match_codex_app_server_schema(self):
        self.assertEqual({"type": "text", "text": "hello"}, text_user_input("hello"))
        self.assertEqual(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
            responses_user_message_item("hello"),
        )

    def test_stdio_client_uses_codex_app_server_jsonrpc_shape(self):
        stdout_text = "\n".join(
            [
                json.dumps(
                    {
                        "id": 1,
                        "result": {
                            "userAgent": "codex-test",
                            "codexHome": "/tmp/codex",
                            "platformFamily": "unix",
                            "platformOs": "linux",
                        },
                    }
                ),
                json.dumps({"id": 2, "result": {}}),
                json.dumps({"method": "turn/started", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "running"}}}),
                "",
            ]
        )
        process = FakeProcess(stdout_text)
        client = CodexAppServerClient(process)  # type: ignore[arg-type]

        initialized = client.initialize(client_name="ciel-runtime", client_title="Ciel Runtime", client_version="0.test")
        self.assertEqual("codex-test", initialized["userAgent"])
        client.turn_start("thread-1", "hello", responsesapi_client_metadata={"source": "test"})

        sent = [json.loads(item) for write in process.stdin.writes for item in write.splitlines() if item]
        self.assertEqual("initialize", sent[0]["method"])
        self.assertNotIn("jsonrpc", sent[0])
        self.assertEqual("initialized", sent[1]["method"])
        self.assertEqual("turn/start", sent[2]["method"])
        self.assertEqual([{"type": "text", "text": "hello"}], sent[2]["params"]["input"])
        self.assertEqual({"source": "test"}, sent[2]["params"]["responsesapiClientMetadata"])


if __name__ == "__main__":
    unittest.main()
