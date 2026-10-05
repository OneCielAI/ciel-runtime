import base64
import socket
import json
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

from ciel_runtime_support.oauth_login import login
from ciel_runtime_support.oauth_token_cli import run_tokens_command
from ciel_runtime_support.oauth_token_endpoints import (
    import_claude_credentials,
    import_codex_auth,
)
from ciel_runtime_support.oauth_token_refresh import OAuthTokenRefresher, OAuthTokenWatcher
from ciel_runtime_support.oauth_token_pool import OAuthTokenPool
from ciel_runtime_support.oauth_token_store import OAuthCredential, OAuthTokenStore
from ciel_runtime_support.oauth_usage_signals import UsageObservation

NOW = 1_790_000_000.0


def jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def codex_access(exp: float) -> str:
    return jwt({"exp": int(exp), "https://api.openai.com/auth": {"chatgpt_account_id": "acct-9"}})


class FakeTokenEndpoint:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, bytes, dict]] = []

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        self.calls.append((url, data, headers))
        status, body = self.responses.pop(0)
        return status, json.dumps(body).encode()


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.now = NOW
        self.store = OAuthTokenStore(Path(self.dir.name), clock=lambda: self.now)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_codex_token_near_expiry_is_refreshed_with_a_form_body(self) -> None:
        token = self.store.add("codex", OAuthCredential(codex_access(NOW + 60), "r-1", account_id="acct-9", expires_at=NOW + 60))
        endpoint = FakeTokenEndpoint([(200, {"access_token": codex_access(NOW + 864000), "refresh_token": "r-2"})])

        outcomes = OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh_due()

        self.assertEqual([True], [outcome.refreshed for outcome in outcomes])
        url, body, headers = endpoint.calls[0]
        self.assertEqual("https://auth.openai.com/oauth/token", url)
        self.assertEqual("application/x-www-form-urlencoded", headers["Content-Type"])
        fields = urllib.parse.parse_qs(body.decode())
        self.assertEqual(["refresh_token"], fields["grant_type"])
        self.assertEqual(["r-1"], fields["refresh_token"])
        stored = self.store.credential(token.token_id)
        self.assertEqual("r-2", stored.refresh_token)
        self.assertEqual(NOW + 864000, stored.expires_at)
        self.assertEqual("acct-9", stored.account_id)

    def test_claude_refresh_is_json_with_scope(self) -> None:
        self.store.add("claude", OAuthCredential("a-1", "r-1", expires_at=NOW + 30, scopes=("user:inference",)))
        endpoint = FakeTokenEndpoint([(200, {"access_token": "a-2", "refresh_token": "r-2", "expires_in": 28800})])

        OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh_due()

        url, body, headers = endpoint.calls[0]
        self.assertEqual("https://platform.claude.com/v1/oauth/token", url)
        self.assertEqual("application/json", headers["Content-Type"])
        self.assertEqual(
            {"grant_type": "refresh_token", "refresh_token": "r-1", "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e", "scope": "user:inference"},
            json.loads(body),
        )

    def test_rejected_access_token_already_replaced_is_not_refreshed_again(self) -> None:
        token = self.store.add("codex", OAuthCredential("new-access", "r-2", expires_at=NOW + 86400))
        endpoint = FakeTokenEndpoint([])

        outcome = OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh(
            token.token_id, stale_access_token="old-access"
        )

        self.assertTrue(outcome.refreshed)
        self.assertEqual("already refreshed", outcome.detail)
        self.assertEqual([], endpoint.calls)

    def test_revoked_refresh_token_takes_the_token_out_until_signed_in_again(self) -> None:
        token = self.store.add("codex", OAuthCredential("a", "r-used", expires_at=NOW + 10))
        endpoint = FakeTokenEndpoint([(400, {"error": "refresh_token_reused"})])

        outcome = OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh(token.token_id)

        self.assertFalse(outcome.refreshed)
        state = self.store.snapshot().get(token.token_id)
        self.assertEqual("refresh_failed", state.status)
        self.assertIsNone(OAuthTokenPool(self.store, clock=lambda: self.now).acquire("codex", "s", turn_start=True))

    def test_network_failure_keeps_the_token_and_waits_before_retrying(self) -> None:
        token = self.store.add("claude", OAuthCredential("a", "r", expires_at=NOW + 10))

        def offline(*_args):
            raise OSError("network down")

        refresher = OAuthTokenRefresher(self.store, post=offline, clock=lambda: self.now)
        self.assertFalse(refresher.refresh(token.token_id).refreshed)
        self.assertEqual("active", self.store.snapshot().get(token.token_id).status)
        self.assertEqual([], refresher.refresh_due())  # inside the retry delay

    def test_watcher_tick_restores_and_refreshes(self) -> None:
        limited = self.store.add("codex", OAuthCredential("a", expires_at=NOW + 86400)).token_id
        self.store.add("codex", OAuthCredential(codex_access(NOW + 5), "r-1", expires_at=NOW + 5))
        pool = OAuthTokenPool(self.store, clock=lambda: self.now)
        pool.observe(limited, UsageObservation(limited=True, reset_at=NOW + 100))
        endpoint = FakeTokenEndpoint([(200, {"access_token": codex_access(NOW + 999999), "refresh_token": "r-2"})])
        logs: list[str] = []
        watcher = OAuthTokenWatcher(
            pool, OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now), lambda _level, message: logs.append(message)
        )

        self.now = NOW + 101
        watcher.tick()

        self.assertTrue(any(f"oauth_token_restored token={limited}" in line for line in logs))
        self.assertTrue(any("refreshed=True" in line for line in logs))


