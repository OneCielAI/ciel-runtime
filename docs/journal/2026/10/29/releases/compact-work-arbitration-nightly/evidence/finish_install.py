"""Install only digest-verified exact package; preserve live processes."""
from pathlib import Path
import json
import re
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
COMMIT = "037c55fc252641c41d8058db66fa055e9f43dfdf"
PAYLOAD = Path(json.loads((OUT / "payload-location.json").read_text())["path"])
PACKAGE = PAYLOAD / "payload/unpack/package"
SNAPSHOT = Path("C:/Users/djlov/.local/share/ciel-runtime-" + COMMIT)
HELPERS = ROOT / "docs/journal/2026/10/27/releases/work-mode-switch-nightly/evidence/scripts"
def run(args, name):
    p = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace")
    (OUT / name).write_text(p.stdout + p.stderr, encoding="utf-8")
    print(name, p.returncode, flush=True)
    if p.returncode:
        print(p.stdout + p.stderr)
        raise SystemExit(p.returncode)
run([sys.executable, "-I", str(HELPERS / "compare_package.py"), str(ROOT), COMMIT, str(PACKAGE), str(OUT / "package-comparison.json")], "package-comparison.txt")
comparison = json.loads((OUT / "package-comparison.json").read_text())
assert not comparison["not_in_commit"], comparison
assert sorted(comparison["other_differences"]) == ["ciel_runtime_support/runtime_constants.py", "package.json"], comparison
expected = json.loads(subprocess.check_output(["git", "-C", str(ROOT), "show", COMMIT + ":package.json"]))
actual = json.loads((PACKAGE / "package.json").read_text())
version = actual.pop("version")
expected.pop("version")
assert actual == expected and version.endswith(".037c55f")
original = subprocess.check_output(["git", "-C", str(ROOT), "show", COMMIT + ":ciel_runtime_support/runtime_constants.py"]).replace(bytes([13,10]), bytes([10]))
constants = (PACKAGE / "ciel_runtime_support/runtime_constants.py").read_bytes().replace(bytes([13,10]), bytes([10]))
assert re.sub(rb'^VERSION = .*$', b'VERSION = "STAMP"', original, flags=re.M) == re.sub(rb'^VERSION = .*$', b'VERSION = "STAMP"', constants, flags=re.M)
run([sys.executable, "-I", str(HELPERS / "install_snapshot.py"), str(PACKAGE), str(SNAPSHOT)], "snapshot-install.json")
archive = PAYLOAD / "payload/download/published.tgz"
run(["npm.cmd", "install", "-g", "--ignore-scripts", str(archive)], "npm-install.txt")
run([sys.executable, "-I", str(ROOT / "docs/journal/2026/10/29/releases/work-compact-nightly/evidence/verify_global.py"), str(PACKAGE), "C:/Users/djlov/AppData/Roaming/npm/node_modules/@one-ciel-ai/ciel-runtime", str(OUT / "global-install.json")], "global-verify.txt")
tests = (OUT / "exact-tests-path.txt").read_text()
run([sys.executable, "-I", "-X", "utf8", str(OUT / "installed_tests.py"), str(SNAPSHOT), tests, str(OUT)], "installed-test-runner.txt")
print("INSTALLED VERIFIED", version, SNAPSHOT, flush=True)
