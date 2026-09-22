"""Router MCP attach for Muse Code: settings merge, IO, and the launch policy."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support import muse_catalog
from ciel_runtime_support.muse_mcp import (
    MUSE_ROUTER_SERVER_NAME,
    MuseSettingsStore,
    native_settings_store,
    router_endpoint_transport,
    router_mcp_decision,
    router_mcp_entry,
    settings_store_for,
    sync_endpoint_transport_text,
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


class EndpointTransportTests(unittest.TestCase):
    """The settings pin Muse requires before it sends the Meta bearer.

    Muse 1.3.0 withholds its bearer from a base URL off its sanctioned front
    door unless ``endpoint_transport`` pins the URL with ``auth = "bearer"``
    (its own message; a ``--base-url`` flag cannot be vouched). Measured
    2026-09-21 on this machine.
    """

    def test_pin_is_written_refreshed_and_left_untouched_when_equal(self):
        pin = router_endpoint_transport("http://172.29.112.1:9494/v1")

        text, action = sync_endpoint_transport_text(None, pin)
        same, again = sync_endpoint_transport_text(text, pin)
        refreshed, refreshed_action = sync_endpoint_transport_text(
            text, router_endpoint_transport("http://172.29.112.1:9500/v1")
        )

        self.assertEqual("updated", action)
        self.assertEqual(
            {"base_url": "http://172.29.112.1:9494/v1", "auth": "bearer"},
            json.loads(text)["endpoint_transport"],
        )
        self.assertIs(same, text)
        self.assertEqual("unchanged", again)
        self.assertEqual("updated", refreshed_action)
        self.assertIn(":9500/v1", refreshed)

    def test_native_reset_removes_only_the_launchers_own_pin(self):
        pre_pin, _action = sync_endpoint_transport_text(
            json.dumps(WALKIE_SETTINGS), router_endpoint_transport("http://172.29.112.1:9494/v1")
        )

        reset, action = sync_endpoint_transport_text(pre_pin, None)

        self.assertEqual("removed", action)
        parsed = json.loads(reset)
        self.assertNotIn("endpoint_transport", parsed)
        self.assertIn("walkie_http", parsed["mcpServers"])
        self.assertEqual("muse-spark-1.3", parsed["model"])

    def test_a_foreign_transport_setting_is_kept(self):
        foreign = {
            "schema_version": 1,
            "provider": "meta",
            "endpoint_transport": {"base_url": "https://proxy.example/v1", "auth": "oauth"},
        }

        kept, action = sync_endpoint_transport_text(json.dumps(foreign), None)

        self.assertEqual("kept", action)
        self.assertEqual(foreign["endpoint_transport"], json.loads(kept)["endpoint_transport"])

    def test_unrelated_transport_fields_survive_a_pin(self):
        current = {
            "endpoint_transport": {"proxy": "http://proxy:8080"},
        }

        pinned, action = sync_endpoint_transport_text(
            json.dumps(current), router_endpoint_transport("http://127.0.0.1:9611/v1")
        )

        self.assertEqual("updated", action)
        self.assertEqual(
            {
                "proxy": "http://proxy:8080",
                "base_url": "http://127.0.0.1:9611/v1",
                "auth": "bearer",
            },
            json.loads(pinned)["endpoint_transport"],
        )

    def test_every_write_carries_schema_version(self):
        """Muse refuses to run when its settings document lacks schema_version.

        Live 2026-09-21 on a fresh pool machine: the launcher wrote
        ``{mcpServers, endpoint_transport}`` and Muse answered
        ``malformed settings file ... missing field `schema_version```
        (`muse exec` exit 1, `muse config status`
        ``enterprise_status_settings_unavailable``).
        """

        fresh, action = sync_settings_text(None, router_mcp_entry("http://127.0.0.1:9611", "tok"))
        self.assertEqual("updated", action)
        self.assertEqual(1, json.loads(fresh)["schema_version"])

        pinned, transport_action = sync_endpoint_transport_text(
            fresh, router_endpoint_transport("http://127.0.0.1:9611/v1")
        )
        self.assertEqual("updated", transport_action)
        self.assertEqual(1, json.loads(pinned)["schema_version"])

    def test_a_settings_file_that_lost_schema_version_is_repaired(self):
        without = json.dumps({"endpoint_transport": {"base_url": "http://x/v1", "auth": "bearer"}})

        repaired, action = sync_endpoint_transport_text(without, None)
        still_repaired, unchanged_action = sync_settings_text(repaired, None)

        # The pin is the launcher's own and goes away; the repair rides along.
        self.assertEqual("removed", action)
        self.assertEqual(1, json.loads(repaired)["schema_version"])
        self.assertNotIn("endpoint_transport", json.loads(repaired))
        self.assertEqual("unchanged", unchanged_action)
        self.assertEqual(1, json.loads(still_repaired)["schema_version"])

    def test_store_writes_entry_and_pin_in_one_pass(self):
        state = {"text": None}
        written: list[str] = []

        def write(text: str) -> None:
            written.append(text)
            state["text"] = text

        store = MuseSettingsStore(read=lambda: state["text"], write=write)

        action = store.sync(
            router_mcp_entry("http://172.29.112.1:9494", "tok"),
            endpoint_transport=router_endpoint_transport("http://172.29.112.1:9494/v1"),
        )
        payload = json.loads(written[-1])

        self.assertEqual("updated", action)
        self.assertEqual(1, len(written))
        self.assertIn(MUSE_ROUTER_SERVER_NAME, payload["mcpServers"])
        self.assertEqual("bearer", payload["endpoint_transport"]["auth"])

    def test_store_reports_a_reset_that_only_changed_the_pin(self):
        state = {
            "text": json.dumps(
                {
                    "schema_version": 1,
                    "provider": "meta",
                    "endpoint_transport": {
                        "base_url": "http://172.29.112.1:9494/v1",
                        "auth": "bearer",
                    },
                }
            )
        }
        logs: list[str] = []
        store = MuseSettingsStore(
            read=lambda: state["text"],
            write=lambda text: state.__setitem__("text", text),
            log=lambda level, message: logs.append(f"{level} {message}"),
        )

        action = store.sync(None)

        self.assertEqual("removed", action)
        self.assertNotIn("endpoint_transport", json.loads(state["text"]))
        self.assertTrue(any("muse_endpoint_transport_removed" in line for line in logs))


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


class MuseCatalogRelayTests(unittest.TestCase):
    """Muse asks its base host for /muse-code/models; the router relays Meta's.

    Live 2026-09-21 on a pool machine: a routed Muse answered
    ``failed to fetch model catalog: API error 404: /muse-code/models`` because
    the router had no such route.
    """

    CONFIG = {
        "providers": {
            "meta": {"base_url": "https://api.meta.ai/v1", "api_key": "k", "current_model": "muse-spark-1.3"}
        }
    }

    def test_url_uses_the_meta_host_not_the_v1_path(self):
        self.assertEqual(
            "https://api.meta.ai/muse-code/models",
            muse_catalog.muse_catalog_url("https://api.meta.ai/v1"),
        )
        self.assertEqual(
            "http://172.29.112.1:9494/muse-code/models",
            muse_catalog.muse_catalog_url("http://172.29.112.1:9494/v1"),
        )

    def test_upstream_catalog_is_relayed_verbatim(self):
        payload = {"object": "list", "data": [{"id": "muse-spark-1.3", "object": "model"}]}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        seen: dict = {}

        def urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["authorization"] = request.headers.get("Authorization")
            return Response()

        result = muse_catalog.muse_model_catalog(self.CONFIG, "meta", {}, urlopen=urlopen)

        self.assertEqual(payload, result)
        self.assertEqual("https://api.meta.ai/muse-code/models", seen["url"])
        self.assertIn("Bearer k", seen["authorization"])

    def test_missing_key_falls_back_to_the_configured_model(self):
        logs: list[str] = []

        result = muse_catalog.muse_model_catalog(
            {"providers": {"meta": {"current_model": "muse-spark-1.3-contributor"}}},
            "meta",
            {},
            log=lambda level, message: logs.append(f"{level} {message}"),
        )

        self.assertEqual("muse-spark-1.3-contributor", result["data"][0]["id"])
        self.assertEqual("missing_meta_api_key", result["muse_catalog_fallback"])
        self.assertTrue(any("muse_model_catalog_fallback" in line for line in logs))

    def test_upstream_failure_still_returns_a_catalog(self):
        def urlopen(_request, timeout=None):
            raise OSError("boom")

        result = muse_catalog.muse_model_catalog(self.CONFIG, "meta", {}, urlopen=urlopen)

        self.assertEqual("muse-spark-1.3", result["data"][0]["id"])
        self.assertTrue(str(result["muse_catalog_fallback"]).startswith("upstream_"))
