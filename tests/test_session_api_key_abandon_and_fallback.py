"""A rebound launch scope abandons a staged startup key; remote startup may opt into fallback=stored.

Found by the S4 desk trace (2026-11-13): ``abandon_rebound`` had no caller and
the startup key carried no fallback.  These tests drive the router-side
wiring (scope probe, router-derived handoff scope, session mapping through
the published record) and the startup parsing.
"""

import email.message
import io
import secrets
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.session_api_key_http import HANDOFF_PATH, SessionApiKeyHttpController, SessionApiKeyHttpPorts
from ciel_runtime_support.session_api_key_routing import SessionApiKeyRouting, SessionApiKeyUnavailable
from ciel_runtime_support.session_api_key_vault import SessionActivityRegistry, SessionApiKeyVault, binding_digest
from ciel_runtime_support.startup_session_key import StartupSessionKeyHolder

UUID_A = "11111111-1111-4111-8111-111111111111"
A = "claude:" + UUID_A
TOKEN = "admin-" + "t" * 40
FALLBACK_HEADER = "X-Ciel-Session-Api-Key-Fallback"
SCOPE_1 = {"epoch": "e1", "messaging_socket_path": "/tmp/first.sock", "cwd": "/w", "started_at": 1.0}
SCOPE_2 = {"epoch": "e2", "messaging_socket_path": "/tmp/second.sock", "cwd": "/w", "started_at": 2.0}


def canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


def binding(scope: dict) -> str:
    return binding_digest(scope["messaging_socket_path"], scope["epoch"])


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class AbandonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.vault = SessionApiKeyVault(clock=self.clock)
        self.current = {"scope": dict(SCOPE_1)}
        self.vault.set_scope_probe(lambda: str(self.current["scope"].get("epoch") or ""))
        # The published record of the first launch's socket names session A.
        self.routing = SessionApiKeyRouting(
            vault=self.vault, activity=SessionActivityRegistry(),
            launcher_state=lambda _pid, _token: "alive",
            scope_session=lambda scope: UUID_A if scope.get("epoch") == "e1" else "")

    def stage_first(self, fallback=None) -> None:
        self.vault.stage_handoff(binding(SCOPE_1), "deepseek", canary(), source="remote-instructions",
                                 ttl_seconds=3600, launcher_pid=7, launcher_identity="1", scope=dict(SCOPE_1),
                                 fallback=fallback)

    def request(self) -> str:
        return self.routing.identity("/v1/messages", {"x-claude-code-session-id": UUID_A}, {}, "deepseek")

    def test_a_rebound_scope_shows_abandoned_on_every_status_read_and_blocks_its_session(self):
        self.stage_first()
        self.assertEqual(1, len(self.vault.staged()))
        self.current["scope"] = dict(SCOPE_2)  # a second launch bound the workspace scope
        self.assertEqual([], self.vault.staged())  # status reads apply the rebound
        (row,) = self.vault.abandoned()
        self.assertEqual("abandoned_scope_rebound", row["state"])
        identity = self.request()
        with self.assertRaises(SessionApiKeyUnavailable) as caught:
            self.routing.overlay(identity, "deepseek", {"api_key": "stored-key-value"})
        self.assertIn("abandoned", str(caught.exception))
        (loss,) = self.vault.losses()
        self.assertEqual((A, "blocked_abandoned"), (loss["session"], loss["state"]))

    def test_operator_clear_sticks_and_the_mapping_happens_once(self):
        self.stage_first()
        self.current["scope"] = dict(SCOPE_2)
        identity = self.request()
        (loss,) = self.vault.losses()
        self.vault.clear(A, "deepseek", source="ctl", expected_version=loss["version"], idempotency_key="c1")
        identity = self.request()
        stored = {"api_key": "stored-key-value"}
        self.assertIs(stored, self.routing.overlay(identity, "deepseek", stored))
        self.assertEqual([], self.vault.losses())

    def test_fallback_stored_uses_the_stored_key_and_says_so(self):
        self.stage_first(fallback="stored")
        self.current["scope"] = dict(SCOPE_2)
        identity = self.request()
        stored = {"api_key": "stored-key-value"}
        self.assertIs(stored, self.routing.overlay(identity, "deepseek", stored))
        (loss,) = self.vault.losses()
        self.assertEqual("fell_back_to_stored", loss["state"])

    def test_another_session_is_never_mapped_to_the_abandoned_key(self):
        self.stage_first()
        self.current["scope"] = dict(SCOPE_2)
        other = "22222222-2222-4222-8222-222222222222"
        identity = self.routing.identity("/v1/messages", {"x-claude-code-session-id": other}, {}, "deepseek")
        stored = {"api_key": "stored-key-value"}
        self.assertIs(stored, self.routing.overlay(identity, "deepseek", stored))
        self.assertEqual([], self.vault.losses())


