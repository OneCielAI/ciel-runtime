"""Menu steps for e2e_backup_safety.py (detached, attached to the menu console)."""
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


def select(label: str, direction: str = "down", limit: int = 40) -> bool:
    for _ in range(limit):
        if selected(label):
            return True
        console.key(direction)
    return selected(label)


def answer(prompt: str, value: str = "") -> None:
    seen = console.wait_for(prompt, 30)
    if value:
        console.text(value)
    console.key("enter")
    log(f"prompt {prompt!r} seen={seen} answered={value or '<default>'}")


def expect(text: str, timeout: float = 120) -> bool:
    ok = console.wait_for(text, timeout)
    log(f"expect {text!r}: {ok}")
    return ok


log(f"attached {PID}")
expect("18. Session backup", 60)
select("18. Session backup")
console.key("enter")
expect("Session backup options")
shot("panel")

# 1 nearly full C: target
select("Back up this session now", "up")
console.key("enter")
expect("not enough space on target cdrive", 180)
shot("refused-c-drive")

# 2 switch to the built-in local target (on G:) and cancel a large backup
select("] local")
console.key("enter")
answer("local: use / test", "use")
select("] cdrive")
console.key("enter")
answer("cdrive: use / test / remove", "use")
expect("cdrive is no longer used")
select("Back up this session now", "up")
console.key("enter")
expect("Backing up:", 120)
time.sleep(2)
shot("progress")
console.key("esc")
expect("Backup not saved: cancelled", 60)
shot("cancelled")

# 3 exclude the big folder (sizes listed first)
select("Exclude from the work folder")
console.key("enter")
expect("bigdata", 60)
shot("sizes-listed")
answer("Folder or file names to leave out", "bigdata")
expect("Left out of this work folder's backups: bigdata")
shot("excluded")

# 4 back up again
select("Back up this session now", "up")
console.key("enter")
expect("Saved snapshot", 180)
expect("Not in this backup", 10)
time.sleep(1)
shot("saved")
console.key("esc")
time.sleep(1)
console.text("q")
log("done")
