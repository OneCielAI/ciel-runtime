"""Menu steps for e2e_folders.py (detached, attached to the menu console)."""
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
    ok = selected(label)
    log(f"select {label!r}: {ok}")
    return ok


def expect(text: str, timeout: float = 120) -> bool:
    ok = console.wait_for(text, timeout)
    log(f"expect {text!r}: {ok}")
    return ok


log(f"attached {PID}")
expect("18. Session backup", 60)
select("18. Session backup")
console.key("enter")
expect("Session backup options")

# 1 the section, then sizes
select("Git worktrees")
expect("Git worktrees  [off · 1 in this folder · 1 elsewhere]", 10)
shot("work-folder-section")
select("Measure folder sizes")
console.key("enter")
expect("Measured ", 60)
select("] data")
shot("sizes-measured")

# 2 leave data out
console.key("enter")
expect("data is left out of this folder's backups", 10)
expect("[ ] data  [left out", 10)
shot("data-left-out")

# 3 back up with worktrees off
select("Back up this session now", "up")
console.key("enter")
expect("Saved snapshot", 180)
expect("Left out by your choice: 1 folder(s): data", 10)
expect("Git worktrees: off; 1 left out", 10)
shot("backup-worktrees-off")

# 4 worktrees inside -> all, back up again
select("Git worktrees")
console.key("enter")
expect("Git worktrees: inside", 10)
console.key("enter")
expect("Git worktrees: all", 10)
expect("Git worktrees  [all · 1 in this folder · 1 elsewhere]", 10)
shot("worktrees-all")
select("Back up this session now", "up")
console.key("enter")
expect("Saved snapshot", 180)
expect("Git worktrees: all; 1 outside this folder included", 10)
shot("backup-worktrees-all")
console.key("esc")
time.sleep(1)
console.text("q")
log("done")
