"""Menu steps for e2e_menu_targets.py, run detached and attached to the menu's console.

Usage: python e2e_menu_steps.py <menu pid> <run dir> <nas folder> <key file>
Every step waits for the text it answers; the screen text after each step is saved, plus a window capture.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN, NAS, KEYFILE = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
SHOTS = RUN / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "steps.log", "w", encoding="utf-8")
HERE = Path(__file__).resolve().parent
console = Console(PID)
NO_WINDOW = 0x08000000


def log(message: str) -> None:
    LOG.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    LOG.flush()


def shot(label: str) -> None:
    index = len(list(SHOTS.glob("*.png"))) + 1
    (SHOTS / f"{index:02d}-{label}.txt").write_text(console.screen(), encoding="utf-8")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_hwnd.ps1"),
                        str(int(console.hwnd or 0)), str(SHOTS / f"{index:02d}-{label}.png")],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def selected(label: str) -> bool:
    return any(line.lstrip().startswith(">") and label in line for line in console.screen().splitlines())


def select(label: str, direction: str = "down", limit: int = 30) -> bool:
    for _ in range(limit):
        if selected(label):
            return True
        console.key(direction)
    ok = selected(label)
    log(f"select {label!r}: {ok}")
    return ok


def answer(prompt: str, value: str = "") -> bool:
    ok = console.wait_for(prompt, 30)
    if value:
        console.text(value)
    console.key("enter")
    log(f"prompt {prompt!r} seen={ok} answered={'<default>' if not value else value!r}")
    return ok


def expect(text: str, timeout: float = 60) -> bool:
    ok = console.wait_for(text, timeout)
    log(f"expect {text!r}: {ok}")
    return ok


log(f"attached to pid {PID}, hwnd {console.hwnd}")
expect("18. Session backup", 60)
shot("menu")
select("18. Session backup")
shot("row-18-selected")
console.key("enter")
expect("Session backup options")
shot("panel")

# Add a target
select("Add a target")
console.key("enter")
answer("Target type", "local")
answer("Name for this target", "nas")
answer("Folder (local disk", NAS)
answer("Test it now")
answer("Use it for backups")
expect("Added target nas")
expect("write, read and delete worked")
shot("target-added-and-tested")

# Deselect the built-in local target
select("] local", "up")
console.key("enter")
answer("local: use / test")
expect("local is no longer used for backups")
shot("local-deselected")

# Test the nas target from its row
select("] nas")
console.key("enter")
answer("nas: use / test / remove", "test")
expect("nas: write, read and delete worked")
shot("nas-tested")

# Backup key file
select("Backup key for credentials")
console.key("enter")
answer("Backup key file", KEYFILE)
expect("New random backup key created")
shot("key-file-created")

# Back up now, twice: the second shows the incremental upload
select("Back up this session now", "up")
console.key("enter")
expect("Saved snapshot", 180)
shot("backup-1")


def saved_id() -> str:
    for line in console.screen().splitlines():
        if "Saved snapshot " in line:
            return line.split("Saved snapshot ", 1)[1].split()[0]
    return ""


first_id = saved_id()
console.key("enter")
end = time.time() + 180
while time.time() < end and saved_id() in ("", first_id):
    time.sleep(0.5)
log(f"backup ids: first={first_id} second={saved_id()}")
time.sleep(1)
shot("backup-2-incremental")

# List
select("Snapshots of this folder")
console.key("enter")
expect("Snapshots on nas", 120)
shot("snapshot-list")

# Verify
select("Verify a snapshot")
console.key("enter")
answer("Snapshot id to verify")
expect("complete, every chunk checks out", 180)
shot("verified")

# Restore (into where it was), with confirmation
select("Restore a snapshot", "up")
console.key("enter")
answer("Snapshot id to restore")
answer("Restore the work folder into")
answer("Restore work folder files too")
answer("over the current session files", "yes")
expect("Restored ", 240)
shot("restored")

# Prune
select("Prune old snapshots now")
console.key("enter")
expect("Pruned nas", 120)
shot("pruned")
console.key("esc")
time.sleep(1)
shot("closed")
log("done")
