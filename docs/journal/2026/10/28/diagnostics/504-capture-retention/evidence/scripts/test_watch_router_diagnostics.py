"""Exercise watch_router_diagnostics.py on an owned synthetic log: filtering, partial lines, rotation, exit.

Usage: python -I test_watch_router_diagnostics.py <out_file>
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

here = Path(__file__).resolve().parent
root = Path(tempfile.mkdtemp(prefix="watch-diag-"))
inst, out = root / "instance", root / "out"
inst.mkdir()
log = inst / "router.log"


def append(text):
    with log.open("a", encoding="utf-8") as handle:
        handle.write(text)


append("2026-10-28T00:00:00 [INFO] old line before watch cielairouter_preoutput_commit should be skipped\n")
watcher = subprocess.Popen([sys.executable, "-I", str(here / "watch_router_diagnostics.py"), str(inst), str(out), "0.01", "6"])
time.sleep(7)
append("2026-10-28T00:00:01 [INFO] access GET /health 200\n")
append("2026-10-28T00:00:02 [INFO] cielairouter_preoutput_guard state=armed A\n")
append("2026-10-28T00:00:03 [INFO] cielairouter_preoutput_commit reason=multiple_frames ")  # partial line
time.sleep(7)
append("B\n")
time.sleep(7)
append("2026-10-28T00:00:04 [INFO] cielairouter_late_sse_retry before rotation C\n")
log.replace(inst / "router.log.1")  # rotation, as RouterFileLogger does
append("2026-10-28T01:00:00 [INFO] access GET /health 200\n")
append("2026-10-28T01:00:01 [WARN] anthropic_sse_error_shape {\"payload\":{}} D\n")
watcher.wait(timeout=60)
kept = (out / "kept-lines.txt").read_text(encoding="utf-8").splitlines()
status = json.loads((out / "watch-status.json").read_text(encoding="utf-8"))
checks = {
    "skips_lines_before_start": not any("old line" in k for k in kept),
    "drops_unrelated_lines": not any("/health" in k for k in kept),
    "partial_line_joined": any(k.endswith("multiple_frames B") for k in kept),
    "line_before_rotation_kept": any(k.endswith("C") for k in kept),
    "line_after_rotation_kept": any(k.endswith("D") for k in kept),
    "rotation_counted": status["rotations"] == 1,
    "exits_after_error": status.get("reason") == "sse_error_captured",
    "kept_count": status["kept"] == 4 == len(kept),
}
result = {"checks": checks, "kept": kept, "status": status}
Path(sys.argv[1]).write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
