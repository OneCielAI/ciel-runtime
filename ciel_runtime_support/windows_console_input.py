"""Windows console input queue adapter."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable


def _windows_console_utf16_units(chars: Iterable[str]) -> list[str]:
    """Expand non-BMP code points into WCHAR-sized UTF-16 surrogate units."""
    units: list[str] = []
    for char in chars:
        codepoint = ord(char)
        if codepoint > 0xFFFF:
            codepoint -= 0x10000
            units.extend((chr(0xD800 + (codepoint >> 10)), chr(0xDC00 + (codepoint & 0x3FF))))
        else:
            units.append(char)
    return units


class WindowsConsoleInputWriter:
    """Inject keystrokes into the active Windows console input queue."""

    def __init__(self, input_handle: Callable[[], Any], mouse_filter_factory: Callable[[], Any]) -> None:
        self.handle = input_handle()
        if self.handle is None:
            raise RuntimeError("Windows console input handle is not available")
        self._mouse_filter = mouse_filter_factory()
        self._queue_baseline: int | None = None

    def wait_until_input_consumed(self, timeout_seconds: float = 2.0) -> bool:
        """Wait until the most recently written console-input batch leaves the queue."""
        if self._queue_baseline is None:
            return True
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetNumberOfConsoleInputEvents.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            pending = wintypes.DWORD(0)
            if not kernel32.GetNumberOfConsoleInputEvents(self.handle, ctypes.byref(pending)):
                return False
            if int(pending.value) <= self._queue_baseline:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def write(self, data: bytes) -> None:
        if not data:
            return
        data = self._mouse_filter.feed(data)
        if not data:
            return
        text = data.decode("utf-8", errors="replace")
        chars: list[str] = []
        index = 0
        while index < len(text):
            char = text[index]
            if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                chars.append("\r")
                index += 2
                continue
            chars.append("\r" if char == "\n" else char)
            index += 1
        self._write_chars(chars)

    def _write_chars(self, chars: list[str]) -> None:
        import ctypes
        from ctypes import wintypes

        if not chars:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", wintypes.BOOL),
                ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD),
                ("uChar", wintypes.WCHAR),
                ("dwControlKeyState", wintypes.DWORD),
            ]

        class EVENT_UNION(ctypes.Union):
            _fields_ = [("KeyEvent", KEY_EVENT_RECORD)]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT_UNION)]

        user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        user32.MapVirtualKeyW.restype = wintypes.UINT
        kernel32.WriteConsoleInputW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(INPUT_RECORD),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.WriteConsoleInputW.restype = wintypes.BOOL
        kernel32.GetNumberOfConsoleInputEvents.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
        key_event = 0x0001
        left_ctrl_pressed = 0x0008
        map_vk_to_vsc = 0
        vk_map = {"\r": 0x0D, "\x1b": 0x1B, "\x08": 0x08, "\t": 0x09, "\x15": 0x55}
        records: list[Any] = []
        for char in _windows_console_utf16_units(chars):
            virtual_key = vk_map.get(char)
            if virtual_key is None and len(char) == 1 and "A" <= char.upper() <= "Z":
                virtual_key = ord(char.upper())
            elif virtual_key is None:
                virtual_key = 0
            control = left_ctrl_pressed if char == "\x15" else 0
            scan_code = int(user32.MapVirtualKeyW(int(virtual_key), map_vk_to_vsc)) if virtual_key else 0
            for key_down in (True, False):
                record = INPUT_RECORD()
                record.EventType = key_event
                record.Event.KeyEvent = KEY_EVENT_RECORD(
                    bool(key_down), 1, int(virtual_key), scan_code, char, control
                )
                records.append(record)
        array_type = INPUT_RECORD * len(records)
        written = wintypes.DWORD(0)
        pending_before = wintypes.DWORD(0)
        if kernel32.GetNumberOfConsoleInputEvents(self.handle, ctypes.byref(pending_before)):
            self._queue_baseline = int(pending_before.value)
        else:
            self._queue_baseline = None
        ok = kernel32.WriteConsoleInputW(self.handle, array_type(*records), len(records), ctypes.byref(written))
        if not ok or int(written.value) != len(records):
            raise OSError(f"WriteConsoleInputW failed: written={int(written.value)} expected={len(records)}")
