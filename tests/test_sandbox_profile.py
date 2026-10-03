import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.sandbox_profile import apply_sandbox_profile, find_sandbox_home


class SandboxProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.user_home = root / "Users" / "aapw-a20u19"
        (self.user_home / ".codex").mkdir(parents=True)
        (self.user_home / ".codex" / "state_5.sqlite").write_bytes(b"")
        self.sandbox = root / "sandboxes" / "u19" / "home" / "robert-ai"
        self.project = self.sandbox / "work" / "repo"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def make_sandbox_profile(self, codex=True, config=True):
        if codex:
            (self.sandbox / ".codex").mkdir(exist_ok=True)
            (self.sandbox / ".codex" / "state_5.sqlite").write_bytes(b"")
        if config:
            (self.sandbox / ".config" / "ciel-runtime").mkdir(parents=True, exist_ok=True)
            (self.sandbox / ".config" / "ciel-runtime" / "config.json").write_text("{}")

    def test_sandbox_profile_comes_before_the_account_profile(self):
        self.make_sandbox_profile()
        environ = {}
        applied = apply_sandbox_profile(environ, cwd=self.project, user_home=self.user_home)
        self.assertEqual(str((self.sandbox / ".codex").resolve()), environ["CODEX_HOME"])
        self.assertEqual(str((self.sandbox / ".config" / "ciel-runtime").resolve()), environ["CIEL_RUNTIME_CONFIG_DIR"])
        self.assertEqual(environ, applied)

    def test_launch_cwd_is_searched_before_the_process_directory(self):
        self.make_sandbox_profile()
        environ = {"CIEL_RUNTIME_LAUNCH_CWD": str(self.sandbox)}
        apply_sandbox_profile(environ, cwd=self.user_home, user_home=self.user_home)
        self.assertEqual(str((self.sandbox / ".codex").resolve()), environ["CODEX_HOME"])

    def test_explicit_environment_wins(self):
        self.make_sandbox_profile()
        environ = {"CODEX_HOME": "X:\codex"}
        apply_sandbox_profile(environ, cwd=self.sandbox, user_home=self.user_home)
        self.assertEqual("X:\codex", environ["CODEX_HOME"])
        self.assertIn("CIEL_RUNTIME_CONFIG_DIR", environ)
        disabled = {"CIEL_RUNTIME_SANDBOX_PROFILE": "0"}
        self.assertEqual({}, apply_sandbox_profile(disabled, cwd=self.sandbox, user_home=self.user_home))

    def test_only_existing_parts_are_applied(self):
        self.make_sandbox_profile(codex=True, config=False)
        environ = {}
        apply_sandbox_profile(environ, cwd=self.sandbox, user_home=self.user_home)
        self.assertIn("CODEX_HOME", environ)
        self.assertNotIn("CIEL_RUNTIME_CONFIG_DIR", environ)

    def test_project_codex_config_without_state_is_not_a_profile(self):
        (self.project / ".codex").mkdir()
        (self.project / ".codex" / "config.toml").write_text("")
        self.assertIsNone(find_sandbox_home(self.project, self.user_home))
        self.assertEqual({}, apply_sandbox_profile({}, cwd=self.project, user_home=self.user_home))

    def test_walk_stops_at_the_account_profile(self):
        nested = self.user_home / "src" / "app"
        nested.mkdir(parents=True)
        self.assertIsNone(find_sandbox_home(nested, self.user_home))
        self.assertIsNone(find_sandbox_home(self.user_home, self.user_home))


if __name__ == "__main__":
    unittest.main()
