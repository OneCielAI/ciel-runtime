import unittest

from ciel_runtime_support.package_lifecycle import (
    NpmPackageLifecycle,
    NpmPackageLifecyclePorts,
)


class PackageLifecycleTests(unittest.TestCase):
    def test_missing_runtime_installs_into_active_prefix(self):
        executables = {"npm": "npm"}
        commands = []
        outputs = []

        def run_upgrade(command, **_kwargs):
            commands.append(command)
            executables["tool"] = "/prefix/bin/tool"
            return 0, "installed"

        lifecycle = self._lifecycle(executables, run_upgrade, outputs)
        result = lifecycle.install_if_missing(
            executable_name="tool",
            label="Tool",
            package_spec="tool@latest",
            skip_env="TEST_SKIP_TOOL_INSTALL",
        )
        self.assertEqual("/prefix/bin/tool", result)
        self.assertEqual(["npm", "install", "tool@latest"], commands[0])
        self.assertTrue(any("Tool installed" in line for line in outputs))

    def test_update_keeps_current_executable_when_latest_is_not_newer(self):
        outputs = []
        lifecycle = self._lifecycle({"npm": "npm"}, lambda *_args, **_kwargs: (0, ""), outputs)
        result = lifecycle.update_check(
            "/bin/tool",
            executable_name="tool",
            label="Tool",
            package_spec="tool@latest",
            skip_env="TEST_SKIP_TOOL_UPDATE",
            current_version=lambda _executable: "2.0.0",
        )
        self.assertEqual("/bin/tool", result)
        self.assertTrue(any("up to date" in line for line in outputs))

    @staticmethod
    def _lifecycle(executables, run_upgrade, outputs):
        return NpmPackageLifecycle(
            NpmPackageLifecyclePorts(
                find_executable=lambda name: executables.get(name),
                install_prefix=lambda: None,
                install_command=lambda npm, package, _prefix: [npm, "install", package],
                run_upgrade=run_upgrade,
                add_prefix_bin=lambda _prefix: None,
                latest_version=lambda _npm, _package: "2.0.0",
                version_newer=lambda latest, current: latest != current,
                output=lambda message, **_kwargs: outputs.append(message),
            )
        )


if __name__ == "__main__":
    unittest.main()
