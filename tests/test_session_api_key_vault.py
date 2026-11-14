"""Session-scoped API keys: memory only, exact session and provider, CAS + idempotency.

Every key here is a random synthetic canary and no assertion message shows it.
"""

import json
import pickle
import secrets
import unittest
from unittest import mock

from ciel_runtime_support.api_key_cooldown import ApiKeyCooldownPorts, ApiKeyCooldownService
from ciel_runtime_support.router_rate_limit_service import RouterRateLimitService
from ciel_runtime_support.session_api_key_routing import (
    SESSION_API_KEY_SCOPE_MARKER,
    SessionApiKeyRouting,
    request_identity,
    session_scoped,
)
from ciel_runtime_support.session_api_key_vault import (
    SessionActivityRegistry,
    SessionApiKeyError,
    SessionApiKeyVault,
    binding_digest,
)

A = "claude:11111111-1111-4111-8111-111111111111"
B = "claude:22222222-2222-4222-8222-222222222222"


def canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.logs: list[str] = []
        self.vault = SessionApiKeyVault(clock=self.clock, log=lambda _level, message: self.logs.append(message))

    def put(self, session, provider, key, expected=0, token=None, ttl=None, fallback=None):
        return self.vault.put(session, provider, key, source="ctl", expected_version=expected,
                              idempotency_key=token or secrets.token_hex(8), ttl_seconds=ttl, fallback=fallback)

    def assert_absent(self, key: str, text: str, where: str) -> None:
        self.assertFalse(key in text, f"synthetic key present in {where}")

    def test_metadata_repr_and_logs_never_contain_the_key(self):
        key = canary()
        result = self.put(A, "deepseek", key)

        self.assert_absent(key, json.dumps(result), "the result")
        self.assert_absent(key, json.dumps(self.vault.metadata()), "the metadata")
        self.assert_absent(key, repr(self.vault) + repr(self.vault._entries), "repr")
        self.assert_absent(key, "\n".join(self.logs), "the log")
        self.assertEqual({"session", "provider", "source", "version", "injected_at", "expires_at", "fingerprint",
                          "fallback", "state", "end_proof"}, set(result["key"]))
        self.assertEqual(("fail_closed", "active"), (result["key"]["fallback"], result["key"]["state"]))
        with self.assertRaises(TypeError):
            pickle.dumps(self.vault)

    def test_exact_session_and_provider_only(self):
        a_deep, b_deep, a_zai = canary(), canary(), canary()
        self.put(A, "deepseek", a_deep)
        self.put(B, "deepseek", b_deep)
        self.put(A, "zai", a_zai)

        self.assertEqual(a_deep, self.vault.key_for(A, "deepseek"))
        self.assertEqual(b_deep, self.vault.key_for(B, "deepseek"))
        self.assertEqual(a_zai, self.vault.key_for(A, "zai"))
        self.assertIsNone(self.vault.key_for(B, "zai"))
        self.assertIsNone(self.vault.key_for("claude:other", "deepseek"))

    def test_versions_reject_stale_replace_and_clear(self):
        first = self.put(A, "deepseek", canary())["key"]["version"]
        with self.assertRaises(SessionApiKeyError) as again:
            self.put(A, "deepseek", canary())  # inject over an existing key
        self.assertEqual("version_conflict", again.exception.code)

        second = self.put(A, "deepseek", canary(), expected=first)["key"]["version"]
        self.assertGreater(second, first)
        with self.assertRaises(SessionApiKeyError) as stale:
            self.put(A, "deepseek", canary(), expected=first)  # a UI that saw the first key
        self.assertEqual("version_conflict", stale.exception.code)
        with self.assertRaises(SessionApiKeyError) as stale_clear:
            self.vault.clear(A, "deepseek", expected_version=first, idempotency_key="c1", source="ctl")
        self.assertEqual("version_conflict", stale_clear.exception.code)

        cleared = self.vault.clear(A, "deepseek", expected_version=second, idempotency_key="c2", source="ctl")
        self.assertEqual(second, cleared["key"]["version"])
        self.assertIsNone(self.vault.key_for(A, "deepseek"))

    def test_idempotency_key_replays_once_and_rejects_reuse(self):
        key = canary()
        first = self.put(A, "deepseek", key, token="same")
        replay = self.put(A, "deepseek", key, token="same")
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["key"]["version"], replay["key"]["version"])
        with self.assertRaises(SessionApiKeyError) as reuse:
            self.put(B, "deepseek", canary(), token="same")
        self.assertEqual("idempotency_conflict", reuse.exception.code)
        with self.assertRaises(SessionApiKeyError) as missing:
            self.vault.put(A, "zai", canary(), source="ctl", expected_version=0, idempotency_key="")
        self.assertEqual("idempotency_required", missing.exception.code)

    def test_expiry_drops_and_wipes_the_key(self):
        self.put(A, "deepseek", canary(), ttl=60)
        secret = self.vault._entries[(A, "deepseek")].secret
        self.clock.now += 61

        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        self.assertEqual([], self.vault.metadata())
        self.assertEqual(b"", bytes(secret))  # overwritten and emptied

    def test_clearing_one_leaves_the_other(self):
        b_key = canary()
        version = self.put(A, "deepseek", canary())["key"]["version"]
        self.put(B, "deepseek", b_key)
        self.vault.clear(A, "deepseek", expected_version=version, idempotency_key="x", source="ctl")

        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        self.assertEqual(b_key, self.vault.key_for(B, "deepseek"))

    def test_invalid_inputs_are_rejected(self):
        for session, provider, key, ttl in (
            ("not-a-session", "deepseek", canary(), None),
            (A, "Bad Provider", canary(), None),
            (A, "deepseek", "short", None),
            (A, "deepseek", "has space inside key", None),
            (A, "deepseek", canary(), 10),
            (A, "deepseek", canary(), 10**9),
        ):
            with self.subTest(session=session, provider=provider, ttl=ttl):
                with self.assertRaises(SessionApiKeyError):
                    self.put(session, provider, key, ttl=ttl)

    def test_launch_handoff_moves_once_and_only_for_its_launcher(self):
        key = canary()
        binding = binding_digest("/tmp/cc-socks-0/ciel-abc.sock", "epoch1")
        self.vault.stage_handoff(binding, "deepseek", key, source="remote-instructions", ttl_seconds=3600,
                                 launcher_pid=4242)
        with self.assertRaises(SessionApiKeyError):
            self.vault.stage_handoff(binding, "deepseek", canary(), source="remote-memory", ttl_seconds=3600,
                                     launcher_pid=4242)
        with self.assertRaises(SessionApiKeyError):
            self.vault.stage_handoff(binding_digest("x", "y"), "deepseek", canary(), source="ctl",
                                     ttl_seconds=3600, launcher_pid=1)

        self.assertIsNone(self.vault.consume_handoff(binding, A, launcher_pid=9999))  # another process
        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        moved = self.vault.consume_handoff(binding, A, launcher_pid=4242)
        self.assertEqual("remote-instructions", moved["source"])
        self.assertEqual(key, self.vault.key_for(A, "deepseek"))
        self.assertIsNone(self.vault.consume_handoff(binding, B, launcher_pid=4242))  # replay
        self.assertIsNone(self.vault.key_for(B, "deepseek"))

    def test_implicit_loss_fails_closed_unless_the_injector_chose_stored(self):
        self.put(A, "deepseek", canary(), ttl=60)  # default fallback: fail_closed
        self.put(B, "deepseek", canary(), ttl=60, fallback="stored")
        self.clock.now += 61
        self.assertEqual(("blocked", "expired"), self.vault.resolve(A, "deepseek"))
        self.assertEqual(("stored", ""), self.vault.resolve(B, "deepseek"))
        states = {row["session"]: row["state"] for row in self.vault.losses()}
        self.assertEqual({A: "blocked_expired", B: "fell_back_to_stored"}, states)
        self.assertEqual(("stored", ""), self.vault.resolve("claude:never-had-a-key", "deepseek"))

        # Operator clear (with the lost key's version) is the way back to the stored key.
        version = next(row["version"] for row in self.vault.losses() if row["session"] == A)
        with self.assertRaises(SessionApiKeyError):
            self.vault.clear(A, "deepseek", expected_version=version + 1, idempotency_key="c0", source="ctl")
        cleared = self.vault.clear(A, "deepseek", expected_version=version, idempotency_key="c1", source="ctl")
        self.assertEqual("cleared_stored_key_in_use", cleared["key"]["state"])
        self.assertEqual(("stored", ""), self.vault.resolve(A, "deepseek"))

        # Injecting again also ends a loss; a restart marker is recorded the same way.
        self.vault.record_loss(A, "zai", "restart", fallback="fail_closed", version=3, source="rest")
        self.assertEqual(("blocked", "restart"), self.vault.resolve(A, "zai"))
        key = canary()
        self.put(A, "zai", key)
        self.assertEqual(("key", key), self.vault.resolve(A, "zai"))
        self.assertNotIn((A, "zai"), {(row["session"], row["provider"]) for row in self.vault.losses()})
        with self.assertRaises(SessionApiKeyError):
            self.put(A, "zai", canary(), fallback="maybe")

    def test_restart_markers_fail_closed_after_restart_and_hold_no_secret(self):
        import tempfile
        from pathlib import Path

        from ciel_runtime_support.session_api_key_markers import MARKER_FILE, SessionKeyMarkerStore

        with tempfile.TemporaryDirectory() as directory:
            store = SessionKeyMarkerStore(Path(directory) / MARKER_FILE)
            self.vault.attach_markers(store, launcher_state=lambda _pid, _token: "alive")
            keys = [canary() for _ in range(4)]
            self.put(A, "deepseek", keys[0])                       # fail_closed, live at "restart"
            self.put(B, "deepseek", keys[1], fallback="stored")    # stored, live at "restart"
            self.put(A, "zai", keys[2], ttl=60)                    # fail_closed, expires before restart
            binding = binding_digest("/tmp/s9", "e9")
            self.vault.stage_handoff(binding, "deepseek", keys[3], source="remote-memory", ttl_seconds=3600,
                                     launcher_pid=99, launcher_identity="99000")
            self.vault.consume_handoff(binding, "claude:launched", 99, "99000")
            raw = (Path(directory) / MARKER_FILE).read_text(encoding="utf-8")
            for key in keys:
                self.assertNotIn(key, raw)
            for fingerprint in (row["fingerprint"] for row in self.vault.metadata()):
                self.assertNotIn(fingerprint, raw)

            # A new router process: a new vault over the same instance dir.
            self.clock.now += 120
            fresh = SessionApiKeyVault(clock=self.clock)
            restored = fresh.attach_markers(store, launcher_state=lambda pid, _token: "ended" if pid == 99 else "alive")
            self.assertEqual(3, restored)  # the launched session ended with its launcher
            self.assertEqual(("blocked", "restart"), fresh.resolve(A, "deepseek"))
            self.assertEqual(("blocked", "expired"), fresh.resolve(A, "zai"))
            self.assertEqual(("stored", ""), fresh.resolve(B, "deepseek"))
            self.assertEqual(("stored", ""), fresh.resolve("claude:launched", "deepseek"))
            states = {(row["session"], row["provider"]): row["state"] for row in fresh.losses()}
            self.assertEqual("fell_back_to_stored", states[(B, "deepseek")])

            # Operator clear ends the marker; a third process no longer blocks.
            version = next(row["version"] for row in fresh.losses() if row["session"] == A and
                           row["provider"] == "deepseek")
            fresh.clear(A, "deepseek", expected_version=version, idempotency_key="c9", source="ctl")
            third = SessionApiKeyVault(clock=self.clock)
            third.attach_markers(store, launcher_state=lambda _pid, _token: "alive")
            self.assertEqual(("stored", ""), third.resolve(A, "deepseek"))
            self.assertEqual(("blocked", "expired"), third.resolve(A, "zai"))

            # Age never ends a block: a week later the session is still blocked.
            self.clock.now += 7 * 24 * 3600
            fourth = SessionApiKeyVault(clock=self.clock)
            fourth.attach_markers(store, launcher_state=lambda _pid, _token: "alive")
            self.assertEqual(("blocked", "expired"), fourth.resolve(A, "zai"))
            raw = (Path(directory) / MARKER_FILE).read_text(encoding="utf-8")
            self.assertNotIn(binding, raw)
            for key in keys:
                self.assertNotIn(key, raw)

    def test_unreadable_marker_file_blocks_every_session_without_a_key_until_reset(self):
        import json as _json
        import tempfile
        from pathlib import Path

        from ciel_runtime_support.session_api_key_markers import MARKER_FILE, MAX_FILE_BYTES, SessionKeyMarkerStore

        good = {"session": A, "provider": "deepseek", "state": "active", "fallback": "fail_closed", "version": 1}
        cases = {
            "truncated": '{"version": 1, "markers": [',
            "extra_field": _json.dumps({"version": 1, "markers": [{**good, "api_key": "x" * 12}]}),
            "wrong_type": _json.dumps({"version": 1, "markers": [{**good, "version": "1"}]}),
            "oversize": " " * (MAX_FILE_BYTES + 1),
        }
        for name, text in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / MARKER_FILE
                path.write_text(text, encoding="utf-8")
                vault = SessionApiKeyVault(clock=self.clock)
                vault.attach_markers(SessionKeyMarkerStore(path), launcher_state=lambda _pid, _token: "alive")
                self.assertTrue(vault.state_unreadable())
                self.assertEqual(("blocked", "state_unreadable"), vault.resolve(B, "zai"))
                key = canary()
                vault.put(B, "zai", key, source="ctl", expected_version=0, idempotency_key="k")
                self.assertEqual(("key", key), vault.resolve(B, "zai"))  # a live key still works
                self.assertEqual(text, path.read_text(encoding="utf-8"))  # evidence not overwritten
                self.assertEqual(("blocked", "state_unreadable"), vault.resolve(A, "deepseek"))
                vault.reset_marker_state("ctl")
                self.assertEqual(("stored", ""), vault.resolve(A, "deepseek"))
                self.assertEqual(1, len(list(Path(directory).glob(MARKER_FILE + ".corrupt-*"))))
                self.assertNotIn(key, path.read_text(encoding="utf-8"))

    def test_full_state_refuses_a_new_key_instead_of_pruning_a_block(self):
        from ciel_runtime_support import session_api_key_vault as module

        with mock.patch.object(module, "MAX_STATE_ROWS", 3):
            for index in range(3):
                self.put(f"claude:s{index}", "deepseek", canary(), ttl=60)
            self.clock.now += 61  # all three become fail_closed losses
            self.assertEqual(("blocked", "expired"), self.vault.resolve("claude:s0", "deepseek"))
            with self.assertRaises(SessionApiKeyError) as caught:
                self.put("claude:s3", "deepseek", canary())
            self.assertEqual("capacity", caught.exception.code)
            message = str(caught.exception)
            for needle in ("3 of 3", "nothing was pruned", "clear", "ended", "session-api-key list"):
                self.assertIn(needle, message)
            self.assertEqual({"rows": 3, "limit": 3}, {k: self.vault.health()[k] for k in ("rows", "limit")})
            for index in range(3):
                self.assertEqual(("blocked", "expired"), self.vault.resolve(f"claude:s{index}", "deepseek"))
            self.put("claude:s0", "deepseek", canary())  # re-inject into a blocked slot is allowed

    def test_session_end_needs_pid_and_creation_token_and_a_reused_pid_is_not_the_launcher(self):
        from ciel_runtime_support import session_api_key_vault as module

        tokens = {4242: "1000"}  # pid -> current creation token; missing pid = exited
        with mock.patch("ciel_runtime_support.workspace_router_selection._process_identity",
                        side_effect=lambda pid: tokens.get(pid, "")):
            self.assertEqual("alive", module.launcher_state(4242, "1000"))
            tokens[4242] = "2000"  # the pid now names another process
            self.assertEqual("ended", module.launcher_state(4242, "1000"))
            del tokens[4242]
            self.assertEqual("ended", module.launcher_state(4242, "1000"))
            self.assertEqual("unknown", module.launcher_state(4242, ""))  # no token: never ended
            self.assertEqual("unknown", module.launcher_state(0, "1000"))
        with mock.patch("ciel_runtime_support.workspace_router_selection._process_identity", return_value=None):
            self.assertEqual("unknown", module.launcher_state(4242, "1000"))  # inspection failed

        # A staged key cannot be claimed by a process that reused the launcher's pid.
        binding = binding_digest("/tmp/r", "er")
        self.vault.stage_handoff(binding, "deepseek", canary(), source="remote-memory", ttl_seconds=3600,
                                 launcher_pid=4242, launcher_identity="1000")
        self.assertIsNone(self.vault.consume_handoff(binding, A, 4242, "2000"))
        self.assertIsNotNone(self.vault.consume_handoff(binding, A, 4242, "1000"))

        # The session's records end only with that exact launcher, never with a reused pid.
        states = {("4242", "1000"): "alive"}
        state = lambda pid, token: states.get((str(pid), token), "unknown")  # noqa: E731
        self.clock.now += 1
        self.assertEqual(0, self.vault.sweep_ended(state, min_interval=0))
        self.assertIsNotNone(self.vault.key_for(A, "deepseek"))
        states[("4242", "1000")] = "unknown"  # cannot inspect: nothing ends
        self.assertEqual(0, self.vault.sweep_ended(state, min_interval=0))
        self.assertIsNotNone(self.vault.key_for(A, "deepseek"))
        states[("4242", "1000")] = "ended"  # exited or pid reused
        self.assertEqual(1, self.vault.sweep_ended(state, min_interval=0))
        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        self.assertEqual([], self.vault.losses())  # session over: no block, no marker
        self.assertEqual(("stored", ""), self.vault.resolve(A, "deepseek"))

    def test_ended_sessions_free_their_rows_at_the_next_observation(self):
        for index in range(3):
            binding = binding_digest(f"/tmp/e{index}", f"e{index}")
            self.vault.stage_handoff(binding, "deepseek", canary(), source="remote-memory", ttl_seconds=60,
                                     launcher_pid=500 + index, launcher_identity="1")
            self.vault.consume_handoff(binding, f"claude:e{index}", 500 + index, "1")
        self.clock.now += 61  # all three expire: fail_closed losses tied to their launchers
        self.assertEqual(3, len(self.vault.losses()))
        alive = {500}
        self.vault.sweep_ended(lambda pid, _token: "alive" if pid in alive else "ended", min_interval=0)
        self.assertEqual(["claude:e0"], [row["session"] for row in self.vault.losses()])

    def test_second_writer_is_refused_before_and_after_the_claim_with_no_detail(self):
        binding = binding_digest("/tmp/s2", "e2")
        self.vault.stage_handoff(binding, "deepseek", canary(), source="remote-memory", ttl_seconds=3600,
                                 launcher_pid=7)
        unknown = binding_digest("/tmp/none", "none")
        refusals = []
        for target in (binding, "not-a-binding"):  # before the claim, and a malformed one
            with self.assertRaises(SessionApiKeyError) as caught:
                self.vault.stage_handoff(target, "deepseek", canary(), source="remote-memory", ttl_seconds=3600,
                                         launcher_pid=7)
            refusals.append((caught.exception.code, str(caught.exception)))
        self.assertIsNotNone(self.vault.consume_handoff(binding, A, launcher_pid=7))
        with self.assertRaises(SessionApiKeyError) as caught:  # after the claim
            self.vault.stage_handoff(binding, "deepseek", canary(), source="remote-memory", ttl_seconds=3600,
                                     launcher_pid=7)
        refusals.append((caught.exception.code, str(caught.exception)))
        self.assertEqual({("handoff_refused", "the launch handoff was refused")}, set(refusals))
        self.assertTrue(self.vault.stage_handoff(unknown, "deepseek", canary(), source="remote-memory",
                                                 ttl_seconds=3600, launcher_pid=7)["ok"])

    def test_rebound_scope_abandons_the_staged_key(self):
        first, second = binding_digest("/tmp/a", "e1"), binding_digest("/tmp/b", "e2")
        self.vault.stage_handoff(first, "deepseek", canary(), source="remote-instructions", ttl_seconds=3600,
                                 launcher_pid=7, scope={"epoch": "e1", "messaging_socket_path": "/tmp/a"})
        self.vault.stage_handoff(second, "deepseek", canary(), source="remote-instructions", ttl_seconds=3600,
                                 launcher_pid=8, scope={"epoch": "e2", "messaging_socket_path": "/tmp/b"})
        self.assertEqual(0, self.vault.abandon_rebound("e1"[:0]))  # unknown current scope: nothing happens
        self.assertEqual(1, self.vault.abandon_rebound("e2"))
        self.assertFalse(self.vault.pending_handoff(first))
        self.assertTrue(self.vault.pending_handoff(second))
        (row,) = self.vault.abandoned()
        self.assertEqual("abandoned_scope_rebound", row["state"])
        self.assertNotIn("_scope", row)
        self.assertEqual([{"epoch": "e1", "messaging_socket_path": "/tmp/a"}],
                         self.vault.abandoned_scopes("deepseek"))
        self.assertIsNone(self.vault.consume_handoff(first, A, launcher_pid=7))
        self.clock.now += 3600
        self.assertEqual([], self.vault.abandoned())

    def test_unclaimed_handoff_lives_for_the_key_ttl_and_shows_as_staged(self):
        binding = binding_digest("/tmp/s", "e")
        staged = self.vault.stage_handoff(binding, "deepseek", canary(), source="remote-memory", ttl_seconds=3600,
                                          launcher_pid=1)
        self.assertEqual(3600, staged["expires_in"])
        (row,) = self.vault.staged()
        self.assertEqual("staged_awaiting_first_request", row["state"])
        self.assertEqual(self.clock.now + 3600, row["expires_at"])
        self.assertNotIn("binding", row)
        # No fixed 300 s window: still waiting well past it.
        self.clock.now += 1800
        self.assertTrue(self.vault.pending_handoff(binding))
        self.clock.now += 1800
        self.assertIsNone(self.vault.consume_handoff(binding, A, launcher_pid=1))
        self.assertFalse(self.vault.has_handoffs())
        self.assertEqual([], self.vault.staged())


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault = SessionApiKeyVault()
        self.bound: dict | None = None
        self.running = {77}
        self.routing = SessionApiKeyRouting(vault=self.vault, activity=SessionActivityRegistry(),
                                            launch_binding=lambda _headers, _body: self.bound,
                                            launcher_state=lambda pid, _token: "alive" if pid in self.running else "ended")

    def test_request_identity(self):
        self.assertEqual(A, request_identity("/v1/messages", {"x-claude-code-session-id": A[7:]}, {}))
        self.assertEqual("codex:thread-1", request_identity("/v1/responses", {}, {"prompt_cache_key": "thread-1"}))
        self.assertEqual("", request_identity("/v1/responses", {}, {}))
        self.assertEqual("", request_identity("/v1/messages", {}, {}))
        self.assertEqual("", request_identity("/v1/chat/completions", {"x-claude-code-session-id": "x"}, {}))
        self.assertEqual("", request_identity("/v1/messages", {"x-claude-code-session-id": "bad id;"}, {}))

    def test_overlay_uses_only_the_exact_session_and_provider(self):
        key = canary()
        stored = {"api_key": "stored-key-value", "base_url": "https://example.test"}
        self.vault.put(A, "deepseek", key, source="ctl", expected_version=0, idempotency_key="k")

        mine = self.routing.overlay(A, "deepseek", stored)
        self.assertEqual([key], mine["api_keys"])
        self.assertEqual(A, session_scoped(mine))
        self.assertIs(stored, self.routing.overlay(B, "deepseek", stored))
        self.assertIs(stored, self.routing.overlay(A, "zai", stored))
        self.assertIs(stored, self.routing.overlay("", "deepseek", stored))
        self.assertNotIn(SESSION_API_KEY_SCOPE_MARKER, stored)

        bridge = {**stored, "_ciel_remote_bridge_request": True}
        self.assertNotIn(SESSION_API_KEY_SCOPE_MARKER, self.routing.overlay(A, "deepseek", bridge))

        # A later overlay (failover, retry) after the key was cleared falls back
        # to the stored config itself, never to the copy holding the session key.
        version = self.vault.metadata()[0]["version"]
        self.vault.clear(A, "deepseek", expected_version=version, idempotency_key="c", source="ctl")
        again = self.routing.overlay(A, "deepseek", mine)
        self.assertEqual("stored-key-value", again["api_key"])
        self.assertNotIn("api_keys", again)
        self.assertEqual("", session_scoped(again))

    def test_launch_key_moves_only_for_the_bound_launch(self):
        key = canary()
        socket, epoch = "/tmp/cc-socks-0/ciel-1.sock", "e1"
        self.vault.stage_handoff(binding_digest(socket, epoch), "deepseek", key, source="remote-instructions",
                                 ttl_seconds=3600, launcher_pid=77)
        headers = {"x-claude-code-session-id": A[7:]}

        self.bound = None  # launch not bound yet
        self.routing.identity("/v1/messages", headers, {}, "deepseek")
        self.bound = {"session_id": A[7:], "epoch": epoch, "socket": "/tmp/cc-socks-0/other.sock", "launcher_pid": 77}
        self.routing.identity("/v1/messages", headers, {}, "deepseek")
        self.bound = {"session_id": B[7:], "epoch": epoch, "socket": socket, "launcher_pid": 77}
        self.routing.identity("/v1/messages", headers, {}, "deepseek")  # bound to another session
        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        self.assertTrue(self.vault.has_handoffs())

        self.bound = {"session_id": A[7:], "epoch": epoch, "socket": socket, "launcher_pid": 77}
        self.assertEqual(A, self.routing.identity("/v1/messages", headers, {}, "deepseek"))
        self.assertEqual(key, self.vault.key_for(A, "deepseek"))
        self.assertFalse(self.vault.has_handoffs())

    def test_blocked_session_raises_an_explicit_error_and_never_uses_the_stored_key(self):
        from ciel_runtime_support.session_api_key_routing import SessionApiKeyUnavailable

        clock = Clock()
        vault = SessionApiKeyVault(clock=clock)
        routing = SessionApiKeyRouting(vault=vault, activity=SessionActivityRegistry())
        stored = {"api_key": "stored-key-value"}
        vault.put(A, "deepseek", canary(), source="rest", expected_version=0, idempotency_key="i", ttl_seconds=60)
        vault.put(B, "deepseek", canary(), source="rest", expected_version=0, idempotency_key="j", ttl_seconds=60,
                  fallback="stored")
        clock.now += 61
        with self.assertRaises(SessionApiKeyUnavailable) as caught:
            routing.overlay(A, "deepseek", stored)
        self.assertEqual("expired", caught.exception.cause)
        self.assertIn("expired", str(caught.exception))
        self.assertNotIn("stored-key-value", str(caught.exception))
        self.assertIs(stored, routing.overlay(B, "deepseek", stored))  # opted in to the stored key
        self.assertIs(stored, routing.overlay("claude:other", "deepseek", stored))  # never had a key

    def test_staged_key_dies_with_its_launcher(self):
        socket, epoch = "/tmp/cc-socks-0/ciel-2.sock", "e2"
        self.vault.stage_handoff(binding_digest(socket, epoch), "deepseek", canary(), source="remote-memory",
                                 ttl_seconds=3600, launcher_pid=88)  # 88 is not running
        self.bound = {"session_id": A[7:], "epoch": epoch, "socket": socket, "launcher_pid": 88}
        self.routing.identity("/v1/messages", {"x-claude-code-session-id": A[7:]}, {}, "deepseek")
        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        self.assertFalse(self.vault.has_handoffs())


