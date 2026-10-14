"""Drive the Ciel menu in another console: 11. Remote instructions -> reload row off, then on.

usage: python menu_drive.py PID TITLE RUN_DIR
Runs detached (no console of its own). Writes RUN_DIR/screen-NN-<label>.txt and .png at each step.
"""
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
from console_driver import Console  # noqa: E402

pid, title, run = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
log = (run / "drive.log").open("a", encoding="utf-8")
count = [0]


def note(text: str) -> None:
    log.write(time.strftime("%H:%M:%S ") + text + "\n")
    log.flush()


def keep(label: str) -> str:
    count[0] += 1
    screen = console.screen()
    (run / f"screen-{count[0]:02d}-{label}.txt").write_text(time.strftime("%H:%M:%S\n") + screen, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(here / "shot_title.ps1"), title,
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
ROW = "Reload session when the company prompt changes"
try:
    if not console.wait_for("Remote instructions", 90):
        keep("no-menu")
        raise SystemExit("menu did not appear")
    if not select("Remote instructions"):
        raise SystemExit("could not select Remote instructions")
    keep("menu")
    console.key("enter")
    if not console.wait_for("remote-instructions options", 30):
        keep("no-panel")
        raise SystemExit("panel did not open")
    if not select(ROW):
        raise SystemExit("could not select the reload row")
    keep("panel-on")
    console.key("enter")
    if console.wait_for("auto_reload=False", 20):
        time.sleep(0.5)
        keep("toggled-off")
    else:
        keep("not-toggled-off")
    select(ROW)
    console.key("enter")
    if console.wait_for("auto_reload=True", 20):
        time.sleep(0.5)
        keep("toggled-on")
    else:
        keep("not-toggled-on")
    console.key("esc")
    time.sleep(1)
    console.text("q")
    time.sleep(2)
finally:
    note("driver done")
