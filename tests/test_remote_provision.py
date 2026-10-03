import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.remote_provision import (
    RemoteProvisioner,
    parse_manifest,
    provision_before_launch,
    remote_provision_command,
)


MANIFEST_URL = "https://provision.example/manifest.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, url: str, data: bytes):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class RemoteProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.state = root / "state"
        self.served: dict[str, bytes] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.logs: list[tuple[str, str]] = []
        self.config = {"remote_provision": {"enabled": True, "manifest_url": MANIFEST_URL}}

    def tearDown(self):
        self.tmp.cleanup()

    def urlopen(self, request, timeout=None):
        del timeout
        self.requests.append((request.full_url, request.get_header("Authorization")))
        if request.full_url not in self.served:
            raise OSError(f"404 {request.full_url}")
        return FakeResponse(request.full_url, self.served[request.full_url])

    def provisioner(self, platform="windows", environ=None):
        return RemoteProvisioner(
            load_config=lambda: self.config,
            workspace=lambda: self.workspace,
            state_dir=self.state,
            log=lambda level, message: self.logs.append((level, message)),
            urlopen=self.urlopen,
            platform=lambda: platform,
            python=sys.executable,
            environ=environ if environ is not None else {"PATH": "", "SYSTEMROOT": "C:\\Windows"},
        )

    def serve_manifest(self, files=(), steps=()):
        self.served[MANIFEST_URL] = json.dumps({"version": 1, "files": list(files), "steps": list(steps)}).encode()

    def python_step(self, step_id, body, **extra):
        url = f"https://provision.example/{step_id}.py"
        self.served[url] = body.encode()
        return {"id": step_id, "shell": "python", "url": url, "sha256": sha(body.encode()), **extra}

    def test_manifest_requires_sha256_and_safe_paths(self):
        cases = [
            {"files": [{"path": "a.txt", "url": "a.txt"}]},
            {"files": [{"path": "../a.txt", "url": "a.txt", "sha256": "0" * 64}]},
            {"steps": [{"id": "x", "shell": "cmd", "url": "x", "sha256": "0" * 64}]},
            {"steps": [{"id": "x", "shell": "bash", "url": "x"}]},
            {"steps": [{"id": "x", "shell": "bash", "url": "x", "sha256": "0" * 64},
                       {"id": "x", "shell": "bash", "url": "y", "sha256": "0" * 64}]},
            {"files": [], "steps": []},
            {"version": 2, "steps": [{"id": "x", "shell": "bash", "url": "x", "sha256": "0" * 64}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_manifest(payload, manifest_url=MANIFEST_URL)
        manifest = parse_manifest(
            {"steps": [{"id": "x", "shell": "bash", "url": "x.sh", "sha256": "A" * 64}]},
            manifest_url=MANIFEST_URL,
        )
        self.assertEqual("https://provision.example/x.sh", manifest.steps[0].url)
        self.assertEqual("once", manifest.steps[0].run)

    def test_disabled_does_nothing(self):
        self.config["remote_provision"]["enabled"] = False
        self.assertEqual("disabled", self.provisioner().provision().status)
        self.assertEqual([], self.requests)

    def test_files_and_steps_run_once_until_the_script_changes(self):
        payload = b"setup archive"
        self.served["https://provision.example/tools/setup.bin"] = payload
        body = (
            "import os, pathlib\n"
            "root = pathlib.Path(os.environ['CIEL_WORKSPACE'])\n"
            "marker = root / 'installed.txt'\n"
            "marker.write_text(str(int(marker.read_text()) + 1) if marker.exists() else '1')\n"
        )
        step = self.python_step("install", body)
        self.serve_manifest(
            files=[{"path": "tools/setup.bin", "url": "tools/setup.bin", "sha256": sha(payload)}],
            steps=[step],
        )
        first = self.provisioner().provision()
        self.assertEqual("ok", first.status, first.detail)
        self.assertEqual(payload, (self.workspace / "tools" / "setup.bin").read_bytes())
        self.assertEqual("1", (self.workspace / "installed.txt").read_text())
        self.assertIn("step install ran", first.lines)

        second = self.provisioner().provision()
        self.assertEqual("ok", second.status)
        self.assertIn("step install already done", second.lines)
        self.assertIn("file tools/setup.bin unchanged", second.lines)
        self.assertEqual("1", (self.workspace / "installed.txt").read_text())

        changed = self.python_step("install", body + "# v2\n")
        self.serve_manifest(steps=[changed])
        self.assertEqual("ok", self.provisioner().provision().status)
        self.assertEqual("2", (self.workspace / "installed.txt").read_text())

    def test_every_launch_steps_always_run(self):
        body = "import os, pathlib\np = pathlib.Path(os.environ['CIEL_WORKSPACE']) / 'n'\np.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
        self.serve_manifest(steps=[self.python_step("refresh", body, run="every_launch")])
        self.provisioner().provision()
        self.provisioner().provision()
        self.assertEqual("xx", (self.workspace / "n").read_text())

    def test_sha256_mismatch_is_never_run(self):
        step = self.python_step("bad", "open('ran', 'w').close()\n")
        step["sha256"] = sha(b"something else")
        self.serve_manifest(steps=[step])
        result = self.provisioner().provision()
        self.assertEqual("failed", result.status)
        self.assertIn("sha256 mismatch for step bad", result.detail)
        self.assertFalse((self.workspace / "ran").exists())

    def test_failed_step_blocks_and_is_retried(self):
        self.serve_manifest(steps=[self.python_step("fail", "raise SystemExit(3)\n")])
        result = self.provisioner().provision()
        self.assertEqual("failed", result.status)
        self.assertIn("step fail exited 3", result.detail)
        state = json.loads((self.state / "provision" / "provision-state.json").read_text(encoding="utf-8"))
        self.assertEqual(3, state["steps"]["fail"]["exit_code"])
        self.assertEqual("failed", state["status"])
        again = self.provisioner().provision()
        self.assertIn("step fail exited 3", again.detail)

    def test_other_platform_steps_are_skipped(self):
        self.serve_manifest(steps=[self.python_step("mac", "raise SystemExit(1)\n", platform="macos")])
        result = self.provisioner(platform="windows").provision()
        self.assertEqual("ok", result.status)
        self.assertIn("step mac skipped (platform macos)", result.lines)

    def test_timeout_fails_the_step(self):
        self.serve_manifest(steps=[self.python_step("slow", "import time\ntime.sleep(30)\n", timeout_s=1)])
        result = self.provisioner().provision()
        self.assertEqual("failed", result.status)
        self.assertIn("step slow timed out after 1s", result.detail)

    def test_authorization_goes_only_to_the_manifest_origin(self):
        self.config["remote_provision"]["authorization"] = "Bearer ${PROVISION_TOKEN}"
        other = "https://cdn.example/tool.py"
        body = b"pass\n"
        self.served[other] = body
        self.serve_manifest(steps=[{"id": "cdn", "shell": "python", "url": other, "sha256": sha(body)}])
        result = self.provisioner(environ={"PROVISION_TOKEN": "t0k", "PATH": ""}).provision()
        self.assertEqual("ok", result.status, result.detail)
        self.assertEqual([(MANIFEST_URL, "Bearer t0k"), (other, None)], self.requests)
        missing = self.provisioner(environ={"PATH": ""}).provision()
        self.assertIn("missing authorization environment variable: PROVISION_TOKEN", missing.detail)

    def test_launch_is_blocked_on_failure(self):
        printed: list[str] = []
        self.serve_manifest(steps=[self.python_step("fail", "raise SystemExit(1)\n")])
        with self.assertRaises(SystemExit) as raised:
            provision_before_launch("assets", reason="launch", provisioner=self.provisioner, output=printed.append)
        self.assertEqual(2, raised.exception.code)
        self.assertEqual("Ciel Runtime launch blocked:", printed[-2])
        self.assertIn("Provisioning failed: step fail exited 1", printed[-1])
        self.assertEqual("assets", provision_before_launch("assets", reason="sync", provisioner=self.provisioner))
        self.config["remote_provision"]["enabled"] = False
        self.assertEqual("assets", provision_before_launch("assets", reason="launch", provisioner=self.provisioner))

    def test_command_sets_options_runs_and_reports_status(self):
        stored = {"remote_provision": {}}
        printed: list[str] = []
        handler = remote_provision_command(lambda: stored, lambda value: stored.update(value),
                                           self.provisioner, printed.append)
        handler(SimpleNamespace(values=[f"manifest_url={MANIFEST_URL}", "enabled=true", "authorization=Bearer x"]))
        self.assertEqual({"manifest_url": MANIFEST_URL, "enabled": True, "authorization": "Bearer x"},
                         stored["remote_provision"])
        self.assertIn("remote-provision updated: manifest_url, enabled, authorization (stored)", printed)
        with self.assertRaises(SystemExit):
            handler(SimpleNamespace(values=["timeout_seconds=999"]))
        self.serve_manifest(steps=[self.python_step("ok", "pass\n")])
        printed.clear()
        handler(SimpleNamespace(values=["run", "status"]))
        self.assertIn("  step ok ran", printed)
        self.assertIn("  remote-provision complete", printed)
        self.assertTrue(any(line.startswith("  last=ok") for line in printed))


if __name__ == "__main__":
    unittest.main()
