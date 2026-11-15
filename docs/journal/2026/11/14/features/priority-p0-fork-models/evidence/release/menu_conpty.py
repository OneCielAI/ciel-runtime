"""Real ConPTY capture of the initial settings menu: Work · SSE settings rows
for the P0-P5 and SSE side fork models.

The launcher runs from the given tree in a pseudo console with every inherited
CIEL_RUNTIME_* removed, a temporary config (current provider + models), the
isolated test marker and update checks off. End jumps to Quit, Up selects
"21. Work · SSE settings", Enter opens it, and the screen is captured while
scrolling to the model rows. The menu is closed with q; no CLI starts.
usage: python menu_conpty.py <tree> <out dir>
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

TREE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(TREE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

config_dir = Path(tempfile.mkdtemp(prefix="ciel-menu-models-"))
(config_dir / "config.json").write_text(json.dumps({
    "language": "ko", "work_mode": True, "current_provider": "cielairouter",
    "providers": {"cielairouter": {"current_model": "_Walkie_Opus55", "api_key": "",
                                   "custom_models": ["_Walkie_Fable5.2", "_Walkie_Haiku_Terra"]}},
    "work_parallel_models": {"cielairouter": {"P0": "_Walkie_Fable5.2", "side": "_Walkie_Haiku_Terra"}},
}), encoding="utf-8")
env = {k: v for k, v in os.environ.items() if not k.startswith("CIEL_RUNTIME_")}
env.update(CIEL_RUNTIME_CONFIG_DIR=str(config_dir), CIEL_RUNTIME_TEST_ISOLATED="1",
           CIEL_RUNTIME_UPDATE_CHECK="0", CIEL_RUNTIME_SELF_UPDATE_CHECK="0", CIEL_RUNTIME_LANGUAGE="ko")
# A stand-in `claude` on PATH so a launch, if one ever happened, prints its argv and exits.
fakebin = config_dir / "fakebin"
fakebin.mkdir()
(fakebin / "claude.cmd").write_text("@echo FAKE_CLAUDE %*" + chr(13) + chr(10), encoding="utf-8")
env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
session = WindowsConPtySession([sys.executable, str(TREE / "ciel_runtime.py"), "cli", "--show_config"], env,
                               log=lambda *_: None, mirror_output=False, forward_stdin=False)


def clean(raw):
    text = raw.decode("utf-8", "replace")
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]", "", text)


def settle(seconds=1.2):
    last, since = -1, time.monotonic()
    while time.monotonic() - since < seconds:
        size = len(session.output_tail())
        if size != last:
            last, since = size, time.monotonic()
        time.sleep(0.05)


try:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and b"Ciel Runtime v" not in session.output_tail():
        if session.poll() is not None:
            break
        time.sleep(0.1)
    settle()
    # The main menu opens on "9. Launch" (prelaunch_action_index) and wraps;
    # Home/End act only inside panels. Down x12 selects "21. Work · SSE settings".
    for _ in range(12):
        session.write(b"\x1b[B")
        time.sleep(0.05)
    settle(0.8)
    session.write(b"\r")
    settle()
    for _ in range(40):           # move down through the panel to the model rows
        session.write(b"\x1b[B")
        time.sleep(0.04)
    settle()
    screen = clean(session.output_tail())
    # The panel frames that last drew the P0 row and the side row (the terminal redraws every frame).
    frames = []
    for row in ("P0 Work 포크 모델", "SSE 사이드 포크 모델"):
        at = screen.rfind(row)
        if at < 0:
            continue
        begin = screen.rfind("Work · SSE 설정", 0, at)
        frames.append(screen[begin:screen.find("q 종료", at) + len("q 종료")])
    kept = [line.rstrip() for line in (chr(10) + "=====" + chr(10)).join(frames).replace(chr(13), "").split(chr(10))
            if line.strip()]
    (OUT / "menu-work-settings-screen.txt").write_text(chr(10).join(kept) + chr(10), encoding="utf-8")
    found = {key: (key in screen) for key in ("P0 Work 포크 모델", "P5 Work 포크 모델", "SSE 사이드 포크 모델",
                                              "_Walkie_Fable5.2", "_Walkie_Haiku_Terra")}
    session.write(b"q")
    time.sleep(1.0)
    print(json.dumps({"found": found, "passed": all(found.values())}, ensure_ascii=False))
finally:
    session.close()
