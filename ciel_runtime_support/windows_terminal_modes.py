"""Keep child ConPTY input protocols from taking over the parent terminal."""

from .terminal_platform_io import TERMINAL_INPUT_MODE_RESET


WINDOWS_TERMINAL_INPUT_MODE_RESET = TERMINAL_INPUT_MODE_RESET + "\x1b[?9001l"


class WindowsTerminalModeFilter:
    """Filter DECSET at the output boundary, not user text at the input boundary.

    Our parent reader consumes ordinary VT input, not Win32-input-mode records.
    Mouse reporting is also intentionally disabled on Windows. Display modes,
    focus events and bracketed paste are preserved. State spans pipe reads;
    control strings are streamed without buffering their potentially large bodies.
    """

    _blocked = {9, 1000, 1001, 1002, 1003, 1005, 1006, 1015, 1016, 9001}

    def __init__(self) -> None:
        self._pending = bytearray()
        self._string = 0
        self._string_escape = False

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        output = bytearray()
        for value in data:
            if self._string:
                output.append(value)
                if (self._string_escape and value == 92) or value in (24, 26) or (
                    self._string == 93 and value == 7
                ):
                    self._string = 0
                self._string_escape = value == 27
                continue
            if value == 27:
                output.extend(self._pending)
                self._pending[:] = b"\x1b"
                continue
            if not self._pending:
                output.append(value)
                continue
            self._pending.append(value)
            if len(self._pending) == 2:
                if value in b"]PX^_":
                    self._string = value
                    self._string_escape = False
                if value == 91:
                    continue
            elif not (64 <= value <= 126) and value not in (24, 26):
                if len(self._pending) < 256:
                    continue
            output.extend(self._filter_csi(bytes(self._pending)))
            self._pending.clear()
        if final:
            output.extend(self._pending)
            self._pending.clear()
        return bytes(output)

    def _filter_csi(self, sequence: bytes) -> bytes:
        if not sequence.startswith(b"\x1b[?") or not sequence.endswith(b"h"):
            return sequence
        parameters = sequence[3:-1].split(b";")
        if not all(part.isdigit() for part in parameters):
            return sequence
        kept = [part for part in parameters if int(part) not in self._blocked]
        return b"\x1b[?" + b";".join(kept) + b"h" if kept else b""
