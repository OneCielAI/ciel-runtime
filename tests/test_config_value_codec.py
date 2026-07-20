import math
import unittest

from ciel_runtime_support.config_value_codec import (
    finite_float,
    parse_bool,
    parse_config_value,
    positive_int,
)


class ConfigValueCodecTests(unittest.TestCase):
    def test_positive_int_accepts_only_values_above_zero(self):
        self.assertEqual(7, positive_int("7"))
        self.assertIsNone(positive_int(0))
        self.assertIsNone(positive_int(-1))
        self.assertIsNone(positive_int("not-an-int"))

    def test_finite_float_rejects_non_finite_values(self):
        self.assertEqual(1.25, finite_float("1.25"))
        self.assertIsNone(finite_float(math.inf))
        self.assertIsNone(finite_float(math.nan))
        self.assertIsNone(finite_float("not-a-float"))

    def test_parse_config_value_decodes_scalars_and_json(self):
        self.assertIs(True, parse_config_value("yes"))
        self.assertIs(False, parse_config_value("off"))
        self.assertIsNone(parse_config_value("null"))
        self.assertEqual({"enabled": True}, parse_config_value('{"enabled": true}'))
        self.assertEqual(42, parse_config_value("42"))
        self.assertEqual(2.5, parse_config_value("2.5"))
        self.assertEqual("plain text", parse_config_value(" plain text "))

    def test_parse_bool_supports_config_vocabulary_and_default(self):
        for value in (True, 1, "true", "YES", "enabled"):
            with self.subTest(value=value):
                self.assertTrue(parse_bool(value))
        for value in (False, 0, "false", "NO", "disabled"):
            with self.subTest(value=value):
                self.assertFalse(parse_bool(value, default=True))
        self.assertTrue(parse_bool(None, default=True))
        self.assertTrue(parse_bool("unknown", default=True))


if __name__ == "__main__":
    unittest.main()
