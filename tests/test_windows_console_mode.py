import unittest
from unittest import mock

from ciel_runtime_support.windows_console_mode import (
    WindowsConsoleModePorts,
    WindowsConsoleModeService,
    WindowsConsoleMouseInputGuard,
)


class WindowsConsoleModeTests(unittest.TestCase):
    def _service(self, *, handle=None, environment=None):
        return WindowsConsoleModeService(
            WindowsConsoleModePorts(
                input_handle=lambda: handle,
                parse_bool=lambda value, default=False: (
                    default if value is None else value == "1"
                ),
                environment=environment or {},
            )
        )

    def test_missing_console_handle_is_not_supported(self):
        service = self._service()

        self.assertFalse(service.input_supported())
        self.assertIsNone(service.current())
        self.assertFalse(service.set(7))

    def test_mouse_filter_configuration_uses_boolean_codec(self):
        self.assertTrue(self._service().mouse_filter_enabled())
        self.assertFalse(
            self._service(
                environment={
                    "CIEL_RUNTIME_WINDOWS_CONSOLE_MOUSE_FILTER": "0"
                }
            ).mouse_filter_enabled()
        )

    def test_terminal_reset_configuration_defaults_on_and_supports_opt_out(self):
        self.assertTrue(self._service().terminal_reset_enabled())
        self.assertFalse(
            self._service(
                environment={
                    "CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET": "0"
                }
            ).terminal_reset_enabled()
        )

    def _reset_kernel32(
        self,
        *,
        original_mode=0x0002,
        activate=True,
        write=True,
    ):
        kernel32 = mock.Mock()
        kernel32.GetStdHandle.return_value = 123
        set_modes = []
        writes = []

        def get_console_mode(_handle, mode_pointer):
            mode_pointer._obj.value = original_mode
            return True

        def set_console_mode(_handle, mode):
            set_modes.append(int(getattr(mode, "value", mode)))
            if len(set_modes) == 1:
                return activate
            return True

        def write_console(_handle, buffer, units, written_pointer, _reserved):
            unit_count = int(getattr(units, "value", units))
            writes.append((getattr(buffer, "value", buffer), unit_count))
            if write:
                written_pointer._obj.value = unit_count
            return write

        kernel32.GetConsoleMode.side_effect = get_console_mode
        kernel32.SetConsoleMode.side_effect = set_console_mode
        kernel32.WriteConsoleW.side_effect = write_console
        return kernel32, set_modes, writes

    def test_terminal_reset_enables_vt_output_writes_and_restores_mode(self):
        kernel32, set_modes, writes = self._reset_kernel32()
        sequence = "\x1b[?1000l\x1b[?2004l"

        with mock.patch("ctypes.WinDLL", return_value=kernel32, create=True):
            reset = self._service().reset_terminal_modes(sequence)

        self.assertTrue(reset)
        self.assertEqual([0x0007, 0x0002], set_modes)
        self.assertEqual(
            [(sequence, len(sequence.encode("utf-16-le")) // 2)],
            writes,
        )

    def test_terminal_reset_restores_mode_when_write_console_fails(self):
        kernel32, set_modes, writes = self._reset_kernel32(write=False)
        sequence = "\x1b[?1000l"

        with mock.patch("ctypes.WinDLL", return_value=kernel32, create=True):
            reset = self._service().reset_terminal_modes(sequence)

        self.assertFalse(reset)
        self.assertEqual([0x0007, 0x0002], set_modes)
        self.assertEqual(
            [(sequence, len(sequence.encode("utf-16-le")) // 2)],
            writes,
        )

    def test_terminal_reset_does_not_write_or_restore_if_vt_enable_fails(self):
        kernel32, set_modes, writes = self._reset_kernel32(activate=False)

        with mock.patch("ctypes.WinDLL", return_value=kernel32, create=True):
            reset = self._service().reset_terminal_modes("\x1b[?1000l")

        self.assertFalse(reset)
        self.assertEqual([0x0007], set_modes)
        self.assertEqual([], writes)

    def test_guard_disables_mouse_bit_and_restores_original_mode(self):
        modes = {"value": 0x01F7}
        writes = []

        def set_mode(value):
            writes.append(value)
            modes["value"] = value
            return True

        guard = WindowsConsoleMouseInputGuard(
            platform_name="nt",
            filter_enabled=lambda: True,
            current_mode=lambda: modes["value"],
            set_mode=set_mode,
            log=lambda _level, _message: None,
        )

        guard.apply()
        self.assertEqual(0x01E7, modes["value"])
        guard.restore()
        self.assertEqual([0x01E7, 0x01F7], writes)

    def test_guard_is_noop_outside_windows(self):
        writes = []
        guard = WindowsConsoleMouseInputGuard(
            platform_name="posix",
            filter_enabled=lambda: True,
            current_mode=lambda: 0x0010,
            set_mode=lambda value: writes.append(value) or True,
            log=lambda _level, _message: None,
        )

        guard.apply()
        guard.restore()

        self.assertEqual([], writes)


if __name__ == "__main__":
    unittest.main()
