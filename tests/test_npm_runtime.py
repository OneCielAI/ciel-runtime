import unittest
from pathlib import Path

from ciel_runtime_support.npm_runtime import (
    npm_global_install_command,
    npm_install_runtime_command,
    npm_prefix_from_package_root,
    package_root_from_installed_path,
    parse_version_tuple,
    registry_package_name,
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
            npm_global_install_command("npm", "pkg@latest", Path("/opt/npm"), npm_major=lambda _npm: 11),
        )

    def test_npm_12_allows_the_installed_package_own_install_scripts(self):
        # npm 12 skips install scripts not listed in allow-scripts; Claude Code's
        # postinstall places its native binary (sarah-ai 2026-09-28).
        self.assertEqual(
            [
                "npm", "install", "-g", "--prefer-online", "--prefix", str(Path("/home/u/.npm-global")),
                "--allow-scripts=@anthropic-ai/claude-code", "@anthropic-ai/claude-code@latest",
            ],
            npm_install_runtime_command(
                "npm", "@anthropic-ai/claude-code@latest", Path("/home/u/.npm-global"), npm_major=lambda _npm: 12
            ),
        )
        self.assertEqual(
            ["npm", "install", "-g", "--allow-scripts=codex", "codex"],
            npm_global_install_command("npm", "codex", npm_major=lambda _npm: 13),
        )

    def test_allow_scripts_is_skipped_for_old_npm_unknown_npm_and_non_registry_specs(self):
        for major in (11, None):
            with self.subTest(major=major):
                self.assertNotIn(
                    "--allow-scripts=@anthropic-ai/claude-code",
                    npm_global_install_command("npm", "@anthropic-ai/claude-code@latest", npm_major=lambda _npm: major),
                )
        for spec in ("./claude-code-2.1.284.tgz", "https://example.com/pkg.tgz", "github:org/repo", "/tmp/pkg"):
            with self.subTest(spec=spec):
                self.assertIsNone(registry_package_name(spec))
                self.assertEqual(
                    ["npm", "install", "-g", spec], npm_global_install_command("npm", spec, npm_major=lambda _npm: 12)
                )

    def test_registry_package_name_strips_tag_and_version(self):
        self.assertEqual("@anthropic-ai/claude-code", registry_package_name("@anthropic-ai/claude-code@2.1.284"))
        self.assertEqual("@oneciel-ai/ciel-runtime", registry_package_name("@oneciel-ai/ciel-runtime@nightly"))
        self.assertEqual("@openai/codex", registry_package_name("@openai/codex"))
        self.assertEqual("pkg", registry_package_name("pkg@latest"))


if __name__ == "__main__":
    unittest.main()
