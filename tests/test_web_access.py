import io
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import web_access_http, web_access_menu
from ciel_runtime_support.oauth_token_store import OAuthTokenStore
from ciel_runtime_support.router_access import (
    RouterAccessHttpController,
    RouterAccessPolicy,
    RouterExternalTokenRepository,
)
from ciel_runtime_support.web_accounts import WebAccountError, WebAccountStore


class Headers(dict):
    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default


class FakeHandler:
    def __init__(self, path="/", *, command="GET", headers=None, peer="10.0.0.9"):
        self.path = path
        self.command = command
        self.headers = Headers(headers or {})
        self.client_address = (peer, 5555)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent.append((name.lower(), value))

    def end_headers(self):
        pass

    def header(self, name):
        return next((value for key, value in self.sent if key == name), None)


class WebAccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = WebAccountStore(Path(self.dir.name))

    def tearDown(self):
        self.dir.cleanup()

    def test_passwords_are_hashed_and_verified(self):
        self.store.add("Admin@Example.com", "correct horse")
        raw = (Path(self.dir.name) / "accounts.json").read_text(encoding="utf-8")

        self.assertNotIn("correct horse", raw)
        self.assertIn("scrypt$", raw)
        self.assertEqual("admin@example.com", self.store.verify("admin@example.com", "correct horse"))
        self.assertIsNone(self.store.verify("admin@example.com", "wrong password"))
        self.assertIsNone(self.store.verify("nobody@example.com", "correct horse"))

    def test_rules_for_accounts(self):
        with self.assertRaises(WebAccountError):
            self.store.add("not-an-email", "long enough")
        with self.assertRaises(WebAccountError):
            self.store.add("a@example.com", "short")
        self.store.add("a@example.com", "long enough")
        with self.assertRaises(WebAccountError):
            self.store.add("a@example.com", "another one")

    def test_password_reset_and_removal_end_that_accounts_sessions(self):
        self.store.add("a@example.com", "first password")
        self.store.add("b@example.com", "second password")
        a_session = self.store.create_session("a@example.com")
        b_session = self.store.create_session("b@example.com")
        self.assertNotIn(a_session, (Path(self.dir.name) / "sessions.json").read_text(encoding="utf-8"))

        self.store.set_password("a@example.com", "replaced password")

        self.assertIsNone(self.store.session_email(a_session))
        self.assertEqual("b@example.com", self.store.session_email(b_session))
        self.assertEqual("a@example.com", self.store.verify("a@example.com", "replaced password"))
        self.store.remove("b@example.com")
        self.assertIsNone(self.store.session_email(b_session))
        self.assertEqual(["a@example.com"], [row["email"] for row in self.store.list_accounts()])

    def test_sessions_expire(self):
        now = [1000.0]
        store = WebAccountStore(Path(self.dir.name), clock=lambda: now[0])
        store.add("a@example.com", "long enough")
        token = store.create_session("a@example.com")
        self.assertEqual("a@example.com", store.session_email(token))
        now[0] += 13 * 3600
        self.assertIsNone(store.session_email(token))


class WebAccessControllerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.accounts = WebAccountStore(self.root / "web-access")
        self.token_repo = RouterExternalTokenRepository(path=self.root / "router-external-token", config_dir=self.root, environ={})
        self.json = []
        self.html = []
        self.controller = web_access_http.WebAccessHttpController(
            web_access_http.WebAccessPorts(
                write_json=lambda handler, payload, status=200: self.json.append((status, payload)),
                write_html=lambda handler, page: self.html.append(page),
                accounts=lambda: self.accounts,
                workspace_state_dir=lambda: self.root / "ws",
                admin_token=self.token_repo,
                external_access_enabled=lambda: True,
                sleep=lambda _seconds: None,
            )
        )

    def tearDown(self):
        self.dir.cleanup()

    def login(self, email="admin@example.com", password="long password"):
        handler = FakeHandler("/ca/auth/login", command="POST")
        self.assertTrue(self.controller.handle_post(handler, "/ca/auth/login", {"email": email, "password": password, "next": "https://evil.example/"}))
        return handler

    def test_login_sets_a_strict_http_only_session_cookie(self):
        self.accounts.add("admin@example.com", "long password")
        handler = self.login()

        self.assertEqual(200, handler.status)
        cookie = handler.header("set-cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        body = json.loads(handler.wfile.getvalue())
        self.assertEqual("/ca/admin", body["next"])  # never an absolute URL
        token = cookie.split(";")[0].split("=", 1)[1]
        with_cookie = FakeHandler(headers={"cookie": f"ciel_session={token}"})
        self.assertTrue(self.controller.session_allowed(with_cookie))

    def test_wrong_password_is_refused(self):
        self.accounts.add("admin@example.com", "long password")
        self.login(password="wrong password")
        self.assertEqual(401, self.json[-1][0])

    def test_cross_site_post_with_a_session_is_refused(self):
        self.accounts.add("admin@example.com", "long password")
        token = self.accounts.create_session("admin@example.com")
        same = FakeHandler(command="POST", headers={"cookie": f"ciel_session={token}", "host": "box:9000", "origin": "http://box:9000"})
        cross = FakeHandler(command="POST", headers={"cookie": f"ciel_session={token}", "host": "box:9000", "origin": "https://evil.example"})
        self.assertTrue(self.controller.session_allowed(same))
        self.assertFalse(self.controller.session_allowed(cross))

    def test_token_crud_over_the_api(self):
        handler = FakeHandler(command="POST")
        content = json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat-web", "refreshToken": "r", "expiresAt": 0}})
        self.controller.handle_post(handler, "/ca/oauth/tokens", {"action": "import", "provider": "claude", "content": content, "label": "team"})
        status, payload = self.json[-1]
        self.assertEqual(200, status, payload)
        token_id = payload["token"]["token_id"]

        self.controller.handle_post(handler, "/ca/oauth/tokens", {"action": "update", "token_id": token_id, "label": "renamed", "enabled": False})
        self.controller.handle_get(FakeHandler(), "/ca/oauth/tokens")
        listed = self.json[-1][1]["tokens"]
        self.assertEqual([("renamed", "disabled")], [(row["label"], row["state"]) for row in listed])
        self.assertNotIn("sk-ant-oat-web", json.dumps(self.json))

        self.controller.handle_post(handler, "/ca/oauth/tokens", {"action": "remove", "token_id": token_id})
        self.assertEqual([], OAuthTokenStore(self.root / "ws").snapshot().tokens)
        self.controller.handle_post(handler, "/ca/oauth/tokens", {"action": "remove", "token_id": token_id})
        self.assertEqual(400, self.json[-1][0])

    def test_web_sign_in_is_two_steps_and_expires(self):
        exchanged = []

        def post(url, data, headers, timeout):
            exchanged.append(data)
            return 200, json.dumps({"access_token": "a", "refresh_token": "r", "expires_in": 3600, "account": {"email_address": "me@example.com"}}).encode()

        controller = web_access_http.WebAccessHttpController(
            web_access_http.WebAccessPorts(
                write_json=lambda handler, payload, status=200: self.json.append((status, payload)),
                write_html=lambda handler, page: None,
                accounts=lambda: self.accounts,
                workspace_state_dir=lambda: self.root / "ws",
                admin_token=self.token_repo,
                external_access_enabled=lambda: True,
                token_post=post,
            )
        )
        handler = FakeHandler(command="POST")
        controller.handle_post(handler, "/ca/oauth/tokens", {"action": "sign_in_start", "provider": "claude"})
        started = self.json[-1][1]
        self.assertIn("state=" + started["sign_in_id"], started["authorize_url"])

        redirect = f"http://localhost:54545/callback?code=web-code&state={started['sign_in_id']}"
        controller.handle_post(handler, "/ca/oauth/tokens", {"action": "sign_in_finish", "sign_in_id": started["sign_in_id"], "redirect": redirect})
        self.assertEqual(200, self.json[-1][0], self.json[-1])
        self.assertEqual("me@example.com", self.json[-1][1]["token"]["email"])
        self.assertIn(b"web-code", exchanged[0])
        # The pending sign-in is single use.
        controller.handle_post(handler, "/ca/oauth/tokens", {"action": "sign_in_finish", "sign_in_id": started["sign_in_id"], "redirect": redirect})
        self.assertEqual(400, self.json[-1][0])

    def test_access_actions_manage_accounts_and_rotate_the_admin_token(self):
        handler = FakeHandler(command="POST")
        first = self.token_repo.ensure()
        self.controller.handle_post(handler, "/ca/access", {"action": "add_account", "email": "ops@example.com", "password": "long password"})
        self.controller.handle_post(handler, "/ca/access", {"action": "rotate_admin_token"})
        rotated = self.json[-1][1]["admin_token"]
        self.assertNotEqual(first, rotated)
        self.assertEqual(rotated, self.token_repo.get())
        self.controller.handle_post(handler, "/ca/access", {"action": "reset_password", "email": "ops@example.com", "password": "new long password"})
        self.assertEqual("ops@example.com", self.accounts.verify("ops@example.com", "new long password"))
        self.controller.handle_get(FakeHandler(), "/ca/access")
        status = self.json[-1][1]
        self.assertEqual(["ops@example.com"], [row["email"] for row in status["accounts"]])
        self.assertEqual("…" + rotated[-4:], status["admin_token"]["hint"])
        self.assertNotIn(rotated, json.dumps(status))


class RouterAccessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.policy = RouterAccessPolicy(
            environ={"CIEL_RUNTIME_ROUTER_DEBUG_EXTERNAL": "1"},
            parse_bool=lambda value, default=False: str(value).lower() in ("1", "true", "yes", "on") if value is not None else default,
            parse_env_bool=lambda value, default=None: None if value is None else str(value).lower() in ("1", "true"),
            load_config=lambda: {},
        )

    def allowed(self, handler, session_ok):
        return self.policy.request_allowed(handler, {}, lambda: "admin-token", lambda: "", lambda _h: session_ok)

    def test_remote_request_needs_the_admin_token_or_a_session(self):
        self.assertFalse(self.allowed(FakeHandler(), False))
        self.assertTrue(self.allowed(FakeHandler(), True))
        self.assertTrue(self.allowed(FakeHandler(headers={"authorization": "Bearer admin-token"}), False))
        self.assertTrue(self.allowed(FakeHandler(peer="127.0.0.1"), False))

    def test_browser_without_access_is_sent_to_the_sign_in_page(self):
        controller = RouterAccessHttpController(request_allowed=lambda h, c: False, external_access_enabled=lambda c: True)
        browser = FakeHandler("/ca/admin", headers={"accept": "text/html,application/xhtml+xml"})
        api = FakeHandler("/ca/oauth/tokens", headers={"accept": "application/json"})

        self.assertTrue(controller.reject_external_request(browser))
        self.assertTrue(controller.reject_external_request(api))
        self.assertEqual((303, "/ca/login?next=%2Fca%2Fadmin"), (browser.status, browser.header("location")))
        self.assertEqual(401, api.status)

    def test_admin_token_rotation_replaces_the_file_token(self):
        with tempfile.TemporaryDirectory() as td:
            repo = RouterExternalTokenRepository(path=Path(td) / "t", config_dir=Path(td), environ={})
            first = repo.ensure()
            second = repo.rotate()
            self.assertNotEqual(first, second)
            self.assertEqual(second, repo.get())
            pinned = RouterExternalTokenRepository(path=Path(td) / "t", config_dir=Path(td), environ={"CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN": "env"})
            with self.assertRaises(RuntimeError):
                pinned.rotate()


class WebAccessMenuTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.accounts = WebAccountStore(self.root / "web-access")
        self.repo = RouterExternalTokenRepository(path=self.root / "token", config_dir=self.root, environ={})

    def tearDown(self):
        self.dir.cleanup()

    def run_menu(self, value, answers=(), secrets=()):
        answers, secrets = list(answers), list(secrets)
        return web_access_menu.apply(
            value, self.accounts, self.repo,
            lambda _label, default: answers.pop(0) if answers else default,
            lambda _label: secrets.pop(0) if secrets else "",
        )

    def test_menu_adds_resets_and_removes_accounts(self):
        self.assertEqual(["Added web account ops@example.com."], self.run_menu("add", ["ops@example.com"], ["long password", "long password"]))
        self.assertEqual(["The passwords do not match."], self.run_menu("account:ops@example.com", ["reset"], ["new long password", "typo"]))
        self.run_menu("account:ops@example.com", ["reset"], ["new long password", "new long password"])
        self.assertEqual("ops@example.com", self.accounts.verify("ops@example.com", "new long password"))
        rows, values = web_access_menu.panel_rows(self.accounts, self.repo)
        self.assertEqual(["rotate", "account:ops@example.com", "add", "revoke", "back"], values)
        self.run_menu("account:ops@example.com", ["remove"])
        self.assertEqual([], self.accounts.list_accounts())

    def test_menu_rotates_the_admin_token_only_when_confirmed(self):
        first = self.repo.ensure()
        self.assertEqual(["Admin token unchanged."], self.run_menu("rotate", ["no"]))
        self.assertEqual(first, self.repo.get())
        messages = self.run_menu("rotate", ["yes"])
        self.assertEqual(self.repo.get(), messages[-1])
        self.assertNotEqual(first, messages[-1])


if __name__ == "__main__":
    unittest.main()

