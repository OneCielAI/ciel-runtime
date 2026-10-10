import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import oauth_token_menu, prelaunch
from ciel_runtime_support.oauth_token_store import OAuthTokenStore


def scripted(*answers):
    queue = list(answers)
    asked = []

    def prompt(label, default):
        asked.append((label, default))
        return queue.pop(0) if queue else ""

    prompt.asked = asked
    return prompt


class OAuthTokenMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.state = self.root / "ws"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def claude_file(self, secret="sk-ant-oat-secret") -> Path:
        path = self.root / f"{secret}.json"
        path.write_text(json.dumps({"claudeAiOauth": {"accessToken": secret, "refreshToken": "r", "expiresAt": 0}}))
        return path

    def test_menu_offers_the_panel_before_quit(self) -> None:
        self.assertEqual("oauth-tokens", prelaunch.MAIN_MENU_ACTIONS[-5])
        self.assertEqual("quit", prelaunch.MAIN_MENU_ACTIONS[-1])

    def test_empty_workspace_offers_sign_in_and_import_for_both_providers(self) -> None:
        rows, values = oauth_token_menu.panel_rows(self.state)

        self.assertEqual(len(rows), len(values))
        self.assertEqual(
            ["__info__", "login:codex", "login:claude", "import:codex", "import:claude", "back"],
            values,
        )
        self.assertIn("Sign in Codex (browser)", rows[1])
        self.assertIn("Import Claude", rows[4])
        self.assertEqual(0, oauth_token_menu.token_count(self.state))

    def test_import_adds_rotating_tokens_one_per_account(self) -> None:
        for secret, label in (("sk-ant-oat-one", "first"), ("sk-ant-oat-two", "second")):
            messages = oauth_token_menu.apply(
                "import:claude", self.state, scripted(str(self.claude_file(secret)), label)
            )
            self.assertTrue(any(line.startswith("Stored ") for line in messages), messages)
            self.assertTrue(all(chr(10) not in line for line in messages), messages)

        tokens = OAuthTokenStore(self.state).snapshot().tokens
        self.assertEqual(["first", "second"], [token.label for token in tokens])
        rows, values = oauth_token_menu.panel_rows(self.state)
        self.assertEqual([f"token:{token.token_id}" for token in tokens], values[:2])
        self.assertIn("first", rows[0])
        self.assertIn("[active", rows[0])
        self.assertNotIn("sk-ant-oat", "\n".join(rows))
        self.assertEqual(2, oauth_token_menu.token_count(self.state))

    def test_token_row_takes_disable_enable_and_remove(self) -> None:
        oauth_token_menu.apply("import:claude", self.state, scripted(str(self.claude_file()), "main"))
        token_id = OAuthTokenStore(self.state).snapshot().tokens[0].token_id

        oauth_token_menu.apply(f"token:{token_id}", self.state, scripted("disable"))
        self.assertIn("[disabled", oauth_token_menu.panel_rows(self.state)[0][0])
        oauth_token_menu.apply(f"token:{token_id}", self.state, scripted("enable"))
        self.assertIn("[active", oauth_token_menu.panel_rows(self.state)[0][0])
        messages = oauth_token_menu.apply(f"token:{token_id}", self.state, scripted("remove"))

        self.assertEqual([f"Removed {token_id}."], messages)
        self.assertEqual([], OAuthTokenStore(self.state).snapshot().tokens)

    def test_errors_become_messages_instead_of_leaving_the_menu(self) -> None:
        missing = oauth_token_menu.apply(
            "import:codex", self.state, scripted(str(self.root / "absent.json"), "")
        )
        unknown = oauth_token_menu.apply("token:tok_missing", self.state, scripted("remove"))
        bad_action = oauth_token_menu.apply("token:tok_missing", self.state, scripted("delete"))

        self.assertTrue(missing and "failed" in missing[0], missing)
        self.assertEqual(["No token tok_missing in this workspace."], unknown)
        self.assertIn("Unknown action", bad_action[0])
        self.assertEqual([], oauth_token_menu.apply("token:tok_missing", self.state, scripted("")))

    def test_sign_in_passes_the_label_and_shows_progress_in_the_terminal(self) -> None:
        calls = []
        printed = []

        def run(args, state_dir, *, output, login=None):
            calls.append((args, state_dir))
            self.assertIsNotNone(login)
            output("Open https://auth.example/authorize to sign in")
            output("Stored tok_new (user@example.com).")
            return 0

        messages = oauth_token_menu.apply(
            "login:codex", self.state, scripted("team-a"), output=printed.append, run=run
        )

        self.assertEqual([(["login", "codex", "--label", "team-a"], self.state)], calls)
        self.assertEqual(printed, messages)
        self.assertIn("Stored tok_new (user@example.com).", messages)


if __name__ == "__main__":
    unittest.main()