class HandoffScopeTests(unittest.TestCase):
    def test_the_router_attaches_its_own_scope_and_ignores_a_client_scope(self):
        vault = SessionApiKeyVault()
        responses = []
        controller = SessionApiKeyHttpController(SessionApiKeyHttpPorts(
            write_json=lambda _handler, payload, status=200: responses.append((payload, status)),
            admin_token=lambda: TOKEN, session_allowed=lambda _handler: False,
            workspace_state_dir=lambda: __import__("pathlib").Path(__import__("tempfile").mkdtemp()),
            current_provider=lambda: "deepseek", vault=vault, activity=SessionActivityRegistry(),
            launch_scope=lambda value: dict(SCOPE_1) if value == binding(SCOPE_1) else None))
        handler = SimpleNamespace(client_address=("127.0.0.1", 5), command="POST", wfile=io.BytesIO(),
                                  headers={"host": "127.0.0.1:1", "authorization": f"Bearer {TOKEN}"})
        controller.handle_post(handler, HANDOFF_PATH, {
            "binding": binding(SCOPE_1), "provider": "deepseek", "api_key": canary(), "ttl_seconds": 3600,
            "source": "remote-instructions", "launcher_pid": 7, "launcher_identity": "1",
            "scope": {"epoch": "forged"}})
        self.assertEqual(200, responses[-1][1], responses[-1])
        vault.abandon_rebound("e2")
        self.assertEqual([SCOPE_1], vault.abandoned_scopes("deepseek"))


def _headers(values: dict) -> email.message.Message:
    message = email.message.Message()
    for name, value in values.items():
        message[name] = value
    return message


class StartupFallbackTests(unittest.TestCase):
    def offer(self, extra: dict) -> StartupSessionKeyHolder:
        holder = StartupSessionKeyHolder()
        nonce = holder.begin_launch()
        holder.accept_headers(_headers({"X-Ciel-Session-Api-Key": canary(),
                                        "X-Ciel-Session-Api-Key-Provider": "deepseek",
                                        "X-Ciel-Session-Api-Key-Expires-In": "3600",
                                        "X-Ciel-Session-Api-Key-Binding": nonce, **extra}),
                              configured_url="https://p.example/c.md", final_url="https://p.example/c.md",
                              authorized=True)
        return holder

    def test_header_and_manifest_choose_the_fallback(self):
        self.assertEqual("fail_closed", self.offer({}).take().fallback)
        self.assertEqual("stored", self.offer({FALLBACK_HEADER: "stored"}).take().fallback)
        holder = StartupSessionKeyHolder()
        nonce = holder.begin_launch()
        holder.accept_manifest_field({"provider": "deepseek", "api_key": canary(), "expires_in": 3600,
                                      "binding": nonce, "fallback": "stored"},
                                     configured_url="https://m.example/m.json", final_url="https://m.example/m.json",
                                     authorized=True)
        self.assertEqual("stored", holder.take().fallback)

    def test_an_unknown_fallback_rejects_the_key(self):
        logs = []
        holder = StartupSessionKeyHolder(log=lambda _level, message: logs.append(message))
        nonce = holder.begin_launch()
        holder.accept_headers(_headers({"X-Ciel-Session-Api-Key": canary(),
                                        "X-Ciel-Session-Api-Key-Provider": "deepseek",
                                        "X-Ciel-Session-Api-Key-Expires-In": "3600",
                                        "X-Ciel-Session-Api-Key-Binding": nonce, FALLBACK_HEADER: "maybe"}),
                              configured_url="https://p.example/c.md", final_url="https://p.example/c.md",
                              authorized=True)
        self.assertFalse(holder.available_for("deepseek"))
        self.assertIn("startup_session_key_rejected source=remote-instructions reason=invalid_fallback", logs)

    def test_the_launcher_hands_the_fallback_to_its_router(self):
        import ciel_runtime
        from ciel_runtime_support.startup_session_key import StartupSessionKey

        sent = []
        client = SimpleNamespace(handoff=lambda payload: sent.append({k: v for k, v in payload.items()
                                                                      if k != "api_key"}))
        key = StartupSessionKey("deepseek", bytearray(canary().encode()), time.time() + 3600, "remote-instructions",
                                fallback="stored")
        keys = ciel_runtime._SESSION_API_KEYS
        with mock.patch.object(keys.holder, "take", return_value=key), \
                mock.patch.object(type(keys), "client", lambda _self: client), \
                mock.patch.object(keys.compaction, "bound_epoch", "e1"):
            # The holder's stager is the composition's own stage (what run_claude calls).
            self.assertEqual("staged", keys.holder.stage("deepseek", "/tmp/s.sock", True))
        self.assertEqual("stored", sent[0]["fallback"])
        self.assertNotIn("scope", sent[0])  # the router derives the scope itself


if __name__ == "__main__":
    unittest.main()
