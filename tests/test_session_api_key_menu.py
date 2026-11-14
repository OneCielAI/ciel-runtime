"""Prelaunch menu panel for session API keys: rows, confirmed reset, hidden-prompt inject."""

from __future__ import annotations

import secrets
import unittest

from ciel_runtime_support import prelaunch
from ciel_runtime_support.session_api_key_client import SessionApiKeyClientError
from ciel_runtime_support import session_api_key_menu as menu

S = "claude:11111111-1111-4111-8111-111111111111"
PHRASE = "reset-session-key-state"


class FakeClient:
    def __init__(self, state):
        self.payload, self.calls = state, []

    def state(self):
        return self.payload

    def action(self, body, operation_id):
        self.calls.append((dict(body), operation_id))
        return {"ok": True, "action": body["action"], "was_unreadable": True,
                "key": {"session": body.get("session"), "provider": body.get("provider"), "version": 7}}


def state(**extra):
    base = {"sessions": [{"session": S}], "keys": [], "losses": [], "staged": [], "abandoned": [],
            "notices": [], "health": {"rows": 1, "limit": 512, "state_unreadable": False},
            "reset": {"confirm": PHRASE, "warning": "Sessions it was blocking will use their stored API key."},
            "current_provider": "deepseek", "ttl_seconds": {"default": 86400}}
    base.update(extra)
    return base


class SessionApiKeyMenuTests(unittest.TestCase):
    def test_entry_sits_before_quit_and_keeps_every_earlier_row_number(self):
        actions = prelaunch.MAIN_MENU_ACTIONS
        self.assertEqual(("work-settings", "session-api-keys", "quit"), actions[-3:])
        self.assertEqual("oauth-tokens", actions[15])
        self.assertEqual("web-access", actions[16])

    def test_rows_show_notices_records_proof_and_launch_states(self):
        rows, values = menu.panel_rows(state(
            notices=["session-key marker unreadable: reset required. ..."],
            health={"rows": 3, "limit": 512, "state_unreadable": True},
            losses=[{"session": S, "provider": "deepseek", "state": "blocked_expired", "version": 2, "source": "ctl",
                     "fallback": "fail_closed", "notice": "no session-end proof: clear manually", "age_seconds": 90}],
            staged=[{"provider": "zai", "expires_at": 1_900_000_000}],
            abandoned=[{"provider": "zai", "abandoned_at": 1_900_000_000}]))
        text = "\n".join(rows)
        for needle in ("marker unreadable: reset required", "Reset session-key state (confirmation required)",
                       "Records: 3 of 512", "blocked_expired", "no session-end proof: clear manually (age 90s)",
                       "staged, awaiting first request", "abandoned: scope rebound by another launch",
                       "+ Inject a session key (hidden prompt)"):
            self.assertIn(needle, text)
        self.assertIn("reset", values)
        self.assertIn(f"key:{S}|deepseek|2", values)
        healthy, _ = menu.panel_rows(state())
        self.assertNotIn("Reset session-key state (confirmation required)", healthy)

    def test_no_router_is_a_row_not_a_crash(self):
        def unreachable():
            raise SessionApiKeyClientError("router_unreachable", "no router")

        loaded, error = menu.load(unreachable)
        rows, values = menu.panel_rows(loaded, error)
        self.assertIsNone(loaded)
        self.assertIn("router_unreachable", rows[0])
        self.assertEqual(["__info__", "back"], values)

    def test_client_comes_from_the_prelaunch_services_not_the_composition_root(self):
        import inspect

        source = inspect.getsource(prelaunch)
        self.assertNotIn("from ciel_runtime import", source)
        self.assertNotIn("import ciel_runtime\n", source)
        self.assertIn("session_api_key_client = services.options.session_api_key_client", source)
        self.assertIsNone(prelaunch.PrelaunchOptions.__dataclass_fields__["session_api_key_client"].default)
        loaded, error = menu.load(None)
        self.assertIsNone(loaded)
        self.assertIn("no router client", error)
        self.assertEqual(["No router client is wired into this menu; nothing changed."],
                         menu.apply("inject", None, lambda _l, _d: "", lambda _l: ""))

    def test_reset_needs_the_exact_phrase(self):
        client = FakeClient(state(health={"rows": 1, "limit": 512, "state_unreadable": True}))
        prompts = []
        said = menu.apply("reset", lambda: client, lambda label, default: prompts.append(label) or "yes", lambda _l: "")
        self.assertEqual(["Not confirmed; nothing changed."], said)
        self.assertEqual([], client.calls)
        self.assertIn("stored API key", prompts[0])
        said = menu.apply("reset", lambda: client, lambda _label, _default: PHRASE, lambda _l: "")
        self.assertEqual({"action": "reset_state", "source": "menu", "confirm": PHRASE}, client.calls[0][0])
        self.assertIn("now use their stored key", said[0])

    def test_inject_reads_the_key_only_from_the_hidden_prompt_and_never_shows_it(self):
        key = "sk-ciel-canary-" + secrets.token_hex(20)
        client = FakeClient(state())
        answers = iter(["1", "deepseek", "3600", "stored"])
        secret_labels = []
        said = menu.apply("inject", lambda: client, lambda _label, _default: next(answers),
                          lambda label: secret_labels.append(label) or key)
        (body, operation), = client.calls
        self.assertEqual(["Session API key (hidden)"], secret_labels)
        self.assertEqual((S, "deepseek", 3600, "stored", "menu", 0),
                         (body["session"], body["provider"], body["ttl_seconds"], body["fallback"], body["source"],
                          body["expected_version"]))
        self.assertEqual(key, body["api_key"])  # the copy the fake recorded before the menu blanked its own
        self.assertTrue(operation)
        self.assertNotIn(key, "\n".join(said))

    def test_clear_asks_and_says_the_stored_key_returns(self):
        client = FakeClient(state())
        answers = iter(["clear", "yes"])
        said = menu.apply(f"key:{S}|deepseek|4", lambda: client, lambda _l, _d: next(answers), lambda _l: "")
        self.assertEqual({"action": "clear", "session": S, "provider": "deepseek", "source": "menu",
                          "expected_version": 4}, client.calls[0][0])
        self.assertIn("stored key is in use", said[0])
        answers = iter(["clear", "no"])
        self.assertEqual(["Not cleared."], menu.apply(f"key:{S}|deepseek|4", lambda: client,
                                                      lambda _l, _d: next(answers), lambda _l: ""))


if __name__ == "__main__":
    unittest.main()
