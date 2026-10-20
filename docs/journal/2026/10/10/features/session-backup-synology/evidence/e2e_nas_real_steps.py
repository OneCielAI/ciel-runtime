"""Real NAS steps (detached, attached to the menu console). The person types the password; everything else is keys."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
QC_ID, ACCOUNT, FOLDER = sys.argv[3], sys.argv[4], sys.argv[5]
WS = Path(sys.argv[6])
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


def clear_result() -> None:
    select("Parallel transfers")
    console.key("enter")
    answer("Chunks compressed and sent at the same time", "8")
    expect("Backups compress and send 8 chunk(s)", 10)


def timed(label: str, done: str, timeout: float = 1800) -> None:
    start = time.monotonic()
    console.key("enter")
    ok = console.wait_for(done, timeout)
    log(f"{label}: done={ok} in {time.monotonic() - start:.1f} s")
    time.sleep(0.5)
    shot(label)


log(f"attached {PID}")
expect("18. Session backup", 60)
select("18. Session backup")
console.key("enter")
expect("Session backup options")
select("Add a target")
console.key("enter")
answer("Target type", "synology")
answer("Name for this target", "greenland")
answer("Synology QuickConnect ID", QC_ID)
answer("DSM account", ACCOUNT)
answer("NAS folder", FOLDER)
console.wait_for("DSM password", 30)
shot("waiting-for-password")
log("waiting for the person to type the password")
(RUN / "waiting-for-password").write_text("1")
if not console.wait_for("Test it now", 900):
    log("password not entered within 15 minutes")
    sys.exit(3)
(RUN / "waiting-for-password").unlink(missing_ok=True)
log("password entered by the person")
answer("Test it now")
answer("Use it for backups")
expect("Added target greenland", 180)
time.sleep(1)
shot("added-and-tested")
select("] local", "up")
console.key("enter")
answer("local: use / test", "use")
expect("local is no longer used")

select("Back up this session now", "up")
timed("first-backup", "Saved snapshot")
clear_result()
select("Back up this session now", "up")
timed("incremental-backup", "Saved snapshot")
(WS / "notes.md").write_text("changed after the NAS backup\n", encoding="utf-8")
select("Verify a snapshot")
console.key("enter")
answer("Snapshot id to verify")
expect("on greenland", 900)
time.sleep(0.5)
shot("verified")
select("Restore a snapshot")
console.key("enter")
answer("Snapshot id to restore")
answer("Restore the work folder into")
answer("Restore work folder files too")
start = time.monotonic()
answer("over the current session files", "yes")
ok = console.wait_for("Restored ", 1800)
log(f"restore: done={ok} in {time.monotonic() - start:.1f} s")
time.sleep(0.5)
shot("restored")
log(f"notes.md after restore: {(WS / 'notes.md').read_text(encoding='utf-8')!r}")
select("Snapshots of this folder", "up")
console.key("enter")
expect("secrets", 300)
time.sleep(0.5)
shot("snapshot-list")
log("done (menu left open)")
