"""Keep the redacted retry/SSE diagnostic lines of one router log before rotation discards them.

Usage: python -I watch_router_diagnostics.py <router_instance_dir> <out_dir> [max_days] [linger_seconds]
Read-only: every poll opens router.log (and router.log.1 after a rotation), reads only the new
bytes and closes the file again, so the router's own rotation is not held up. Only lines whose
message carries one of KEYS are kept; those lines are already redacted by the router (no message
text, request bodies, URLs or credentials). The watcher exits linger_seconds (default 120) after
the first SSE error line, so the matching retry/commit lines are kept, or after max_days (default 7).
"""
import json
import sys
import time
from pathlib import Path

KEYS = ("anthropic_sse_error_shape", "anthropic_sse_upstream_error_event", "cielairouter_preoutput_",
        "cielairouter_late_sse_", "transient_retry", "upstream_retry", "budget_exhausted")
ERRORS = ("anthropic_sse_error_shape", "anthropic_sse_upstream_error_event")
root, out = Path(sys.argv[1]), Path(sys.argv[2])
deadline = time.time() + float(sys.argv[3] if len(sys.argv) > 3 else 7) * 86400
linger = float(sys.argv[4] if len(sys.argv) > 4 else 120)
out.mkdir(parents=True, exist_ok=True)
kept, status = out / "kept-lines.txt", out / "watch-status.json"
log, backup = root / "router.log", root / "router.log.1"


def head(path):
    try:
        with path.open("rb") as handle:
            return handle.read(64)
    except OSError:
        return b""


def read_from(path, offset):
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return b"", offset
    end = data.rfind(b"\n") + 1  # keep a partial last line for the next poll
    return data[:end], offset + end


state = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "router_instance": str(root), "rotations": 0,
         "kept": 0, "first_error_at": None, "polls": 0}
identity, offset = head(log), (log.stat().st_size if log.exists() else 0)  # only lines from now on
error_seen = None
while time.time() < deadline and (error_seen is None or time.time() - error_seen < linger):
    chunks = []
    current = head(log)
    if not current:  # between a rotation and the router's next write; read the old file next poll
        time.sleep(5)
        continue
    if current[:25] != identity[:25]:  # router.log was replaced: finish the old file, now router.log.1
        if head(backup)[:25] == identity[:25]:
            data, _ = read_from(backup, offset)
            chunks.append(data)
        state["rotations"] += 1
        identity, offset = current, 0
    data, offset = read_from(log, offset)
    chunks.append(data)
    lines = [line for chunk in chunks for line in chunk.decode("utf-8", "replace").splitlines()
             if any(key in line for key in KEYS)]
    if lines:
        with kept.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        state["kept"] += len(lines)
        if error_seen is None and any(key in line for line in lines for key in ERRORS):
            error_seen = time.time()
            state["first_error_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["polls"] += 1
    state["last_poll"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    status.write_text(json.dumps(state, indent=2), encoding="utf-8")
    time.sleep(5)
state["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S")
state["reason"] = "sse_error_captured" if error_seen else "max_days"
status.write_text(json.dumps(state, indent=2), encoding="utf-8")
print(json.dumps(state, indent=2))
