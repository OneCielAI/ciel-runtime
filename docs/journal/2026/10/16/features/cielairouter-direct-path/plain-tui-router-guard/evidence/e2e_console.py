"""Console watcher for e2e_plain_codex.py (detached, attached to the CLI's console). Never presses a key.

Every 0.25 s the visible screen goes to <run>/screen-latest-<tag>.txt; the first screen that shows each watched
phrase is kept as screen-<tag>-NN-<label>.txt. Stops when <run>/stop-<tag> exists or the console is gone.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN, TAG = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
WATCH = {
    "direct-fallback": "direct gateway unavailable",
    "direct-active": "model requests go directly to gateway",
    "trust-codex": "Trust this folder?",
    "wake-1": "[ciel-wake] pending_ids=",
    "restarting": "Ciel Runtime: restarting",
    "stub-reply": "STUB-REPLY-",
}
console = Console(PID)
seen: set[str] = set()
index = 0
misses = 0
while not (RUN / f"stop-{TAG}").exists() and misses < 40:
    try:
        screen = console.screen()
        misses = 0
    except OSError:
        misses += 1
        time.sleep(0.25)
        continue
    (RUN / f"screen-latest-{TAG}.txt").write_text(screen, encoding="utf-8")
    for label, phrase in WATCH.items():
        if phrase in screen and label not in seen:
            seen.add(label)
            index += 1
            (RUN / f"screen-{TAG}-{index:02d}-{label}.txt").write_text(f"{time.strftime('%H:%M:%S')}\n{screen}", encoding="utf-8")
    time.sleep(0.25)
