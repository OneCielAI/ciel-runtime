import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import urllib.error

from ciel_runtime_support.remote_instructions import (
    RemoteInstructionSynchronizer,
    SynchronizedLaunch,
    expand_environment_references,
    normalized_instruction_sha256,
    panel_rows,
)


class _Response:
    def __init__(self, body: bytes, *, url: str = "https://config.example/agents", headers=None):
        self._body = io.BytesIO(body)
        self._url = url
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return self._url


class RemoteInstructionTests(unittest.TestCase):
    def _service(self, root: Path, config: dict, opener):
        return RemoteInstructionSynchronizer(
            load_config=lambda: config,
            workspace=lambda: root / "workspace",
            state_dir=root / "state",
            log=lambda *_args: None,
            urlopen=opener,
        )

    def test_downloads_codex_agents_file_with_authorization_header(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            "os.environ", {"SYSTEM_PROMPT_AUTH": "secret-token"}, clear=False
        ):
            root = Path(td)
            requests = []
            def opener(request, **_kwargs):
                requests.append(request)
                return _Response(b"# Managed instructions\n", headers={"etag": '"v1"'})
            config = {"remote_instructions": {
                "enabled": True,
                "codex_url": "https://config.example/agents",
                "authorization": "Bearer %SYSTEM_PROMPT_AUTH%",
            }}

            result = self._service(root, config, opener).sync("codex")

            self.assertEqual("updated", result.status)
            self.assertEqual("# Managed instructions\n", (root / "workspace" / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual("Bearer secret-token", requests[0].get_header("Authorization"))
            state = json.loads(
                (root / "state" / "remote-instructions-codex.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                normalized_instruction_sha256("# Managed instructions\n"),
                state["normalized_sha256"],
            )

    def test_normalized_instruction_sha_is_platform_line_ending_independent(self):
        self.assertEqual(
            normalized_instruction_sha256("# First\n\nSecond\n"),
            normalized_instruction_sha256("# First\r\n\r\nSecond\r\n"),
        )

    def test_instruction_sync_preserves_lf_and_crlf_download_bytes(self):
        for label, body in (
            ("lf", b"# First\n\nSecond\n"),
            ("crlf", b"# First\r\n\r\nSecond\r\n"),
        ):
            with self.subTest(line_endings=label), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                config = {
                    "remote_instructions": {
                        "enabled": True,
                        "codex_url": "https://config.example/agents",
                    }
                }

                result = self._service(
                    root,
                    config,
                    lambda *_args, **_kwargs: _Response(body),
                ).sync("codex")

                self.assertEqual("updated", result.status)
                self.assertEqual(body, (root / "workspace" / "AGENTS.md").read_bytes())

    def test_legacy_state_is_refreshed_without_conditional_headers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            state_dir.mkdir()
            (state_dir / "remote-instructions-codex.json").write_text(
                json.dumps(
                    {
                        "url": "https://config.example/agents",
                        "target": "AGENTS.md",
                        "sha256": "legacy-download-digest",
                        "etag": '"legacy"',
                        "last_modified": "Sat, 22 Aug 2026 00:00:00 GMT",
                    }
                ),
                encoding="utf-8",
            )
            requests = []

            def opener(request, **_kwargs):
                requests.append(request)
                return _Response(b"# First\n\nSecond\n", headers={"etag": '"new"'})

            config = {
                "remote_instructions": {
                    "enabled": True,
                    "codex_url": "https://config.example/agents",
                }
            }

            result = self._service(root, config, opener).sync("codex")

            self.assertEqual("updated", result.status)
            self.assertIsNone(requests[0].get_header("If-None-Match"))
            self.assertIsNone(requests[0].get_header("If-Modified-Since"))
            refreshed = json.loads(
                (state_dir / "remote-instructions-codex.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                normalized_instruction_sha256("# First\n\nSecond\n"),
                refreshed["normalized_sha256"],
            )

    def test_missing_authorization_environment_variable_fails_before_request(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"remote_instructions": {
                "enabled": True,
                "claude_url": "https://config.example/claude",
                "authorization": "Bearer %DOES_NOT_EXIST_FOR_TEST%",
            }}
            opener = mock.Mock()
            result = self._service(Path(td), config, opener).sync("claude")
            self.assertEqual("failed", result.status)
            self.assertIn("DOES_NOT_EXIST_FOR_TEST", result.detail)
            opener.assert_not_called()

    def test_304_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "workspace" / "CLAUDE.md"
            target.parent.mkdir(parents=True)
            target.write_text("existing", encoding="utf-8")
            config = {"remote_instructions": {"enabled": True, "claude_url": "https://config.example/claude"}}
            def opener(request, **_kwargs):
                raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)
            result = self._service(root, config, opener).sync("claude")
            self.assertEqual("unchanged", result.status)
            self.assertEqual("existing", target.read_text(encoding="utf-8"))

    def test_environment_reference_syntax_is_os_independent(self):
        for value in ("Bearer %TOKEN%", "Bearer ${TOKEN}", "Bearer {TOKEN}"):
            expanded, missing = expand_environment_references(value, {"TOKEN": "abc"})
            self.assertEqual("Bearer abc", expanded)
            self.assertEqual([], missing)

    def test_panel_masks_authorization_value(self):
        rows, values = panel_rows({"remote_instructions": {"authorization": "Bearer top-secret"}})
        self.assertIn("authorization", values)
        self.assertTrue(any("Authorization header  [configured]" in row for row in rows))
        self.assertFalse(any("top-secret" in row for row in rows))

    def test_grok_downloads_into_the_agents_file_like_the_other_runtimes(self):
        # Grok is offered by the Launch menu, so it needs the same instruction
        # parameter. Without `grok_url` the panel had no row for it and a Grok
        # launch skipped instruction synchronization entirely.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            requests = []

            def opener(request, **_kwargs):
                requests.append(request)
                return _Response(b"# Grok instructions\n")

            config = {"remote_instructions": {
                "enabled": True,
                "grok_url": "https://config.example/agents",
            }}

            result = self._service(root, config, opener).sync("grok")

            self.assertEqual("updated", result.status)
            self.assertEqual(
                "# Grok instructions\n",
                (root / "workspace" / "AGENTS.md").read_text(encoding="utf-8"),
            )
            self.assertEqual("https://config.example/agents", requests[0].full_url)

    def test_panel_offers_every_launchable_runtime(self):
        rows, values = panel_rows({"remote_instructions": {}})

        for key in ("claude_url", "codex_url", "agy_url", "kimi_url", "grok_url"):
            self.assertIn(key, values)
        self.assertTrue(any("Grok URL → AGENTS.md" in row for row in rows))

    def test_launch_adapter_synchronizes_before_delegate(self):
        calls = []
        launch = SynchronizedLaunch(
            lambda value: calls.append(("launch", value)) or 7,
            lambda runtime, **kwargs: calls.append(("sync", runtime, kwargs["reason"])),
            "codex",
        )
        self.assertEqual(7, launch("now"))
        self.assertEqual([("sync", "codex", "launch"), ("launch", "now")], calls)


if __name__ == "__main__":
    unittest.main()
