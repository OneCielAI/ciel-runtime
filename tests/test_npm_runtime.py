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

    def test_new_and_pre_rename_scopes_are_both_recognized(self):
        from ciel_runtime_support.npm_runtime import RUNTIME_PACKAGE_NAME, runtime_package_spec
        from ciel_runtime_support.runtime_restart import running_from_npm_package

        self.assertEqual("@one-ciel-ai/ciel-runtime", RUNTIME_PACKAGE_NAME)
        self.assertEqual("@one-ciel-ai/ciel-runtime@nightly", runtime_package_spec("0.2.51-nightly.20261003-000000.abc1234"))
        for scope in ("@one-ciel-ai", "@oneciel-ai"):
            package = Path(f"/opt/npm/lib/node_modules/{scope}/ciel-runtime").resolve(strict=False)
            self.assertEqual(package, package_root_from_installed_path(package / "ciel_runtime.py"))
            self.assertTrue(running_from_npm_package(package / "ciel_runtime.py", {}))
        self.assertIsNone(package_root_from_installed_path(Path("/opt/npm/lib/node_modules/@other/ciel-runtime/x.py")))

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
