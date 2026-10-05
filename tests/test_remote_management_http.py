import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ciel_runtime_support.model_override import forced_model, with_forced_model
from ciel_runtime_support.oauth_token_store import OAuthCredential, OAuthTokenStore
from ciel_runtime_support.remote_management_http import (
    AUDIT_FILE,
    ManagementError,
    ManagementPorts,
    RemoteManagementController,
)
from ciel_runtime_support.web_access_http import WebAccessHttpController, WebAccessPorts

NOW = 1_790_000_000.0
CATALOG = {"cielairouter": {"ciel-sol", "ciel-luna"}, "anthropic": {"claude-opus-5-5"}}


class FakeConfig:
    """The provider configuration functions the controller drives, in memory."""

    def __init__(self) -> None:
        self.config: dict[str, Any] = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {"current_model": "claude-opus-5-5"},
                "cielairouter": {"current_model": "ciel-sol", "api_key": "sk-old-1"},
            },
        }
        self.saves = 0

    def load(self) -> dict[str, Any]:
        return copy.deepcopy(self.config)

    def save(self, config: dict[str, Any]) -> None:
        self.saves += 1
        self.config = copy.deepcopy(config)

    def current(self, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = config["current_provider"]
        return name, config["providers"][name]

    def select_provider(self, name: str) -> list[str]:
        self.config["current_provider"] = name
        self.saves += 1
        return [f"Provider set to {name}."]

    def select_model(self, model: str) -> list[str]:
        name = self.config["current_provider"]
        if model not in CATALOG.get(name, set()):
            return [f"Model selection rejected: {model} is not in the {name} catalog."]
        self.config["providers"][name]["current_model"] = model
        self.saves += 1
        return [f"Model set to {model}."]

    def keys(self, _name: str, settings: dict[str, Any]) -> list[str]:
        if settings.get("api_keys"):
            return list(settings["api_keys"])
        return [settings["api_key"]] if settings.get("api_key") else []

    def store_keys(self, name: str, keys: list[str]) -> list[str]:
        if any(" " in key for key in keys):
            raise SystemExit("API keys cannot contain spaces; unchanged.")
        settings = self.config["providers"][name]
        settings["api_key"], settings["api_keys"] = keys[0], list(keys)
        self.saves += 1
        return [f"Stored {len(keys)} key(s) for {name}."]

    def clear_keys(self, name: str) -> list[str]:
        self.config["providers"][name].pop("api_key", None)
        self.config["providers"][name].pop("api_keys", None)
        self.saves += 1
        return [f"Cleared stored API key(s) for {name}."]


class Handler:
    def __init__(self) -> None:
        self.client_address = ("100.64.0.9", 50000)
        self.command = "POST"
        self.headers: dict[str, str] = {}


class RemoteManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.dir.name)
        self.fake = FakeConfig()
        self.responses: list[tuple[dict[str, Any], int]] = []
        self.controller = RemoteManagementController(
            ManagementPorts(
                load_config=self.fake.load,
                save_config=self.fake.save,
                current_provider=self.fake.current,
                normalize_choice=lambda name: name if name == "codex-routed" else None,
                select_provider=self.fake.select_provider,
                select_model=self.fake.select_model,
                store_keys=self.fake.store_keys,
                clear_keys=self.fake.clear_keys,
                configured_keys=self.fake.keys,
                mask=lambda key: key[:3] + "..." + key[-2:],
                fingerprint=lambda key: "fp-" + key[-1],
                workspace_state_dir=lambda: self.state_dir,
                write_json=lambda _handler, payload, status=200: self.responses.append((payload, status)),
                version="0.2.51-test",
                clock=lambda: NOW,
            )
        )

    def tearDown(self) -> None:
        self.dir.cleanup()

    def post(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        self.assertTrue(self.controller.handle_post(Handler(), "/ca/manage/apply", body))
        return self.responses[-1]

    def test_state_shows_masks_and_fingerprints_never_key_values(self) -> None:
        self.assertTrue(self.controller.handle_get(Handler(), "/ca/manage/state"))
        payload, status = self.responses[-1]
        self.assertEqual(200, status)
        self.assertEqual(("anthropic", "claude-opus-5-5"), (payload["provider"], payload["model"]))
        self.assertEqual([{"mask": "sk-...-1", "fingerprint": "fp-1"}], payload["providers"]["cielairouter"]["api_keys"])
        self.assertNotIn("sk-old-1", json.dumps(payload))

    def test_provider_model_and_keys_apply_together(self) -> None:
        payload, status = self.post({
            "provider": "cielairouter",
            "model": "ciel-luna",
            "api_keys": {"cielairouter": {"mode": "append", "keys": ["sk-new-2", "sk-new-3"]}},
        })

        self.assertEqual(200, status, payload)
        self.assertEqual(("cielairouter", "ciel-luna"), (payload["provider"], payload["model"]))
        self.assertEqual(["sk-old-1", "sk-new-2", "sk-new-3"], self.fake.config["providers"]["cielairouter"]["api_keys"])
        self.assertIn("next request", payload["applies"])
        # model_scope defaults to running: the router overrides running sessions' launch model.
        self.assertEqual("ciel-luna", payload["providers"]["cielairouter"]["forced_model"])

    def test_model_scope_launch_releases_the_forced_model(self) -> None:
        self.post({"provider": "cielairouter", "model": "ciel-luna"})
        payload, status = self.post({"model": "ciel-sol", "model_scope": "launch"})
        self.assertEqual(200, status, payload)
        self.assertEqual(("ciel-sol", ""), (payload["model"], payload["providers"]["cielairouter"]["forced_model"]))
        self.post({"model": "ciel-luna"})
        payload, _status = self.post({"model_scope": "launch"})
        self.assertEqual("", payload["providers"]["cielairouter"]["forced_model"])
        for body in ({"model_scope": "running"}, {"model": "ciel-sol", "model_scope": "always"}):
            with self.subTest(body=body), self.assertRaises(ManagementError):
                self.controller.apply(body)

    def test_forced_model_replaces_the_requested_model(self) -> None:
        body = {"model": "ciel-runtime-vllm-m-old", "input": "hi"}
        self.assertIs(body, with_forced_model(body, {"current_model": "m-old"}))
        self.assertEqual({"model": "m-new", "input": "hi"}, with_forced_model(body, {"forced_model": " m-new "}))
        self.assertEqual("", forced_model(None))

    def test_remove_by_fingerprint_and_clear(self) -> None:
        self.post({"api_keys": {"cielairouter": {"mode": "replace", "keys": ["sk-a-7", "sk-b-8"]}}})
        self.post({"api_keys": {"cielairouter": {"mode": "remove", "keys": ["fp-7"]}}})
        self.assertEqual(["sk-b-8"], self.fake.config["providers"]["cielairouter"]["api_keys"])
        self.post({"api_keys": {"cielairouter": {"mode": "clear"}}})
        self.assertNotIn("api_key", self.fake.config["providers"]["cielairouter"])

    def test_rejected_model_restores_everything_already_applied(self) -> None:
        before = self.fake.load()
        payload, status = self.post({
            "provider": "cielairouter",
            "api_keys": {"cielairouter": {"mode": "replace", "keys": ["sk-new-9"]}},
            "model": "not-a-model",
        })

        self.assertEqual(400, status)
        self.assertFalse(payload["changed"])
        self.assertIn("Model selection rejected", payload["message"])
        self.assertEqual(before, self.fake.config)
        self.assertFalse((self.state_dir / AUDIT_FILE).exists())

    def test_a_failing_key_store_restores_the_provider(self) -> None:
        before = self.fake.load()
        payload, status = self.post({"provider": "cielairouter", "api_keys": {"cielairouter": {"keys": ["bad key"]}}})
        self.assertEqual((400, False), (status, payload["changed"]))
        self.assertEqual(before, self.fake.config)

    def test_invalid_change_sets_change_nothing(self) -> None:
        for body in (
            {"provider": "nope"},
            {"api_keys": {"nope": {"keys": ["k"]}}},
            {"api_keys": {"cielairouter": {"mode": "swap", "keys": ["k"]}}},
            {"oauth_tokens": [{"action": "add", "provider": "claude"}]},
            {"oauth_tokens": [{"action": "remove", "token_id": "claude-missing"}]},
            {"extra": 1},
            {},
        ):
            with self.subTest(body=body):
                with self.assertRaises(ManagementError):
                    self.controller.apply(body)
        self.assertEqual(0, self.fake.saves)

    def test_oauth_tokens_added_disabled_removed_and_audited_without_secrets(self) -> None:
        store = OAuthTokenStore(self.state_dir)
        old = store.add("codex", OAuthCredential("old-access", "old-refresh", expires_at=NOW + 3600), label="old")
        claude_file = json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat-file", "refreshToken": "rt-file", "expiresAt": int((NOW + 3600) * 1000)}})

        payload, status = self.post({"oauth_tokens": [
            {"action": "add", "provider": "claude", "label": "team", "credential": {"access_token": "sk-ant-oat-pushed", "refresh_token": "rt-pushed", "expires_at": int((NOW + 7200) * 1000)}},
            {"action": "add", "provider": "claude", "content": claude_file},
            {"action": "update", "token_id": old.token_id, "enabled": False},
        ]})

        self.assertEqual(200, status, payload)
        rows = {row["label"]: row for row in payload["oauth_tokens"]}
        self.assertEqual({"old", "team", "claude"}, set(rows))
        self.assertEqual("disabled", rows["old"]["state"])
        self.assertEqual(NOW + 7200, rows["team"]["expires_at"])
        added = [row["token_id"] for row in payload["oauth_tokens"] if row["source"] == "remote-management"]
        self.assertEqual("sk-ant-oat-pushed", store.credential(rows["team"]["token_id"]).access_token)
        dumped = json.dumps(payload) + (self.state_dir / AUDIT_FILE).read_text()
        for secret in ("sk-ant-oat-pushed", "rt-pushed", "sk-ant-oat-file", "rt-file"):
            self.assertNotIn(secret, dumped)
        self.assertEqual(2, len(added))

        self.post({"oauth_tokens": [{"action": "remove", "token_id": old.token_id}]})
        self.assertIsNone(store.snapshot().get(old.token_id))
        audit = [json.loads(line) for line in (self.state_dir / AUDIT_FILE).read_text().splitlines()]
        self.assertEqual(["100.64.0.9", "100.64.0.9"], [record["origin"] for record in audit])

    def test_web_access_controller_delegates_manage_paths(self) -> None:
        written: list[Any] = []
        web = WebAccessHttpController(WebAccessPorts(
            write_json=lambda _handler, payload, status=200: written.append((payload, status)),
            write_html=lambda *_a: None,
            accounts=lambda: None,  # type: ignore[arg-type,return-value]
            workspace_state_dir=lambda: self.state_dir,
            admin_token=None,
            external_access_enabled=lambda: False,
            management=self.controller,
        ))
        self.assertTrue(web.handle_get(Handler(), "/ca/manage/state"))
        self.assertTrue(web.handle_post(Handler(), "/ca/manage/apply", {"model": "claude-opus-5-5"}))
        self.assertFalse(web.handle_get(Handler(), "/ca/manage/other"))
        self.assertEqual(200, self.responses[-1][1])


if __name__ == "__main__":
    unittest.main()
