"""Windows Console mode adapter and mouse-input lifecycle guard."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WindowsConsoleModePorts:
    input_handle: Callable[[], Any]
    parse_bool: Callable[..., bool]
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class WindowsConsoleModeService:
    ports: WindowsConsoleModePorts

    def input_supported(self) -> bool:
        return self.current() is not None

    def mouse_filter_enabled(self) -> bool:
        return self.ports.parse_bool(
            self.ports.environment.get(
                "CIEL_RUNTIME_WINDOWS_CONSOLE_MOUSE_FILTER"
            ),
            True,
        )

    def current(self) -> int | None:
        handle = self.ports.input_handle()
        if handle is None:
            return None
        try:
            import ctypes
            from ctypes import wintypes

            mode = wintypes.DWORD(0)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetConsoleMode.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetConsoleMode.restype = wintypes.BOOL
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return None
            return int(mode.value)
        except Exception:
            return None

    def set(self, mode: int) -> bool:
        handle = self.ports.input_handle()
        if handle is None:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetConsoleMode.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
            ]
            kernel32.SetConsoleMode.restype = wintypes.BOOL
            return bool(
                kernel32.SetConsoleMode(handle, wintypes.DWORD(int(mode)))
            )
        except Exception:
            return False


class WindowsConsoleMouseInputGuard:
    ENABLE_MOUSE_INPUT = 0x0010

    def __init__(
        self,
        *,
        platform_name: str,
        filter_enabled: Callable[[], bool],
        current_mode: Callable[[], int | None],
        set_mode: Callable[[int], bool],
        log: Callable[[str, str], None],
    ) -> None:
        self._platform_name = platform_name
        self._filter_enabled = filter_enabled
        self._current_mode = current_mode
        self._set_mode = set_mode
        self._log = log
        self.original_mode: int | None = None

    def apply(self) -> None:
        if self._platform_name != "nt" or not self._filter_enabled():
            return
        current = self._current_mode()
        if current is None:
            return
        if self.original_mode is None:
            self.original_mode = current
        filtered = current & ~self.ENABLE_MOUSE_INPUT
        if filtered != current and self._set_mode(filtered):
            self._log(
                "INFO",
                "windows_console_mouse_input_disabled "
                f"mode={current:#x}->{filtered:#x}",
            )

    def restore(self) -> None:
        if self._platform_name != "nt" or self.original_mode is None:
            return
        self._set_mode(self.original_mode)
