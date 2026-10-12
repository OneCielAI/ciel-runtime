"""One real backup of a work folder through run_backup (what the menu calls), from this checkout.

usage: python real_run.py CWD LOG_FILE [--full]
Logs the progress every 10 s with this process tree's read/write rates and CPU, then the summary
(file names and secrets are not logged: counts, sizes and timings only).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[8]
sys.path.insert(0, str(REPO))
from ciel_runtime_support.session_backup_service import run_backup  # noqa: E402

cwd, log_path, full = Path(sys.argv[1]), Path(sys.argv[2]), "--full" in sys.argv
config_dir = Path(os.environ["APPDATA"]) / "ciel-runtime"
last = [0.0, None]


def io_counters() -> dict:
    command = ("$p = Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*backup create*' -and $_.CommandLine -like '*"
               + str(cwd).replace("\\", "\\\\") + "*' }; $p | % { $x = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue;"
               " [pscustomobject]@{ r = $_.ReadTransferCount; w = $_.WriteTransferCount; cpu = $x.TotalProcessorTime.TotalSeconds } }"
               " | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True).stdout.strip()
    try:
        value = json.loads(out)
        return value[0] if isinstance(value, list) else value
    except ValueError:
        return {}


def log(text: str) -> None:
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(time.strftime("%H:%M:%S ") + text + "\n")


def show(state: dict, elapsed: float) -> bool:
    if elapsed - last[0] >= 10:
        counters = io_counters()
        rates = ""
        if last[1] and counters:
            span = elapsed - last[0]
            rates = (f" | read {(counters['r'] - last[1]['r']) / span / 1048576:.0f} MB/s"
                     f" write {(counters['w'] - last[1]['w']) / span / 1048576:.0f} MB/s"
                     f" cpu {(counters['cpu'] - last[1]['cpu']) / span:.1f} cores")
        last[0], last[1] = elapsed, counters or last[1]
        log(f"{elapsed:7.0f}s phase={state.get('phase', 'reading')} files={state.get('files', 0)} "
            f"unchanged={state.get('unchanged', 0)} read={int(state.get('read') or 0) / 1e9:.1f}GB "
            f"stored={int(state.get('uploaded') or 0) / 1e9:.2f}GB{rates}")
    return True


log(f"start cwd={cwd} full={full} code={REPO}")
result = run_backup(config_dir, cwd, "menu", on_progress=show, full=full)
keep = {key: result.get(key) for key in ("ok", "id", "seconds", "files", "skipped", "not_backed_up_count", "stats", "pruned", "output")}
log("result " + json.dumps(keep, default=str))
