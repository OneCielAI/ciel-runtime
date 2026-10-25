"""Detached console agent for e2e_semantic_retry.py: reads screens, types ONE prompt.

python -I e2e_console_agent.py <launcher-pid> <run-dir> <prompt-file>

Attaches to the isolated E2E launcher's console (it must be started with
DETACHED_PROCESS so it owns no console). Every distinct screen is written as
screens/screen-NNN.txt with its wall time; the newest one also goes to
screen-latest.txt. When Claude Code's composer is ready (separator line followed
by a "❯" line, a footer hint, no dialog, screen unchanged for 2 s) the agent types
the prompt text from <prompt-file> once, confirms it is drawn, presses Enter once
and records typed.json. It never types anything else (no continue, no dialog
answers - the runtime's own startup auto-answer handles dialogs). Stops when
<run-dir>/stop exists or the console is gone.
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


def note(event: str, **extra) -> None:
    with open(RUN / "agent.log", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"t": time.time(), "at": time.strftime("%H:%M:%S"), "event": event, **extra}) + "\n")


def composer_ready(screen: str) -> bool:
    if any(marker in screen for marker in DIALOG_MARKERS):
        return False
    if not any(marker in screen for marker in FOOTER_MARKERS):
        return False
    lines = screen.splitlines()
    for above, line in zip(lines, lines[1:]):
        if above.strip().startswith("───") and line.lstrip().startswith("❯"):
            return True
    return False


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
        note("composer_ready", screen_index=index)
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
                                                    "keys": ["text", "enter"]}), encoding="utf-8")
        note("typed", drawn_before_enter=drawn)
    time.sleep(0.2)
note("exit", typed=typed, screens=index, misses=misses)
