"""Detached console reader for probe_published_record.py. Never presses a key.

Keeps every distinct screen of the probed Claude console as screen-NN.txt with
its time, until <out>/stop exists or the console is gone.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "16/features/cielairouter-direct-path/evidence/e2e"))
from console_driver import Console  # noqa: E402

PID, OUT = int(sys.argv[1]), Path(sys.argv[2])
time.sleep(0.5)
console = None
for _ in range(40):
    try:
        console = Console(PID)
        break
    except OSError:
        time.sleep(0.25)
last, index, misses = "", 0, 0
while console is not None and not (OUT / "stop").exists() and misses < 40:
    try:
        screen = console.screen()
        misses = 0
    except OSError:
        misses += 1
        time.sleep(0.25)
        continue
    if screen.strip() and screen != last:
        last = screen
        index += 1
        (OUT / f"screen-{index:02d}.txt").write_text(f"{time.strftime('%H:%M:%S')}\n{screen}", encoding="utf-8")
    time.sleep(0.2)
