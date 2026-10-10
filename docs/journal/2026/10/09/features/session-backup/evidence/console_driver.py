"""Test-only driver for a console program: attach to its console, type real key events, read its screen.

WriteConsoleInputW puts key records straight into the target console's input queue (no window focus,
no IME, no clipboard). ReadConsoleOutputCharacterW reads what the program drew, so each step waits
for the prompt it answers. The calling process must have no console of its own (DETACHED_PROCESS).
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

VK = {"enter": 0x0D, "up": 0x26, "down": 0x28, "home": 0x24, "end": 0x23, "esc": 0x1B, "left": 0x25}


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class CSBI(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("UnicodeChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.GetConsoleWindow.restype = wintypes.HWND


class Console:
    def __init__(self, pid: int) -> None:
        kernel32.FreeConsole()
        if not kernel32.AttachConsole(pid):
            raise OSError(f"AttachConsole({pid}) failed: {ctypes.get_last_error()}")
        rw, share = 0x80000000 | 0x40000000, 0x1 | 0x2
        self.conin = kernel32.CreateFileW("CONIN$", rw, share, None, 3, 0, None)
        self.conout = kernel32.CreateFileW("CONOUT$", rw, share, None, 3, 0, None)
        self.hwnd = kernel32.GetConsoleWindow()

    def _write(self, records: list[INPUT_RECORD]) -> None:
        array = (INPUT_RECORD * len(records))(*records)
        written = wintypes.DWORD(0)
        if not kernel32.WriteConsoleInputW(self.conin, array, len(records), ctypes.byref(written)):
            raise OSError(f"WriteConsoleInputW failed: {ctypes.get_last_error()}")

    @staticmethod
    def _record(vk: int, char: str, down: bool) -> INPUT_RECORD:
        record = INPUT_RECORD()
        record.EventType = 1
        record.Event.KeyEvent.bKeyDown = down
        record.Event.KeyEvent.wRepeatCount = 1
        record.Event.KeyEvent.wVirtualKeyCode = vk
        record.Event.KeyEvent.wVirtualScanCode = user32.MapVirtualKeyW(vk, 0) if vk else 0
        record.Event.KeyEvent.UnicodeChar = char
        return record

    def key(self, name: str, times: int = 1, pause: float = 0.25) -> None:
        vk = VK[name]
        char = "\r" if name == "enter" else ("\x1b" if name == "esc" else "\0")
        for _ in range(times):
            self._write([self._record(vk, char, True), self._record(vk, char, False)])
            time.sleep(pause)

    def text(self, value: str) -> None:
        records = []
        for ch in value:
            vk = user32.VkKeyScanW(ch) & 0xFF if ord(ch) < 128 else 0
            records += [self._record(vk, ch, True), self._record(vk, ch, False)]
        self._write(records)
        time.sleep(0.3)

    def screen(self) -> str:
        info = CSBI()
        kernel32.GetConsoleScreenBufferInfo(self.conout, ctypes.byref(info))
        width = info.dwSize.X
        lines = []
        for row in range(info.srWindow.Top, info.srWindow.Bottom + 1):
            buffer = ctypes.create_unicode_buffer(width + 1)
            read = wintypes.DWORD(0)
            kernel32.ReadConsoleOutputCharacterW(self.conout, buffer, width, COORD(0, row), ctypes.byref(read))
            lines.append(buffer.value[: read.value].rstrip())
        return "\n".join(lines)

    def wait_for(self, needle: str, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if needle in self.screen():
                return True
            time.sleep(0.3)
        return False
