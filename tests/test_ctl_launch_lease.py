"""ciel-runtimectl commands never take or check a launch's 30 s startup lease.

Measured 2026-11-13 (journal ctl-work-sse-parity, prelaunch-console run-2):
`ciel-runtimectl work set` run 8 s after a menu process started in the same
workspace died at import with a traceback, because runtime_paths selected the
router port at import through select_workspace_router_port, which checks the
startup lease and raised "launch already reserved".

Commands that neither launch nor serve a router resolve the port read-only:
no lease check, no registry write. Launch paths keep the lease, the registry
and the /health scan (two-router protection), and a launch that cannot get the
lease prints one line, never a traceback.

Every process here runs in an owned temp profile (CIEL_RUNTIME_TEST_ISOLATED=1,
CIEL_RUNTIME_CONFIG_DIR under the system temp root); setUp refuses otherwise.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import ciel_runtime_support.workspace_router_selection as selection
from ciel_runtime_support import runtime_paths

ROOT = Path(__file__).resolve().parents[1]
HOLDER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ciel_runtime_support.workspace_router_selection as s
registry, workspace, base, go = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
port = s.select_workspace_router_port(base, workspace, {}, registry_path=registry)
print("held", port, flush=True)
deadline = time.time() + 25
while not go.exists() and time.time() < deadline:
    time.sleep(0.05)
# The launch continues: its own lease is still its own.
print("again", s.select_workspace_router_port(base, workspace, {}, registry_path=registry), flush=True)
"""


def free_range(size=50):
    for base in range(41000, 49000, 97):
        if all(selection.port_is_free(port) for port in range(base, base + size)):
            return base
    raise RuntimeError("no free port range")


class Isolated(unittest.TestCase):
    def setUp(self):
        temp_root = Path(tempfile.gettempdir()).resolve()
        self.assertTrue(str(Path(runtime_paths.CONFIG_DIR).resolve()).lower().startswith(str(temp_root).lower()),
                        "not an isolated profile")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.config_dir = self.root / "profile"
        self.workspace = self.root / "ws"
        for path in (self.config_dir, self.workspace, self.root / "claude-home"):
            path.mkdir()
        self.registry = self.config_dir / "workspace-router-ports.json"


class LeaseFreeResolutionTests(Isolated):
    def holder_lease(self, port=9464):
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        identity = selection._process_identity(holder.pid)
        self.assertTrue(identity)
        target = selection.workspace_identity(self.workspace)
        record = {"workspace": target, "port": port, "reservation": "auto",
                  "launch_lease": {"pid": holder.pid, "identity": identity, "expires_at": time.time() + 30}}
        self.registry.write_text(json.dumps({"version": 1, "workspaces": {selection.workspace_digest(target): record}}),
                                 encoding="utf-8")
        return self.registry.read_bytes()

    def test_resolution_neither_checks_nor_writes_a_live_lease(self):
        before = self.holder_lease()
        port = selection.resolve_workspace_router_port(
            9464, self.workspace, {}, registry_path=self.registry, health=lambda _port: None,
            available=lambda _port: True)
        self.assertEqual(9464, port)                       # the port the starting launch reserved
        self.assertEqual(before, self.registry.read_bytes())
        self.assertFalse(self.registry.with_suffix(".json.lock").exists())
        # The launch path keeps its protection.
        with self.assertRaisesRegex(RuntimeError, "launch already reserved"):
            selection.select_workspace_router_port(9464, self.workspace, {}, registry_path=self.registry,
                                                   health=lambda _port: None, available=lambda _port: True)

    def test_a_live_router_of_the_workspace_wins_then_the_mapping_then_a_candidate(self):
        target = selection.workspace_identity(self.workspace)
        self.holder_lease(port=9466)
        live = selection.resolve_workspace_router_port(
            9464, self.workspace, {}, registry_path=self.registry,
            health=lambda port: {"ok": True, "workspace": target} if port == 9470 else None,
            available=lambda _port: True)
        self.assertEqual(9470, live)
        self.registry.unlink()
        candidate = selection.resolve_workspace_router_port(
            9464, self.workspace, {}, registry_path=self.registry,
            health=lambda port: {"ok": True, "workspace": "C:/elsewhere"} if port == 9464 else None,
            available=lambda _port: True)
        self.assertEqual(9465, candidate)
        self.assertFalse(self.registry.exists())           # nothing reserved by a read

    def test_explicit_port_is_returned_without_a_claim(self):
        self.holder_lease(port=9480)
        self.assertEqual(9480, selection.resolve_workspace_router_port(
            9480, self.workspace, {"CIEL_RUNTIME_ROUTER_PORT": "9480"}, registry_path=self.registry,
            health=lambda _port: None, available=lambda _port: False))

    def test_only_listed_ctl_commands_skip_the_lease(self):
        script = str(ROOT / "ciel_runtime.py")
        for argv, takes in ((["x/ciel_runtime.py", "work", "status"], False),
                            ([script, "session-api-key", "list"], False),
                            ([script, "status"], False),
                            ([script, "cli"], True), ([script, "launch"], True), ([script, "serve"], True),
                            ([script, "test"], True), ([script, "bridge", "serve"], True),
                            ([script, "codex"], True), ([script], True), ([script, "--help"], True),
                            ([script, "no-such-command"], True),
                            (["menu_host.py", "work"], True), (["-c", "work"], True), ([], True)):
            with self.subTest(argv=argv[1:2]):
                self.assertEqual(takes, selection.takes_launch_lease(argv))
        for name in selection.LEASE_FREE_COMMANDS:
            self.assertNotIn(name, ("cli", "launch", "serve", "test", "bridge"))


