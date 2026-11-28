import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ciel_runtime_support.router_restart_command import RestartPorts, restart_owned_router


class RouterRestartTests(unittest.TestCase):
    def test_restart_cli_does_not_initialize_workspace_services(self):
        root = Path(__file__).resolve().parents[1]
        script = '''
import os, runpy, sys
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support import workspace_state, otlp_logs
entry, form, service = sys.argv[1:]
sys.argv = [entry] + (['cli'] if form == 'ctl' else []) + ['restart-router', '--help']
with patch.object(workspace_state, 'migrate_workspace_state', side_effect=PermissionError('migration fixture') if service == 'migration' else None), patch.object(otlp_logs.TelemetryLogTokenRepository, 'ensure', side_effect=PermissionError('telemetry fixture') if service == 'telemetry' else None):
    runpy.run_path(entry, run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as directory:
            for form in ('direct', 'ctl'):
                for service in ('migration', 'telemetry'):
                    with self.subTest(form=form, service=service):
                        env = {k: v for k, v in os.environ.items()
                               if not k.upper().startswith('CIEL_RUNTIME_')}
                        env.update(CIEL_RUNTIME_CONFIG_DIR=directory, CIEL_RUNTIME_ROUTER_PORT='59163',
                                   CIEL_RUNTIME_LAUNCH_CWD=directory, PYTHONPATH=str(root))
                        result = subprocess.run([sys.executable, '-B', '-c', script,
                                                 str(root / 'ciel_runtime.py'), form, service],
                                                cwd=directory, env=env, capture_output=True,
                                                text=True, timeout=30)
                        self.assertEqual(0, result.returncode, result.stderr)
                        self.assertIn('--reason', result.stdout)
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
