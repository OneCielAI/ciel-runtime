"""Menu steps for e2e_speed_menu.py (detached, attached to the menu console)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
HERE = Path(__file__).resolve().parent
SHOTS = RUN / "shots"
SHOTS.mkdir(exist_ok=True)
LOG = open(RUN / "steps.log", "w", encoding="utf-8")
console = Console(PID)


def log(message: str) -> None:
    LOG.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    LOG.flush()


def shot(label: str) -> None:
    index = len(list(SHOTS.glob("*.png"))) + 1
    (SHOTS / f"{index:02d}-{label}.txt").write_text(console.screen(), encoding="utf-8")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_hwnd.ps1"),
                        str(int(console.hwnd or 0)), str(SHOTS / f"{index:02d}-{label}.png")],
                       capture_output=True, text=True, creationflags=0x08000000)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def selected(label: str) -> bool:
    return any(line.lstrip().startswith(">") and label in line for line in console.screen().splitlines())


def select(label: str, direction: str = "down", limit: int = 60) -> bool:
    for _ in range(limit):
        if selected(label):
            return True
        console.key(direction)
    return selected(label)


def expect(text: str, timeout: float = 120) -> bool:
    ok = console.wait_for(text, timeout)
    log(f"expect {text!r}: {ok}")
    return ok


def set_jobs(number: int) -> None:
    select("Parallel transfers")
    console.key("enter")
    console.wait_for("Chunks compressed and sent at the same time", 20)
    console.text(str(number))
    console.key("enter")
    expect(f"Backups compress and send {number} chunk(s) at the same time.", 10)
    expect(f"Parallel transfers  [{number} at a time]", 10)


def timed_backup(label: str) -> None:
    select("Back up this session now", "up")
    start = time.monotonic()
    console.key("enter")
    ok = console.wait_for("Saved snapshot", 900)
    log(f"backup {label}: saved={ok} in {time.monotonic() - start:.1f} s (from Enter to the result on screen)")
    time.sleep(0.5)
    shot(label)


log(f"attached {PID}")
expect("18. Session backup", 60)
select("18. Session backup")
console.key("enter")
expect("Session backup options")

set_jobs(1)
shot("parallel-1")
timed_backup("first-backup-1-at-a-time")
(RUN / "reset").write_text("1")
while not (RUN / "reset-done").exists():
    time.sleep(0.5)

set_jobs(8)
shot("parallel-8")
timed_backup("first-backup-8-at-a-time")
set_jobs(8)  # replaces the previous result, so the next "Saved snapshot" is the new one
timed_backup("incremental-8-at-a-time")
console.key("esc")
time.sleep(1)
console.text("q")
log("done")
