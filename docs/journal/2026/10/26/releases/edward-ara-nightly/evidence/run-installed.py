"""Run native proofs against an already verified fresh published snapshot."""
import json, os, subprocess, sys
from pathlib import Path
repo = Path(__file__).resolve().parents[8]
snapshot = Path(sys.argv[1])
assert (snapshot / "ciel_runtime.py").is_file()
results = []
for kind, script, extra in [
    ("handoff", "diagnostics/edward-sse-injection/evidence/e2e_handoff.py", {"HANDOFF_PROOF_REPO": str(snapshot), "HANDOFF_LAUNCHER": "1"}),
    ("work-stop", "diagnostics/ara-work-queue/evidence/e2e_stop_notice.py", {"WORK_PROOF_REPO": str(snapshot)}),
]:
    env = os.environ.copy(); env.update(extra)
    script_path = repo / "docs/journal/2026/10/26" / script
    log = Path(__file__).parent / ("installed-" + kind + ".txt")
    with log.open("w", encoding="utf-8") as stream:
        run = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script_path)], env=env, stdout=stream, stderr=subprocess.STDOUT)
    results.append({"kind": kind, "exit_code": run.returncode, "log": str(log)})
Path(__file__).with_name("installed-proof-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
raise SystemExit(any(row["exit_code"] for row in results))