class ImportedSourceSyncTests(unittest.TestCase):
    """A token imported from a CLI credential file stays in step with that file."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.now = NOW
        self.store = OAuthTokenStore(self.root / "ws", clock=lambda: self.now)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def claude_file(self, access: str, refresh: str, expires_at: float) -> Path:
        path = self.root / ".credentials.json"
        path.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": access, "refreshToken": refresh, "expiresAt": int(expires_at * 1000),
                "scopes": ["user:inference"], "subscriptionType": "max", "refreshTokenExpiresAt": 1793044006936,
            },
            "mcpOAuth": {"server": {"accessToken": "mcp"}},
        }))
        return path

    def import_claude(self, path: Path):
        credential, _email = import_claude_credentials(path)
        return self.store.add("claude", credential, label=path.name, source=f"import:{path}")

    def test_refresh_writes_the_new_tokens_back_and_keeps_the_rest(self) -> None:
        path = self.claude_file("a-1", "r-1", NOW + 30)
        token = self.import_claude(path)
        endpoint = FakeTokenEndpoint([(200, {"access_token": "a-2", "refresh_token": "r-2", "expires_in": 28800})])

        outcome = OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh(token.token_id)

        self.assertEqual((True, "refreshed; source updated"), (outcome.refreshed, outcome.detail))
        value = json.loads(path.read_text())
        oauth = value["claudeAiOauth"]
        self.assertEqual(("a-2", "r-2", int((NOW + 28800) * 1000)), (oauth["accessToken"], oauth["refreshToken"], oauth["expiresAt"]))
        self.assertEqual(("max", 1793044006936), (oauth["subscriptionType"], oauth["refreshTokenExpiresAt"]))
        self.assertEqual({"server": {"accessToken": "mcp"}}, value["mcpOAuth"])
        self.assertEqual("r-2", import_claude_credentials(path)[0].refresh_token)

    def test_a_credential_the_cli_refreshed_is_adopted_instead_of_refreshing(self) -> None:
        path = self.claude_file("a-1", "r-1", NOW + 30)
        token = self.import_claude(path)
        self.claude_file("a-cli", "r-cli", NOW + 28800)  # Claude Code refreshed on its own
        endpoint = FakeTokenEndpoint([])

        outcome = OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh_due()

        self.assertEqual([(True, "adopted from source")], [(o.refreshed, o.detail) for o in outcome])
        self.assertEqual([], endpoint.calls)  # the replaced refresh token r-1 is never presented
        stored = self.store.credential(token.token_id)
        self.assertEqual(("a-cli", "r-cli", NOW + 28800), (stored.access_token, stored.refresh_token, stored.expires_at))

    def test_rejected_access_token_is_answered_with_the_cli_credential(self) -> None:
        path = self.claude_file("a-1", "r-1", NOW + 3600)
        token = self.import_claude(path)
        self.claude_file("a-cli", "r-cli", NOW + 28800)

        outcome = OAuthTokenRefresher(self.store, post=FakeTokenEndpoint([]), clock=lambda: self.now).refresh(
            token.token_id, stale_access_token="a-1"
        )

        self.assertEqual((True, "adopted from source"), (outcome.refreshed, outcome.detail))
        self.assertEqual("a-cli", self.store.credential(token.token_id).access_token)

    def test_an_emptied_cli_file_gets_the_refreshed_login_back(self) -> None:
        # sarah-ai 2026-10-04: the file was left with empty tokens and expiresAt 0.
        path = self.claude_file("a-1", "r-1", NOW + 30)
        token = self.import_claude(path)
        self.claude_file("", "", 0)
        endpoint = FakeTokenEndpoint([(200, {"access_token": "a-2", "refresh_token": "r-2", "expires_in": 28800})])

        OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh(token.token_id)

        self.assertEqual("r-1", json.loads(endpoint.calls[0][1])["refresh_token"])
        restored, _email = import_claude_credentials(path)
        self.assertEqual(("a-2", "r-2"), (restored.access_token, restored.refresh_token))

    def test_codex_auth_json_is_updated_in_place(self) -> None:
        path = self.root / "auth.json"
        path.write_text(json.dumps({"OPENAI_API_KEY": None, "auth_mode": "chatgpt", "tokens": {
            "access_token": codex_access(NOW + 60), "refresh_token": "r-1", "id_token": jwt({"email": "c@example.com"}), "account_id": "acct-9",
        }, "last_refresh": "2026-01-01T00:00:00Z"}))
        credential, email = import_codex_auth(path)
        token = self.store.add("codex", credential, email=email, source=f"import:{path}")
        fresh_access = codex_access(NOW + 864000)
        endpoint = FakeTokenEndpoint([(200, {"access_token": fresh_access, "refresh_token": "r-2"})])

        OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh(token.token_id)

        value = json.loads(path.read_text())
        self.assertEqual((fresh_access, "r-2", "acct-9"), (value["tokens"]["access_token"], value["tokens"]["refresh_token"], value["tokens"]["account_id"]))
        self.assertEqual(("chatgpt", None), (value["auth_mode"], value["OPENAI_API_KEY"]))
        self.assertNotEqual("2026-01-01T00:00:00Z", value["last_refresh"])

    def test_tokens_without_an_import_source_leave_files_alone(self) -> None:
        path = self.claude_file("a-1", "r-1", NOW + 30)
        before = path.read_text()
        token = self.store.add("claude", OAuthCredential("a-1", "r-1", expires_at=NOW + 30), source="login")
        endpoint = FakeTokenEndpoint([(200, {"access_token": "a-2", "refresh_token": "r-2", "expires_in": 28800})])

        outcome = OAuthTokenRefresher(self.store, post=endpoint, clock=lambda: self.now).refresh(token.token_id)

        self.assertEqual("refreshed", outcome.detail)
        self.assertEqual(before, path.read_text())


class LoginTests(unittest.TestCase):
    def test_claude_browser_sign_in_exchanges_the_code_with_pkce(self) -> None:
        endpoint = FakeTokenEndpoint(
            [(200, {"access_token": "a-1", "refresh_token": "r-1", "expires_in": 3600, "account": {"email_address": "me@example.com"}})]
        )

        def browser(url: str) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertEqual(["S256"], query["code_challenge_method"])
            self.assertEqual(["login"], query["prompt"])
            redirect = query["redirect_uri"][0].replace("localhost", "127.0.0.1")
            urllib.request.urlopen(f"{redirect}?code=the-code&state={query['state'][0]}", timeout=5).read()

        credential, email = login("claude", open_browser=browser, post=endpoint, output=lambda _line: None, timeout=10)

        self.assertEqual(("a-1", "r-1", "me@example.com"), (credential.access_token, credential.refresh_token, email))
        body = json.loads(endpoint.calls[0][1])
        self.assertEqual("authorization_code", body["grant_type"])
        self.assertEqual("the-code", body["code"])
        self.assertTrue(body["code_verifier"])
        self.assertIn("state", body)

    def test_state_mismatch_is_refused(self) -> None:
        def browser(url: str) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            redirect = query["redirect_uri"][0].replace("localhost", "127.0.0.1")
            urllib.request.urlopen(f"{redirect}?code=x&state=forged", timeout=5).read()

        with self.assertRaisesRegex(RuntimeError, "state"):
            login("claude", open_browser=browser, post=FakeTokenEndpoint([]), output=lambda _line: None, timeout=10)


    def test_remote_sign_in_takes_the_pasted_redirect_even_with_the_port_busy(self) -> None:
        # cindy-ai 2026-09-30: the browser runs on the user's PC, so the
        # redirect to localhost:1455 never reaches the container, and a stale
        # sign-in held 1455. The pasted address still carries the code.
        from ciel_runtime_support import oauth_login

        endpoint = FakeTokenEndpoint([(200, {"access_token": codex_access(NOW), "refresh_token": "r-2", "id_token": jwt({"email": "b@example.com"})})])
        seen: list[str] = []
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                busy.bind(("127.0.0.1", 1455))
                busy.listen(1)
            except OSError:
                pass  # already held by something else: the same condition

            def paste() -> str:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
                self.assertEqual(["http://localhost:1455/auth/callback"], query["redirect_uri"])
                return f"http://localhost:1455/auth/callback?code=pasted-code&state={query['state'][0]}"

            credential, email = oauth_login.login(
                "codex", open_browser=seen.append, post=endpoint, output=lambda _line: None, redirect_input=paste
            )
        finally:
            busy.close()

        self.assertEqual(("r-2", "b@example.com"), (credential.refresh_token, email))
        self.assertIn(b"code=pasted-code", endpoint.calls[0][1])

    def test_local_sign_in_uses_the_callback_when_enter_is_pressed(self) -> None:
        endpoint = FakeTokenEndpoint([(200, {"access_token": "a-3", "refresh_token": "r-3", "expires_in": 3600})])

        def browser(url: str) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            redirect = query["redirect_uri"][0].replace("localhost", "127.0.0.1")
            urllib.request.urlopen(f"{redirect}?code=loop-code&state={query['state'][0]}", timeout=5).read()

        credential, _email = login("claude", open_browser=browser, post=endpoint, output=lambda _line: None, redirect_input=lambda: "")

        self.assertEqual("r-3", credential.refresh_token)
        self.assertEqual("loop-code", json.loads(endpoint.calls[0][1])["code"])

    def test_nothing_pasted_and_no_callback_cancels(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            login("claude", open_browser=lambda _url: None, post=FakeTokenEndpoint([]), output=lambda _line: None, redirect_input=lambda: "")

    @staticmethod
    def _callback_port(url: str) -> int:
        redirect = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["redirect_uri"][0]
        return int(urllib.parse.urlparse(redirect).port)

    @staticmethod
    def _listening(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            return probe.connect_ex(("127.0.0.1", port)) == 0

    def test_unanswered_callback_port_closes_after_the_timeout(self) -> None:
        seen: list[str] = []
        with self.assertRaises(TimeoutError):
            login("claude", open_browser=seen.append, post=FakeTokenEndpoint([]), output=lambda _line: None, timeout=0.3)

        self.assertFalse(self._listening(self._callback_port(seen[0])))

    def test_callback_port_closes_after_the_timeout_while_waiting_for_a_paste(self) -> None:
        seen: list[str] = []
        observed: list[bool] = []

        def slow_paste() -> str:
            port = self._callback_port(seen[0])
            observed.append(self._listening(port))
            time.sleep(1.0)
            observed.append(self._listening(port))
            return ""

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            login(
                "claude",
                open_browser=seen.append,
                post=FakeTokenEndpoint([]),
                output=lambda _line: None,
                timeout=0.3,
                redirect_input=slow_paste,
            )

        self.assertEqual([True, False], observed)

    def test_pasted_redirect_forms(self) -> None:
        from ciel_runtime_support.oauth_login import parse_pasted_redirect

        expected = {"code": "c1", "state": "s1"}
        self.assertEqual(expected, parse_pasted_redirect(" http://localhost:1455/auth/callback?code=c1&state=s1 "))
        self.assertEqual(expected, parse_pasted_redirect("?code=c1&state=s1"))
        self.assertEqual(expected, parse_pasted_redirect("c1#s1"))
        self.assertEqual({}, parse_pasted_redirect(""))


class ImportAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_codex_auth_json_and_claude_credentials_are_read(self) -> None:
        codex = self.root / "auth.json"
        codex.write_text(json.dumps({"tokens": {"access_token": codex_access(NOW), "refresh_token": "r", "id_token": jwt({"email": "c@example.com"}), "account_id": "acct-1"}}))
        claude = self.root / ".credentials.json"
        claude.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat-x", "refreshToken": "r", "expiresAt": int(NOW * 1000), "scopes": ["user:inference"]}}))

        credential, email = import_codex_auth(codex)
        self.assertEqual(("acct-1", "c@example.com", NOW), (credential.account_id, email, credential.expires_at))
        credential, _email = import_claude_credentials(claude)
        self.assertEqual((NOW, ("user:inference",)), (credential.expires_at, credential.scopes))

    def test_tokens_command_imports_lists_disables_and_removes(self) -> None:
        source = self.root / "creds.json"
        source.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat-secret", "refreshToken": "r", "expiresAt": 0}}))
        lines: list[str] = []
        state_dir = self.root / "ws"

        run_tokens_command(["import", "claude", "--from", str(source), "--label", "main"], state_dir, output=lines.append)
        token_id = OAuthTokenStore(state_dir).snapshot().tokens[0].token_id
        self.assertTrue(any("can revoke one side" in line for line in lines))
        self.assertFalse(any("single use" in line for line in lines))
        run_tokens_command(["disable", token_id], state_dir, output=lines.append)
        run_tokens_command(["list"], state_dir, output=lines.append)
        self.assertIn("disabled", lines[-1])
        self.assertNotIn("sk-ant-oat-secret", "\n".join(lines))
        run_tokens_command(["remove", token_id], state_dir, output=lines.append)
        self.assertEqual([], OAuthTokenStore(state_dir).snapshot().tokens)


if __name__ == "__main__":
    unittest.main()
