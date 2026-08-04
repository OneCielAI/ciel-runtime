import unittest
from unittest import mock

from ciel_runtime_support.runtime_maintenance_assembly import (
    RuntimeMaintenanceAssembly,
    RuntimeMaintenanceCommandPorts,
)


class RuntimeMaintenanceAssemblyTests(unittest.TestCase):
    def test_context_uses_service_graph_and_explicit_commands(self):
        services = mock.Mock()
        commands = RuntimeMaintenanceCommandPorts(
            environment={"CIEL_TEST": "1"},
            claude_version=lambda executable: f"claude:{executable}",
            codex_version=lambda executable: f"codex:{executable}",
            upgrade_runtime=lambda: 0,
            upgrade_claude=lambda: 0,
            upgrade_codex=lambda: 0,
            upgrade_agy=lambda: 0,
        )

        context = RuntimeMaintenanceAssembly(services, commands).context()

        self.assertEqual({"CIEL_TEST": "1"}, context.packages.environment)
        self.assertEqual("claude:x", context.packages.claude_version("x"))
        self.assertEqual("codex:y", context.packages.codex_version("y"))
        self.assertIs(services.npm_lifecycle, context.packages.lifecycle)
        self.assertIs(services.install_diagnostics, context.lifecycle.diagnostics)
        self.assertIs(services.restart_service, context.lifecycle.restart)
        self.assertIs(services.self_update, context.lifecycle.self_update)
        self.assertIs(services.upgrade, context.lifecycle.upgrade)
        self.assertIs(services.agy_installer, context.agy.installer)
        self.assertEqual(0, context.run_quiet_upgrade_and_exit())


if __name__ == "__main__":
    unittest.main()
