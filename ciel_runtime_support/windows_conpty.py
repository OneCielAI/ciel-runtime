"""Native Windows pseudo-console transport for interactive agent CLIs."""

from __future__ import annotations

import os
import codecs
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable

from .terminal_platform_io import TERMINAL_INPUT_MODE_RESET
from .windows_command_line import command_line_for_create_process


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
    supports_prompt_ready_wait = True
    # Codex collapses long pastes to a placeholder, so the captured output tail
    # cannot prove that the original prompt prefix is present.  The snapshot is
    # still usable for comparing output before and after a submit key.
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
        self._stdout_console_handle: Any = None
        self._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._mirror_output = bool(mirror_output)
        self._forward_stdin = bool(forward_stdin)
        self._write_lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._output_tail = bytearray()
        self._output_total_bytes = 0
        self._closed = False
        self._stop = threading.Event()
        self._stdin_console_handle: Any = None
        self._old_input_mode: int | None = None
        self._old_output_mode: int | None = None
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

    def input_snapshot(self) -> str:
        """Return the captured child-output tail for submission confirmation."""

        return self.output_tail().decode("utf-8", errors="replace")

    def prompt_readiness_checkpoint(self) -> int:
        """Return an absolute child-output position for prompt render checks."""

        with self._output_lock:
            return int(self._output_total_bytes)

    def wait_until_prompt_ready(
        self,
        previous_snapshot: object,
        timeout_seconds: float = 2.0,
        *,
        expected_prompt: str | None = None,
    ) -> bool:
        """Wait for the child to render and settle after injected prompt input."""

        if isinstance(previous_snapshot, int) and not isinstance(previous_snapshot, bool):
            return self._wait_for_prompt_output(
                previous_snapshot,
                timeout_seconds,
                expected_prompt,
            )

        baseline = str(previous_snapshot or "")
        last = baseline
        rendered = False
        stable_since: float | None = None
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            now = time.monotonic()
            current = self.input_snapshot()
            if self._prompt_rendered_since(baseline, current, expected_prompt):
                rendered = True
            if rendered and current != last:
                last = current
                stable_since = now
            elif rendered and stable_since is not None and now - stable_since >= 0.5:
                return True
            if now >= deadline:
                return False
            time.sleep(0.02)

    def _wait_for_prompt_output(
        self,
        checkpoint: int,
        timeout_seconds: float,
        expected_prompt: str | None,
    ) -> bool:
        """Observe bytes emitted after input instead of comparing a rolling tail."""

        cursor = max(0, int(checkpoint))
        observed = bytearray()
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            chunk, cursor = self._output_since(cursor)
            if chunk:
                observed.extend(chunk)
                del observed[: max(0, len(observed) - 64 * 1024)]
                if self._prompt_rendered_in_output(bytes(observed), expected_prompt):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def _output_since(self, checkpoint: int) -> tuple[bytes, int]:
        with self._output_lock:
            total = int(self._output_total_bytes)
            base = total - len(self._output_tail)
            start = max(base, min(total, int(checkpoint)))
            return bytes(self._output_tail[start - base :]), total

    @staticmethod
    def _prompt_rendered_in_output(
        output: bytes,
        expected_prompt: str | None,
    ) -> bool:
        prompt = WindowsConPtySession.normalize_prompt(str(expected_prompt or ""))
        if not prompt:
            return bool(output)
        prefix = prompt[:48].encode("utf-8", errors="replace")
        return bool(prefix and prefix in output) or b"[Pasted Content" in output

    @staticmethod
    def _prompt_rendered_since(
        baseline: str,
        current: str,
        expected_prompt: str | None,
    ) -> bool:
        if current == baseline:
            return False
        prompt = WindowsConPtySession.normalize_prompt(str(expected_prompt or ""))
        prefix = prompt[:48]
        if prefix and current.count(prefix) > baseline.count(prefix):
            return True
        paste_marker = "[Pasted Content"
        if prompt and current.count(paste_marker) > baseline.count(paste_marker):
            return True
        return not prompt

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
        # Interactive TUIs enable DEC mouse/focus reporting in the terminal
        # emulator.  A normal CLI shutdown disables those modes itself, but a
        # crash leaves Windows Terminal sending sequences such as
        # ``ESC[<35;29;23M`` into the parent shell on every mouse move.  The
        # Win32 console-mode restore below cannot clear emulator-owned DEC
        # state, so reset it explicitly while VT output is still enabled.
        self._reset_parent_terminal_modes()
        self._restore_parent_console()
        if self._output_handle and self._kernel32:
            try:
                self._kernel32.CloseHandle(self._output_handle)
            except (AttributeError, OSError):
                pass
            self._output_handle = None
        if self._process_handle and self._kernel32:
            self._kernel32.CloseHandle(self._process_handle)
            self._process_handle = None

    def _reset_parent_terminal_modes(self) -> None:
        if not self._mirror_output:
            return
        try:
            if self._stdout_console_handle is not None:
                self._write_console_text(TERMINAL_INPUT_MODE_RESET)
                return
            data = TERMINAL_INPUT_MODE_RESET.encode("ascii")
            view = memoryview(data)
            while view:
                view = view[os.write(self._stdout_fd, view) :]
        except (OSError, ValueError):
            # Cleanup must never mask the child process's real exit status.
            return

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
        try:
            return command_line_for_create_process(cmd)
        except ValueError as exc:
            raise ValueError("ConPTY command is empty") from exc

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
        kernel32.WriteConsoleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.WriteConsoleW.restype = wintypes.BOOL
        kernel32.ReadConsoleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.ReadConsoleW.restype = wintypes.BOOL
        input_handle = kernel32.GetStdHandle(-10 & 0xFFFFFFFF)
        mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(input_handle, ctypes.byref(mode)):
            return
        self._old_input_mode = int(mode.value)
        raw_mode = (
            self._old_input_mode
            & ~(0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0008)
        ) | 0x0200
        if not kernel32.SetConsoleMode(input_handle, raw_mode):
            self._old_input_mode = None
            return
        # Read the parent console through ReadConsoleW in _pump_input.  Changing
        # the shared console code pages here corrupts active CJK IME input in the
        # parent terminal for the lifetime of the routed CLI.  Wide-character
        # input lets ConPTY receive UTF-8 without mutating that shared state.
        self._stdin_console_handle = input_handle
        output_handle = kernel32.GetStdHandle(-11 & 0xFFFFFFFF)
        output_mode = wintypes.DWORD(0)
        if kernel32.GetConsoleMode(output_handle, ctypes.byref(output_mode)):
            self._stdout_console_handle = output_handle
            self._old_output_mode = int(output_mode.value)
            kernel32.SetConsoleMode(output_handle, self._old_output_mode | 0x0004)

    def _restore_parent_console(self) -> None:
        if not self._kernel32 or self._old_input_mode is None:
            return
        input_handle = self._kernel32.GetStdHandle(-10 & 0xFFFFFFFF)
        self._kernel32.SetConsoleMode(input_handle, self._old_input_mode)
        self._stdin_console_handle = None
        self._old_input_mode = None
        if self._old_output_mode is not None:
            output_handle = self._kernel32.GetStdHandle(-11 & 0xFFFFFFFF)
            self._kernel32.SetConsoleMode(output_handle, self._old_output_mode)
            self._old_output_mode = None
        self._stdout_console_handle = None

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
        try:
            while not self._stop.is_set() and self._output_handle:
                read = wintypes.DWORD(0)
                if not self._kernel32.ReadFile(
                    self._output_handle,
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                ):
                    break
                data = buffer.raw[: int(read.value)]
                if not data:
                    break
                with self._output_lock:
                    self._output_tail.extend(data)
                    self._output_total_bytes += len(data)
                    del self._output_tail[: max(0, len(self._output_tail) - 64 * 1024)]
                if self._mirror_output:
                    self._mirror_bytes(data)
        except OSError:
            pass
        finally:
            if self._mirror_output:
                self._mirror_bytes(b"", final=True)

    def _mirror_bytes(self, data: bytes, *, final: bool = False) -> None:
        if self._stdout_console_handle is not None and self._output_decoder is not None:
            text = self._output_decoder.decode(data, final=final)
            if text:
                self._write_console_text(text)
            if final:
                self._output_decoder = None
            return
        view = memoryview(data)
        while view:
            view = view[os.write(self._stdout_fd, view) :]

    def _write_console_text(self, text: str) -> None:
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_unicode_buffer(text)
        units = len(text.encode("utf-16-le")) // 2
        written = wintypes.DWORD(0)
        if not self._kernel32.WriteConsoleW(
            self._stdout_console_handle,
            buffer,
            units,
            ctypes.byref(written),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if int(written.value) != units:
            raise OSError(
                f"short WriteConsoleW write: expected {units} UTF-16 units, "
                f"wrote {int(written.value)}"
            )

    def _read_input_bytes(self) -> bytes:
        if self._stdin_console_handle is None:
            return os.read(self._stdin_fd, 4096)
        import ctypes
        from ctypes import wintypes

        # WCHAR is always a 16-bit UTF-16 code unit on Windows, while
        # ctypes.c_wchar is 32-bit on some test hosts.  Use an explicitly
        # fixed-width buffer so both the Win32 call and byte conversion have
        # the same representation on every host.
        buffer = (ctypes.c_uint16 * 4096)()
        read = wintypes.DWORD(0)
        if not self._kernel32.ReadConsoleW(
            self._stdin_console_handle,
            buffer,
            len(buffer) - 1,
            ctypes.byref(read),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        units = int(read.value)
        if units <= 0:
            return b""
        raw = ctypes.string_at(ctypes.addressof(buffer), units * ctypes.sizeof(ctypes.c_uint16))
        return raw.decode("utf-16-le").encode("utf-8")

    def _pump_input(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._read_input_bytes()
                if not data:
                    return
                self.write(data)
            except OSError:
                return


__all__ = ["WindowsConPtySession", "conpty_enabled"]
