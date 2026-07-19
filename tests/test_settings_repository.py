import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.settings_repository import JsonSettingsRepository, SettingsFileEffects
from ciel_runtime_support.statusline_settings import StatusLineServices, install_statusline_settings


class SettingsRepositoryTests(unittest.TestCase):
    def test_repository_saves_atomically_and_reports_permission_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            log = mock.Mock()
            repository = JsonSettingsRepository(
                path=path,
                effects=SettingsFileEffects(
                    log=log,
                    chmod=mock.Mock(side_effect=OSError("denied")),
                    process_id=lambda: 42,
                    time_ns=lambda: 99,
                ),
            )

            repository.save({"language": "ko"}, "test")

            self.assertEqual({"language": "ko"}, repository.load("test"))
            self.assertIn("settings_chmod_failed", log.call_args.args[1])
            self.assertFalse(path.with_name("settings.json.42.99.tmp").exists())

    def test_statusline_installer_updates_shared_settings_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            script_path = root / "statusline.py"
            repository = JsonSettingsRepository(
                path=settings_path,
                effects=SettingsFileEffects(log=mock.Mock()),
            )
            services = StatusLineServices(repository=repository, warn=mock.Mock())

            install_statusline_settings(script_path, "print('ok')\n", "python", services)

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual("command", settings["statusLine"]["type"])
            self.assertIn("statusline.py", settings["statusLine"]["command"])
            self.assertEqual("print('ok')\n", script_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
