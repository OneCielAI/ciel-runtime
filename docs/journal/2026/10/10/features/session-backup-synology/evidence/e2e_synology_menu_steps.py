"""Menu steps for e2e_synology_menu.py (detached, attached to the menu console)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
HERE = Path(__file__).resolve().parent
PORT = (RUN / "port.txt").read_text().strip()
WS = Path(r"G:\ciel-e2e\synology\ws")
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
    log(f"prompt {prompt!r} seen={seen} answered={'<hidden>' if 'password' in prompt.lower() else value or '<default>'}")


def expect(text: str, timeout: float = 120) -> bool:
    ok = console.wait_for(text, timeout)
    log(f"expect {text!r}: {ok}")
    return ok


log(f"attached {PID}")
expect("18. Session backup", 60)
select("18. Session backup")
console.key("enter")
expect("Session backup options")
select("Add a target")
shot("panel-add-row")

# 1 add the NAS target
console.key("enter")
answer("Target type", "synology")
answer("Name for this target", "nas-e2e")
answer("Synology QuickConnect ID", f"http://127.0.0.1:{PORT}")
answer("DSM account", "ciel-backup")
answer("NAS folder")
seen = console.wait_for("DSM password", 30)
console.text("e2e-stand-in-pass-1")
time.sleep(0.5)
shot("password-typed-hidden")
console.key("enter")
log(f"prompt 'DSM password' seen={seen} answered=<hidden>")
answer("Test it now")
answer("Use it for backups")
expect("Added target nas-e2e", 60)
expect("Test: ", 10)
shot("added-and-tested")

# 2 only the NAS
select("] local", "up")
console.key("enter")
answer("local: use / test", "use")
expect("local is no longer used")
shot("nas-only")

# 3 two backups
select("Back up this session now", "up")
console.key("enter")
expect("Saved snapshot", 180)
shot("first-backup")
console.key("down")
select("Back up this session now", "up")
console.key("enter")
expect("incremental: 0 of", 180)
shot("second-backup-nothing-new")

# 4 verify, then restore after a change
select("Verify a snapshot")
console.key("enter")
answer("Snapshot id to verify")
expect("complete, every chunk checks out", 120)
shot("verified")
(WS / "notes.md").write_text("changed after the backup\n", encoding="utf-8")
select("Restore a snapshot")
console.key("enter")
answer("Snapshot id to restore")
answer("Restore the work folder into")
answer("Restore work folder files too")
answer("over the current session files", "yes")
expect("Restored ", 180)
shot("restored")

# 5 the NAS password changes: store the new one from the target row, then test
(RUN / "change-password").write_text("1")
time.sleep(1)
select("] nas-e2e", "up")
console.key("enter")
answer("nas-e2e: use / test / password / remove", "test")
expect("nas-e2e test failed", 60)
shot("old-password-refused")
select("] nas-e2e", "up")
console.key("enter")
answer("nas-e2e: use / test / password / remove", "password")
console.wait_for("DSM password", 30)
console.text("e2e-stand-in-pass-2")
console.key("enter")
expect("nas-e2e: password stored for this Windows user", 30)
select("] nas-e2e", "up")
console.key("enter")
answer("nas-e2e: use / test / password / remove", "test")
expect("nas-e2e: ", 60)
time.sleep(1)
shot("new-password-test")
console.key("esc")
time.sleep(1)
console.text("q")
log("done")
