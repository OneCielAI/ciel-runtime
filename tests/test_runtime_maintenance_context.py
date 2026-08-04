import unittest
from pathlib import Path
from unittest.mock import Mock

from ciel_runtime_support.runtime_maintenance_context import (
    RuntimeAgyPorts,
    RuntimeLifecyclePorts,
    RuntimeMaintenanceContext,
    RuntimePackagePorts,
)


class RuntimeMaintenanceContextTests(unittest.TestCase):
    def context(self, *, environment=None):
        packages = Mock()
        diagnostics = Mock()
        restart = Mock()
        self_update = Mock()
        upgrade = Mock()
        agy = Mock()
        context = RuntimeMaintenanceContext(
            packages=RuntimePackagePorts(
                lifecycle=lambda: packages,
                environment=environment or {},
                claude_version=lambda _path: "claude-version",
                codex_version=lambda _path: "codex-version",
            ),
            lifecycle=RuntimeLifecyclePorts(
                diagnostics=lambda: diagnostics,
                restart=lambda: restart,
                self_update=lambda: self_update,
                upgrade=lambda: upgrade,
            ),
            agy=RuntimeAgyPorts(installer=lambda: agy),
        )
        return context, packages, diagnostics, restart, self_update, upgrade, agy

    def test_claude_update_uses_environment_package_override(self):
        context, packages, *_ = self.context(
            environment={"CIEL_RUNTIME_CLAUDE_CODE_PACKAGE": "claude@test"}
        )
        packages.update_check.return_value = "claude"

        self.assertEqual("claude", context.run_claude_update_check("claude.exe"))
        self.assertEqual("claude@test", packages.update_check.call_args.kwargs["package_spec"])
        self.assertEqual(
            "claude-version",
            packages.update_check.call_args.kwargs["current_version"]("ignored"),
        )

    def test_codex_install_uses_provider_default_when_unconfigured(self):
        context, packages, *_ = self.context()
        packages.install_if_missing.return_value = "codex"

        self.assertEqual("codex", context.install_codex_if_missing())
        self.assertEqual(
            "@openai/codex@latest",
            packages.install_if_missing.call_args.kwargs["package_spec"],
        )

    def test_diagnostics_and_restart_delegate_to_lifecycle_ports(self):
        context, _, diagnostics, restart, *_ = self.context()
        diagnostics.candidates.return_value = [Path("ciel-runtime")]
        restart.user_args.return_value = ["--continue"]

        self.assertEqual([Path("ciel-runtime")], context.launcher_candidates())
        self.assertEqual(["--continue"], context.restart_user_args())
        diagnostics.candidates.assert_called_once_with()
        restart.user_args.assert_called_once_with()

    def test_agy_operations_delegate_only_to_installer_port(self):
        context, *_, agy = self.context()
        agy.latest_manifest.return_value = {"version": "1.2.3"}

        self.assertEqual({"version": "1.2.3"}, context.agy_latest_manifest(2.5))
        agy.latest_manifest.assert_called_once_with(2.5)

    def test_quiet_upgrade_aggregates_all_results(self):
        context, *_, upgrade, _agy = self.context()
        upgrade.ciel_runtime.return_value = 0
        upgrade.claude.return_value = 1
        upgrade.codex.return_value = 0
        upgrade.agy.return_value = 0

        self.assertEqual(1, context.run_quiet_upgrade_and_exit())
        upgrade.ciel_runtime.assert_called_once_with()
        upgrade.claude.assert_called_once_with()
        upgrade.codex.assert_called_once_with()
        upgrade.agy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
