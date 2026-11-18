"""Console watcher for e2e_fork_fill.py (copied from the safe-restart E2E; only WATCH differs) (detached, attached to the launcher's console).

Every 0.25 s the visible screen goes to <run>/screen-latest.txt. A screen showing a
watched phrase is kept once as screen-NN-<label>.txt. The folder-trust dialog is
answered (Down, Enter = "Yes, I trust this folder") only while <run>/answer-trust
exists; every time the dialog is seen it is recorded in <run>/trust-seen.jsonl. A
line written to <run>/type.txt is typed into the console followed by Enter.
Stops when <run>/stop exists.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

PID, RUN = int(sys.argv[1]), Path(sys.argv[2])
WATCH = {
    "dispatch": "DISPATCH:",
    "dispatch-check": "Ciel Work dispatch check",
    "resumed": "resumed parked worker",
}
console = Console(PID)
seen: set[str] = set()
index = 0
trust_active = False
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
    compact = screen.replace(" ", "").lower()
    dialog = "accessingworkspace" in compact and "itrustthisfolder" in compact
    if dialog and not trust_active:
        trust_active = True
        index += 1
        (RUN / f"screen-{index:02d}-trust-dialog.txt").write_text(f"{time.strftime('%H:%M:%S')}\n{screen}", encoding="utf-8")
        answer = (RUN / "answer-trust").exists()
        with (RUN / "trust-seen.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": time.time(), "answered": answer}) + "\n")
        if answer:
            time.sleep(1.0)
            console.key("down")
            console.key("enter")
            (RUN / "answer-trust").unlink(missing_ok=True)
    elif not dialog:
        trust_active = False
    typed = RUN / "type.txt"
    if typed.exists():
        text = typed.read_text(encoding="utf-8").strip()
        typed.unlink(missing_ok=True)
        if text:
            console.text(text)
            time.sleep(0.5)
            console.key("enter")
            with (RUN / "typed.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": time.time(), "text": text}) + "\n")
    time.sleep(0.25)
