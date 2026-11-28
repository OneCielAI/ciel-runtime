import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ciel_runtime_support.router_restart_command import RestartPorts, restart_owned_router


class RouterRestartTests(unittest.TestCase):
    def test_unresponsive_http_restarts_only_owned_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'router.pid').write_text('42', encoding='ascii')
            terminate = Mock(return_value=True)
            ports = RestartPorts(lambda: [42], lambda pid: 'old' if pid == 42 else 'new',
                                 lambda pid: pid == 42, terminate,
                                 Mock(side_effect=[None, {'pid': 43, 'version': 'beta'}]),
                                 lambda row: row is not None, Mock())
            result = restart_owned_router(root / 'router.pid', 'MCP stuck', ports)
            self.assertEqual({'previous_pid': 42, 'pid': 43, 'version': 'beta'}, result)
            terminate.assert_called_once_with(42, 'old')
            with self.assertRaisesRegex(RuntimeError, 'cooldown'):
                restart_owned_router(root / 'router.pid', 'again', ports)
            terminate.assert_called_once()

    def test_foreign_listener_is_never_signalled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'router.pid'
            path.write_text('42', encoding='ascii')
            terminate = Mock()
            ports = RestartPorts(lambda: [42, 57], lambda pid: 'old', lambda pid: True,
                                 terminate, Mock(return_value=None), lambda row: False, Mock())
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                restart_owned_router(path, 'test', ports)
            terminate.assert_not_called()
