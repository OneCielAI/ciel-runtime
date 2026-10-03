import unittest
from unittest import mock

from ciel_runtime_support.package_lifecycle import (
    NpmPackageLifecycle,
    NpmPackageLifecyclePorts,
    SelfUpdateLifecycle,
    SelfUpdatePorts,
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

    def test_windows_update_is_deferred_while_native_executable_is_running(self):
        outputs = []
        run_upgrade = mock.Mock(return_value=(0, ""))
        lifecycle = self._lifecycle({"npm": "npm"}, run_upgrade, outputs)

        with mock.patch(
            "ciel_runtime_support.package_lifecycle.windows_executable_image_running",
            return_value=True,
        ):
            result = lifecycle.update_check(
                r"C:\npm\codex.cmd",
                executable_name="codex",
                label="Codex",
                package_spec="@openai/codex@latest",
                skip_env="TEST_SKIP_CODEX_UPDATE",
                current_version=lambda _executable: "1.0.0",
            )

        self.assertEqual(r"C:\npm\codex.cmd", result)
        run_upgrade.assert_not_called()
        self.assertTrue(any("update deferred" in line for line in outputs))

    def test_windows_process_probe_matches_filtered_csv_image(self):
        completed = mock.Mock(stdout='"codex.exe","123","Console","1","10,000 K"\n')
        with (
            mock.patch("ciel_runtime_support.package_lifecycle.os.name", "nt"),
            mock.patch(
                "ciel_runtime_support.package_lifecycle.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            from ciel_runtime_support.package_lifecycle import (
                windows_executable_image_running,
            )

            self.assertTrue(windows_executable_image_running("codex"))

        self.assertIn("IMAGENAME eq codex.exe", run.call_args.args[0])

    def test_self_update_installs_current_prefix_and_restarts(self):
        outputs = []
        restarted = []
        lifecycle = SelfUpdateLifecycle(
            "1.0.0",
            SelfUpdatePorts(
                running_from_package=lambda: True,
                find_executable=lambda name: "npm" if name == "npm" else None,
                latest_version=lambda _npm, _package: "2.0.0",
                version_newer=lambda latest, current: latest != current,
                package_root=lambda: None,
                prefix_from_root=lambda _root: None,
                install_command=lambda npm, package, _prefix: [npm, "install", package],
                forced_environment=lambda: {},
                restart=lambda npm, **kwargs: restarted.append((npm, kwargs)),
                output=lambda message, **_kwargs: outputs.append(message),
            ),
        )
        with mock.patch(
            "ciel_runtime_support.package_lifecycle.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="updated"),
        ):
            self.assertTrue(lifecycle.run())
        self.assertEqual("npm", restarted[0][0])
        self.assertTrue(any("Restarting" in line for line in outputs))

    def test_nightly_self_update_follows_nightly_tag(self):
        from ciel_runtime_support.npm_runtime import version_newer

        current = "0.2.51-nightly.20260923-185827.4cccb27"
        tags = {
            "@one-ciel-ai/ciel-runtime@latest": "0.2.52",
            "@one-ciel-ai/ciel-runtime@nightly": "0.2.51-nightly.20260924-003723.ae32250",
        }
        queried, installed = [], []
        lifecycle = SelfUpdateLifecycle(
            current,
            SelfUpdatePorts(
                running_from_package=lambda: True,
                find_executable=lambda name: "npm" if name == "npm" else None,
                latest_version=lambda _npm, spec: queried.append(spec) or tags[spec],
                version_newer=version_newer,
                package_root=lambda: None,
                prefix_from_root=lambda _root: None,
                install_command=lambda npm, spec, _prefix: installed.append(spec) or [npm, "install", spec],
                forced_environment=lambda: {},
                restart=lambda npm, **kwargs: None,
                output=lambda message, **_kwargs: None,
            ),
        )
        with mock.patch(
            "ciel_runtime_support.package_lifecycle.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="updated"),
        ):
            self.assertTrue(lifecycle.run())
        self.assertEqual(["@one-ciel-ai/ciel-runtime@nightly"], queried)
        self.assertEqual(["@one-ciel-ai/ciel-runtime@nightly"], installed)

    def test_nightly_install_is_not_updated_to_same_or_older_nightly(self):
        from ciel_runtime_support.npm_runtime import version_newer

        current = "0.2.51-nightly.20260924-003723.ae32250"
        self.assertFalse(version_newer(current, current))
        self.assertFalse(version_newer("0.2.51-nightly.20260923-185827.4cccb27", current))
        self.assertTrue(version_newer("0.2.51-nightly.20260924-010000.0000001", current))
        self.assertTrue(version_newer("0.2.52-nightly.20260101-000000.fffffff", current))

    def test_runtime_package_spec_keeps_release_channel(self):
        from ciel_runtime_support.npm_runtime import runtime_package_spec

        self.assertEqual("@one-ciel-ai/ciel-runtime@latest", runtime_package_spec("0.2.51"))
        self.assertEqual(
            "@one-ciel-ai/ciel-runtime@nightly",
            runtime_package_spec("0.2.51-nightly.20260924-003723.ae32250"),
        )

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
