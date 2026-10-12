"""Drive the Ciel menu in another console: 18. Session backup -> a backup row, keep screens and screenshots.

usage: python menu_drive.py PID TITLE RUN_DIR ROW_TEXT
Runs detached (no console of its own). Writes RUN_DIR/screen-NN-<label>.txt and .png at each step.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\djlov\ciel-runtime\docs\journal\2026\10\10\features\session-restart\evidence")
from console_driver import Console  # noqa: E402

pid, title, run, row_text = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
SHOT = r"C:\Users\djlov\ciel-runtime\docs\journal\2026\10\10\features\session-restart\evidence\shot_title.ps1"
log = (run / "drive.log").open("a", encoding="utf-8")
count = [0]


def note(text: str) -> None:
    log.write(time.strftime("%H:%M:%S ") + text + "\n")
    log.flush()


def keep(label: str) -> str:
    count[0] += 1
    screen = console.screen()
    (run / f"screen-{count[0]:02d}-{label}.txt").write_text(time.strftime("%H:%M:%S\n") + screen, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SHOT, title,
                    str(run / f"screen-{count[0]:02d}-{label}.png")], capture_output=True, creationflags=0x08000000)
    note(f"kept {label}")
    return screen


def select(text: str, limit: int = 60) -> bool:
    """Move the '> ' marker to the row containing text."""

    for _ in range(limit):
        if any(line.lstrip().startswith("> ") and text in line for line in console.screen().splitlines()):
            return True
        console.key("down", pause=0.15)
    return False


console = Console(pid)
try:
    if not console.wait_for("Session backup", 90):
        keep("no-menu")
        raise SystemExit("menu did not appear")
    keep("menu")
    if not select("Session backup"):
        raise SystemExit("could not select Session backup")
    console.key("enter")
    if not console.wait_for("Full backup now", 30):
        keep("no-panel")
        raise SystemExit("panel did not open")
    if not select(row_text):
        raise SystemExit(f"could not select {row_text}")
    keep("panel")
    console.key("enter")
    started = time.time()
    last_shot = 0.0
    while time.time() - started < 3600:
        screen = console.screen()
        if "Saved snapshot" in screen or "Backup not saved" in screen:
            note(f"finished after {time.time() - started:.0f} s")
            keep("result")
            break
        if "Backing up:" in screen and time.time() - last_shot > 20:
            last_shot = time.time()
            keep("progress")
        time.sleep(1)
    console.key("esc")
    time.sleep(1)
    keep("after-esc")
    console.text("q")
    time.sleep(2)
    keep("quit")
finally:
    note("driver done")
