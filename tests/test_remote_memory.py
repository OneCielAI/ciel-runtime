import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.remote_instructions import RemoteInstructionSynchronizer
from ciel_runtime_support.remote_memory import (
    MEMORY_POINTER_BEGIN,
    MEMORY_REFERENCE_INSTRUCTION,
    RemoteMemorySynchronizer,
    current_memory_index_address,
    current_memory_prompt,
    current_memory_root_address,
    parse_manifest,
    sync_instruction_with_memory_pointer,
    sync_all_memory_pointers,
    sync_launch_assets,
    update_memory_pointer,
)


class _Response:
    def __init__(self, body: bytes, url: str):
        self._body = io.BytesIO(body)
        self._url = url
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return self._url


class _Server:
    def __init__(self, values):
        self.values = dict(values)
        self.requests = []

    def __call__(self, request, **_kwargs):
        self.requests.append(request)
        return _Response(self.values[request.full_url], request.full_url)


class RemoteMemoryTests(unittest.TestCase):
    def _service(self, root: Path, config: dict, server: _Server):
        return RemoteMemorySynchronizer(
            load_config=lambda: config,
            workspace=lambda: root / "workspace",
            state_dir=root / "state",
            log=lambda *_args: None,
            urlopen=server,
        )

    @staticmethod
    def _manifest(files, index="index.okf"):
        return json.dumps(
            {"version": 1, "index": index, "files": files}
        ).encode()

    def test_downloads_multiple_formats_and_overwrites_the_tree_on_each_sync(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            "os.environ", {"MEMORY_TOKEN": "secret"}, clear=False
        ):
            root = Path(td)
            workspace = root / "workspace"
            memory = workspace / ".ciel" / "memory"
            memory.mkdir(parents=True)
            (memory / "stale.md").write_text("stale", encoding="utf-8")
            manifest_url = "https://memory.example/v1/manifest.json"
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [
                            {
                                "path": "index.okf",
                                "url": "files/index.okf",
                                "format": "okf",
                            },
                            {
                                "path": "projects/ciel.md",
                                "download_url": "files/ciel.md",
                                "format": "markdown",
                            },
                            {
                                "path": "state/runtime.json",
                                "url": "files/runtime.json",
                                "format": "json",
                            },
                        ]
                    ),
                    "https://memory.example/v1/files/index.okf": b"root: memory\n",
                    "https://memory.example/v1/files/ciel.md": b"# Ciel\n",
                    "https://memory.example/v1/files/runtime.json": b'{"ready": true}\n',
                }
            )
            config = {
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                    "authorization": "Bearer {MEMORY_TOKEN}",
                }
            }
            service = self._service(root, config, server)

            first = service.sync("codex")

            self.assertEqual("updated", first.status)
            self.assertEqual(3, first.file_count)
            self.assertEqual(str((memory / "index.okf").resolve()), first.index_address)
            self.assertFalse((memory / "stale.md").exists())
            self.assertEqual("root: memory\n", (memory / "index.okf").read_text())
            self.assertEqual("# Ciel\n", (memory / "projects" / "ciel.md").read_text())
            self.assertEqual(
                '{"ready": true}\n',
                (memory / "state" / "runtime.json").read_text(),
            )
            self.assertFalse((workspace / "AGENTS.md").exists())
            prompt = current_memory_prompt(root / "state", config, workspace)
            self.assertIn("Memory root: .ciel/memory", prompt)
            self.assertIn("Memory index: .ciel/memory/index.okf", prompt)
            self.assertNotIn(str(workspace.resolve()), prompt)
            self.assertIn(MEMORY_REFERENCE_INSTRUCTION, prompt)

            self.assertTrue(
                all(
                    request.get_header("Authorization") == "Bearer secret"
                    for request in server.requests
                )
            )

            server.values["https://memory.example/v1/files/index.okf"] = b"root: replaced\n"
            second = service.sync("codex")

            self.assertEqual("updated", second.status)
            self.assertEqual("root: replaced\n", (memory / "index.okf").read_text())
            self.assertFalse((workspace / "AGENTS.md").exists())

    def test_real_local_http_manifest_downloads_a_nested_memory_tree(self):
        requests = []
        bodies = {
            "/instructions.md": b"# Remote instructions\r\n\r\nSecond line\r\n",
            "/manifest.json": self._manifest(
                [
                    {"path": "index.okf", "url": "/files/index.okf"},
                    {"path": "journal/current.md", "url": "/files/current.md"},
                ]
            ),
            "/files/index.okf": b"entries:\n  - journal/current.md\n",
            "/files/current.md": b"# Current memory\n",
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                body = bodies.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                manifest_url = f"http://127.0.0.1:{server.server_port}/manifest.json"
                config = {
                    "remote_instructions": {
                        "enabled": True,
                        "codex_url": (
                            f"http://127.0.0.1:{server.server_port}/instructions.md"
                        ),
                    },
                    "remote_memory": {
                        "enabled": True,
                        "manifest_url": manifest_url,
                        "authorization": "Bearer local-test",
                    }
                }

                instructions = RemoteInstructionSynchronizer(
                    load_config=lambda: config,
                    workspace=lambda: root / "workspace",
                    state_dir=root / "state",
                    log=lambda *_args: None,
                )
                memory = RemoteMemorySynchronizer(
                    load_config=lambda: config,
                    workspace=lambda: root / "workspace",
                    state_dir=root / "state",
                    log=lambda *_args: None,
                )
                result = sync_launch_assets(
                    "codex",
                    reason="launch",
                    instruction_sync=instructions.sync,
                    memory_sync=memory.sync,
                )

                self.assertEqual("updated", result.status)
                self.assertEqual(2, result.file_count)
                self.assertEqual(
                    "# Current memory\n",
                    (root / "workspace" / ".ciel" / "memory" / "journal" / "current.md").read_text(),
                )
                self.assertEqual(
                    [
                        ("/instructions.md", None),
                        ("/manifest.json", "Bearer local-test"),
                        ("/files/index.okf", "Bearer local-test"),
                        ("/files/current.md", "Bearer local-test"),
                    ],
                    requests,
                )
                workspace = root / "workspace"
                rendered = (workspace / "AGENTS.md").read_text(encoding="utf-8")
                self.assertIn("# Remote instructions", rendered)
                self.assertIn("Second line", rendered)
                self.assertIn("Memory root: .ciel/memory", rendered)
                self.assertIn("Memory index: .ciel/memory/index.okf", rendered)
                self.assertNotIn(str(workspace.resolve()), rendered)
                self.assertIn(MEMORY_REFERENCE_INSTRUCTION, rendered)
                self.assertTrue(
                    rendered.rstrip().endswith(
                        "<!-- ciel-runtime:remote-memory:end -->"
                    )
                )

                bodies["/instructions.md"] = b"# Refreshed system prompt\n"
                sync_instruction_with_memory_pointer(
                    "codex",
                    reason="pre-compact",
                    instruction_synchronizer=lambda: instructions,
                    memory_synchronizer=lambda: memory,
                    log=lambda *_args: None,
                )
                refreshed = (workspace / "AGENTS.md").read_text(encoding="utf-8")
                self.assertTrue(refreshed.startswith("# Refreshed system prompt\n"))
                self.assertEqual(1, refreshed.count(MEMORY_POINTER_BEGIN))
                self.assertIn("Memory root: .ciel/memory", refreshed)
                self.assertTrue(
                    refreshed.rstrip().endswith(
                        "<!-- ciel-runtime:remote-memory:end -->"
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_configured_directory_is_projected_as_a_portable_workspace_relative_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            manifest_url = "https://memory.example/manifest.json"
            config = {
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                    "directory": "agent-data/memory",
                }
            }
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "catalog/index.md", "url": "index.md"}],
                        index="catalog/index.md",
                    ),
                    "https://memory.example/index.md": b"# memory\n",
                }
            )

            result = self._service(root, config, server).sync("codex")
            prompt = current_memory_prompt(root / "state", config, workspace)

            self.assertEqual("updated", result.status)
            self.assertIn("Memory root: agent-data/memory", prompt)
            self.assertIn("Memory index: agent-data/memory/catalog/index.md", prompt)
            self.assertNotIn(str(workspace.resolve()), prompt)

    def test_downloaded_instruction_file_ends_with_memory_guidance_for_every_runtime(self):
        runtime_files = {
            "claude": ("claude_url", "CLAUDE.md"),
            "codex": ("codex_url", "AGENTS.md"),
            "codex-app-server": ("codex_url", "AGENTS.md"),
            "agy": ("agy_url", "GEMINI.md"),
            "kimi": ("kimi_url", "AGENTS.md"),
            "grok": ("grok_url", "AGENTS.md"),
        }
        for runtime, (url_key, filename) in runtime_files.items():
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                workspace = root / "workspace"
                instruction_url = f"https://instructions.example/{runtime}.md"
                manifest_url = "https://memory.example/manifest.json"
                server = _Server(
                    {
                        instruction_url: b"# Downloaded system prompt\n",
                        manifest_url: self._manifest(
                            [{"path": "catalog/index.okf", "url": "index.okf"}],
                            index="catalog/index.okf",
                        ),
                        "https://memory.example/index.okf": b"memory: ready\n",
                    }
                )
                config = {
                    "remote_instructions": {
                        "enabled": True,
                        url_key: instruction_url,
                    },
                    "remote_memory": {
                        "enabled": True,
                        "manifest_url": manifest_url,
                    },
                }
                instructions = RemoteInstructionSynchronizer(
                    load_config=lambda: config,
                    workspace=lambda: workspace,
                    state_dir=root / "state",
                    log=lambda *_args: None,
                    urlopen=server,
                )
                memory = RemoteMemorySynchronizer(
                    load_config=lambda: config,
                    workspace=lambda: workspace,
                    state_dir=root / "state",
                    log=lambda *_args: None,
                    urlopen=server,
                )

                result = sync_launch_assets(
                    runtime,
                    reason="launch",
                    instruction_sync=instructions.sync,
                    memory_sync=memory.sync,
                )

                rendered = (workspace / filename).read_text(encoding="utf-8")
                self.assertEqual("updated", result.status)
                self.assertTrue(rendered.startswith("# Downloaded system prompt\n"))
                self.assertIn("Memory root: .ciel/memory", rendered)
                self.assertIn("Memory index: .ciel/memory/catalog/index.okf", rendered)
                self.assertNotIn(str(workspace.resolve()), rendered)
                self.assertIn(MEMORY_REFERENCE_INSTRUCTION, rendered)
                self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
                self.assertTrue(
                    rendered.rstrip().endswith(
                        "<!-- ciel-runtime:remote-memory:end -->"
                    )
                )

    def test_cross_origin_public_file_does_not_receive_manifest_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_url = "https://memory.example/manifest.json"
            public_url = "https://cdn.example/index.okf"
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": public_url, "format": "okf"}]
                    ),
                    public_url: b"index\n",
                }
            )
            config = {
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                    "authorization": "Bearer private",
                }
            }

            result = self._service(root, config, server).sync("claude")

            self.assertEqual("updated", result.status)
            self.assertEqual("Bearer private", server.requests[0].get_header("Authorization"))
            self.assertIsNone(server.requests[1].get_header("Authorization"))

    def test_memory_only_sync_removes_a_legacy_pointer_without_creating_instructions(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "home"
            workspace.mkdir()
            agents = workspace / "AGENTS.md"
            agents.write_text(
                "# User rules\n\n"
                "<!-- ciel-runtime:remote-memory:begin -->\n"
                "Memory index: .ciel/memory/index.okf\n"
                "<!-- ciel-runtime:remote-memory:end -->\n",
                encoding="utf-8",
            )
            manifest_url = "https://memory.example/manifest.json"
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"index\n",
                }
            )
            result = RemoteMemorySynchronizer(
                load_config=lambda: {
                    "remote_memory": {
                        "enabled": True,
                        "manifest_url": manifest_url,
                    }
                },
                workspace=lambda: workspace,
                state_dir=Path(td) / "state",
                log=lambda *_args: None,
                urlopen=server,
            ).sync("codex")

            self.assertEqual("updated", result.status)
            self.assertEqual(2, len(server.requests))
            rendered = agents.read_text(encoding="utf-8")
            self.assertEqual("# User rules\n", rendered)
            self.assertEqual(
                "index\n",
                (workspace / ".ciel" / "memory" / "index.okf").read_text(
                    encoding="utf-8"
                ),
            )

    def test_distinct_project_workspaces_receive_isolated_memory_trees(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_url = "https://memory.example/manifest.json"
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"project memory\n",
                }
            )
            config = {
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                }
            }

            for name in ("alpha", "beta"):
                workspace = root / name
                result = RemoteMemorySynchronizer(
                    load_config=lambda: config,
                    workspace=lambda workspace=workspace: workspace,
                    state_dir=root / "state" / name,
                    log=lambda *_args: None,
                    urlopen=server,
                ).sync("codex")
                self.assertEqual("updated", result.status)

            for name in ("alpha", "beta"):
                self.assertEqual(
                    "project memory\n",
                    (root / name / ".ciel" / "memory" / "index.okf").read_text(
                        encoding="utf-8"
                    ),
                )
                self.assertFalse((root / name / "AGENTS.md").exists())
            self.assertFalse((root / ".ciel" / "memory").exists())

    def test_sha_mismatch_keeps_the_previous_memory_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            memory = root / "workspace" / ".ciel" / "memory"
            memory.mkdir(parents=True)
            (memory / "index.okf").write_text("previous", encoding="utf-8")
            manifest_url = "https://memory.example/manifest.json"
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [
                            {
                                "path": "index.okf",
                                "url": "index.okf",
                                "format": "okf",
                                "sha256": hashlib.sha256(b"different").hexdigest(),
                            }
                        ]
                    ),
                    "https://memory.example/index.okf": b"replacement",
                }
            )
            config = {
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                }
            }

            result = self._service(root, config, server).sync("codex")

            self.assertEqual("failed", result.status)
            self.assertIn("sha256 mismatch", result.detail)
            self.assertEqual("previous", (memory / "index.okf").read_text())

    def test_failed_refresh_preserves_the_previous_native_memory_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            manifest_url = "https://memory.example/manifest.json"
            server = _Server(
                {
                    "https://instructions.example/AGENTS.md": b"# Downloaded instructions\n",
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"previous memory\n",
                }
            )
            config = {
                "remote_instructions": {
                    "enabled": True,
                    "codex_url": "https://instructions.example/AGENTS.md",
                },
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                }
            }
            instructions = RemoteInstructionSynchronizer(
                load_config=lambda: config,
                workspace=lambda: workspace,
                state_dir=root / "state",
                log=lambda *_args: None,
                urlopen=server,
            )
            service = self._service(root, config, server)
            first = sync_launch_assets(
                "codex",
                reason="launch",
                instruction_sync=instructions.sync,
                memory_sync=service.sync,
            )
            agents = workspace / "AGENTS.md"
            previous_pointer = agents.read_text(encoding="utf-8")

            server.values[manifest_url] = self._manifest(
                [
                    {
                        "path": "index.okf",
                        "url": "index.okf",
                        "sha256": hashlib.sha256(b"unexpected").hexdigest(),
                    }
                ]
            )
            server.values["https://memory.example/index.okf"] = b"replacement\n"
            failed = service.sync("codex")

            self.assertEqual("updated", first.status)
            self.assertEqual("failed", failed.status)
            self.assertIn("sha256 mismatch", failed.detail)
            self.assertEqual(previous_pointer, agents.read_text(encoding="utf-8"))
            self.assertEqual(
                "previous memory\n",
                (workspace / ".ciel" / "memory" / "index.okf").read_text(
                    encoding="utf-8"
                ),
            )

    def test_pointer_projection_failure_reports_updated_download_with_detail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            agents = workspace / "AGENTS.md"
            agents.write_text("# Downloaded instructions\n", encoding="utf-8")
            manifest_url = "https://memory.example/manifest.json"
            config = {
                "remote_instructions": {
                    "enabled": True,
                    "codex_url": "https://instructions.example/AGENTS.md",
                },
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                },
            }
            server = _Server(
                {
                    "https://instructions.example/AGENTS.md": b"# Downloaded instructions\n",
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"new memory\n",
                }
            )
            service = self._service(root, config, server)
            RemoteInstructionSynchronizer(
                load_config=lambda: config,
                workspace=lambda: workspace,
                state_dir=root / "state",
                log=lambda *_args: None,
                urlopen=server,
            ).sync("codex")

            with mock.patch(
                "ciel_runtime_support.remote_memory.project_memory_pointer",
                side_effect=OSError("read-only instruction file"),
            ):
                result = service.sync("codex")

            self.assertEqual("updated", result.status)
            self.assertIn("memory pointer projection failed", result.detail)
            self.assertEqual(
                "new memory\n",
                (workspace / ".ciel" / "memory" / "index.okf").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIn(
                "Memory root:",
                current_memory_prompt(root / "state", config, workspace),
            )
            self.assertEqual(
                "# Downloaded instructions\n",
                agents.read_text(encoding="utf-8"),
            )

    def test_state_commit_failure_rolls_back_the_memory_tree_and_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            instruction_url = "https://instructions.example/AGENTS.md"
            manifest_url = "https://memory.example/manifest.json"
            server = _Server(
                {
                    instruction_url: b"# Downloaded instructions\n",
                    manifest_url: self._manifest(
                        [{"path": "old.okf", "url": "old.okf"}],
                        index="old.okf",
                    ),
                    "https://memory.example/old.okf": b"old memory\n",
                }
            )
            config = {
                "remote_instructions": {
                    "enabled": True,
                    "codex_url": instruction_url,
                },
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                },
            }
            instructions = RemoteInstructionSynchronizer(
                load_config=lambda: config,
                workspace=lambda: workspace,
                state_dir=root / "state",
                log=lambda *_args: None,
                urlopen=server,
            )
            service = self._service(root, config, server)
            first = sync_launch_assets(
                "codex",
                reason="launch",
                instruction_sync=instructions.sync,
                memory_sync=service.sync,
            )
            agents = workspace / "AGENTS.md"
            previous_pointer = agents.read_text(encoding="utf-8")
            previous_state = (root / "state" / "remote-memory.json").read_text(
                encoding="utf-8"
            )
            server.values[manifest_url] = self._manifest(
                [{"path": "new.okf", "url": "new.okf"}],
                index="new.okf",
            )
            server.values["https://memory.example/new.okf"] = b"new memory\n"

            with mock.patch.object(
                RemoteMemorySynchronizer,
                "_write_state",
                side_effect=OSError("state disk full"),
            ):
                failed = service.sync("codex")

            self.assertEqual("updated", first.status)
            self.assertEqual("failed", failed.status)
            self.assertIn("state disk full", failed.detail)
            memory = workspace / ".ciel" / "memory"
            self.assertTrue((memory / "old.okf").is_file())
            self.assertFalse((memory / "new.okf").exists())
            self.assertEqual(
                previous_state,
                (root / "state" / "remote-memory.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(previous_pointer, agents.read_text(encoding="utf-8"))
            self.assertIn(
                "Memory index: .ciel/memory/old.okf",
                service.current_prompt_text(),
            )

    def test_manual_memory_sync_does_not_create_native_instruction_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            manifest_url = "https://memory.example/manifest.json"
            config = {
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                }
            }
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"memory\n",
                }
            )

            report = sync_all_memory_pointers(self._service(root, config, server))

            self.assertIn("remote-memory: updated", report[0])
            self.assertFalse((workspace / "AGENTS.md").exists())
            self.assertFalse((workspace / "CLAUDE.md").exists())
            self.assertFalse((workspace / "GEMINI.md").exists())

    def test_manual_memory_sync_preserves_every_configured_agents_runtime_pointer(self):
        for runtime, state_name, url_key, sibling_runtime in (
            ("codex", "codex", "codex_url", "kimi"),
            ("codex-app-server", "codex_app_server", "codex_url", "grok"),
            ("kimi", "kimi", "kimi_url", "codex"),
            ("grok", "grok", "grok_url", "codex-app-server"),
        ):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                workspace = root / "workspace"
                workspace.mkdir()
                agents = workspace / "AGENTS.md"
                instruction_text = f"# {runtime} instructions\n"
                agents.write_text(instruction_text, encoding="utf-8")
                state = root / "state"
                state.mkdir()
                instruction_url = f"https://instructions.example/{runtime}.md"
                (state / f"remote-instructions-{state_name}.json").write_text(
                    json.dumps(
                        {
                            "url": instruction_url,
                            "target": "AGENTS.md",
                            "sha256": hashlib.sha256(
                                instruction_text.encode("utf-8")
                            ).hexdigest(),
                            "normalized_sha256": hashlib.sha256(
                                instruction_text.strip().encode("utf-8")
                            ).hexdigest(),
                        }
                    ),
                    encoding="utf-8",
                )
                manifest_url = "https://memory.example/manifest.json"
                config = {
                    "remote_instructions": {
                        "enabled": True,
                        url_key: instruction_url,
                    },
                    "remote_memory": {
                        "enabled": True,
                        "manifest_url": manifest_url,
                    },
                }
                server = _Server(
                    {
                        manifest_url: self._manifest(
                            [{"path": "index.okf", "url": "index.okf"}]
                        ),
                        "https://memory.example/index.okf": b"memory\n",
                    }
                )

                service = self._service(root, config, server)
                sibling_result = service.sync(sibling_runtime)
                self.assertEqual("updated", sibling_result.status)
                self.assertEqual(1, agents.read_text(encoding="utf-8").count(MEMORY_POINTER_BEGIN))
                report = sync_all_memory_pointers(service)

                rendered = agents.read_text(encoding="utf-8")
                self.assertIn("remote-memory: updated", report[0])
                self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
                self.assertIn(
                    "Memory root: .ciel/memory",
                    rendered,
                )
                self.assertTrue(
                    rendered.rstrip().endswith(
                        "<!-- ciel-runtime:remote-memory:end -->"
                    )
                )

    def test_stale_shared_instruction_state_does_not_modify_a_user_owned_agents_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            agents = workspace / "AGENTS.md"
            user_text = "# USER-OWNED REPLACEMENT\n"
            agents.write_text(user_text, encoding="utf-8")
            state = root / "state"
            state.mkdir()
            instruction_url = "https://instructions.example/codex.md"
            old_text = "# Old downloaded instructions\n"
            (state / "remote-instructions-codex.json").write_text(
                json.dumps(
                    {
                        "url": instruction_url,
                        "target": "AGENTS.md",
                        "sha256": hashlib.sha256(old_text.encode("utf-8")).hexdigest(),
                        "normalized_sha256": hashlib.sha256(
                            old_text.strip().encode("utf-8")
                        ).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            manifest_url = "https://memory.example/manifest.json"
            config = {
                "remote_instructions": {
                    "enabled": True,
                    "codex_url": instruction_url,
                },
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                },
            }
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"memory\n",
                }
            )

            result = self._service(root, config, server).sync("kimi")

            self.assertEqual("updated", result.status)
            self.assertEqual(user_text, agents.read_text(encoding="utf-8"))
            self.assertNotIn(MEMORY_POINTER_BEGIN, agents.read_text(encoding="utf-8"))

    def test_failed_instruction_download_does_not_modify_a_user_instruction_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            agents = workspace / "AGENTS.md"
            agents.write_text("# User-owned instructions\n", encoding="utf-8")
            manifest_url = "https://memory.example/manifest.json"
            config = {
                "remote_instructions": {
                    "enabled": True,
                    "codex_url": "https://instructions.example/AGENTS.md",
                },
                "remote_memory": {
                    "enabled": True,
                    "manifest_url": manifest_url,
                },
            }
            server = _Server(
                {
                    manifest_url: self._manifest(
                        [{"path": "index.okf", "url": "index.okf"}]
                    ),
                    "https://memory.example/index.okf": b"memory\n",
                }
            )
            instructions = RemoteInstructionSynchronizer(
                load_config=lambda: config,
                workspace=lambda: workspace,
                state_dir=root / "state",
                log=lambda *_args: None,
                urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("instruction endpoint unavailable")
                ),
            )
            memory = self._service(root, config, server)

            result = sync_launch_assets(
                "codex",
                reason="launch",
                instruction_sync=instructions.sync,
                memory_sync=memory.sync,
            )

            self.assertEqual("updated", result.status)
            self.assertEqual(
                "# User-owned instructions\n",
                agents.read_text(encoding="utf-8"),
            )
            self.assertFalse(
                (root / "state" / "remote-instructions-codex.json").exists()
            )

    def test_disabling_remote_memory_removes_only_the_managed_block(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            agents = workspace / "AGENTS.md"
            agents.parent.mkdir(parents=True)
            agents.write_text("# User instructions\n", encoding="utf-8")
            update_memory_pointer(
                agents,
                str(workspace / ".ciel" / "memory" / "index.okf"),
                str(workspace / ".ciel" / "memory"),
            )
            service = RemoteMemorySynchronizer(
                load_config=lambda: {"remote_memory": {"enabled": False}},
                workspace=lambda: workspace,
                state_dir=root / "state",
                log=lambda *_args: None,
            )

            result = service.sync("codex")

            self.assertEqual("disabled", result.status)
            self.assertEqual(
                "# User instructions\n",
                agents.read_text(encoding="utf-8"),
            )

    def test_saved_index_is_rejected_for_a_different_launch_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace-a"
            index = workspace / ".ciel" / "memory" / "index.okf"
            index.parent.mkdir(parents=True)
            index.write_text("memory\n", encoding="utf-8")
            state = root / "state"
            state.mkdir()
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.okf",
                    }
                ),
                encoding="utf-8",
            )
            config = {"remote_memory": {"enabled": True}}

            self.assertEqual(
                ".ciel/memory/index.okf",
                current_memory_index_address(state, config, workspace),
            )
            self.assertEqual(
                ".ciel/memory",
                current_memory_root_address(state, config, workspace),
            )
            self.assertEqual(
                "",
                current_memory_index_address(state, config, root / "workspace-b"),
            )
            self.assertEqual(
                "",
                current_memory_root_address(state, config, root / "workspace-b"),
            )

    def test_manifest_rejects_traversal_duplicates_and_unknown_formats(self):
        base = "https://memory.example/manifest.json"
        invalid = (
            {
                "version": 1,
                "index": "../index.okf",
                "files": [{"path": "../index.okf", "url": "index", "format": "okf"}],
            },
            {
                "version": 1,
                "index": "index.okf",
                "files": [
                    {"path": "index.okf", "url": "a", "format": "okf"},
                    {"path": "index.okf", "url": "b", "format": "okf"},
                ],
            },
            {
                "version": 1,
                "index": "index.exe",
                "files": [{"path": "index.exe", "url": "index", "format": "exe"}],
            },
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_manifest(payload, manifest_url=base)

    def test_manifest_accepts_all_documented_text_formats_and_aliases(self):
        paths = (
            ("index.okf", "okf"),
            ("notes.md", "md"),
            ("state.json", "json"),
            ("facts.yml", "yml"),
            ("settings.toml", "toml"),
            ("readme.txt", "txt"),
        )
        manifest = parse_manifest(
            {
                "version": 1,
                "index": "index.okf",
                "files": [
                    {"path": path, "url": path, "format": file_format}
                    for path, file_format in paths
                ],
            },
            manifest_url="https://memory.example/manifest.json",
        )

        self.assertEqual(
            ["okf", "markdown", "json", "yaml", "toml", "text"],
            [item.format for item in manifest.files],
        )

    def test_pointer_replacement_preserves_user_text_and_stays_at_the_bottom(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "AGENTS.md"
            path.write_text("# User rules\n", encoding="utf-8")

            self.assertTrue(update_memory_pointer(path, ".ciel/memory/index.md"))
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "<!-- ciel-runtime:remote-memory:begin -->\n"
                    "Memory index: duplicate.md\n"
                    "<!-- ciel-runtime:remote-memory:end -->\n"
                )
            self.assertTrue(update_memory_pointer(path, ".ciel/memory/index.okf"))

            rendered = path.read_text(encoding="utf-8")
            self.assertIn("# User rules", rendered)
            self.assertNotIn("index.md", rendered)
            self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
            self.assertIn("Memory root: .ciel/memory", rendered)
            self.assertIn(MEMORY_REFERENCE_INSTRUCTION, rendered)
            self.assertTrue(rendered.rstrip().endswith("<!-- ciel-runtime:remote-memory:end -->"))

            self.assertTrue(update_memory_pointer(path, ""))
            self.assertEqual("# User rules\n", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