class PersistenceIsolationTests(unittest.TestCase):
    """A session key's cooldown and rate state never reach the shared files."""

    def scoped(self):
        return {"api_key": "x", "api_keys": ["x"], SESSION_API_KEY_SCOPE_MARKER: A, "base_url": "https://u.test"}

    def test_cooldown_of_a_session_key_is_not_written_to_the_repository(self):
        repository = mock.Mock()
        service = ApiKeyCooldownService(ApiKeyCooldownPorts(
            repository=repository, rotation_name=lambda provider, _c: provider, config_keys=lambda _p, c: c["api_keys"],
            meaningful_key=lambda key: bool(key), log=lambda *_a: None))
        service.register("deepseek", self.scoped(), "x", {"retry-after": "30"})
        service.cooldown_until("deepseek", self.scoped(), "x")

        repository.register_cooldown.assert_not_called()
        repository.cooldown_until.assert_not_called()

    def test_rate_limit_state_treats_a_session_key_as_request_scoped(self):
        self.assertTrue(RouterRateLimitService.request_scoped(self.scoped()))
        self.assertEqual("deepseek:__request_scoped__", RouterRateLimitService.key("deepseek", self.scoped()))
        self.assertFalse(RouterRateLimitService.request_scoped({"api_key": "x"}))


if __name__ == "__main__":
    unittest.main()
