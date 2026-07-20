import unittest
from pathlib import Path

from ciel_runtime_support.npm_runtime import (
    npm_global_install_command,
    npm_prefix_from_package_root,
    package_root_from_installed_path,
    parse_version_tuple,
    version_newer,
)


class NpmRuntimeTests(unittest.TestCase):
    def test_version_comparison_normalizes_different_tuple_lengths(self):
        self.assertEqual((1, 2, 0), parse_version_tuple("v1.2.0-beta"))
        self.assertTrue(version_newer("1.2.1", "1.2"))
        self.assertFalse(version_newer("1.2.0", "1.2"))

    def test_package_root_and_prefix_are_projected_from_installed_path(self):
        package = Path("/opt/npm/lib/node_modules/@oneciel-ai/ciel-runtime").resolve(
            strict=False
        )
        script = package / "ciel_runtime.py"
        self.assertEqual(package, package_root_from_installed_path(script))
        self.assertEqual(Path("/opt/npm").resolve(strict=False), npm_prefix_from_package_root(package))

    def test_global_install_command_targets_active_prefix(self):
        self.assertEqual(
            ["npm", "install", "-g", "--prefix", str(Path("/opt/npm")), "pkg@latest"],
            npm_global_install_command("npm", "pkg@latest", Path("/opt/npm")),
        )


if __name__ == "__main__":
    unittest.main()
