"""REST for session API keys: access rules, CAS, no key in any response or audit line."""

import io
import json
import secrets
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.session_api_key_http import (
    HANDOFF_PATH,
    KEYS_PATH,
    SessionApiKeyHttpController,
    SessionApiKeyHttpPorts,
)
from ciel_runtime_support.session_api_key_vault import SessionActivityRegistry, SessionApiKeyVault, binding_digest

A = "claude:11111111-1111-4111-8111-111111111111"
TOKEN = "admin-" + "t" * 40


def canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


class Handler(SimpleNamespace):
    pass


class SessionApiKeyHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.vault = SessionApiKeyVault()
        self.activity = SessionActivityRegistry()
        self.responses: list[tuple[dict, int]] = []
        self.signed_in = False
        self.controller = SessionApiKeyHttpController(SessionApiKeyHttpPorts(
            write_json=lambda _handler, payload, status=200: self.responses.append((payload, status)),
            admin_token=lambda: TOKEN,
            session_allowed=lambda _handler: self.signed_in,
            workspace_state_dir=lambda: self.state,
            current_provider=lambda: "deepseek",
            vault=self.vault,
            activity=self.activity,
        ))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def handler(self, *, peer="127.0.0.1", token=None, origin=None, forwarded=False, idem=None):
        headers = {"host": "127.0.0.1:18080", "content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        if origin:
            headers["origin"] = origin
        if forwarded:
            headers["x-forwarded-for"] = "203.0.113.9"
        headers["idempotency-key"] = idem or secrets.token_hex(8)
        return Handler(client_address=(peer, 50000), headers=headers, command="POST", wfile=io.BytesIO())

    def post(self, body, **options):
        self.controller.handle_post(self.handler(**options), KEYS_PATH, body)
        return self.responses[-1]

    def everything_written(self) -> str:
        audit = self.state / "management-audit.jsonl"
        return json.dumps(self.responses) + (audit.read_text(encoding="utf-8") if audit.exists() else "")

    def test_inject_inspect_replace_clear_with_versions(self):
        first, second = canary(), canary()
        body = {"action": "inject", "session": A, "provider": "deepseek", "api_key": first, "ttl_seconds": 600}
        payload, status = self.post(body, token=TOKEN)
        self.assertEqual(200, status, payload)
        self.assertNotIn("api_key", body)  # taken out of the parsed body
        version = payload["key"]["version"]

        self.controller.handle_get(self.handler(token=TOKEN), KEYS_PATH)
        listed, _ = self.responses[-1]
        self.assertEqual([version], [row["version"] for row in listed["keys"]])

        stale, status = self.post({"action": "replace", "session": A, "provider": "deepseek",
                                   "api_key": second, "expected_version": version + 5}, token=TOKEN)
        self.assertEqual((409, "version_conflict"), (status, stale["error"]))
        replaced, status = self.post({"action": "replace", "session": A, "provider": "deepseek",
                                      "api_key": second, "expected_version": version}, token=TOKEN)
        self.assertEqual(200, status, replaced)
        self.assertEqual(second, self.vault.key_for(A, "deepseek"))

        cleared, status = self.post({"action": "clear", "session": A, "provider": "deepseek",
                                     "expected_version": replaced["key"]["version"]}, token=TOKEN)
        self.assertEqual(200, status, cleared)
        self.assertIsNone(self.vault.key_for(A, "deepseek"))
        written = self.everything_written()
        self.assertFalse(first in written or second in written, "synthetic key in a response or the audit")

    def test_access_rules(self):
        # Team-lead ruling: every session-key request needs the admin token or a
        # signed-in same-origin web session; loopback alone is not enough.
        key = canary()
        body = lambda: {"action": "inject", "session": A, "provider": "deepseek", "api_key": key}  # noqa: E731
        for name, options, allowed in (
            ("admin token", {"token": TOKEN}, True),
            ("local, no credential", {}, False),
            ("local same-origin, no credential", {"origin": "http://127.0.0.1:18080"}, False),
            ("local cross-origin page", {"origin": "https://evil.example"}, False),
            ("forged forwarded loopback", {"forwarded": True}, False),
            ("external without token", {"peer": "192.0.2.10"}, False),
            ("external wrong token", {"peer": "192.0.2.10", "token": "wrong" * 10}, False),
        ):
            with self.subTest(name):
                self.vault.clear_all()
                payload, status = self.post(body(), **options)
                self.assertEqual(allowed, status == 200, (name, payload))
                if not allowed:
                    self.assertEqual((401, "unauthorized"), (status, payload["error"]))
                    self.assertIsNone(self.vault.key_for(A, "deepseek"))
                self.controller.handle_get(self.handler(**options), KEYS_PATH)
                self.assertEqual(allowed, self.responses[-1][1] == 200, ("GET", name))
        self.signed_in = True
        self.vault.clear_all()
        payload, status = self.post(body(), peer="192.0.2.10")
        self.assertEqual(200, status, payload)  # signed-in same-origin web session
        self.assertEqual("dashboard", payload["key"]["source"])
        payload, status = self.post({"action": "inject", "session": A, "provider": "zai", "api_key": canary()},
                                    peer="192.0.2.10", origin="https://evil.example")
        self.assertEqual(401, status)  # a session cookie from another origin is not a session
        self.assertFalse(key in self.everything_written(), "synthetic key in a response or the audit")

    def test_source_label_is_server_derived_and_a_forged_header_or_field_is_rest(self):
        local_client = "local-" + "c" * 40
        controller = SessionApiKeyHttpController(SessionApiKeyHttpPorts(
            write_json=lambda _handler, payload, status=200: self.responses.append((payload, status)),
            admin_token=lambda: TOKEN, session_allowed=lambda _handler: False,
            workspace_state_dir=lambda: self.state, current_provider=lambda: "deepseek", vault=self.vault,
            activity=self.activity, local_client_token=lambda: local_client))

        def inject(provider, source, *, peer="127.0.0.1", client_header=None, forwarded=False):
            handler = self.handler(token=TOKEN, peer=peer, forwarded=forwarded)
            if client_header is not None:
                handler.headers["x-ciel-session-key-client"] = client_header
            handler.headers["x-ciel-runtime-source"] = source  # a forged header never counts
            controller.handle_post(handler, KEYS_PATH, {"action": "inject", "session": A, "provider": provider,
                                                        "api_key": canary(), "source": source})
            payload, status = self.responses[-1]
            self.assertEqual(200, status, payload)
            return payload["key"]["source"]

        self.assertEqual("ctl", inject("p1", "ctl", client_header=local_client))
        self.assertEqual("menu", inject("p2", "menu", client_header=local_client))
        self.assertEqual("rest", inject("p3", "ctl"))  # admin token, no local-client credential
        self.assertEqual("rest", inject("p4", "menu", client_header="forged-" + "x" * 40))
        self.assertEqual("rest", inject("p5", "ctl", client_header=local_client, forwarded=True))
        self.assertEqual("rest", inject("p6", "ctl", client_header=local_client, peer="192.0.2.10"))
        self.assertEqual("rest", inject("p7", "dashboard", client_header=local_client))
        audit = (self.state / "management-audit.jsonl").read_text(encoding="utf-8")
        self.assertEqual(["ctl", "menu", "rest", "rest", "rest", "rest", "rest"],
                         [json.loads(line)["source"] for line in audit.splitlines()])
        self.assertNotIn(local_client, audit + json.dumps(self.responses))

    def test_idempotent_retry_and_required_key(self):
        key = canary()
        body = {"action": "inject", "session": A, "provider": "deepseek", "api_key": key}
        first, _ = self.post(dict(body), token=TOKEN, idem="retry-1")
        again, status = self.post(dict(body), token=TOKEN, idem="retry-1")
        self.assertEqual(200, status)
        self.assertTrue(again["replayed"])
        self.assertEqual(first["key"]["version"], again["key"]["version"])
        missing, status = self.post({"action": "inject", "session": A, "provider": "zai"}, token=TOKEN)
        self.assertEqual((400, "invalid_key"), (status, missing["error"]))
        no_cas, status = self.post({"action": "clear", "session": A, "provider": "deepseek"}, token=TOKEN)
        self.assertEqual((400, "invalid_version"), (status, no_cas["error"]))

    def test_unreadable_markers_show_on_status_and_reset_needs_the_exact_confirmation(self):
        from ciel_runtime_support.session_api_key_markers import MARKER_FILE, SessionKeyMarkerStore
        from ciel_runtime_support.session_api_key_routing import SessionApiKeyRouting, SessionApiKeyUnavailable

        path = self.state / MARKER_FILE
        path.write_text('{"version": 1, "markers": [', encoding="utf-8")
        self.vault.attach_markers(SessionKeyMarkerStore(path), launcher_state=lambda _pid, _token: "alive")
        self.controller.handle_get(self.handler(token=TOKEN), KEYS_PATH)
        state, _ = self.responses[-1]
        self.assertTrue(state["health"]["state_unreadable"])
        self.assertTrue(any("marker unreadable: reset required" in notice for notice in state["notices"]))
        self.assertIn("stored API key", state["reset"]["warning"])

        # The client of a keyless session gets the remedy in its error.
        routing = SessionApiKeyRouting(vault=self.vault, activity=self.activity)
        with self.assertRaises(SessionApiKeyUnavailable) as caught:
            routing.overlay(A, "deepseek", {"api_key": "stored-key-value"})
        self.assertIn("reset required", str(caught.exception))
        self.assertIn("reset-state", str(caught.exception))

        for confirm in (None, "yes", "RESET-SESSION-KEY-STATE"):
            payload, status = self.post({"action": "reset_state", "confirm": confirm}, token=TOKEN)
            self.assertEqual((400, "confirmation_required"), (status, payload["error"]))
            self.assertIn("stored API key", payload["message"])
        self.assertTrue(self.vault.state_unreadable())
        payload, status = self.post({"action": "reset_state", "confirm": state["reset"]["confirm"]}, token=TOKEN)
        self.assertEqual((200, True), (status, payload["was_unreadable"]))
        self.assertFalse(payload["health"]["state_unreadable"])
        self.assertEqual([], payload["notices"])
        self.assertEqual(1, len(list(self.state.glob(MARKER_FILE + ".corrupt-*"))))
        self.assertIs(routing.overlay(A, "deepseek", stored := {"api_key": "stored-key-value"}), stored)

    def test_page_is_served_with_the_reset_and_notice_slots_and_takes_no_key_from_the_url(self):
        from ciel_runtime_support.session_api_key_ui import UI_PATH

        pages = []
        controller = SessionApiKeyHttpController(SessionApiKeyHttpPorts(
            write_json=lambda *_a, **_k: None, admin_token=lambda: TOKEN, session_allowed=lambda _h: False,
            workspace_state_dir=lambda: self.state, current_provider=lambda: "deepseek", vault=self.vault,
            activity=self.activity, write_html=lambda _handler, page: pages.append(page)))
        self.assertTrue(controller.handle_get(self.handler(token=TOKEN), UI_PATH))
        page = pages[0]
        for needle in ('id="notices"', 'id="resetBox"', 'id="reset"', "reset_state", 'type="password"',
                       'autocomplete="off"', "staged, awaiting first request",
                       "abandoned: scope rebound by another launch", "records ", "window.prompt",
                       "on loss: stored key"):
            self.assertIn(needle, page)
        self.assertNotIn("location.search", page)
        self.assertNotIn("URLSearchParams", page)

    def test_page_script_never_assigns_html_so_server_fields_stay_text(self):
        # The browser check with a hostile row (cause/provider/session/source
        # holding <img onerror>) runs in the dashboard E2E; this guards the
        # code path: the script has no way to turn a server value into markup.
        from ciel_runtime_support.session_api_key_ui import _SCRIPT

        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser",
                     "createContextualFragment", "srcdoc", "eval(", "new Function"):
            self.assertNotIn(sink, _SCRIPT, sink)
        self.assertIn("node.append(kid instanceof Node ? kid : String(kid))", _SCRIPT)
        self.assertIn("node.setAttribute(name, String(value))", _SCRIPT)

    def test_handoff_needs_the_local_admin_token(self):
        binding = binding_digest("/tmp/s.sock", "e1")
        body = lambda: {"binding": binding, "provider": "deepseek", "api_key": canary(),  # noqa: E731
                        "source": "remote-instructions", "launcher_pid": 123, "launcher_identity": "133456789",
                        "ttl_seconds": 3600}
        for options in ({}, {"origin": "http://127.0.0.1:18080"}, {"peer": "192.0.2.10", "token": TOKEN}):
            self.controller.handle_post(self.handler(**options), HANDOFF_PATH, body())
            self.assertEqual(403, self.responses[-1][1])
        self.assertFalse(self.vault.has_handoffs())
        # Session end is proven by pid + creation token, so the stage must carry both.
        self.controller.handle_post(self.handler(token=TOKEN), HANDOFF_PATH,
                                    {**body(), "launcher_identity": ""})
        self.assertEqual((400, "invalid_request"), (self.responses[-1][1], self.responses[-1][0]["error"]))
        self.controller.handle_post(self.handler(token=TOKEN), HANDOFF_PATH, body())
        self.assertEqual(200, self.responses[-1][1], self.responses[-1])
        self.assertTrue(self.vault.has_handoffs())


if __name__ == "__main__":
    unittest.main()
