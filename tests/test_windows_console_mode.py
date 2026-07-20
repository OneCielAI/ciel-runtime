import unittest

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
