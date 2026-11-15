"""The npm launcher replaces a Python below the pinned version (2026-11-15)."""

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "npm-bin" / "run-ciel-runtime.js"


def node(body: str) -> dict:
    """Run ``body`` with the launcher loaded as a library; it prints one JSON line."""

    script = textwrap.dedent("""
        process.env.CIEL_RUNTIME_LAUNCHER_LIBRARY = "1";
        const launcher = require(%s);
        const crypto = require("node:crypto");
        const fs = require("node:fs");
        const path = require("node:path");
        (async () => { %s })().catch((e) => { console.log(JSON.stringify({ crash: String(e.stack || e) })); });
    """) % (json.dumps(str(LAUNCHER)), body)
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise AssertionError(done.stdout + done.stderr)
    value = json.loads(lines[-1])
    if "crash" in value:
        raise AssertionError(value["crash"])
    return value


INSTALL = """
    const base = %(base)s;
    const payload = Buffer.from("fixture runtime zip");
    const entry = { url: "https://example.invalid/python.zip",
      sha256: %(sha)s || crypto.createHash("sha256").update(payload).digest("hex") };
    const fetched = [], logs = [];
    const result = await launcher.ensureManagedPython("Python 3.14.2 via py is older", {
      platform: %(platform)s, arch: "x64", base, entry,
      fetch: async (url) => { fetched.push(url); return { ok: true, status: 200, arrayBuffer: async () => payload }; },
      extract: (zip, target) => fs.writeFileSync(path.join(target, "python.exe"), "stub"),
      probe: () => ({ version: [3, 14, 10], versionText: "3.14.10" }),
      log: (line) => logs.push(line) });
    const dir = launcher.managedDirectory(base, "x64");
    const leftovers = fs.existsSync(path.dirname(dir)) ? fs.readdirSync(path.dirname(dir)) : [];
    console.log(JSON.stringify({ result, fetched, logs, dir, leftovers }));
"""


class NpmLauncherPythonTests(unittest.TestCase):
    def test_versions_below_the_pin_are_replaced_and_newer_ones_kept(self):
        value = node("""
            const below = (v) => launcher.versionBelow(launcher.parseVersion(v), launcher.MANAGED_PYTHON.version);
            console.log(JSON.stringify({ pin: launcher.MANAGED_PYTHON.version,
              cases: ["3.14.2", "3.13.5", "3.11.6", "3.10.0", "3.14.10", "3.14.11", "3.15.2"].map(v => [v, below(v)]) }));
        """)
        self.assertEqual("3.14.10", value["pin"])
        self.assertEqual([["3.14.2", True], ["3.13.5", True], ["3.11.6", True], ["3.10.0", True],
                          ["3.14.10", False], ["3.14.11", False], ["3.15.2", False]], value["cases"])

    def test_pinned_urls_are_the_official_python_org_runtime_zips(self):
        value = node("console.log(JSON.stringify(launcher.MANAGED_PYTHON.win32));")
        self.assertEqual("https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.zip", value["x64"]["url"])
        self.assertEqual("https://www.python.org/ftp/python/3.14.10/python-3.14.10-arm64.zip", value["arm64"]["url"])
        for entry in value.values():
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_download_verified_extracted_and_reused(self):
        with tempfile.TemporaryDirectory() as raw:
            first = node(INSTALL % {"base": json.dumps(raw), "sha": "null", "platform": json.dumps("win32")})
            self.assertEqual(str(Path(first["dir"]) / "python.exe"), first["result"])
            self.assertEqual(["https://example.invalid/python.zip"], first["fetched"])
            self.assertEqual(["3.14.10-x64"], first["leftovers"])  # no staging dir or zip left
            again = node(INSTALL % {"base": json.dumps(raw), "sha": "null", "platform": json.dumps("win32")})
            self.assertEqual(first["result"], again["result"])
            self.assertEqual([], again["fetched"])

    def test_hash_mismatch_installs_nothing_and_the_launch_goes_on(self):
        with tempfile.TemporaryDirectory() as raw:
            value = node(INSTALL % {"base": json.dumps(raw), "sha": json.dumps("0" * 64), "platform": json.dumps("win32")})
            self.assertIsNone(value["result"])
            self.assertTrue(any("does not match the pinned" in line for line in value["logs"]))
            self.assertEqual([], value["leftovers"])

    def test_other_platforms_continue_with_a_notice(self):
        with tempfile.TemporaryDirectory() as raw:
            value = node(INSTALL % {"base": json.dumps(raw), "sha": "null", "platform": json.dumps("linux")})
            self.assertIsNone(value["result"])
            self.assertEqual([], value["fetched"])

    def test_opt_out_variable_disables_the_install(self):
        with tempfile.TemporaryDirectory() as raw:
            value = node('process.env.CIEL_RUNTIME_PYTHON_AUTO_INSTALL = "0";'
                         + INSTALL % {"base": json.dumps(raw), "sha": "null", "platform": json.dumps("win32")})
            self.assertIsNone(value["result"])
            self.assertEqual([], value["fetched"])


if __name__ == "__main__":
    unittest.main()
