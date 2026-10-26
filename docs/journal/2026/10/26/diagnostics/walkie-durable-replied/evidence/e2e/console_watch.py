"""Detached console watcher for the owned native restart harness.

Copied 2026-10-26 from docs/journal/2026/10/20/diagnostics/restart-delivery/evidence/console_watch.py.
Changes for the Walkie id-reuse E2E: the console starts minimized without activation, so before a
screenshot the window is shown without activation (SW_SHOWNOACTIVATE) and minimized again afterwards
(SW_SHOWMINNOACTIVE); the console window handle is written to <run>/hwnd.txt so the harness can check
that the owned window never became the foreground window.

argv: <console owner pid> <run dir> <console_driver dir> <mode: claude|codex-remote>

It attaches to the console of the process the harness created (CREATE_NEW_CONSOLE), mirrors the screen to
screen-latest.txt, records every screen change to screens.jsonl, answers first-run dialogs by moving the
selection onto the wanted option (the default option is frequently "No, exit"), and types <run>/input.txt
as real key events.  A dialog handler re-arms when its phrase leaves the screen, so a relaunched native
process after a restart is handled too.  Every action is appended to watcher-actions.txt.
"""
import json
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, sys.argv[3])
from console_driver import Console  # noqa: E402

pid, run, mode = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[4]
actions = run / "watcher-actions.txt"


def note(message):
    with actions.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {message}\n")


def select(console, text, label):
    """Move the selection onto the option line containing `label`, then press Enter."""
    lines = text.splitlines()
    target = next((i for i, row in enumerate(lines) if label in row), None)
    marks = [i for i, row in enumerate(lines) if re.match(r"\s*[❯›>]", row)]
    cursor = min(marks, key=lambda i: abs(i - target), default=None) if target is not None else None
    if cursor is None or target is None:
        note(f"select({label!r}): cursor={cursor} target={target}; pressing enter only")
    else:
        delta = target - cursor
        note(f"select({label!r}): cursor line {cursor} -> target line {target}")
        console.key("down" if delta > 0 else "up", abs(delta))
    console.key("enter")


# name -> (screen predicate, label to select or None for a plain Enter)
if mode == "claude":
    DIALOGS = {
        "security-notes": (lambda t: "Press Enter to continue" in t, None),
        "theme": (lambda t: "Choose the text style" in t, None),
        "trust": (lambda t: "Yes, I trust this folder" in t, "Yes, I trust"),
        "bypass": (lambda t: "Yes, I accept" in t and "Bypass Permissions" in t, "Yes, I accept"),
        "api-key": (lambda t: "use this API key" in t, "Yes"),
    }
else:
    DIALOGS = {
        "trust": (lambda t: "Do you trust the contents of this directory" in t, "Yes, continue"),
    }

for attempt in range(100):
    try:
        console = Console(pid)
        break
    except Exception:
        (run / "attach-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        time.sleep(0.3)
else:
    raise RuntimeError("console attach failed")
(run / "hwnd.txt").write_text(str(int(console.hwnd or 0)), encoding="utf-8")

armed = {name: True for name in DIALOGS}
last_text = None
history = run / "screens.jsonl"
rows = 0
while not (run / "stop").exists():
    try:
        if (run / "shot-request").exists():
            # PrintWindow targets only this owned console window; never capture the desktop.
            import ctypes
            from ctypes import wintypes
            from PIL import Image
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            for fn in (user32.GetDC, gdi32.CreateCompatibleDC, gdi32.CreateCompatibleBitmap, gdi32.SelectObject):
                fn.restype = ctypes.c_void_p
            user32.GetDC.argtypes = [ctypes.c_void_p]
            gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
            gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.GetWindowRect.restype = wintypes.BOOL
            user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
            gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
            class RECT(ctypes.Structure):
                _fields_ = [(x, ctypes.c_long) for x in ("left", "top", "right", "bottom")]
            class BI(ctypes.Structure):
                _fields_ = [("size", ctypes.c_uint), ("width", ctypes.c_int), ("height", ctypes.c_int),
                            ("planes", ctypes.c_ushort), ("bits", ctypes.c_ushort), ("compression", ctypes.c_uint),
                            ("bytes", ctypes.c_uint), ("xpels", ctypes.c_int), ("ypels", ctypes.c_int),
                            ("used", ctypes.c_uint), ("important", ctypes.c_uint)]
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.ShowWindow(console.hwnd, 4)  # SW_SHOWNOACTIVATE
            time.sleep(1.2)
            rect = RECT()
            user32.GetWindowRect(console.hwnd, ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width > 0 and height > 0:
                dc = user32.GetDC(console.hwnd)
                mem = gdi32.CreateCompatibleDC(dc)
                bitmap = gdi32.CreateCompatibleBitmap(dc, width, height)
                old = gdi32.SelectObject(mem, bitmap)
                ok = user32.PrintWindow(console.hwnd, mem, 2)
                info = BI(ctypes.sizeof(BI), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
                buf = ctypes.create_string_buffer(width * height * 4)
                gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
                got = gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), 0)
                image = Image.frombuffer("RGB", (width, height), buf, "raw", "BGRX", 0, 1)
                label = (run / "shot-request").read_text(encoding="utf-8").strip() or "owned-console"
                image.save(run / (re.sub(r"[^A-Za-z0-9._-]+", "-", label) + ".png"))
                note(f"PrintWindow screenshot {width}x{height} ok={ok} rows={got} extrema={image.getextrema()}")
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(console.hwnd, dc)
            else:
                note(f"PrintWindow unavailable rect={width}x{height}")
            user32.ShowWindow(console.hwnd, 7)  # SW_SHOWMINNOACTIVE
            (run / "shot-request").unlink()
        text = console.screen()
        if text != last_text:
            last_text = text
            (run / "screen-latest.txt").write_text(text, encoding="utf-8")
            if rows < 600:
                rows += 1
                with history.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": time.time(), "screen": text}, ensure_ascii=False) + "\n")
        for name, (present, label) in DIALOGS.items():
            if present(text):
                if armed[name]:
                    armed[name] = False
                    (run / f"dialog-{name}-{int(time.time())}.txt").write_text(text, encoding="utf-8")
                    note(f"dialog {name}")
                    if label:
                        select(console, text, label)
                    else:
                        console.key("enter")
            else:
                armed[name] = True
        if (run / "input.txt").exists():
            # Probe fresh handles: initial input reached the old child, but writes through
            # long-lived handles after restart did not reach the new child (cause unconfirmed).
            console = Console(pid)
            value = (run / "input.txt").read_text(encoding="utf-8")
            (run / "input.txt").unlink()
            note(f"typed {len(value)} chars: {value[:60]!r}")
            console.text(value)
            console.key("enter")
    except Exception:
        (run / "console-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    time.sleep(0.3)
note("stop requested")
