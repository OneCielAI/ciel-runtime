import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.agy_installer import AgyInstaller, AgyInstallerPorts


class AgyInstallerTests(unittest.TestCase):
    def installer(self, root: Path):
        output = mock.Mock()
        installer = AgyInstaller(
            "https://example.test",
            AgyInstallerPorts(
                lambda: root / "bin",
                lambda: {"CI": "1"},
                lambda _name: None,
                lambda latest, current: latest != current,
                mock.Mock(return_value=(0, "updated")),
                output,
            ),
        )
        return installer, output

    def test_manifest_name_and_override_are_platform_aware(self):
        with tempfile.TemporaryDirectory() as tmp:
            installer, _output = self.installer(Path(tmp))
            with (
                mock.patch("ciel_runtime_support.agy_installer.os.name", "nt"),
                mock.patch("ciel_runtime_support.agy_installer.platform.machine", return_value="AMD64"),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                self.assertEqual("windows_amd64.json", installer.manifest_name())
                self.assertEqual("https://example.test/manifests/windows_amd64.json", installer.manifest_url())

    def test_sha512_verification_detects_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agy"
            path.write_bytes(b"agy")
            expected = hashlib.sha512(b"agy").hexdigest()
            self.assertTrue(AgyInstaller.verify_sha512(path, expected))
            self.assertFalse(AgyInstaller.verify_sha512(path, "0" * 128))

    def test_update_check_skips_upgrade_when_manifest_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            installer, output = self.installer(Path(tmp))
            with (
                mock.patch.object(AgyInstaller, "latest_manifest", return_value={"version": "1.2.3", "url": "x"}),
                mock.patch.object(AgyInstaller, "current_version", return_value="1.2.3"),
            ):
                self.assertEqual("agy", installer.update_check("agy"))
            installer.ports.run_upgrade.assert_not_called()
            self.assertTrue(any("up to date" in call.args[0] for call in output.call_args_list))


if __name__ == "__main__":
    unittest.main()
