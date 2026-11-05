import unittest
from ciel_runtime_support.codex_launch_assembly import codex_config_loader


class CodexWorkOffTests(unittest.TestCase):
    def test_codex_config_persists_only_work_off(self):
        config = {"work_mode": True, "language": "ko", "providers": {"test": {}}}
        saved = []
        result = codex_config_loader(lambda: config, lambda c: saved.append(c))()
        self.assertIs(result["work_mode"], False)
        self.assertEqual("ko", result["language"])
        self.assertEqual([result], saved)
        self.assertTrue(config["work_mode"])

    def test_already_off_does_not_write(self):
        read = codex_config_loader(lambda: {"work_mode": False}, lambda _: self.fail("unexpected write"))
        self.assertFalse(read()["work_mode"])
