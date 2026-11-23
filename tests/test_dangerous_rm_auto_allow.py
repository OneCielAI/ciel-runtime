import unittest

from ciel_runtime_support import dangerous_rm_auto_allow as option
from ciel_runtime_support.prelaunch import MAIN_MENU_ACTIONS


class DangerousRmAutoAllowTests(unittest.TestCase):
    def test_off_by_default_and_never_inherits_a_stale_switch(self):
        env = {option.ENV_NAME: "1"}
        self.assertFalse(option.apply_launch_env({}, env))
        self.assertNotIn(option.ENV_NAME, env)
        self.assertFalse(option.enabled({option.CONFIG_KEY: "yes"}))

    def test_waiver_env_is_set_from_the_option_and_never_inherited(self):
        # Claude Code 2.1.330 waives the dangerous-removal prompt in
        # bypassPermissions only under its own switch, and the option must
        # own it: a launch with the option off strips any inherited value.
        env = {option.WAIVER_ENV_NAME: "1"}
        option.apply_launch_env({}, env)
        self.assertNotIn(option.WAIVER_ENV_NAME, env)
        config = {}
        option.toggle(config)
        option.apply_launch_env(config, env)
        self.assertEqual("1", env[option.WAIVER_ENV_NAME])
        option.toggle(config)
        option.apply_launch_env(config, env)
        self.assertNotIn(option.WAIVER_ENV_NAME, env)

    def test_toggle_turns_the_launch_switch_on_and_off(self):
        config = {}
        option.toggle(config)
        env = {}
        self.assertTrue(option.apply_launch_env(config, env))
        self.assertEqual("1", env[option.ENV_NAME])
        self.assertIn("[on]", option.panel_rows(config)[0][0])
        option.toggle(config)
        self.assertFalse(option.apply_launch_env(config, env))
        self.assertNotIn(option.ENV_NAME, env)
        self.assertEqual("Claude · auto-allow off", option.summary(config))

    def test_menu_action_sits_before_quit(self):
        self.assertEqual(("dangerous-rm", "session-backup", "provider-fallback", "unattended-restart", "work-settings",
                          "session-api-keys", "quit"), MAIN_MENU_ACTIONS[-7:])


if __name__ == "__main__":
    unittest.main()
