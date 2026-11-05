import unittest
from types import SimpleNamespace
from ciel_runtime_support.codex_launch_assembly import CodexLaunchAssembly


class CodexWorkOffTests(unittest.TestCase):
    def test_codex_config_persists_only_work_off(self):
        config = {"work_mode": True, "language": "ko", "providers": {"test": {}}}
        saved = []
        owner = SimpleNamespace(config=SimpleNamespace(load_config=lambda: config,
                                save_config=lambda c: saved.append(c)))
        result = CodexLaunchAssembly.codex_config(owner)
        self.assertIs(result["work_mode"], False)
        self.assertEqual("ko", result["language"])
        self.assertEqual([result], saved)
        self.assertTrue(config["work_mode"])

    def test_already_off_does_not_write(self):
        owner = SimpleNamespace(config=SimpleNamespace(load_config=lambda: {"work_mode": False},
                                save_config=lambda _: self.fail("unexpected write")))
        self.assertFalse(CodexLaunchAssembly.codex_config(owner)["work_mode"])
