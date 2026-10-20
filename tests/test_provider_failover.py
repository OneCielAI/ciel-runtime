import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support import provider_failover_menu
from ciel_runtime_support.provider_failover import (
    Backup,
    FailoverRequested,
    ProviderFailoverService,
    fallback_command,
    fallback_command_arguments,
    maybe_fail_over,
    use_failover_service,
)

CFG = {
    "current_provider": "vllm",
    "providers": {
        "vllm": {"base_url": "http://a", "api_key": "ka", "current_model": "model-a"},
        "zai": {"api_key": "kz", "current_model": "glm-5"},
        "kimi": {"current_model": "k3"},
        "codex": {"api_key": "x", "current_model": "gpt"},
    },
}


def headers(**values):
    message = Message()
    for key, value in values.items():
        message[key.replace("_", "-")] = str(value)
    return message


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.now = [1_000_000.0]
        self.service = ProviderFailoverService(Path(self._tmp.name), clock=lambda: self.now[0])
        self.service.update_settings(enabled=True, backups=[{"provider": "kimi", "model": ""}, {"provider": "zai", "model": "glm-5.3"}])
        use_failover_service(self.service)

    def tearDown(self):
        use_failover_service(None)
        self._tmp.cleanup()

    def handler(self):
        return SimpleNamespace(_ciel_runtime_response_status=None, _ciel_failover_cfg=CFG)

    def test_only_backups_with_stored_credentials_are_usable(self):
        self.assertEqual([Backup("zai", "glm-5.3")], self.service.usable_backups(CFG))
        self.assertFalse(self.service.provider_ready("codex", CFG["providers"]["codex"]))

    def test_rate_limit_activates_the_first_usable_backup_until_retry_after(self):
        handler = self.handler()
        with self.assertRaises(FailoverRequested) as raised:
            maybe_fail_over(handler, "vllm", status=429, headers=headers(retry_after=120), reason=b'{"error":{"message":"slow down"}}')
        self.assertEqual("zai", raised.exception.to_provider)
        active = self.service.active()
        self.assertEqual({"provider": "zai", "model": "glm-5.3"}, active["to"])
        self.assertEqual(self.now[0] + 120, active["until"])
        self.assertIn("slow down", active["reason"])

    def test_no_reset_hint_uses_the_default_cooldown(self):
        self.service.update_settings(default_cooldown_minutes=10)
        with self.assertRaises(FailoverRequested):
            maybe_fail_over(self.handler(), "vllm", status=429)
        self.assertEqual(self.now[0] + 600, self.service.active()["until"])

    def test_nothing_happens_when_disabled_started_or_not_rate_limited(self):
        self.service.update_settings(enabled=False)
        maybe_fail_over(self.handler(), "vllm", status=429)
        self.service.update_settings(enabled=True)
        started = self.handler()
        started._ciel_runtime_response_status = 200
        maybe_fail_over(started, "vllm", status=429)
        maybe_fail_over(self.handler(), "vllm", status=500)
        self.assertIsNone(self.service.active())

    def test_backup_that_is_also_limited_relays_when_none_is_left(self):
        handler = self.handler()
        with self.assertRaises(FailoverRequested):
            maybe_fail_over(handler, "vllm", status=429)
        maybe_fail_over(handler, "zai", status=429)  # zai limited too: nothing left, the error is relayed
        self.assertEqual("vllm", self.service.active()["from"])

    def test_effective_provider_returns_only_at_a_turn_start_after_the_cooldown(self):
        with self.assertRaises(FailoverRequested):
            maybe_fail_over(self.handler(), "vllm", status=429, headers=headers(retry_after=60))
        provider, pcfg = self.service.effective(CFG, "vllm", CFG["providers"]["vllm"], turn_start=True)
        self.assertEqual(("zai", "glm-5.3"), (provider, pcfg["current_model"]))
        self.now[0] += 120
        self.assertEqual("zai", self.service.effective(CFG, "vllm", CFG["providers"]["vllm"], turn_start=False)[0])
        self.assertEqual("vllm", self.service.effective(CFG, "vllm", CFG["providers"]["vllm"], turn_start=True)[0])
        self.assertIsNone(self.service.active())
        events = [event["event"] for event in self.service.load()["history"]]
        self.assertEqual(["activated", "returned"], events)

    def test_manual_switch_and_return(self):
        active = self.service.switch(CFG, "vllm", Backup("zai", ""), 15)
        self.assertEqual("glm-5", active["to"]["model"])
        self.assertEqual("manual", active["trigger"])
        with self.assertRaises(ValueError):
            self.service.switch(CFG, "vllm", Backup("kimi", ""), 15)
        self.assertEqual("vllm", self.service.return_now()["from"])
        self.assertIsNone(self.service.active())

    def test_slash_command(self):
        body = {"messages": [{"role": "user", "content": "CIEL_RUNTIME_PROVIDER_FALLBACK\n\nArguments: zai 20 glm-x"}]}
        self.assertEqual("zai 20 glm-x", fallback_command_arguments(body))
        self.assertIsNone(fallback_command_arguments({"messages": [{"role": "user", "content": "hi"}]}))
        lines = fallback_command(CFG, "zai 20 glm-x", self.service)
        self.assertIn("zai/glm-x serves vllm requests until", lines[0])
        self.assertIn("vllm", fallback_command(CFG, "return", self.service)[0])
        status = fallback_command(CFG, "", self.service)
        self.assertIn("Providers with stored credentials: zai", "\n".join(status))
        self.assertEqual(["Automatic provider fallback: off."], fallback_command(CFG, "off", self.service))


class MenuTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.service = ProviderFailoverService(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def answers(self, *values):
        queue = list(values)
        return lambda _label, default: queue.pop(0) if queue else default

    def test_every_operation_is_in_the_panel(self):
        provider_failover_menu.apply(CFG, "backup-add", self.answers("zai", "glm-5.3"), self.service)
        rows, values = provider_failover_menu.panel_rows(CFG, self.service)
        for value in ("toggle-enabled", "toggle-cli", "cooldown", "backup:0", "backup-add", "switch", "history", "back"):
            self.assertIn(value, values)
        self.assertIn("zai · glm-5.3  [credentials ✓]", rows[values.index("backup:0")])
        self.assertIn("auto off · 1/1 backups ready", provider_failover_menu.summary(CFG, self.service))

    def test_toggle_order_switch_history_return(self):
        provider_failover_menu.apply(CFG, "toggle-enabled", service=self.service)
        provider_failover_menu.apply(CFG, "backup-add", self.answers("zai", ""), self.service)
        self.service.update_settings(backups=[*self.service.settings()["backups"], {"provider": "kimi", "model": "k3"}])
        provider_failover_menu.apply(CFG, "backup:1", self.answers("up"), self.service)
        self.assertEqual(["kimi", "zai"], [item["provider"] for item in self.service.settings()["backups"]])
        lines = provider_failover_menu.apply(CFG, "switch", self.answers("zai", "glm-5", "5"), self.service)
        self.assertIn("zai/glm-5 serves vllm requests until", lines[0])
        rows, values = provider_failover_menu.panel_rows(CFG, self.service)
        self.assertIn("return", values)
        self.assertIn("vllm -> zai/glm-5 manual", "".join(provider_failover_menu.apply(CFG, "history", service=self.service)))
        self.assertIn("vllm serves requests again", provider_failover_menu.apply(CFG, "return", service=self.service)[0])
        self.assertTrue(self.service.settings()["enabled"])

    def test_cooldown_validation(self):
        self.assertIn("greater than 0", provider_failover_menu.apply(CFG, "cooldown", self.answers("x"), self.service)[0])
        provider_failover_menu.apply(CFG, "cooldown", self.answers("12"), self.service)
        self.assertEqual(12, self.service.settings()["default_cooldown_minutes"])


class CliLoginFallbackTests(unittest.TestCase):
    def test_all_limited_tokens_go_straight_to_the_cli_sign_in(self):
        from ciel_runtime_support.oauth_routing import OAuthRequestRun

        logs = []
        pool = SimpleNamespace(all_limited=lambda provider, excluded: True, acquire=lambda *a, **k: None)
        routing = SimpleNamespace(pool=pool, clock=lambda: 0.0)
        run = OAuthRequestRun(routing, "claude", "s", True, lambda base, lease: {}, lambda *a, **k: None,
                              lambda level, message: logs.append(message))
        base = {"Authorization": "Bearer cli-own", "anthropic-version": "2023-06-01"}
        self.assertEqual(base, run.headers(base))
        self.assertTrue(any("oauth_token_fallback_cli" in line for line in logs))
        self.assertIsNone(run.cli_login_headers(base), "only once per request")

    def test_429_on_the_last_token_retries_with_the_cli_sign_in(self):
        from ciel_runtime_support.oauth_routing import OAuthRequestRun
        from ciel_runtime_support.oauth_token_pool import OAuthLease

        state = {"limited": False}
        lease = OAuthLease("t1", "claude", SimpleNamespace(access_token="x"))
        pool = SimpleNamespace(all_limited=lambda provider, excluded: state["limited"],
                               acquire=lambda *a, **k: None if state["limited"] else lease,
                               observe=lambda *a, **k: state.update(limited=True))
        routing = SimpleNamespace(pool=pool, clock=lambda: 0.0)
        observation = SimpleNamespace(limited=True, reset_at=10.0)
        run = OAuthRequestRun(routing, "claude", "s", True, lambda base, lease: {"Authorization": "Bearer stored"},
                              lambda *a, **k: observation, lambda *_a: None)
        base = {"Authorization": "Bearer cli-own"}
        self.assertEqual({"Authorization": "Bearer stored"}, run.headers(base))
        error = urllib.error.HTTPError("u", 429, "limit", headers(retry_after=10), BytesIO(json.dumps({}).encode()))
        retry, _relay = run.retry_headers(error, base)
        self.assertEqual(base, retry)


if __name__ == "__main__":
    unittest.main()
