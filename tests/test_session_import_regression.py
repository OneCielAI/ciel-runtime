"""Synthetic import fixtures; no runtime launch, user homes, or conversation reads."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_schemas, dispatch_channel_mcp_tool
from ciel_runtime_support.request_shortcuts import ShortcutTextServices, import_session_args
from ciel_runtime_support.session_import import ImportSessionLimits, ImportSessionRepository, ImportSessionService, ImportSessionHttpController, ImportSessionHttpPorts
from ciel_runtime_support.slash_command_assets import IMPORT_SESSION_SLASH_COMMAND


class SessionImportRegressionTests(unittest.TestCase):
    def repository(self, home, max_bytes=8192, max_chars=8192):
        return ImportSessionRepository(Path(home), {"CODEX_HOME": str(Path(home) / "codex")}, ImportSessionLimits(max_bytes, max_chars))

    def service(self, repository):
        return ImportSessionService(repository, lambda body: import_session_args(
            body, ("CIEL_RUNTIME_IMPORT_SESSION",),
            ShortcutTextServices(latest_user_text=lambda b: b["text"]), posix=False))

    def template(self, source, path=""):
        arguments = source + (f' "{path}"' if path else "")
        return {"stream": False, "text": IMPORT_SESSION_SLASH_COMMAND.replace("$ARGUMENTS", arguments).replace("$1", source).replace("$2", path)}

    def test_shipped_template_runs_real_import_http_controller(self):
        with TemporaryDirectory() as home:
            repository = self.repository(home)
            for source, client, record in (
                ("Codex", "claude", {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "synthetic codex task"}]}}),
                ("Claude", "codex", {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "synthetic claude answer"}]}}),
            ):
                with self.subTest(source=source):
                    path = Path(home) / (source + " fixture.jsonl")
                    path.write_text(json.dumps(record), encoding="utf-8")
                    writes = []
                    controller = ImportSessionHttpController(ImportSessionHttpPorts(
                        is_request=lambda b: "CIEL_RUNTIME_IMPORT_SESSION" in b["text"],
                        response_text=self.service(repository).response_text,
                        load_config=lambda: {}, current_alias=lambda _: "synthetic-model",
                        current_provider=lambda _: ("synthetic-provider", {}),
                        estimate_tokens=lambda _: 1, write_openai=lambda *a, **k: None,
                        write_anthropic=lambda *a: writes.append(a), publish_event=lambda **k: None))
                    self.assertTrue(controller.handle(object(), self.template(source, str(path)), client_runtime=client))
                    self.assertIn("ImportSession completed", writes[0][2])
                    self.assertIn("synthetic", writes[0][2])

    def test_real_mcp_http_handler_has_no_import_tool(self):
        writes = []
        controller = ChannelMcpHttpController(ChannelMcpHttpServices(
            version="synthetic", tool_schemas=channel_mcp_tool_schemas,
            tool_call_response=lambda ident, params: dispatch_channel_mcp_tool(ident, params, None),
            write_json=lambda h, payload, status=200: writes.append(payload),
            write_accepted=lambda h: None, log=lambda *a: None))
        handler = SimpleNamespace(headers={})
        controller.post(handler, "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertFalse(any("import" in tool["name"].lower() for tool in writes[-1]["result"]["tools"]))
        for name in ("importsession", "ImportSession", "import_session"):
            controller.post(handler, "/ca/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": {"source": "Codex", "path": "synthetic.jsonl"}}})
            self.assertTrue(writes[-1]["result"]["isError"])
            self.assertIn("Unknown ciel-runtime-router tool", writes[-1]["result"]["content"][0]["text"])

    def test_latest_directory_missing_url_and_same_runtime_policy(self):
        with TemporaryDirectory() as home:
            repository = self.repository(home)
            root = Path(home) / "codex" / "sessions" / "2026"
            root.mkdir(parents=True)
            path = root / "synthetic.jsonl"
            path.write_text('{"type":"event_msg","payload":{"type":"user_message","message":"synthetic"}}')
            self.assertEqual(path, repository.resolve("codex", "")[0])
            self.assertEqual(path, repository.resolve("codex", str(root))[0])
            self.assertIn("not found", repository.resolve("codex", str(root / "missing"))[1])
            self.assertIn("not URLs", repository.resolve("codex", "https://example.invalid/session")[1])
            self.assertIn("blocked", self.service(repository).response_text("claude", self.template("Claude", str(path))))
            self.assertIn("Usage:", self.service(repository).response_text("codex", self.template("Muse", str(path))))

    def test_byte_and_character_truncation(self):
        with TemporaryDirectory() as home:
            path = Path(home) / "synthetic.jsonl"
            def row(text):
                return json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": text}})

            path.write_text(row("old" * 100) + chr(10) + row("synthetic tail"), encoding="utf-8")
            text, meta = self.repository(home, max_bytes=130, max_chars=12).read("codex", path)
            self.assertTrue(meta["byte_truncated"])
            self.assertTrue(meta["char_truncated"])
            self.assertIn("tail", text)
            self.assertEqual(1, meta["records"])

    def test_permission_error_while_reading_is_reported(self):
        with TemporaryDirectory() as home:
            path = Path(home) / "synthetic.jsonl"
            path.touch()
            repository = self.repository(home)
            with patch.object(repository, "read", side_effect=PermissionError("synthetic denied")):
                text = self.service(repository).response_text("claude", self.template("Codex", str(path)))
            self.assertIn("PermissionError", text)

    def test_oversized_single_record_falls_back_to_partial_raw_json(self):
        with TemporaryDirectory() as home:
            path = Path(home) / "synthetic.jsonl"
            path.write_text(json.dumps({"type": "user", "message": {"content": "synthetic" * 100}}))
            text, meta = self.repository(home, max_bytes=60).read("claude", path)
            self.assertFalse(meta["jsonl"])
            self.assertEqual(0, meta["records"])
            self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()
