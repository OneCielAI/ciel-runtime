import unittest

import ciel_runtime


class CliParserTests(unittest.TestCase):
    def test_launch_command_preserves_remainder_arguments(self):
        args = ciel_runtime.build_parser().parse_args(["launch-codex", "resume", "session-id"])

        self.assertIs(ciel_runtime.cmd_launch_codex, args.func)
        self.assertEqual(["resume", "session-id"], args.argv)

    def test_model_command_preserves_legacy_value_destination(self):
        args = ciel_runtime.build_parser().parse_args(["model", "openai", "gpt-test"])

        self.assertIs(ciel_runtime.cmd_model, args.func)
        self.assertEqual(["openai", "gpt-test"], args.value)

    def test_provider_key_and_test_command_shapes(self):
        keys = ciel_runtime.build_parser().parse_args(["set-api-keys", "openai", "first", "second"])
        test = ciel_runtime.build_parser().parse_args(["test", "45", "smoke"])

        self.assertEqual("openai", keys.provider)
        self.assertEqual(["first", "second"], keys.keys)
        self.assertEqual(45.0, test.timeout)
        self.assertEqual("smoke", test.mode)


if __name__ == "__main__":
    unittest.main()
