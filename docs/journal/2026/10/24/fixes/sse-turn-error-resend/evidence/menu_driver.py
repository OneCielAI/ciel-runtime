"""Detached key driver for menu_e2e.py: attach to the menu console and walk it.

Selected rows are drawn with "> " (main menu at column 0, panel rows
indented; prelaunch_terminal.py), so the driver presses Down until the
selected row shows the target, then Enter.  Panels scroll, so a row below
the visible window is reached by moving, not by waiting for it to appear.
Each step writes the screen and a ready-<step> flag, then waits for the
orchestrator's shot-<step> flag so the PNG shows the same frame.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT_E2E = Path(__file__).resolve().parents[4] / "16/features/cielairouter-direct-path/evidence/e2e"
sys.path.insert(0, str(ROOT_E2E))
from console_driver import Console  # noqa: E402

PID, RUN, EVIDENCE = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
log = (RUN / "driver.log").open("a", encoding="utf-8")


def note(text: str) -> None:
    log.write(f"{time.strftime('%H:%M:%S')} {text}\n")
    log.flush()


def selected(screen: str) -> str:
    return next((line for line in screen.splitlines() if line.lstrip().startswith("> ")), "")


def select(console: Console, needle: str, limit: int = 40) -> bool:
    for _ in range(limit):
        if needle in selected(console.screen()):
            return True
        console.key("down", pause=0.2)
    return needle in selected(console.screen())


def checkpoint(console: Console, step: str, name: str) -> None:
    (EVIDENCE / f"screen-{name}.txt").write_text(console.screen(), encoding="utf-8")
    (RUN / f"ready-{step}").write_text("1")
    end = time.time() + 30
    while time.time() < end and not (RUN / f"shot-{step}").exists():
        time.sleep(0.2)


console = Console(PID)
try:
    ok = console.wait_for("External event inputs", 90)
    note(f"main menu visible={ok}")
    checkpoint(console, "main", "01-main-menu")
    ok = select(console, "External event inputs")
    note(f"main row selected={ok}")
    console.key("enter", pause=1.0)
    ok = console.wait_for("External event inputs options", 20)
    note(f"panel visible={ok}")
    ok = select(console, "Resend after upstream error")
    note(f"panel row selected={ok}")
    checkpoint(console, "panel", "02-external-events-panel")
    console.key("enter", pause=1.0)
    ok = console.wait_for("Resend attempts after a transient upstream error", 15)
    note(f"value prompt visible={ok}")
    console.text("5")
    console.key("enter", pause=1.5)
    ok = console.wait_for("Resend after upstream error  [5 attempts]", 15)
    note(f"limit applied={ok}")
    checkpoint(console, "set", "03-limit-set-to-5")
    console.key("esc", pause=0.5)
    console.key("esc", pause=0.5)
except Exception as exc:  # recorded for the journal; the orchestrator cleans up
    note(f"driver error {type(exc).__name__}: {exc}")
finally:
    log.close()
