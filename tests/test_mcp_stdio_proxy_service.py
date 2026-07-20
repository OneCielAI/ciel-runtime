import unittest
from pathlib import Path

from ciel_runtime_support.mcp_proxy_process import (
    McpStdioConfigPorts,
    McpStdioEffects,
    McpStdioProxyService,
    McpStdioTransportPorts,
)


class _Process:
    def __init__(self, chunks: list[bytes], return_code: int = 0) -> None:
        self.stdout = self
        self._chunks = list(chunks)
        self.return_code = return_code
        self.terminated = False

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0)

    def wait(self) -> int:
        return self.return_code

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True


class McpStdioProxyServiceTests(unittest.TestCase):
    def _service(
        self,
        *,
        read,
        popen,
        logs: list[tuple[str, str]],
        errors: list[str],
        writes: list[bytes],
        observed: list[tuple[str, bytes]],
        threads: list[str],
    ) -> McpStdioProxyService:
        class Observer:
            def __init__(self, server_name: str) -> None:
                self.server_name = server_name

            def feed(self, chunk: bytes) -> None:
                observed.append((self.server_name, chunk))

        return McpStdioProxyService(
            config=McpStdioConfigPorts(
                read=read,
                is_stdio=lambda server: server.get("transport") == "stdio",
                resolve_process=lambda command, args: (f"/bin/{command}", args),
                environment=lambda: {"BASE": "1"},
            ),
            transport=McpStdioTransportPorts(
                popen=popen,
                stdio_mode=lambda server: str(server.get("mode") or "framed"),
                forward_stdin=lambda _proc: None,
                forward_stdin_jsonl=lambda _proc: None,
                forward_stdout_jsonl=lambda _name, _proc: None,
                forward_stderr=lambda _proc: None,
                observer=Observer,
            ),
            effects=McpStdioEffects(
                log=lambda level, message: logs.append((level, message)),
                error=errors.append,
                start_thread=lambda _target, _args, name: threads.append(name),
                write_stdout=writes.append,
                flush_stdout=lambda: None,
            ),
        )

    def test_invalid_config_is_reported_without_spawning(self):
        logs: list[tuple[str, str]] = []
        errors: list[str] = []
        service = self._service(
            read=lambda _path: {"transport": "http"},
            popen=lambda *_args, **_kwargs: self.fail("must not spawn"),
            logs=logs,
            errors=errors,
            writes=[],
            observed=[],
            threads=[],
        )

        self.assertEqual(2, service.run("demo", Path("server.json")))
        self.assertIn("mcp_proxy_invalid_config", logs[-1][1])
        self.assertIn("not a stdio MCP server", errors[-1])

    def test_spawn_failure_has_dedicated_exit_code(self):
        logs: list[tuple[str, str]] = []
        errors: list[str] = []
        service = self._service(
            read=lambda _path: {
                "transport": "stdio",
                "command": "missing",
            },
            popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                FileNotFoundError("missing")
            ),
            logs=logs,
            errors=errors,
            writes=[],
            observed=[],
            threads=[],
        )

        self.assertEqual(127, service.run("demo", Path("server.json")))
        self.assertIn("mcp_proxy_start_failed", logs[-1][1])
        self.assertIn("failed to start /bin/missing", errors[-1])

    def test_framed_transport_merges_environment_and_forwards_stdout(self):
        logs: list[tuple[str, str]] = []
        writes: list[bytes] = []
        observed: list[tuple[str, bytes]] = []
        threads: list[str] = []
        spawn: dict[str, object] = {}
        process = _Process([b"payload", b""], return_code=0)

        def popen(command, **kwargs):
            spawn["command"] = command
            spawn.update(kwargs)
            return process

        service = self._service(
            read=lambda _path: {
                "transport": "stdio",
                "command": "server",
                "args": ["--flag"],
                "env": {"TOKEN": "secret"},
                "workingDirectory": "workspace",
            },
            popen=popen,
            logs=logs,
            errors=[],
            writes=writes,
            observed=observed,
            threads=threads,
        )

        self.assertEqual(0, service.run("demo", Path("server.json")))
        self.assertEqual(["/bin/server", "--flag"], spawn["command"])
        self.assertEqual(
            {"BASE": "1", "TOKEN": "secret"},
            spawn["env"],
        )
        self.assertEqual("workspace", spawn["cwd"])
        self.assertEqual([b"payload"], writes)
        self.assertEqual([("demo", b"payload")], observed)
        self.assertEqual(
            ["mcp-proxy-stdin-demo", "mcp-proxy-stderr-demo"],
            threads,
        )
        self.assertFalse(process.terminated)
        self.assertEqual("INFO", logs[-1][0])


if __name__ == "__main__":
    unittest.main()
