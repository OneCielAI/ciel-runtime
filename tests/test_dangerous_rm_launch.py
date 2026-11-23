"""The removal-waiver option must reach a launched Claude session whole.

Celly 2026-11-23: the launcher exported the guard switch but left the guard's
own PermissionRequest handler installed, which is one of the two conditions
under which Claude Code 2.1.330 still shows the "can't rule out a dangerous
removal" prompt in bypassPermissions (the other is its waiver being unset).
These checks pin the launcher's side: the option on, both switches exported
and the handler withheld; the option off, both switches stripped and the
handler installed.
"""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ciel_runtime
from ciel_runtime_support import dangerous_rm_auto_allow as option


class RemovalWaiverLaunchTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(
            ciel_runtime, "terminate_existing_codex_processes_for_launch", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def launch(self, config):
        provider_config = {"current_model": "k3"}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict("os.environ", {"PATH": "runtime-path"}, clear=True))
            stack.enter_context(mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0))
            stack.enter_context(mock.patch.object(ciel_runtime, "apply_launch_endpoint_policy", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "load_config", return_value=config))
            stack.enter_context(mock.patch.object(ciel_runtime, "get_current_provider",
                                                  return_value=("kimi", provider_config)))
            stack.enter_context(mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"))
            stack.enter_context(mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_with_router_lifetime",
                                                  side_effect=lambda runner, _managed: runner()))
            stack.enter_context(mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch"))
            stack.enter_context(mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["k3"]))
            stack.enter_context(mock.patch.object(ciel_runtime, "ensure_current_model_from_provider_list",
                                                  return_value=("k3", [])))
            stack.enter_context(mock.patch.object(ciel_runtime, "find_executable", return_value="runtime-claude"))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_claude_code_if_missing",
                                                  return_value="runtime-claude"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_claude_update_check",
                                                  return_value="runtime-claude"))
            stack.enter_context(mock.patch.object(ciel_runtime, "claude_supports_permission_mode_arg",
                                                  return_value=True))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_ciel_runtime_slash_commands"))
            install = stack.enter_context(mock.patch.object(ciel_runtime, "install_tool_guard_hooks"))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_ciel_runtime_statusline"))
            stack.enter_context(mock.patch.object(ciel_runtime, "should_attach_web_search", return_value=False))
            stack.enter_context(mock.patch.object(ciel_runtime, "should_append_compat_prompt", return_value=False))
            proxy = stack.enter_context(mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy",
                                                          return_value=0))
            direct = stack.enter_context(mock.patch.object(ciel_runtime.subprocess, "call", return_value=0))
            result = ciel_runtime.launch_claude.delegate([], skip_menu=True, update_check=False,
                                                         self_update_check=False)
        self.assertEqual(0, result)
        invocation = proxy if proxy.called else direct
        environment = invocation.call_args.args[1] if proxy.called else invocation.call_args.kwargs["env"]
        return install, environment

    def test_option_on_exports_both_switches_and_withholds_the_permission_hook(self):
        install, environment = self.launch({option.CONFIG_KEY: True})
        self.assertEqual("1", environment.get(option.ENV_NAME))
        self.assertEqual("1", environment.get(option.WAIVER_ENV_NAME))
        self.assertTrue(install.call_args.kwargs.get("without_permission_request"))

    def test_option_off_strips_both_switches_and_keeps_the_hook(self):
        install, environment = self.launch({})
        self.assertNotIn(option.ENV_NAME, environment)
        self.assertNotIn(option.WAIVER_ENV_NAME, environment)
        self.assertFalse(install.call_args.kwargs.get("without_permission_request"))


if __name__ == "__main__":
    unittest.main()
