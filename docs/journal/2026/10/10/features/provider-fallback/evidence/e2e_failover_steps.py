"""Console steps for e2e_failover.py (run detached, attached to one console).

Usage: python e2e_failover_steps.py <menu|claude|codex> <pid> <run dir> <router port> <stub A port> <stub B port>
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

MODE, PID, RUN = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
ROUTER, PORT_A, PORT_B = int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
HERE = Path(__file__).resolve().parent
SHOTS = RUN / "shots"
LOG = open(RUN / f"steps-{MODE}.log", "w", encoding="utf-8")
console = Console(PID)


def log(message: str) -> None:
    LOG.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    LOG.flush()


def shot(label: str) -> None:
    index = len(list(SHOTS.glob("*.png"))) + 1
    (SHOTS / f"{index:02d}-{MODE}-{label}.txt").write_text(console.screen(), encoding="utf-8")
    out = str(SHOTS / f"{index:02d}-{MODE}-{label}.png")
    if MODE == "menu":
        command = [str(HERE / "shot_hwnd.ps1"), str(int(console.hwnd or 0)), out]
    else:  # Windows Terminal window, found by its title
        command = [str(HERE / "shot_title.ps1"), f"CIELE2E-FAILOVER-{MODE}", out]
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *command],
                       capture_output=True, text=True, creationflags=0x08000000)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def http(port: int, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}"}


def count(port: int) -> int:
    return int(http(port, "GET", "/__state").get("count") or 0)


def expect(text: str, timeout: float = 90) -> bool:
    ok = console.wait_for(text, timeout)
    log(f"expect {text!r}: {ok}")
    return ok


def selected(label: str) -> bool:
    return any(line.lstrip().startswith(">") and label in line for line in console.screen().splitlines())


def select(label: str, direction: str = "down", limit: int = 30) -> bool:
    for _ in range(limit):
        if selected(label):
            return True
        console.key(direction)
    return selected(label)


def answer(prompt: str, value: str = "") -> None:
    seen = console.wait_for(prompt, 30)
    if value:
        console.text(value)
    console.key("enter")
    log(f"prompt {prompt!r} seen={seen} answered={value or '<default>'}")


def turn(word: str, _expect_text: str = "") -> dict:
    """Send one turn; report which stub answered the request carrying this turn's word."""

    result = http(ROUTER, "POST", "/ca/chat/notify", {"message": f"{word}: reply with one word.", "channel": "e2e", "sender_id": "e2e-driver"})
    log(f"notify {word}: {json.dumps(result)[:120]}")
    end = time.time() + 150
    served = ""
    while time.time() < end and not served:
        for name, port in (("A", PORT_A), ("B", PORT_B)):
            requests = http(port, "GET", "/__state").get("requests") or []
            if any(word in item.get("words", []) and not item.get("limited") for item in requests):
                served = name
        time.sleep(1)
    limited_at_a = sum(1 for item in http(PORT_A, "GET", "/__state").get("requests") or []
                       if word in item.get("words", []) and item.get("limited"))
    time.sleep(4)
    log(f"{word}: served_by={served or 'nobody'} limited_hits_at_A={limited_at_a}")
    return {"served_by": served, "limited_hits_at_A": limited_at_a}


def slash(command: str, expect_text: str) -> bool:
    console.text(command)
    time.sleep(0.8)
    console.key("enter")
    ok = expect(expect_text, 60)
    time.sleep(1)
    return ok


log(f"attached {PID} hwnd {console.hwnd}")
if MODE == "menu":
    expect("19. Provider fallback", 60)
    shot("menu")
    select("19. Provider fallback")
    console.key("enter")
    expect("Automatic fallback on 429")
    shot("panel-empty")
    select("Automatic fallback on 429", "up")
    console.key("enter")
    expect("Automatic fallback: on")
    select("Add a backup")
    console.key("enter")
    answer("Backup provider", "lm-studio")
    answer("Model on lm-studio", "stub-b-model")
    expect("Added backup 1: lm-studio")
    shot("backup-added")
    console.key("esc")
    time.sleep(1)
    shot("main-row-19")
    console.text("q")
elif MODE == "claude":
    expect("bypass", 120)
    time.sleep(8)
    shot("ready")
    http(PORT_A, "POST", "/__arm", {"retry_after": 45})
    first = turn("TURN-1", "STUB-B-REPLY")
    shot("turn1-served-by-backup")
    second = turn("TURN-2", "STUB-B-REPLY-2")
    shot("turn2-still-backup")
    slash("/fallback", "Provider fallback: automatic on")
    shot("fallback-status")
    remaining = max(0.0, float(http(PORT_A, "GET", "/__state").get("limited_until") or 0) - time.time())
    log(f"waiting {remaining + 5:.0f}s for the cooldown")
    time.sleep(remaining + 5)
    third = turn("TURN-3", "STUB-A-REPLY")
    shot("turn3-returned-to-A")
    slash("/fallback lm-studio 5", "serves vllm requests until")
    shot("manual-switch")
    fourth = turn("TURN-4", "STUB-B-REPLY-3")
    slash("/fallback return", "vllm serves requests again")
    shot("manual-return")
    fifth = turn("TURN-5", "STUB-A-REPLY")
    shot("turn5-on-A")
    log("RESULT " + json.dumps({"turn1": first, "turn2": second, "turn3": third, "turn4": fourth, "turn5": fifth}))
elif MODE == "codex":
    time.sleep(25)
    shot("ready")
    http(PORT_A, "POST", "/__arm", {"retry_after": 60})
    result = turn("CODEX-TURN", "STUB-B-REPLY")
    shot("codex-served-by-backup")
    log("RESULT " + json.dumps({"codex": result}))
log("done")
