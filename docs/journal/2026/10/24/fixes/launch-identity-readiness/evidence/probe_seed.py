"""Type one interactive seed turn into a probed Claude console (detached helper).

Waits for the prompt, types the text and Enter, then waits for the stub reply so
the transcript is an interactive one that ``--continue`` can select.
Usage: python probe_seed.py <pid> <text> <result-file>
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "16/features/cielairouter-direct-path/evidence/e2e"))
from console_driver import Console  # noqa: E402

PID, TEXT, RESULT = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
console = None
for _ in range(80):
    try:
        console = Console(PID)
        break
    except OSError:
        time.sleep(0.25)
steps = []
if console is not None:
    steps.append(("prompt", console.wait_for("? for shortcuts", 60) or console.wait_for("shift+tab", 5)))
    console.text(TEXT)
    console.key("enter")
    steps.append(("reply", console.wait_for("● ok", 60)))
    time.sleep(1.0)
RESULT.write_text(repr(steps), encoding="utf-8")