class CtlDuringALaunchTests(Isolated):
    """A real launch-lease holder process and the real ciel_runtime.py ctl entry."""

    def env(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CIEL_RUNTIME_", "CIEL_CODE_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI",
                                      "MAX_MCP"))}
        env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(self.config_dir),
                   CLAUDE_CONFIG_DIR=str(self.root / "claude-home"), PYTHONPATH=str(ROOT),
                   PYTHONDONTWRITEBYTECODE="1")
        return env

    def start_launch(self):
        base = free_range()
        (self.config_dir / "config.json").write_text(json.dumps({"web_backend": {"port": base}}), encoding="utf-8")
        go = self.root / "go"
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), str(self.registry), str(self.workspace),
                                   str(base), str(go)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=self.env(), cwd=str(self.workspace))
        self.addCleanup(lambda: holder.poll() is None and holder.kill())
        line = holder.stdout.readline().split()
        self.assertEqual(["held", str(base)], line, holder.stderr.read() if holder.poll() is not None else line)
        return holder, base, go

    def ctl(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "ciel_runtime.py"), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=self.env(),
                              cwd=str(self.workspace), timeout=120)

    def finish(self, holder, go):
        go.write_text("go")
        out, err = holder.communicate(timeout=60)
        return holder.returncode, out, err

    def test_ctl_work_status_succeeds_and_leaves_the_launch_alone(self):
        holder, base, go = self.start_launch()
        before = self.registry.read_bytes()
        result = self.ctl("work", "status", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("settings", json.loads(result.stdout))
        self.assertEqual(before, self.registry.read_bytes())   # lease and mapping untouched
        # No router was started: nothing answers /health or listens on the reserved port.
        self.assertIsNone(selection.probe_router_health(base))
        self.assertTrue(selection.port_is_free(base))
        self.assertEqual([], [str(path) for path in self.config_dir.rglob("router.pid")])
        # The launch is unaffected: it still owns its lease and port.
        code, out, err = self.finish(holder, go)
        self.assertEqual(0, code, err)
        self.assertIn(f"again {base}", out)
        record = json.loads(self.registry.read_text(encoding="utf-8"))["workspaces"]
        (only,) = record.values()
        self.assertEqual((base, holder.pid), (only["port"], only["launch_lease"]["pid"]))

    def test_a_lease_taking_command_refused_by_the_lease_prints_one_line(self):
        holder, base, go = self.start_launch()
        result = self.ctl("bridge", "status")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        self.assertEqual(1, len(lines), result.stderr)
        self.assertIn("launch already reserved", lines[0])
        self.finish(holder, go)


if __name__ == "__main__":
    unittest.main()
