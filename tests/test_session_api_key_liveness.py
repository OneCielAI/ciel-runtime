"""A directly injected session key ends with its session's own process, or stays visible.

Team-lead ruling (2026-11-13): tie a direct inject to the session's own
liveness proof (Claude Code's published record: pid + creation token) when one
exists and drop it when that process ended; with no proof keep it ("no
session-end proof: clear manually", with its age); every key has a TTL
(24 h when none is given) and then becomes an expired block.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.session_api_key_liveness import session_owner
from ciel_runtime_support.session_api_key_vault import DEFAULT_TTL_SECONDS, SessionApiKeyVault

UUID = "5e55a9e1-0000-4000-8000-000000000001"
S = f"claude:{UUID}"


def canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class LivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = Path(self.temp.name) / "sessions"
        self.sessions.mkdir()
        self.processes: dict[tuple[int, str], str] = {}
        self.clock = Clock()
        self.vault = SessionApiKeyVault(clock=self.clock)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def state(self, pid: int, token: str) -> str:
        return self.processes.get((pid, token), "unknown")

    def owner(self, session: str):
        return session_owner(session, lambda: [self.sessions], self.state)

    def publish(self, pid: int, token: str, session_id: str = UUID) -> None:
        (self.sessions / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "procStart": token, "sessionId": session_id, "cwd": "/w"}), encoding="utf-8")

    def inject(self, session: str = S, ttl=None) -> dict:
        return self.vault.put(session, "deepseek", canary(), source="ctl", expected_version=0,
                              idempotency_key=secrets.token_hex(6), ttl_seconds=ttl, owner=self.owner(session))

    def sweep(self) -> int:
        return self.vault.sweep_ended(self.state, min_interval=0, session_owner=self.owner)

    def test_owner_only_from_exactly_one_live_record_of_that_session(self):
        self.assertIsNone(self.owner(S))  # no record
        self.publish(10, "111")
        self.assertIsNone(self.owner(S))  # record, but its process cannot be inspected
        self.processes[(10, "111")] = "ended"
        self.assertIsNone(self.owner(S))  # stale record of an exited process
        self.processes[(10, "111")] = "alive"
        self.assertEqual((10, "111"), self.owner(S))
        self.publish(11, "222")
        self.processes[(11, "222")] = "alive"
        self.assertIsNone(self.owner(S))  # two live processes claim it: no proof
        self.assertIsNone(self.owner("codex:thread-1"))  # Codex publishes no such record
        self.publish(12, "333", session_id="other")
        self.processes[(12, "333")] = "alive"
        self.assertIsNone(self.owner("claude:other-than-that"))

    def test_direct_inject_with_proof_ends_when_that_process_ends(self):
        self.publish(10, "111")
        self.processes[(10, "111")] = "alive"
        key = self.inject()["key"]
        self.assertEqual("session_record", key["end_proof"])
        self.assertEqual(0, self.sweep())
        self.processes[(10, "111")] = "ended"  # exited (or the pid now has another token)
        (self.sessions / "10.json").unlink()  # the record may vanish too; the captured proof stays
        self.assertEqual(1, self.sweep())
        self.assertEqual([], self.vault.metadata())
        self.assertEqual([], self.vault.losses())  # session over: no block left behind

    def test_direct_inject_without_proof_stays_and_shows_its_age(self):
        key = self.inject()["key"]
        self.assertEqual("none", key["end_proof"])
        self.clock.now += 3600
        self.assertEqual(0, self.sweep())  # unknown never ends a row
        (row,) = self.vault.metadata()
        self.assertEqual(("none", 3600.0), (row["end_proof"], self.clock.now - row["injected_at"]))

    def test_proof_captured_later_when_the_record_appears(self):
        self.inject()
        self.publish(10, "111")
        self.processes[(10, "111")] = "alive"
        self.sweep()
        self.assertEqual("session_record", self.vault.metadata()[0]["end_proof"])
        self.processes[(10, "111")] = "ended"
        self.assertEqual(1, self.sweep())
        self.assertEqual([], self.vault.metadata())

    def test_every_direct_inject_has_a_ttl_and_then_blocks_as_expired(self):
        key = self.inject()["key"]  # no TTL given
        self.assertEqual(DEFAULT_TTL_SECONDS, 24 * 3600)
        self.assertEqual(self.clock.now + 24 * 3600, key["expires_at"])
        self.clock.now += 24 * 3600
        self.assertEqual(("blocked", "expired"), self.vault.resolve(S, "deepseek"))
        (loss,) = self.vault.losses()
        self.assertEqual(("blocked_expired", "none"), (loss["state"], loss["end_proof"]))
        self.assertEqual(0, self.sweep())  # still no proof: the block stays until cleared
        self.assertEqual(("blocked", "expired"), self.vault.resolve(S, "deepseek"))


if __name__ == "__main__":
    unittest.main()
