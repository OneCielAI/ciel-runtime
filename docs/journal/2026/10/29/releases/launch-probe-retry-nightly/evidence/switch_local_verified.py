"""Switch only a fully verified installed release, preserving running sessions."""
from pathlib import Path
import json
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
commit = sys.argv[1]
assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)
snapshot = Path("C:/Users/djlov/.local/share/ciel-runtime-" + commit)
tests = json.loads((OUT / "installed-tests.json").read_text())
assert tests["success"]
proof = json.loads((OUT / "installed-cli/result.json").read_text(encoding="utf-8"))
assert proof["runtime_root"] == str(snapshot)
assert proof["status"] == "gate_slash_local_verified" and proof["live_unchanged"]
assert proof["foreground"]["owned_hits"] == 0
assert len(proof["catalog"]) >= 2
subprocess.run([sys.executable, "-I", "-X", "utf8", str(ROOT / "docs/journal/2026/10/27/releases/work-mode-switch-nightly/evidence/scripts/switch_local.py"), str(snapshot), commit[:7], str(OUT / "local-switch.json")], check=True)
subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "post_install.py"), str(ROOT), str(OUT)], check=True)
cmd = ["powershell", "-NoProfile", "-Command", "$env:CIEL_RUNTIME_HOME=[Environment]::GetEnvironmentVariable('CIEL_RUNTIME_HOME','User'); & ($env:USERPROFILE+'/.local/bin/ciel-runtimectl.ps1') version"]
p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=45)
(OUT / "local-version.txt").write_text(p.stdout + p.stderr, encoding="utf-8")
assert p.returncode == 0 and commit[:7] in p.stdout
print(p.stdout)
