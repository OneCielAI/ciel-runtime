"""Router MCP attach for Muse Code: settings merge, IO, and the launch policy."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.muse_mcp import (
    MUSE_ROUTER_SERVER_NAME,
    MuseSettingsStore,
    native_settings_store,
    router_mcp_decision,
    router_mcp_entry,
    settings_store_for,
    sync_settings_text,
    wsl_settings_store,
)

WALKIE_SETTINGS = {
    "schema_version": 1,
    "provider": "meta",
    "model": "muse-spark-1.3",
    "reasoning_effort": "high",
    "mcpServers": {
        "walkie_http": {
            "type": "streamable-http",
            "url": "http://100.64.0.9:8787/api/mcp",
            "headers": {"Authorization": "Bearer wka_example"},
            "mode": "optional",
        }
    },
}


class RouterMcpEntryTests(unittest.TestCase):
    def test_entry_matches_the_settings_format(self):
        entry = router_mcp_entry("http://127.0.0.1:9611/", "tok")

        self.assertEqual(
            {
                "type": "streamable-http",
                "url": "http://127.0.0.1:9611/ca/mcp",
                "headers": {"Authorization": "Bearer tok"},
                "mode": "optional",
            },
            entry,
        )


class RouterMcpDecisionTests(unittest.TestCase):
    def decide(self, **overrides):
        values = dict(
            enabled=True,
            manage_router=True,
            base_url="http://127.0.0.1:9611",
            token="ciel-runtime-router-local-key",
            wsl=False,
            loopback=True,
        )
        values.update(overrides)
        return router_mcp_decision(**values)

    def test_native_launch_attaches_over_loopback(self):
        decision = self.decide()

        self.assertTrue(decision.attach)

    def test_wsl_launch_cannot_reach_the_windows_loopback(self):
        decision = self.decide(wsl=True)

        self.assertFalse(decision.attach)
        self.assertIn("--ca-web-address", decision.reason)

    def test_wsl_reachable_host_needs_the_external_token(self):
        denied = self.decide(wsl=True, loopback=False, base_url="http://172.29.112.1:9491", token="")
        allowed = self.decide(
            wsl=True, loopback=False, base_url="http://172.29.112.1:9491", token="ext-token"
        )

        self.assertFalse(denied.attach)
        self.assertIn("external access", denied.reason)
        self.assertTrue(allowed.attach)

    def test_disabled_or_unmanaged_launches_do_not_attach(self):
        self.assertFalse(self.decide(enabled=False).attach)
        self.assertFalse(self.decide(manage_router=False).attach)
        self.assertFalse(self.decide(base_url="").attach)


class SettingsMergeTests(unittest.TestCase):
    def test_attach_preserves_every_other_setting_and_server(self):
        text = json.dumps(WALKIE_SETTINGS)

        updated, action = sync_settings_text(text, router_mcp_entry("http://127.0.0.1:9611", "tok"))

        self.assertEqual("updated", action)
        parsed = json.loads(updated)
        self.assertEqual(1, parsed["schema_version"])
        self.assertEqual("muse-spark-1.3", parsed["model"])
        self.assertEqual("wka_example" in json.dumps(parsed), True)
        self.assertEqual(
            sorted([MUSE_ROUTER_SERVER_NAME, "walkie_http"]),
            sorted(parsed["mcpServers"]),
        )

    def test_attach_refreshes_a_stale_entry(self):
        first, _action = sync_settings_text(None, router_mcp_entry("http://127.0.0.1:9611", "old"))
        second, action = sync_settings_text(first, router_mcp_entry("http://127.0.0.1:9611", "new"))

        self.assertEqual("updated", action)
        self.assertIn("Bearer new", second)
        self.assertNotIn("Bearer old", second)

    def test_identical_entry_is_left_untouched(self):
        entry = router_mcp_entry("http://127.0.0.1:9611", "tok")
        text, _action = sync_settings_text(None, entry)

        unchanged, action = sync_settings_text(text, entry)

        self.assertIs(unchanged, text)
        self.assertEqual("unchanged", action)

    def test_removal_keeps_other_servers_and_drops_an_empty_block(self):
        text, _action = sync_settings_text(None, router_mcp_entry("http://127.0.0.1:9611", "tok"))
        with_walkie = json.dumps({**WALKIE_SETTINGS, "mcpServers": {**WALKIE_SETTINGS["mcpServers"], **json.loads(text)["mcpServers"]}})

        removed, action = sync_settings_text(with_walkie, None)
        emptied, _action2 = sync_settings_text(text, None)

        self.assertEqual("removed", action)
        self.assertEqual(["walkie_http"], list(json.loads(removed)["mcpServers"]))
        self.assertNotIn("mcpServers", json.loads(emptied))

    def test_unreadable_settings_are_never_overwritten(self):
        updated, action = sync_settings_text("not json", router_mcp_entry("http://x", "t"))

        self.assertEqual("unreadable", action)
        self.assertEqual("not json", updated)


class SettingsStoreTests(unittest.TestCase):
    def test_store_writes_only_when_something_changed(self):
        state = {"text": None}
        written: list[str] = []

        def write(text: str) -> None:
            written.append(text)
            state["text"] = text

        store = MuseSettingsStore(read=lambda: state["text"], write=write)

        first = store.sync(router_mcp_entry("http://127.0.0.1:9611", "tok"))
        second = store.sync(router_mcp_entry("http://127.0.0.1:9611", "tok"))

        self.assertEqual("updated", first)
        self.assertEqual("unchanged", second)
        self.assertEqual(1, len(written))

    def test_store_reports_instead_of_raising(self):
        logs: list[str] = []
        store = MuseSettingsStore(
            read=lambda: (_ for _ in ()).throw(OSError("boom")),
            write=lambda _text: None,
            log=lambda level, message: logs.append(f"{level} {message}"),
        )

        self.assertEqual("failed", store.sync(router_mcp_entry("http://x", "t")))
        self.assertIn("muse_mcp_settings_read_failed", logs[0])

    def test_native_store_round_trips_through_a_home_directory(self):
        with tempfile.TemporaryDirectory() as home:
            store = native_settings_store(home=Path(home))

            store.sync(router_mcp_entry("http://127.0.0.1:9611", "tok"))
            settings = json.loads(
                (Path(home) / ".config" / "muse" / "settings.json").read_text(encoding="utf-8")
            )

            self.assertIn(MUSE_ROUTER_SERVER_NAME, settings["mcpServers"])
            self.assertEqual("removed", store.sync(None))

    def test_wsl_store_runs_through_the_wsl_command(self):
        calls: list[tuple[list[str], dict]] = []

        def run(command, **kwargs):
            calls.append((list(command), kwargs))
            if "cat" in command[-1]:
                return SimpleNamespace(returncode=0, stdout=json.dumps(WALKIE_SETTINGS))
            return SimpleNamespace(returncode=0, stdout="")

        store = wsl_settings_store(run)
        action = store.sync(router_mcp_entry("http://172.29.112.1:9491", "ext"))

        self.assertEqual("updated", action)
        self.assertEqual("wsl", calls[0][0][0])
        self.assertIn("cat > $HOME/.config/muse/settings.json", calls[1][0][-1])
        payload = json.loads(calls[1][1]["input"])
        self.assertEqual(
            "http://172.29.112.1:9491/ca/mcp",
            payload["mcpServers"][MUSE_ROUTER_SERVER_NAME]["url"],
        )
        self.assertIn("walkie_http", payload["mcpServers"])

    def test_store_factory_selects_the_wsl_bridge(self):
        store = settings_store_for(wsl=True, run=lambda *a, **k: SimpleNamespace(stdout=""))
        native = settings_store_for(wsl=False, home=Path(tempfile.gettempdir()))

        self.assertIsInstance(store, MuseSettingsStore)
        self.assertIsInstance(native, MuseSettingsStore)


if __name__ == "__main__":
    unittest.main()
