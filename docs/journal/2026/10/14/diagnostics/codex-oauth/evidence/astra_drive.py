"""Drive Codex (inside Ciel) in another console: turn, /model gpt-6-astra, turn, /model gpt-6.1-sol high, turn.

usage: python astra_drive.py PID TITLE RUN_DIR
Runs detached. Writes RUN_DIR/screen-NN-<label>.txt and .png at each step and drive.log.
"""
import json
import urllib.request
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
from console_driver import Console  # noqa: E402

pid, title, run = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
router = int(sys.argv[4]) if len(sys.argv) > 4 else 0
log = (run / "drive.log").open("a", encoding="utf-8")
count = [0]
MARKERS = ("›", "❯", ">")


def note(text: str) -> None:
    log.write(time.strftime("%H:%M:%S ") + text + "\n")
    log.flush()


def keep(label: str) -> str:
    count[0] += 1
    screen = console.screen()
    (run / f"screen-{count[0]:02d}-{label}.txt").write_text(time.strftime("%H:%M:%S\n") + screen, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(here / "shot_title.ps1"), title,
                    str(run / f"screen-{count[0]:02d}-{label}.png")], capture_output=True, creationflags=0x08000000)
    note(f"kept {label}")
    return screen


def requests() -> list:
    try:
        return json.loads((run / "stub-requests.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def wait_requests(n: int, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if len([r for r in requests() if r.get("method") == "POST"]) >= n:
            return True
        time.sleep(0.5)
    return False


def selected(text: str) -> bool:
    for line in console.screen().splitlines():
        s = line.strip()
        if s[:1] in MARKERS and text.lower() in s.lower():
            return True
    return False


def select(text: str, limit: int = 25) -> bool:
    for _ in range(limit):
        if selected(text):
            return True
        console.key("down", pause=0.3)
    return False


def ready(secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        screen = console.screen()
        if "? for shortcuts" in screen or "Ask Codex" in screen or "context left" in screen:
            return True
        time.sleep(0.5)
    return False


def turn(text: str, label: str, posts_before: int) -> None:
    console.text(text)
    time.sleep(0.5)
    console.key("enter")
    got = wait_requests(posts_before + 1, 60)
    time.sleep(6)
    note(f"turn {label}: request seen={got}")
    keep(label)


def switch(slug: str, effort: str, label: str) -> None:
    console.text("/model")
    time.sleep(1.0)
    console.key("enter")
    end = time.time() + 20
    while time.time() < end and slug.lower() not in console.screen().lower():
        time.sleep(0.3)
    if slug.lower() not in console.screen().lower():
        keep(f"{label}-no-picker")
        raise SystemExit("model picker did not show " + slug)
    time.sleep(1.0)
    ok = select(slug)
    keep(f"{label}-picker")
    note(f"select {slug}: {ok}")
    console.key("enter")
    time.sleep(1.5)
    screen = console.screen()
    if "Reasoning" in screen or "reasoning" in screen:
        if effort:
            note(f"select effort {effort}: {select(effort)}")
        keep(f"{label}-effort")
        console.key("enter")
        time.sleep(1.5)
    keep(f"{label}-done")


console = Console(pid)
try:
    if not ready(150):
        keep("not-ready")
        raise SystemExit("Codex prompt did not appear")
    time.sleep(3)
    keep("start")
    posts = lambda: len([r for r in requests() if r.get("method") == "POST"])
    turn("E2E turn one on the default model", "turn1-default", posts())
    switch("gpt-6-astra", "", "to-astra")
    turn("E2E turn two on astra", "turn2-astra", posts())
    switch("gpt-6.1-sol", "high", "to-sol")
    if router:
        # A channel message (Walkie-style) delivered by Ciel after the switch.
        before = posts()
        req = urllib.request.Request(f"http://127.0.0.1:{router}/ca/chat/notify", method="POST",
                                     data=json.dumps({"message": "E2E channel message after switching to sol", "channel": "e2e",
                                                      "sender_id": "e2e-driver"}).encode(), headers={"content-type": "application/json"})
        try:
            note("notify -> " + urllib.request.urlopen(req, timeout=15).read().decode()[:200])
        except Exception as exc:
            note(f"notify failed {type(exc).__name__}: {exc}")
        note(f"channel turn request seen={wait_requests(before + 1, 90)}")
        time.sleep(6)
        keep("channel-after-switch")
    turn("E2E turn three after switching to sol", "turn3-after-switch", posts())
    turn("E2E turn four", "turn4", posts())
finally:
    note("driver done")
