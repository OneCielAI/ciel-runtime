import io
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

from ciel_runtime_support.oauth_routing import (
    OAuthRouting,
    anthropic_headers,
    anthropic_request_run,
    anthropic_turn_start,
    codex_request_run,
    codex_turn_start,
    open_with_oauth,
    use_oauth_routing,
)
from ciel_runtime_support.oauth_token_store import OAuthCredential

NOW = 1_790_000_000.0
CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"


def http_error(status: int, body: dict, headers: dict | None = None) -> urllib.error.HTTPError:
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(CODEX_URL, status, "error", message, io.BytesIO(json.dumps(body).encode()))


class FakeResponse:
    def __init__(self, headers: dict) -> None:
        self.headers = headers
        self.status = 200


class OAuthRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.now = NOW
        self.routing = OAuthRouting(Path(self.dir.name), threshold_percent=95.0, clock=lambda: self.now)
        self.routing.ensure_watcher = lambda _log: None  # no background thread in unit tests
        use_oauth_routing(self.routing)
        self.logs: list[str] = []
        self.body = {"prompt_cache_key": "conv-1", "input": [{"type": "message", "role": "user", "content": "hi"}]}

    def tearDown(self) -> None:
        use_oauth_routing(None)
        self.dir.cleanup()

    def log(self, _level: str, message: str) -> None:
        self.logs.append(message)

    def add(self, provider: str, access: str, account: str = "", refresh: str = "") -> str:
        return self.routing.store.add(provider, OAuthCredential(access, refresh, account_id=account, expires_at=NOW + 86400)).token_id

    def test_without_stored_tokens_the_cli_headers_pass_unchanged(self) -> None:
        self.assertIsNone(codex_request_run(CODEX_URL, {}, self.body, self.log))
        sent = []
        open_with_oauth(None, {"Authorization": "Bearer cli"}, lambda headers: sent.append(headers) or FakeResponse({}))
        self.assertEqual([{"Authorization": "Bearer cli"}], sent)

    def test_other_upstreams_never_get_stored_tokens(self) -> None:
        self.add("codex", "tok-a", "A")
        self.assertIsNone(codex_request_run("https://api.openai.com/v1/responses", {}, self.body, self.log))

    def test_codex_limit_moves_the_request_to_the_next_account_and_records_usage(self) -> None:
        first = self.add("codex", "tok-a", "A")
        second = self.add("codex", "tok-b", "B")
        run = codex_request_run(CODEX_URL, {"session_id": "s"}, self.body, self.log)
        sent: list[dict] = []

        def upstream(headers: dict) -> FakeResponse:
            sent.append(headers)
            if headers["Authorization"] == "Bearer tok-a":
                raise http_error(429, {"error": {"type": "usage_limit_reached", "resets_at": int(NOW + 3600)}})
            return FakeResponse({"x-codex-primary-used-percent": "12", "x-codex-primary-reset-at": str(int(NOW + 600))})

        open_with_oauth(run, {"Authorization": "Bearer cli", "ChatGPT-Account-ID": "cli-acct", "x-other": "1"}, upstream)

        self.assertEqual(["Bearer tok-a", "Bearer tok-b"], [h["Authorization"] for h in sent])
        self.assertEqual(["A", "B"], [h["ChatGPT-Account-ID"] for h in sent])
        self.assertEqual("1", sent[-1]["x-other"])
        snapshot = self.routing.store.snapshot()
        self.assertEqual(NOW + 3600, snapshot.get(first).exhausted_until)
        self.assertEqual(12.0, snapshot.get(second).usage["primary"]["used_percent"])
        self.assertTrue(any("oauth_token_refused" in line and first in line for line in self.logs))

    def test_every_account_limited_relays_the_upstream_429(self) -> None:
        self.add("codex", "tok-a", "A")
        run = codex_request_run(CODEX_URL, {}, self.body, self.log)

        def upstream(_headers: dict) -> FakeResponse:
            raise http_error(429, {"error": {"type": "usage_limit_reached", "resets_at": int(NOW + 60)}})

        with self.assertRaises(urllib.error.HTTPError) as raised:
            open_with_oauth(run, {"Authorization": "Bearer cli"}, upstream)
        self.assertEqual(429, raised.exception.code)
        self.assertIn(b"usage_limit_reached", raised.exception.read())

    def test_unauthorized_token_is_refreshed_once_and_retried(self) -> None:
        token = self.add("codex", "tok-old", "A", refresh="r-1")
        self.routing.refresher.post = lambda *_args: (200, json.dumps({"access_token": "tok-new", "refresh_token": "r-2"}).encode())
        run = codex_request_run(CODEX_URL, {}, self.body, self.log)
        sent: list[str] = []

        def upstream(headers: dict) -> FakeResponse:
            sent.append(headers["Authorization"])
            if headers["Authorization"] == "Bearer tok-old":
                raise http_error(401, {"error": {"message": "expired"}})
            return FakeResponse({})

        open_with_oauth(run, {"Authorization": "Bearer cli"}, upstream)

        self.assertEqual(["Bearer tok-old", "Bearer tok-new"], sent)
        self.assertEqual("r-2", self.routing.store.credential(token).refresh_token)

    def test_token_rejected_after_refresh_is_relayed_as_424_not_401(self) -> None:
        self.add("codex", "tok-a", "A", refresh="r-1")
        self.routing.refresher.post = lambda *_args: (400, b'{"error":"refresh_token_reused"}')
        run = codex_request_run(CODEX_URL, {}, self.body, self.log)

        def upstream(_headers: dict) -> FakeResponse:
            raise http_error(401, {"error": {"message": "bad"}})

        with self.assertRaises(urllib.error.HTTPError) as raised:
            open_with_oauth(run, {"Authorization": "Bearer cli"}, upstream)
        self.assertEqual(424, raised.exception.code)
        self.assertEqual("refresh_failed", self.routing.store.snapshot().tokens[0].status)

    def test_anthropic_tokens_replace_only_the_subscription_bearer(self) -> None:
        self.add("claude", "sk-ant-oat-stored")
        self.assertIsNone(anthropic_request_run("anthropic", {"x-api-key": "sk-ant-api"}, {}, {}, self.log))
        self.assertIsNone(anthropic_request_run("openrouter", {"authorization": "Bearer x"}, {}, {}, self.log))
        run = anthropic_request_run("anthropic", {"authorization": "Bearer cli-oauth"}, {"x-claude-code-session-id": "s1"}, {"messages": []}, self.log)
        self.assertIsNotNone(run)
        headers = run.headers({"authorization": "Bearer cli-oauth", "anthropic-beta": "claude-code-20250219"})
        self.assertEqual("Bearer sk-ant-oat-stored", headers["Authorization"])
        self.assertNotIn("authorization", headers)
        self.assertEqual("claude-code-20250219,oauth-2025-04-20", headers["anthropic-beta"])

    def test_anthropic_headers_do_not_duplicate_the_oauth_beta(self) -> None:
        self.add("claude", "tok")
        lease = self.routing.pool.acquire("claude", "s", turn_start=True)
        headers = anthropic_headers({"Anthropic-Beta": "oauth-2025-04-20", "x-api-key": "k"}, lease)
        self.assertEqual("oauth-2025-04-20", headers["anthropic-beta"])
        self.assertNotIn("x-api-key", headers)

    def test_turn_boundaries(self) -> None:
        self.assertTrue(codex_turn_start({"input": [{"type": "message", "role": "user", "content": "x"}]}))
        self.assertFalse(codex_turn_start({"input": [{"type": "message", "role": "user"}, {"type": "function_call_output", "output": "x"}]}))
        self.assertTrue(anthropic_turn_start({"messages": [{"role": "user", "content": "hello"}]}))
        self.assertFalse(anthropic_turn_start({"messages": [{"role": "user", "content": [{"type": "tool_result", "content": "x"}]}]}))
        self.assertTrue(anthropic_turn_start({"messages": [{"role": "user", "content": [{"type": "text", "text": "next"}]}]}))


if __name__ == "__main__":
    unittest.main()
