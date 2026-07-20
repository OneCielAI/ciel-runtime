import unittest
from pathlib import Path

from ciel_runtime_support.runtime_upgrade import (
    RuntimeUpgradeNpmPorts,
    RuntimeUpgradeService,
    RuntimeUpgradeSettings,
    RuntimeUpgradeToolPorts,
)


class RuntimeUpgradeServiceTests(unittest.TestCase):
    def service(self, *, executables=None, latest="2.0.0", outputs=None, runs=None):
        executables = executables or {}
        outputs = outputs if outputs is not None else []
        runs = runs if runs is not None else []
        return RuntimeUpgradeService(
            settings=RuntimeUpgradeSettings("1.0.0", {}),
            npm=RuntimeUpgradeNpmPorts(
                find_executable=lambda name: executables.get(name),
                latest_version=lambda _npm, _package: latest,
                version_newer=lambda candidate, current: candidate != current,
                current_package_root=lambda: Path("/prefix/lib/node_modules/package"),
                package_prefix=lambda _root: Path("/prefix"),
                current_prefix=lambda: Path("/prefix"),
                global_install_command=lambda npm, package, prefix: [npm, package, str(prefix)],
                runtime_install_command=lambda npm, package, prefix: [str(npm), package, str(prefix)],
                run_command=lambda command, timeout: (runs.append((command, timeout)) or (0, "")),
            ),
            tools=RuntimeUpgradeToolPorts(
                claude_version=lambda _path: "1.0.0",
                codex_version=lambda _path: "1.0.0",
                install_claude=lambda: "claude",
                install_codex=lambda: "codex",
                install_agy=lambda: "agy",
                update_agy=lambda _path, _enabled: "agy",
            ),
            output=outputs.append,
        )

    def test_runtime_upgrade_targets_the_active_install_prefix(self):
        runs = []
        service = self.service(executables={"npm": "npm"}, runs=runs)
        self.assertEqual(0, service.ciel_runtime())
        self.assertEqual(
            (["npm", "@oneciel-ai/ciel-runtime@latest", str(Path("/prefix"))], 300.0),
            runs[0],
        )

    def test_missing_cli_uses_its_installer(self):
        service = self.service(executables={"npm": "npm"})
        self.assertEqual(0, service.claude())
        self.assertEqual(0, service.codex())
        self.assertEqual(0, service.agy())

    def test_codex_requires_npm_when_already_installed(self):
        outputs = []
        service = self.service(executables={"codex": "codex"}, outputs=outputs)
        self.assertEqual(1, service.codex())
        self.assertIn("npm was not found", outputs[-1])


if __name__ == "__main__":
    unittest.main()
