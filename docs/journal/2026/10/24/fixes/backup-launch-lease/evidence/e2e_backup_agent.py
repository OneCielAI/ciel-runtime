"""Detached console agent for e2e_backup_menu.py: drives the real prelaunch menu.

python -I e2e_backup_agent.py <console-pid> <run-dir>

Attaches to the isolated menu's console (it must be started with DETACHED_PROCESS).
Moves the cursor to "18. Session backup", presses Enter, selects "Back up this
session now", presses Enter, and waits for the menu's own result text. It writes
each step's screen to <run-dir>/screen-NN-<label>.txt and a marker file the
orchestrator waits on before taking a window capture. Nothing else is typed.
Stops when <run-dir>/stop exists.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "16/features/cielairouter-direct-path/evidence/e2e"))
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
step = 0


def note(event: str, **extra) -> None:
    with open(RUN / "agent.log", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": time.strftime("%H:%M:%S"), "event": event, **extra}) + "\n")


def keep(label: str, screen: str) -> None:
    global step
    step += 1
    (RUN / f"screen-{step:02d}-{label}.txt").write_text(screen, encoding="utf-8")
    (RUN / f"marker-{label}").write_text(time.strftime("%H:%M:%S"), encoding="utf-8")
    note("screen", label=label)


def wait_screen(predicate, timeout: float) -> str:
    end = time.time() + timeout
    screen = ""
    while time.time() < end and not (RUN / "stop").exists():
        screen = console.screen()
        if predicate(screen):
            return screen
        time.sleep(0.3)
    return ""


def selected(screen: str) -> str:
    for line in screen.splitlines():
        if line.lstrip().startswith(">"):
            return line.lstrip()[1:].strip()
    return ""


def move_to(predicate, limit: int = 40) -> bool:
    for _ in range(limit):
        if predicate(selected(console.screen())):
            return True
        console.key("down", pause=0.35)
    return predicate(selected(console.screen()))


console = None
for _ in range(240):
    try:
        console = Console(PID)
        break
    except OSError as exc:
        note("attach_retry", error=str(exc))
        time.sleep(0.25)
if console is None:
    note("attach_failed")
    raise SystemExit(1)
note("attached", pid=PID)

screen = wait_screen(lambda s: "18. Session backup" in s and selected(s), 120)
if not screen:
    note("menu_not_seen", screen=console.screen()[-2000:])
    raise SystemExit(2)
time.sleep(1.0)
keep("menu", console.screen())
if not move_to(lambda row: bool(re.match(r"18\.\s+Session backup", row))):
    note("item18_not_selected", row=selected(console.screen()))
    raise SystemExit(3)
keep("item18-selected", console.screen())
console.key("enter")
if not wait_screen(lambda s: "Back up this session now" in s, 30):
    note("submenu_not_seen", screen=console.screen()[-2000:])
    raise SystemExit(4)
time.sleep(0.8)
if not move_to(lambda row: row.startswith("Back up this session now")):
    note("backup_row_not_selected", row=selected(console.screen()))
    raise SystemExit(5)
keep("submenu", console.screen())
console.key("enter")
screen = wait_screen(lambda s: "Backup not saved" in s or "Saved snapshot" in s, 240)
time.sleep(1.0)
screen = console.screen()
outcome = "saved" if "Saved snapshot" in screen else ("not-saved" if "Backup not saved" in screen else "none")
keep("result", screen)
(RUN / "outcome.json").write_text(json.dumps({"outcome": outcome}), encoding="utf-8")
note("outcome", outcome=outcome)
while not (RUN / "stop").exists():
    time.sleep(0.3)
