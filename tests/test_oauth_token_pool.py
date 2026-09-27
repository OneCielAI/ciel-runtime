import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.oauth_token_pool import OAuthTokenPool
from ciel_runtime_support.oauth_token_store import OAuthCredential, OAuthTokenStore
from ciel_runtime_support.oauth_usage_signals import (
    UsageObservation,
    anthropic_observation,
    codex_observation,
)

NOW = 1_790_000_000.0


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class OAuthTokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = OAuthTokenStore(Path(self.dir.name))

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_credentials_are_encrypted_at_rest_and_round_trip(self) -> None:
        token = self.store.add(
            "codex",
            OAuthCredential("access-secret-1", "refresh-secret-1", account_id="acct-1", expires_at=NOW + 3600),
            label="work",
        )

        vault_text = self.store.vault_path.read_text(encoding="utf-8")
        state_text = self.store.state_path.read_text(encoding="utf-8")
        for secret in ("access-secret-1", "refresh-secret-1"):
            self.assertNotIn(secret, vault_text)
            self.assertNotIn(secret, state_text)
        self.assertEqual("acct-1", self.store.credential(token.token_id).account_id)
        self.assertEqual("refresh-secret-1", self.store.credential(token.token_id).refresh_token)
        self.assertEqual(["work"], [t.label for t in self.store.snapshot().tokens])

    def test_tampered_vault_entry_is_rejected(self) -> None:
        token = self.store.add("claude", OAuthCredential("a", "r"))
        vault = json.loads(self.store.vault_path.read_text(encoding="utf-8"))
        entry = vault["tokens"][token.token_id]["credential"]
        vault["tokens"][token.token_id]["credential"] = entry[:-6] + ("A" if entry[-6] != "A" else "B") + entry[-5:]
        self.store.vault_path.write_text(json.dumps(vault), encoding="utf-8")

        with self.assertRaises(Exception):
            self.store.credential(token.token_id)

    def test_remove_drops_credential_state_and_pins(self) -> None:
        token = self.store.add("codex", OAuthCredential("a"))
        OAuthTokenPool(self.store).acquire("codex", "s1", turn_start=True)

        self.assertTrue(self.store.remove(token.token_id))

        self.assertIsNone(self.store.credential(token.token_id))
        self.assertEqual([], self.store.snapshot().tokens)
        self.assertEqual({}, self.store.snapshot().sessions)


class UsageSignalTests(unittest.TestCase):
    def test_codex_headers_give_percent_windows_with_epoch_resets(self) -> None:
        observation = codex_observation(
            {
                "x-codex-primary-used-percent": "96.5",
                "x-codex-primary-window-minutes": "300",
                "x-codex-primary-reset-at": str(int(NOW + 600)),
                "x-codex-secondary-used-percent": "40",
                "x-codex-secondary-reset-at": str(int(NOW + 86400)),
            },
            now=NOW,
        )

        self.assertEqual({"primary": (96.5, NOW + 600), "secondary": (40.0, NOW + 86400)}, observation.windows)
        self.assertFalse(observation.limited)

    def test_codex_other_model_limit_is_ignored_unless_active(self) -> None:
        headers = {"x-codex-spark-primary-used-percent": "99", "x-codex-primary-used-percent": "10"}

        self.assertEqual({"primary"}, set(codex_observation(headers, now=NOW).windows))
        active = dict(headers, **{"x-codex-active-limit": "spark"})
        self.assertIn("spark:primary", codex_observation(active, now=NOW).windows)

    def test_codex_usage_limit_body_sets_the_reset(self) -> None:
        body = json.dumps({"error": {"type": "usage_limit_reached", "plan_type": "plus", "resets_at": int(NOW + 7200)}})

        observation = codex_observation({}, status=429, body=body, now=NOW)

        self.assertTrue(observation.limited)
        self.assertEqual(NOW + 7200, observation.reset_at)

    def test_codex_plain_429_is_transient(self) -> None:
        observation = codex_observation({"retry-after": "12"}, status=429, body="{}", now=NOW)

        self.assertTrue(observation.transient)
        self.assertFalse(observation.limited)
        self.assertEqual(NOW + 12, observation.reset_at)

    def test_anthropic_utilization_is_a_fraction(self) -> None:
        observation = anthropic_observation(
            {
                "anthropic-ratelimit-unified-5h-utilization": "0.97",
                "anthropic-ratelimit-unified-5h-reset": str(int(NOW + 900)),
                "anthropic-ratelimit-unified-7d-utilization": "0.2",
                "anthropic-ratelimit-unified-status": "allowed_warning",
            },
            now=NOW,
        )

        self.assertAlmostEqual(97.0, observation.windows["5h"][0])
        self.assertEqual(NOW + 900, observation.windows["5h"][1])
        self.assertFalse(observation.limited)

    def test_anthropic_rejected_status_is_a_limit_until_unified_reset(self) -> None:
        observation = anthropic_observation(
            {"anthropic-ratelimit-unified-status": "rejected", "anthropic-ratelimit-unified-reset": str(int(NOW + 1800))},
            status=429,
            now=NOW,
        )

        self.assertTrue(observation.limited)
        self.assertEqual(NOW + 1800, observation.reset_at)


class OAuthTokenPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = OAuthTokenStore(Path(self.dir.name), clock=self.clock)
        self.pool = OAuthTokenPool(self.store, threshold_percent=95.0, clock=self.clock)
        self.a = self.store.add("codex", OAuthCredential("tok-a", account_id="A")).token_id
        self.b = self.store.add("codex", OAuthCredential("tok-b", account_id="B")).token_id

    def tearDown(self) -> None:
        self.dir.cleanup()

    def usage(self, token_id: str, percent: float, reset_in: float = 600.0) -> None:
        self.pool.observe(token_id, UsageObservation(windows={"primary": (percent, NOW + reset_in)}))

    def test_new_conversations_fill_the_first_token_before_the_next(self) -> None:
        self.assertEqual(self.a, self.pool.acquire("codex", "s1", turn_start=True).token_id)
        self.assertEqual(self.a, self.pool.acquire("codex", "s2", turn_start=True).token_id)
        self.assertEqual("A", self.pool.acquire("codex", "s3", turn_start=True).credential.account_id)

    def test_conversation_keeps_its_token_across_turns_while_usage_remains(self) -> None:
        self.pool.acquire("codex", "s1", turn_start=True)
        self.usage(self.a, 60)

        for turn_start in (False, True, False, True):
            self.assertEqual(self.a, self.pool.acquire("codex", "s1", turn_start=turn_start).token_id)

    def test_draining_token_is_kept_inside_a_turn_and_left_at_the_next_turn(self) -> None:
        self.pool.acquire("codex", "s1", turn_start=True)
        self.usage(self.a, 96)

        inside = self.pool.acquire("codex", "s1", turn_start=False)
        self.assertEqual(self.a, inside.token_id)
        self.assertEqual("", inside.moved_from)

        next_turn = self.pool.acquire("codex", "s1", turn_start=True)
        self.assertEqual(self.b, next_turn.token_id)
        self.assertEqual(self.a, next_turn.moved_from)
        self.assertIn("95", next_turn.reason)
        # New conversations also avoid the draining token.
        self.assertEqual(self.b, self.pool.acquire("codex", "s2", turn_start=True).token_id)

    def test_limit_refusal_moves_the_request_at_once_and_reset_restores_the_token(self) -> None:
        self.pool.acquire("codex", "s1", turn_start=True)
        self.pool.observe(self.a, UsageObservation(limited=True, reset_at=NOW + 1200))

        retried = self.pool.acquire("codex", "s1", turn_start=False, exclude=[self.a])
        self.assertEqual(self.b, retried.token_id)
        self.assertEqual(self.b, self.pool.acquire("codex", "s2", turn_start=True).token_id)

        self.clock.now = NOW + 1201
        self.assertEqual([self.a], self.pool.recover())
        self.assertEqual(self.a, self.pool.acquire("codex", "s3", turn_start=True).token_id)
        # The moved conversation stays where it is while that token has usage left.
        self.assertEqual(self.b, self.pool.acquire("codex", "s1", turn_start=True).token_id)

    def test_window_reset_ends_draining(self) -> None:
        self.usage(self.a, 99, reset_in=300)
        self.assertTrue(self.store.snapshot().get(self.a).draining)

        self.clock.now = NOW + 301
        self.assertEqual([self.a], self.pool.recover())
        self.assertFalse(self.store.snapshot().get(self.a).draining)

    def test_when_every_token_drains_the_least_used_one_serves(self) -> None:
        self.usage(self.a, 99)
        self.usage(self.b, 97)

        self.assertEqual(self.b, self.pool.acquire("codex", "s9", turn_start=True).token_id)

    def test_other_provider_and_disabled_tokens_are_not_used(self) -> None:
        self.assertIsNone(self.pool.acquire("claude", "s1", turn_start=True))
        with self.store.transaction() as snapshot:
            for token in snapshot.tokens:
                token.status = "disabled"
        self.assertIsNone(self.pool.acquire("codex", "s1", turn_start=True))


if __name__ == "__main__":
    unittest.main()
