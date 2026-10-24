"""Read-only probe: run the installed native_checkpoint_from_transcript on celly's
transcript and record why it does or does not return the compact boundary."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.path.expanduser(r"~/.local/share/ciel-runtime"))
from ciel_runtime_support import runtime_compaction_control as rcc

path = Path(r"G:\ciel-bridge-service\.claude\projects\G--ciel-bridge-service\30179e9a-f9b9-4860-99ff-6045b647bf6f.jsonl")
scope = rcc.CompactionScope("claude", "30179e9a-f9b9-4860-99ff-6045b647bf6f", "6d240662d67c44eba5fed4bdb68678c4")

def sig(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

rows = []
for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 6):
    rcc._scan_cache.clear()
    pre = sig(path.stat())
    with path.open("rb") as s:
        f0 = sig(os.fstat(s.fileno()))
    t = time.monotonic()
    cp = rcc.native_checkpoint_from_transcript(path, scope)
    el = time.monotonic() - t
    with path.open("rb") as s:
        f1 = sig(os.fstat(s.fileno()))
    post = sig(path.stat())
    rows.append({"i": i, "at": time.strftime("%H:%M:%S"), "elapsed_s": round(el, 2),
                 "checkpoint": None if cp is None else {"uuid_line_sha": cp.identity[:16], "persisted_at": cp.persisted_at,
                                                         "post_tokens": cp.post_tokens, "trigger": cp.trigger,
                                                         "last_input_tokens": cp.last_input_tokens},
                 "path_stat_pre": pre, "fstat_pre": f0, "fstat_post": f1, "path_stat_post": post,
                 "stat_eq_fstat_pre": pre == f0, "stat_eq_fstat_post": post == f1, "unchanged": pre == post})
    print(json.dumps(rows[-1]), flush=True)
    time.sleep(1)
json.dump(rows, open(sys.argv[1], "w"), indent=1)
