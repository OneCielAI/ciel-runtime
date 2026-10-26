"""Replay celly's admission decision on a private copy of its state.

The live transcript is only read. The admission state is copied into a temp
directory, so nothing of celly's session is written. Run once with the
installed code unchanged, once with the transcript scan comparing the same
signature fields except st_ctime_ns between Path.stat and os.fstat.
"""
import json, os, shutil, sys, tempfile
from dataclasses import replace
from pathlib import Path

package = Path(sys.argv[1])
mode = sys.argv[2]  # "installed" or "no-ctime"
sys.path.insert(0, str(package))
from ciel_runtime_support import runtime_compaction_control as rcc

live_state = Path(r"G:\ciel-bridge-service\AppData\Roaming\ciel-runtime\router-instances\9385-52ea0f8a2296\compaction-admission.json")
transcript = Path(r"G:\ciel-bridge-service\.claude\projects\G--ciel-bridge-service\30179e9a-f9b9-4860-99ff-6045b647bf6f.jsonl")
scope = rcc.CompactionScope("claude", "30179e9a-f9b9-4860-99ff-6045b647bf6f", "6d240662d67c44eba5fed4bdb68678c4")

if mode == "no-ctime":
    real_stat = Path.stat
    from types import SimpleNamespace
    def stat_with_fstat_ctime(self, *a, **k):
        st = real_stat(self, *a, **k)
        if self == transcript:
            # Same fields as the installed signature; only st_ctime_ns comes
            # from os.fstat so both sides of the comparison use one meaning.
            with open(self, "rb") as f:
                ctime = os.fstat(f.fileno()).st_ctime_ns
            return SimpleNamespace(st_dev=st.st_dev, st_ino=st.st_ino, st_size=st.st_size,
                                   st_mtime_ns=st.st_mtime_ns, st_ctime_ns=ctime)
        return st
    Path.stat = stat_with_fstat_ctime

class Requests:
    def queue_automatic(self, *a, **k): return None
    def clear(self, *a, **k): return True

tmp = Path(tempfile.mkdtemp())
state_path = tmp / "compaction-admission.json"
shutil.copyfile(live_state, state_path)
logs = []
control = rcc.build_runtime_compaction_control(
    state_path, scope=lambda: scope, transcript_path=lambda: transcript,
    requests=Requests(), compact_kind=lambda body: None, log=lambda level, text: logs.append(f"{level} {text}"))
import time
direct = []
for _ in range(2):
    rcc._scan_cache.clear()
    cp = rcc.native_checkpoint_from_transcript(transcript, scope)
    direct.append(None if cp is None else {"sha": cp.identity[:16], "persisted_at": cp.persisted_at, "trigger": cp.trigger})
results = []
for attempt in range(10):
    paused = control.external_admission_paused()
    results.append(paused)
    if not paused:
        break
    time.sleep(1)
after = json.loads(state_path.read_text(encoding="utf-8"))
print(json.dumps({"python": sys.version.split()[0], "mode": mode, "direct_scan": direct, "paused_per_call": results,
                  "phase_after": after.get("phase"), "failure_after": after.get("failure"),
                  "next_raw_tokens": after.get("next_raw_tokens"),
                  "logs": [l for l in logs if "checkpoint_scan" not in l]}, ensure_ascii=False))
shutil.rmtree(tmp)
