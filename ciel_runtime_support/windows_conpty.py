"""Native Windows pseudo-console transport for interactive agent CLIs."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from typing import Any, Callable


_STILL_ACTIVE = 259
_WAIT_TIMEOUT = 258
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF


def conpty_enabled(
    environment: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> bool:
    """Return whether the native ConPTY transport should be attempted."""
    if (platform_name or os.name) != "nt":
        return False
    raw = str((environment or os.environ).get("CIEL_RUNTIME_WINDOWS_CONPTY", "1"))
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class WindowsConPtySession:
    """Own a Windows pseudo console and expose it as a byte input transport."""

    separate_input_stages = False
    supports_input_snapshot = False

    def __init__(
        self,
        cmd: list[str],
        env: Mapping[str, str],
        *,
        log: Callable[[str, str], None],
        stdin_fd: int | None = None,
        stdout_fd: int | None = None,
        mirror_output: bool = True,
        forward_stdin: bool = True,
    ) -> None:
        if os.name != "nt":
            raise OSError("ConPTY is available only on Windows")
        self._log = log
        self._stdin_fd = sys.stdin.fileno() if stdin_fd is None else int(stdin_fd)
        self._stdout_fd = sys.stdout.fileno() if stdout_fd is None else int(stdout_fd)
        self._mirror_output = bool(mirror_output)
        self._forward_stdin = bool(forward_stdin)
        self._write_lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._output_tail = bytearray()
        self._closed = False
        self._stop = threading.Event()
        self._old_input_mode: int | None = None
        self._old_output_mode: int | None = None
        self._old_input_cp: int | None = None
        self._old_output_cp: int | None = None
        self._last_size = (0, 0)
        self._coord_type: Any = None
        self._kernel32: Any = None
        self._hpc: Any = None
        self._process_handle: Any = None
        self._input_handle: Any = None
        self._output_handle: Any = None
        self._output_thread: threading.Thread | None = None
        self.pid = 0
        try:
            self._create(cmd, env)
            self._configure_parent_console()
            self._start_pumps()
        except Exception:
            self.close()
            raise

    @staticmethod
    def normalize_prompt(prompt: str) -> str:
        return " ".join(str(prompt or "").replace("\t", " ").splitlines()).strip()

    @staticmethod
    def input_snapshot() -> None:
        return None

    @staticmethod
    def pending_input_events() -> None:
        return None

    @staticmethod
    def wait_until_input_consumed(timeout_seconds: float = 2.0) -> bool:
        del timeout_seconds
        return True

    def write(self, data: bytes) -> None:
        if not data or not self._input_handle:
            return
        import ctypes
        from ctypes import wintypes

        with self._write_lock:
            view = memoryview(data)
            while view:
                chunk = bytes(view[: 64 * 1024])
                written = wintypes.DWORD(0)
                if not self._kernel32.WriteFile(
                    self._input_handle,
                    chunk,
                    len(chunk),
                    ctypes.byref(written),
                    None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                count = int(written.value)
                if count <= 0:
                    raise BrokenPipeError("ConPTY input pipe closed")
                view = view[count:]

    def output_tail(self) -> bytes:
        with self._output_lock:
            return bytes(self._output_tail)

    def poll(self) -> int | None:
        if not self._process_handle or not self._kernel32:
            return 0
        import ctypes
        from ctypes import wintypes

        code = wintypes.DWORD(0)
        if not self._kernel32.GetExitCodeProcess(
            self._process_handle, ctypes.byref(code)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return None if int(code.value) == _STILL_ACTIVE else int(code.value)

    def wait(self, timeout: float | None = None) -> int:
        if not self._process_handle or not self._kernel32:
            return 0
        milliseconds = (
            _INFINITE if timeout is None else max(0, min(0xFFFFFFFE, int(timeout * 1000)))
        )
        result = int(self._kernel32.WaitForSingleObject(self._process_handle, milliseconds))
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("ConPTY child", timeout)
        if result != _WAIT_OBJECT_0:
            import ctypes

            raise ctypes.WinError(ctypes.get_last_error())
        return int(self.poll() or 0)

    def terminate(self) -> None:
        if self.poll() is None:
            self._kernel32.TerminateProcess(self._process_handle, 1)

    def kill(self) -> None:
        self.terminate()

    def resize_if_needed(self) -> None:
        if not self._hpc or not self._kernel32:
            return
        size = self._terminal_size()
        if size == self._last_size:
            return
        coord = self._coord_type(size[0], size[1])
        result = int(self._kernel32.ResizePseudoConsole(self._hpc, coord))
        if result == 0:
            self._last_size = size

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process_handle and self._kernel32:
            try:
                if self.poll() is None:
                    self._kernel32.TerminateProcess(self._process_handle, 1)
                    self._kernel32.WaitForSingleObject(self._process_handle, 2000)
            except OSError:
                pass
        self._restore_parent_console()
        if self._input_handle and self._kernel32:
            try:
                self._kernel32.CloseHandle(self._input_handle)
            except (AttributeError, OSError):
                pass
            self._input_handle = None
        # Keep the output reader alive while ClosePseudoConsole asks its host
        # to flush and exit. Closing the output pipe first can deadlock conhost.
        if self._hpc and self._kernel32:
            self._kernel32.ClosePseudoConsole(self._hpc)
            self._hpc = None
        if self._output_thread is not None:
            self._output_thread.join(timeout=1.0)
        self._stop.set()
        if self._output_handle and self._kernel32:
            try:
                self._kernel32.CloseHandle(self._output_handle)
            except (AttributeError, OSError):
                pass
            self._output_handle = None
        if self._process_handle and self._kernel32:
            self._kernel32.CloseHandle(self._process_handle)
            self._process_handle = None

    def _create(self, cmd: list[str], env: Mapping[str, str]) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not hasattr(kernel32, "CreatePseudoConsole"):
            raise OSError("CreatePseudoConsole is unavailable on this Windows version")
        self._kernel32 = kernel32

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        self._coord_type = COORD

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", wintypes.LPVOID)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            ]

        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.CreatePseudoConsole.argtypes = [
            COORD,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
        kernel32.ResizePseudoConsole.restype = ctypes.c_long
        kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.UpdateProcThreadAttribute.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_size_t,
            wintypes.LPVOID,
            ctypes.c_size_t,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL

        input_read = wintypes.HANDLE()
        input_write = wintypes.HANDLE()
        output_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        temporary_handles: list[Any] = []
        attribute_buffer: Any = None
        try:
            if not kernel32.CreatePipe(
                ctypes.byref(input_read), ctypes.byref(input_write), None, 0
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            temporary_handles.extend((input_read, input_write))
            if not kernel32.CreatePipe(
                ctypes.byref(output_read), ctypes.byref(output_write), None, 0
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            temporary_handles.extend((output_read, output_write))
            cols, rows = self._terminal_size()
            self._last_size = (cols, rows)
            hpc = wintypes.HANDLE()
            result = int(
                kernel32.CreatePseudoConsole(
                    COORD(cols, rows), input_read, output_write, 0, ctypes.byref(hpc)
                )
            )
            if result != 0:
                raise OSError(result, "CreatePseudoConsole failed")
            self._hpc = hpc
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(output_write)
            temporary_handles = [input_write, output_read]

            attribute_size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(
                None, 1, 0, ctypes.byref(attribute_size)
            )
            attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
            attribute_list = ctypes.cast(attribute_buffer, wintypes.LPVOID)
            if not kernel32.InitializeProcThreadAttributeList(
                attribute_list, 1, 0, ctypes.byref(attribute_size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                0x00020016,
                hpc,
                ctypes.sizeof(wintypes.HANDLE),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            startup = STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            # Do not let an existing parent/debugger console handle win over the
            # pseudoconsole attribute. This is also required by common ConPTY
            # implementations when launched under redirected test runners.
            startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = None
            startup.StartupInfo.hStdOutput = None
            startup.StartupInfo.hStdError = None
            startup.lpAttributeList = attribute_list
            process_info = PROCESS_INFORMATION()
            command_line = ctypes.create_unicode_buffer(self._command_line(cmd))
            environment = ctypes.create_unicode_buffer(self._environment_block(env))
            creation_flags = 0x00080000 | 0x00000400
            if not kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                False,
                creation_flags,
                environment,
                os.getcwd(),
                ctypes.cast(ctypes.byref(startup), ctypes.POINTER(STARTUPINFOW)),
                ctypes.byref(process_info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            kernel32.CloseHandle(process_info.hThread)
            self._process_handle = process_info.hProcess
            self.pid = int(process_info.dwProcessId)
            self._input_handle = input_write
            self._output_handle = output_read
            temporary_handles.clear()
        finally:
            if attribute_buffer is not None:
                try:
                    kernel32.DeleteProcThreadAttributeList(attribute_buffer)
                except (AttributeError, OSError):
                    pass
            for handle in temporary_handles:
                try:
                    kernel32.CloseHandle(handle)
                except (AttributeError, OSError):
                    pass

    @staticmethod
    def _command_line(cmd: list[str]) -> str:
        if not cmd:
            raise ValueError("ConPTY command is empty")
        executable = str(cmd[0]).lower()
        if executable.endswith((".cmd", ".bat")):
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            inner = subprocess.list2cmdline(cmd)
            return subprocess.list2cmdline([comspec, "/d", "/s", "/c", inner])
        return subprocess.list2cmdline(cmd)

    @staticmethod
    def _environment_block(environment: Mapping[str, str]) -> str:
        entries = [f"{key}={value}" for key, value in environment.items()]
        entries.sort(key=str.casefold)
        return "\0".join(entries) + "\0\0"

    def _terminal_size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self._stdout_fd)
            return max(1, min(32767, size.columns)), max(1, min(32767, size.lines))
        except OSError:
            return 120, 30

    def _configure_parent_console(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = self._kernel32
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        input_handle = kernel32.GetStdHandle(-10 & 0xFFFFFFFF)
        mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(input_handle, ctypes.byref(mode)):
            return
        self._old_input_mode = int(mode.value)
        self._old_input_cp = int(kernel32.GetConsoleCP())
        self._old_output_cp = int(kernel32.GetConsoleOutputCP())
        raw_mode = (
            self._old_input_mode
            & ~(0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0008)
        ) | 0x0200
        if not kernel32.SetConsoleMode(input_handle, raw_mode):
            self._old_input_mode = None
            return
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
        output_handle = kernel32.GetStdHandle(-11 & 0xFFFFFFFF)
        output_mode = wintypes.DWORD(0)
        if kernel32.GetConsoleMode(output_handle, ctypes.byref(output_mode)):
            self._old_output_mode = int(output_mode.value)
            kernel32.SetConsoleMode(output_handle, self._old_output_mode | 0x0004)

    def _restore_parent_console(self) -> None:
        if not self._kernel32 or self._old_input_mode is None:
            return
        input_handle = self._kernel32.GetStdHandle(-10 & 0xFFFFFFFF)
        self._kernel32.SetConsoleMode(input_handle, self._old_input_mode)
        if self._old_input_cp:
            self._kernel32.SetConsoleCP(self._old_input_cp)
        if self._old_output_cp:
            self._kernel32.SetConsoleOutputCP(self._old_output_cp)
            self._old_output_cp = None
        self._old_input_mode = None
        if self._old_output_mode is not None:
            output_handle = self._kernel32.GetStdHandle(-11 & 0xFFFFFFFF)
            self._kernel32.SetConsoleMode(output_handle, self._old_output_mode)
            self._old_output_mode = None

    def _start_pumps(self) -> None:
        self._output_thread = threading.Thread(
            target=self._pump_output,
            name="ciel-conpty-output",
            daemon=True,
        )
        self._output_thread.start()
        if self._forward_stdin:
            threading.Thread(
                target=self._pump_input,
                name="ciel-conpty-input",
                daemon=True,
            ).start()

    def _pump_output(self) -> None:
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_string_buffer(64 * 1024)
        while not self._stop.is_set() and self._output_handle:
            try:
                read = wintypes.DWORD(0)
                if not self._kernel32.ReadFile(
                    self._output_handle,
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                ):
                    return
                data = buffer.raw[: int(read.value)]
                if not data:
                    return
                with self._output_lock:
                    self._output_tail.extend(data)
                    del self._output_tail[: max(0, len(self._output_tail) - 64 * 1024)]
                if self._mirror_output:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(self._stdout_fd, view) :]
            except OSError:
                return

    def _pump_input(self) -> None:
        while not self._stop.is_set():
            try:
                data = os.read(self._stdin_fd, 4096)
                if not data:
                    return
                self.write(data)
            except OSError:
                return


__all__ = ["WindowsConPtySession", "conpty_enabled"]
