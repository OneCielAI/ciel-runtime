import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.remote_memory import (
    MEMORY_POINTER_BEGIN,
    RemoteMemorySynchronizer,
    parse_manifest,
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
            self.assertEqual(".ciel/memory/index.okf", first.index_address)
            self.assertFalse((memory / "stale.md").exists())
            self.assertEqual("root: memory\n", (memory / "index.okf").read_text())
            self.assertEqual("# Ciel\n", (memory / "projects" / "ciel.md").read_text())
            self.assertEqual(
                '{"ready": true}\n',
                (memory / "state" / "runtime.json").read_text(),
            )
            agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
            self.assertTrue(agents.rstrip().endswith("<!-- ciel-runtime:remote-memory:end -->"))
            self.assertEqual(1, agents.count("Memory index: .ciel/memory/index.okf"))

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
            agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(1, agents.count("Memory index: .ciel/memory/index.okf"))

    def test_real_local_http_manifest_downloads_a_nested_memory_tree(self):
        requests = []
        bodies = {
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
                    "remote_memory": {
                        "enabled": True,
                        "manifest_url": manifest_url,
                        "authorization": "Bearer local-test",
                    }
                }

                result = RemoteMemorySynchronizer(
                    load_config=lambda: config,
                    workspace=lambda: root / "workspace",
                    state_dir=root / "state",
                    log=lambda *_args: None,
                ).sync("codex")

                self.assertEqual("updated", result.status)
                self.assertEqual(2, result.file_count)
                self.assertEqual(
                    "# Current memory\n",
                    (root / "workspace" / ".ciel" / "memory" / "journal" / "current.md").read_text(),
                )
                self.assertEqual(
                    [
                        ("/manifest.json", "Bearer local-test"),
                        ("/files/index.okf", "Bearer local-test"),
                        ("/files/current.md", "Bearer local-test"),
                    ],
                    requests,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

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

    def test_user_home_scope_refuses_download_and_removes_managed_pointer(self):
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
                home=lambda: workspace,
            ).sync("codex")

            self.assertEqual("failed", result.status)
            self.assertIn("user home is not allowed", result.detail)
            self.assertEqual([], server.requests)
            self.assertEqual("# User rules\n", agents.read_text(encoding="utf-8"))
            self.assertFalse((workspace / ".ciel" / "memory").exists())

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
                    home=lambda: root / "home",
                ).sync("codex")
                self.assertEqual("updated", result.status)

            for name in ("alpha", "beta"):
                workspace = root / name
                self.assertEqual(
                    "project memory\n",
                    (workspace / ".ciel" / "memory" / "index.okf").read_text(
                        encoding="utf-8"
                    ),
                )
                self.assertTrue((workspace / "AGENTS.md").is_file())
            self.assertFalse((root / ".ciel" / "memory").exists())

    def test_sha_mismatch_keeps_the_previous_memory_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            memory = workspace / ".ciel" / "memory"
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
            self.assertTrue(update_memory_pointer(path, ".ciel/memory/index.okf"))

            rendered = path.read_text(encoding="utf-8")
            self.assertIn("# User rules", rendered)
            self.assertNotIn("index.md", rendered)
            self.assertEqual(1, rendered.count(MEMORY_POINTER_BEGIN))
            self.assertTrue(rendered.rstrip().endswith("<!-- ciel-runtime:remote-memory:end -->"))

            self.assertTrue(update_memory_pointer(path, ""))
            self.assertEqual("# User rules\n", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
