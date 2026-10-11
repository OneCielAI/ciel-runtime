from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, r"C:\Users\djlov\ciel-runtime\docs\journal\2026\10\10\features\session-restart\evidence")
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
console = Console(PID)
trusted = False
while not (RUN / "stop").exists():
    try:
        screen = console.screen()
    except OSError:
        time.sleep(0.25); continue
    (RUN / "screen-latest.txt").write_text(screen, encoding="utf-8")
    low = screen.lower()
    if not trusted and "trust" in low and "folder" in low:
        trusted = True
        console.key("enter")
    time.sleep(0.5)
