"""Console watcher for e2e_restart.py (detached, attached to the CLI's console).

Every second: the visible screen goes to <run>/screen-latest.txt, and every new screen that shows a
watched phrase is kept as screen-NN-<label>.txt. Accepts Claude Code's folder trust prompt with Enter.
Stops when <run>/stop exists.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
WATCH = {
    "waiting": "진행 중인 turn이 끝나면 재시작합니다",
    "starting": "세션을 재시작합니다",
    "restarting": "Ciel Runtime: restarting",
    "notice": "재부팅 완료",
}
console = Console(PID)
seen: set[str] = set()
index = 0
while not (RUN / "stop").exists():
    try:
        screen = console.screen()
    except OSError:
        time.sleep(0.25)
        continue
    (RUN / "screen-latest.txt").write_text(screen, encoding="utf-8")
    for label, phrase in WATCH.items():
        if phrase in screen and label not in seen:
            seen.add(label)
            index += 1
            (RUN / f"screen-{index:02d}-{label}.txt").write_text(f"{time.strftime('%H:%M:%S')}\n{screen}", encoding="utf-8")
    if ("trust" in screen.lower() and "folder" in screen.lower()) and "trust" not in seen:
        seen.add("trust")
        console.key("enter")
    time.sleep(0.25)
