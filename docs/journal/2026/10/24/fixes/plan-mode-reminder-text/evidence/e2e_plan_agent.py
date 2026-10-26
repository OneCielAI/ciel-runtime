"""Detached console agent for e2e_plan_exit.py: Shift+Tab into plan mode, then ONE prompt.

python -I e2e_plan_agent.py <launcher-pid> <run-dir> <prompt-file>

Attaches to the isolated launcher's console (started with DETACHED_PROCESS). Every
distinct screen goes to screens/screen-NNN.txt and screen-latest.txt. When Claude
Code's composer is ready, it presses Shift+Tab (one key at a time) until the footer
shows "plan mode on", which is how a user enters plan mode without any
EnterPlanMode call. It then types the prompt once and presses Enter once, and
records typed.json. It types nothing else. It stops when <run-dir>/stop exists
or the console is gone.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "16/features/cielairouter-direct-path/evidence/e2e"))
from console_driver import Console  # noqa: E402

PID, RUN, PROMPT_FILE = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
PROMPT = PROMPT_FILE.read_text(encoding="utf-8").strip()
SCREENS = RUN / "screens"
SCREENS.mkdir(parents=True, exist_ok=True)
DIALOG_MARKERS = ("Enter to confirm", "Accessing workspace", "Yes, I accept", "Do you want to")
FOOTER_MARKERS = ("? for shortcuts", "shift+tab")
SHIFT_PRESSED = 0x0010


def note(event: str, **extra) -> None:
    with open(RUN / "agent.log", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"t": time.time(), "at": time.strftime("%H:%M:%S"), "event": event, **extra}) + "\n")


def composer_ready(screen: str) -> bool:
    if any(marker in screen for marker in DIALOG_MARKERS):
        return False
    if not any(marker in screen for marker in FOOTER_MARKERS):
        return False
    lines = screen.splitlines()
    return any(above.strip().startswith("───") and line.lstrip().startswith("❯") for above, line in zip(lines, lines[1:]))


def shift_tab() -> None:
    down, up = Console._record(0x09, "\t", True), Console._record(0x09, "\t", False)
    down.Event.KeyEvent.dwControlKeyState = SHIFT_PRESSED
    up.Event.KeyEvent.dwControlKeyState = SHIFT_PRESSED
    console._write([down, up])


console = None
for _ in range(120):
    try:
        console = Console(PID)
        break
    except OSError as exc:
        note("attach_retry", error=str(exc))
        time.sleep(0.25)
note("attached" if console else "attach_failed", pid=PID)

last, index, misses = "", 0, 0
stable_since = time.time()
typed = False
presses = 0
while console is not None and not (RUN / "stop").exists() and misses < 40:
    try:
        screen = console.screen()
        misses = 0
    except OSError:
        misses += 1
        time.sleep(0.25)
        continue
    now = time.time()
    if screen != last:
        last = screen
        stable_since = now
        if screen.strip():
            index += 1
            stamped = f"{time.strftime('%H:%M:%S')}\n{screen}"
            (SCREENS / f"screen-{index:03d}.txt").write_text(stamped, encoding="utf-8")
            (RUN / "screen-latest.txt").write_text(stamped, encoding="utf-8")
    if not typed and composer_ready(screen) and now - stable_since >= 2.0:
        if "plan mode on" not in screen:
            if presses >= 8:
                note("plan_mode_not_reached", presses=presses)
                break
            shift_tab()
            presses += 1
            note("shift_tab", presses=presses)
            stable_since = time.time()
            time.sleep(0.8)
            continue
        note("composer_ready_in_plan_mode", screen_index=index, shift_tab_presses=presses)
        (RUN / "ready").write_text(str(now), encoding="utf-8")
        console.text(PROMPT)
        drawn = False
        for _ in range(20):
            if PROMPT[:24] in console.screen():
                drawn = True
                break
            time.sleep(0.25)
        console.key("enter")
        typed = True
        (RUN / "typed.json").write_text(json.dumps({"t": time.time(), "at": time.strftime("%H:%M:%S"),
                                                    "prompt": PROMPT, "drawn_before_enter": drawn,
                                                    "keys": ["shift+tab"] * presses + ["text", "enter"]}), encoding="utf-8")
        note("typed", drawn_before_enter=drawn)
    time.sleep(0.2)
note("exit", typed=typed, screens=index, misses=misses)
