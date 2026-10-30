"""Run the isolated actual CLI harness against the exact installed gate snapshot."""
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[8]
commit = sys.argv[1]
assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)
snapshot = Path("C:/Users/djlov/.local/share/ciel-runtime-" + commit)
assert (snapshot / "ciel_runtime_support/sse_gate.py").exists()
proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ROOT / "docs/journal/2026/10/29/features/sse-gate-control/evidence/scripts/sse_gate_cli_e2e.py"), "--runtime-root", str(snapshot), "--label", "installed-gate"])
raise SystemExit(proc.returncode)
