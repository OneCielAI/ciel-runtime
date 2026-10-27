"""Run owned native acceptance against one exact published snapshot."""
import json, os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "ciel_runtime.py").exists())
SNAP = Path(sys.argv[1]).resolve()
OUT = HERE.parent
cases = [
 ("compact-sse", "diagnostics/compact-retry/evidence/repro/shared_recovery_e2e.py", ["--runtime-root", str(SNAP), "--label", "installed-shared"], {}),
 ("hybrid", "fixes/work-hybrid-contract/evidence/e2e_hybrid.py", [], {"WORK_PROOF_REPO": str(SNAP)}),
 ("shell", "fixes/work-shell-permissions/evidence/e2e_shell.py", [], {"WORK_PROOF_REPO": str(SNAP)}),
 ("handoff", "diagnostics/edward-sse-injection/evidence/e2e_handoff.py", [], {"HANDOFF_PROOF_REPO": str(SNAP), "HANDOFF_LAUNCHER": "1"}),
 ("codex", "fixes/codex-durable-recovery/evidence/native_e2e.py", [str(SNAP), str(OUT / "installed-codex")], {}),
]
results = []
for name, rel, args, extra in cases:
 env = os.environ.copy(); env.update(extra)
 log = OUT / ("installed-" + name + ".txt")
 with log.open("w", encoding="utf-8") as stream:
  result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(REPO / "docs/journal/2026/10/26" / rel), *args], env=env, stdout=stream, stderr=subprocess.STDOUT)
 results.append({"case": name, "exit_code": result.returncode, "log": str(log)})
 (OUT / "installed-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
 print(name, result.returncode, flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
