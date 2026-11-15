"""Real ConPTY check: `ciel-runtime --continue --show_config` opens the settings menu.

The launcher runs from the given tree in a pseudo console (a real TTY), with
every inherited CIEL_RUNTIME_* removed, a temporary config dir, the isolated
test marker and update checks off. The menu is cancelled with Esc, so no
runtime CLI starts. usage: python show_config_conpty.py <tree> <out dir> [flags...]
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

TREE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
FLAGS = sys.argv[3:] or ["--continue", "--show_config"]
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(TREE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

env = {k: v for k, v in os.environ.items() if not k.startswith("CIEL_RUNTIME_")}
env.update(CIEL_RUNTIME_CONFIG_DIR=tempfile.mkdtemp(prefix="ciel-showcfg-"), CIEL_RUNTIME_TEST_ISOLATED="1",
           CIEL_RUNTIME_UPDATE_CHECK="0", CIEL_RUNTIME_SELF_UPDATE_CHECK="0", CIEL_RUNTIME_LANGUAGE="ko")
fakebin = Path(__file__).resolve().parent / "fakebin"
env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
session = WindowsConPtySession([sys.executable, str(TREE / "ciel_runtime.py"), "cli", *FLAGS], env,
                               log=lambda *_: None, mirror_output=False, forward_stdin=False)


def stable(timeout):
    deadline, last, since = time.monotonic() + timeout, -1, time.monotonic()
    while time.monotonic() < deadline:
        tail = session.output_tail()
        if len(tail) != last:
            last, since = len(tail), time.monotonic()
        elif last > 0 and time.monotonic() - since >= 1.5:
            break
        if session.poll() is not None:
            break
        time.sleep(0.05)
    return session.output_tail()


try:
    deadline = time.monotonic() + 30
    raw = b""
    while time.monotonic() < deadline:
        raw = session.output_tail()
        if b"Ciel Runtime v" in raw or session.poll() is not None:
            break
        time.sleep(0.1)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]", "", raw.decode("utf-8", "replace"))
    (OUT / "screen.txt").write_text(text, encoding="utf-8")
    exited_before_input = session.poll()
    if exited_before_input is None:
        session.write(b"\x1b")
        time.sleep(1.5)
        if session.poll() is None:
            session.write(b"q")
            time.sleep(1.5)
    rc = session.poll()
    print("FLAGS", " ".join(FLAGS))
    print("EXITED_BEFORE_INPUT", exited_before_input)
    print("RC_AFTER_ESC", rc)
    print("SCREEN_TAIL", text.strip()[-1200:].encode("unicode_escape").decode())
finally:
    session.close()
