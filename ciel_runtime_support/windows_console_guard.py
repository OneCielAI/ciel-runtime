"""Out-of-process restoration of shared console modes after owner termination.

The helper shares the existing console; it never creates a window. It opens its
own console handles before acknowledging readiness and waits on an owner process
handle, so PID reuse cannot cause it to watch a different process.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading


def start_console_guard(input_mode: int, output_mode: int) -> subprocess.Popen:
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(os.getpid()),
         str(input_mode), str(output_mode)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    ready = threading.Event()
    response = []

    def read_ready() -> None:
        try:
            response.append(child.stdout.readline())
        finally:
            ready.set()

    threading.Thread(target=read_ready, daemon=True).start()
    if not ready.wait(5) or response != [b"READY\n"]:
        stop_console_guard(child)
        raise OSError("console recovery helper failed to become ready")
    child.stdout.close()
    return child


def stop_console_guard(child: subprocess.Popen) -> None:
    if child.poll() is None:
        child.terminate()
    child.wait(timeout=5)
    if child.stdout is not None:
        child.stdout.close()


def _watch(owner_pid: int, input_mode: int, output_mode: int) -> int:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WriteConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    owner = kernel.OpenProcess(0x00100000, False, owner_pid)
    handles = [owner]
    try:
        if not owner:
            return 2
        console_in = kernel.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
        console_out = kernel.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
        handles.extend([console_in, console_out])
        invalid = ctypes.c_void_p(-1).value
        if any(h in (None, invalid) for h in handles):
            return 3
        sys.stdout.buffer.write(b"READY\n")
        sys.stdout.buffer.flush()
        if kernel.WaitForSingleObject(owner, 0xFFFFFFFF) != 0:
            return 4
        # The owner is gone; its finally/atexit handlers cannot restore this.
        # Reset emulator modes while VT output is enabled, then restore exact
        # Win32 modes. Do not flush pending user keystrokes from the input queue.
        kernel.SetConsoleMode(console_out, output_mode | 5)
        reset = "".join(f"\x1b[?{mode}l" for mode in
                        (9, 1000, 1001, 1002, 1003, 1004, 1005, 1006, 1015, 1016, 2004, 9001))
        written = wintypes.DWORD()
        kernel.WriteConsoleW(console_out, reset, len(reset), ctypes.byref(written), None)
        kernel.SetConsoleMode(console_in, input_mode)
        kernel.SetConsoleMode(console_out, output_mode)
        return 0
    finally:
        for handle in handles:
            if handle and handle != ctypes.c_void_p(-1).value:
                kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(_watch(*(int(value) for value in sys.argv[1:])))
