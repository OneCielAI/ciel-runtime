"""gateway_data_path=router|direct: provider-owned direct gateway data path at CLI launch."""

from __future__ import annotations

import argparse
import ast
import copy
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support import launch_data_path, runtime_launch
from ciel_runtime_support.cielairouter_gateway_overlay import select_launch_gateway
from ciel_runtime_support.cielairouter_gateways import (
    CielAiRouterGatewayService,
    DiscoveryError,
    GatewayCandidate,
    GatewaySelection,
    ProbeResult,
)
from ciel_runtime_support.launch_data_path import (
    DIRECT,
    GATEWAY_API_KEY_ENV,
    ROUTER,
    ROUTER_PATH,
    LaunchDataPath,
    LaunchDataPathResolver,
    LaunchGateway,
)
from ciel_runtime_support.runtime_paths import ROUTER_BASE
from ciel_runtime_support.runtime_session_restart import (
    RuntimeSessionRestartControl,
    RuntimeSessionRestartRequest,
)
from ciel_runtime_support.workspace_mcp import WorkspaceMcpLaunch

SECRET = "sk-ciel-router-secret-0123456789"
CENTRAL = "https://central.example"
OTHER_PROVIDERS = ("anthropic", "openrouter", "opencode", "vllm", "deepseek", "codex")


def _candidate(node="n1", endpoint="https://gw1.example"):
    now = time.time()
    return GatewayCandidate(node, endpoint, endpoint + "/healthz", "healthy", now + 300, now, 0.1, 0.0, now)


def _selection(candidate, *, degraded=False, reason="ranked", endpoint=None):
    return GatewaySelection(endpoint or (candidate.endpoint if candidate else CENTRAL), candidate,
                            ProbeResult(True, 0.01, 200) if candidate else None, reason, degraded, 1,
                            (CENTRAL, "digest", "ASTRA"))


class FakeSelector:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def select(self, base, key, model, *, fresh=False):
        self.calls.append((base, key, model, fresh))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeGatewayPolicy:
    """A provider policy that supports the direct data path and returns queued gateways."""

    supports_gateway_data_path = True

    def __init__(self, *gateways):
        self.gateways = list(gateways)
        self.calls = 0

    def launch_gateway(self, config, *, log=None):
        self.calls += 1
        gateway = self.gateways.pop(0) if len(self.gateways) > 1 else self.gateways[0]
        if isinstance(gateway, BaseException):
            raise gateway
        return gateway


class NoDirectPolicy:
    supports_gateway_data_path = False

    def launch_gateway(self, config, *, log=None):  # pragma: no cover - must never be called
        raise AssertionError("launch_gateway called for a provider without a direct data path")


def _gateway(endpoint="https://gw1.example", node="n1", model="ASTRA", key=SECRET, reason="ranked"):
    return LaunchGateway(endpoint=endpoint, node_id=node, reason=reason, central="central.example",
                         model=model, api_key=key)


class ResolverHarness(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.record = Path(temp.name) / launch_data_path.RECORD_FILE_NAME
        self.logs: list[tuple[str, str]] = []
        self.output: list[str] = []

    def resolver(self, policy):
        return LaunchDataPathResolver(lambda _provider, _pcfg: policy, lambda level, message: self.logs.append((level, message)),
                                      output=self.output.append, record_path=self.record)

    def everything_written(self):
        record = self.record.read_text(encoding="utf-8") if self.record.exists() else ""
        return " ".join([*(message for _level, message in self.logs), *self.output, record])


class LaunchDataPathResolverTests(ResolverHarness):
    def test_router_default_adds_nothing_and_never_selects(self):
        policy = FakeGatewayPolicy(_gateway())
        for pcfg in ({}, {"gateway_data_path": "router"}, {"gateway_data_path": "bogus"}):
            route = self.resolver(policy)("claude", "cielairouter", pcfg)
            self.assertEqual(ROUTER, route.active)
            self.assertFalse(route.direct)
        self.assertEqual(0, policy.calls)
        self.assertEqual([], self.logs)
        self.assertEqual([], self.output)
        self.assertFalse(self.record.exists())

    def test_router_launch_replaces_a_stale_direct_record(self):
        direct = self.resolver(FakeGatewayPolicy(_gateway()))("claude", "cielairouter", {"gateway_data_path": "direct"})
        self.assertTrue(direct.direct)
        self.resolver(FakeGatewayPolicy(_gateway()))("claude", "cielairouter", {})
        record = json.loads(self.record.read_text(encoding="utf-8"))["launches"]["claude"]
        self.assertEqual(("router", "router"), (record["configured"], record["active"]))
        self.assertIsNone(record["endpoint"])

    def test_direct_healthy_gateway_is_used_and_recorded_without_the_key(self):
        route = self.resolver(FakeGatewayPolicy(_gateway()))("codex-app-server", "cielairouter", {"gateway_data_path": "direct"})
        self.assertTrue(route.direct)
        self.assertEqual(("https://gw1.example", "n1", "ASTRA", SECRET), (route.endpoint, route.node_id, route.model, route.api_key))
        self.assertEqual([("INFO",)], [(level,) for level, _message in self.logs])
        self.assertIn("endpoint=https://gw1.example node=n1", self.logs[0][1])
        self.assertIn("router keeps MCP, channels and restart", self.logs[0][1])
        record = json.loads(self.record.read_text(encoding="utf-8"))["launches"]["codex-app-server"]
        self.assertEqual({"configured": "direct", "active": "direct", "endpoint": "https://gw1.example", "node": "n1",
                          "central": "central.example", "reason": "ranked"},
                         {key: record[key] for key in ("configured", "active", "endpoint", "node", "central", "reason")})
        self.assertNotIn(SECRET, self.everything_written())
        self.assertNotIn(SECRET, repr(route))
        self.assertNotIn(SECRET, route.summary())

    def test_every_selection_problem_falls_back_to_the_router(self):
        cases = {
            "selection off": LaunchGateway(reason="gateway_selection is off"),
            "discovery failure": LaunchGateway(reason="central base (discovery unavailable)"),
            "degraded": LaunchGateway(reason="degraded gateway (reprobed fallback)", node_id="n1"),
            "remote http": _gateway(endpoint="http://10.1.2.3:20128"),
            "credentials in url": _gateway(endpoint="https://user:pw@gw.example"),
            "no key": _gateway(key=""),
            "no model": _gateway(model=""),
            "not a gateway object": {"endpoint": "https://gw1.example"},
            "selector raised": DiscoveryError(f"boom {SECRET}"),
        }
        for name, gateway in cases.items():
            with self.subTest(name):
                self.logs.clear()
                self.output.clear()
                route = self.resolver(FakeGatewayPolicy(gateway))("claude", "cielairouter", {"gateway_data_path": "direct"})
                self.assertFalse(route.direct)
                self.assertEqual((DIRECT, ROUTER), (route.configured, route.active))
                self.assertTrue(route.reason)
                self.assertEqual("WARN", self.logs[-1][0])
                self.assertIn("active=router", self.logs[-1][1])
                self.assertIn("model requests use the local router", self.output[-1])
                self.assertNotIn(SECRET, self.everything_written())
        record = json.loads(self.record.read_text(encoding="utf-8"))["launches"]["claude"]
        self.assertEqual(("direct", "router"), (record["configured"], record["active"]))

    def test_loopback_http_gateway_is_allowed(self):
        route = self.resolver(FakeGatewayPolicy(_gateway(endpoint="http://127.0.0.1:20128")))(
            "claude", "cielairouter", {"gateway_data_path": "direct"})
        self.assertTrue(route.direct)

    def test_other_providers_never_select_a_gateway(self):
        for provider in OTHER_PROVIDERS:
            with self.subTest(provider=provider):
                policy = ciel_runtime.provider_configuration_policy(provider, {})
                self.assertFalse(policy.supports_gateway_data_path)
                self.assertIsNone(policy.launch_gateway({"gateway_data_path": "direct"}))
                route = self.resolver(NoDirectPolicy())("claude", provider, {"gateway_data_path": "direct"})
                self.assertFalse(route.direct)
                self.assertEqual("provider has no direct gateway data path", route.reason)
        self.assertFalse(self.record.exists())

    def test_marker_only_channel_delivery_keeps_the_router_before_any_selection(self):
        marker_only = launch_data_path.ChannelDelivery(terminal_wake=True)
        policy = FakeGatewayPolicy(_gateway())
        route = launch_data_path.resolve(self.resolver(policy), "codex", "cielairouter", {"gateway_data_path": "direct"},
                                         channel=marker_only)
        self.assertEqual(0, policy.calls)
        self.assertEqual((DIRECT, ROUTER, launch_data_path.CHANNEL_MARKER_REASON),
                         (route.configured, route.active, route.reason))
        self.assertEqual("WARN", self.logs[-1][0])
        self.assertIn("runtime=codex", self.logs[-1][1])
        self.assertEqual(f"router (direct unavailable: {launch_data_path.CHANNEL_MARKER_REASON})", route.summary())
        record = launch_data_path.read_records(self.record)["codex"]
        self.assertEqual(("direct", "router", launch_data_path.CHANNEL_MARKER_REASON),
                         (record["configured"], record["active"], record["reason"]))
        self.assertIn(f"codex: router (direct unavailable: {launch_data_path.CHANNEL_MARKER_REASON})",
                      "\n".join(launch_data_path.status_lines({"gateway_data_path": "direct"}, self.record)))
        self.assertLessEqual(len(launch_data_path.CHANNEL_MARKER_REASON), 160)  # records keep 160 characters

    def test_whole_message_transport_or_no_terminal_wake_permits_selection(self):
        for channel in (None, launch_data_path.ChannelDelivery(),
                        launch_data_path.ChannelDelivery(terminal_wake=True, whole_message="session_socket"),
                        launch_data_path.ChannelDelivery(whole_message="app_server")):
            with self.subTest(channel=channel):
                policy = FakeGatewayPolicy(_gateway())
                route = launch_data_path.resolve(self.resolver(policy), "codex", "cielairouter",
                                                 {"gateway_data_path": "direct"}, channel=channel)
                self.assertEqual(1, policy.calls)
                self.assertTrue(route.direct)

    def test_channel_delivery_adds_nothing_to_router_mode_or_other_providers(self):
        marker_only = launch_data_path.ChannelDelivery(terminal_wake=True)
        route = self.resolver(FakeGatewayPolicy(_gateway()))("codex", "cielairouter", {}, channel=marker_only)
        self.assertEqual((ROUTER, ROUTER, ""), (route.configured, route.active, route.reason))
        self.assertEqual(([], []), (self.logs, self.output))
        route = self.resolver(NoDirectPolicy())("codex", "vllm", {"gateway_data_path": "direct"}, channel=marker_only)
        self.assertEqual("provider has no direct gateway data path", route.reason)
        self.assertFalse(self.record.exists())

    def test_launchers_without_a_resolver_stay_on_the_router(self):
        self.assertIs(ROUTER_PATH, launch_data_path.resolve(None, "claude", "cielairouter", {}))
        self.assertIs(ROUTER_PATH, launch_data_path.resolve(mock.MagicMock(), "codex", "cielairouter", {}))

    def test_status_lines_state_the_live_reroute_limit_without_secrets(self):
        self.resolver(FakeGatewayPolicy(_gateway(endpoint="https://gw1.example/base")))(
            "claude", "cielairouter", {"gateway_data_path": "direct"})
        self.resolver(FakeGatewayPolicy(LaunchGateway(reason="central base (discovery unavailable)")))(
            "codex-app-server", "cielairouter", {"gateway_data_path": "direct"})
        text = "\n".join(launch_data_path.status_lines({"gateway_data_path": "direct"}, self.record))
        self.assertIn("Gateway data path: direct", text)
        self.assertIn("keeps the gateway it was launched with until it restarts", text)
        self.assertIn("claude: direct https://gw1.example/base (node n1; ranked)", text)
        self.assertIn("codex-app-server: router (direct unavailable: central base (discovery unavailable))", text)
        self.assertNotIn(SECRET, text)
        default = "\n".join(launch_data_path.status_lines({}, self.record.with_name("absent.json")))
        self.assertIn("Gateway data path: router (default)", default)
        self.assertIn("No CLI launch data path recorded", default)
        self.assertEqual("https://gw.example:8443/x", launch_data_path.display_endpoint("https://u:p@gw.example:8443/x/?k=v#f"))


class SelectLaunchGatewayTests(unittest.TestCase):
    def pcfg(self, **overrides):
        return {"base_url": CENTRAL, "api_key": SECRET, "current_model": "ASTRA", **overrides}

    def test_healthy_gateway_comes_from_the_existing_selector_with_a_fresh_rank(self):
        selector = FakeSelector(_selection(_candidate()))
        gateway = select_launch_gateway(self.pcfg(), selector_factory=lambda: selector)
        self.assertEqual(("https://gw1.example", "n1", "ranked", "central.example", "ASTRA", SECRET),
                         (gateway.endpoint, gateway.node_id, gateway.reason, gateway.central, gateway.model, gateway.api_key))
        self.assertEqual([(CENTRAL, SECRET, "ASTRA", True)], selector.calls)

    def test_launch_model_is_the_upstream_id_the_router_would_send(self):
        selector = FakeSelector(_selection(_candidate()), _selection(_candidate()))
        self.assertEqual("cc/claude-opus-5-5", select_launch_gateway(
            self.pcfg(current_model="cc/claude-opus-5-5[1m]"), selector_factory=lambda: selector).model)
        self.assertEqual("forced/model", select_launch_gateway(
            self.pcfg(forced_model="forced/model"), selector_factory=lambda: selector).model)

    def test_unusable_outcomes_carry_a_reason_and_no_endpoint(self):
        cases = {
            "degraded": (_selection(_candidate(), degraded=True, reason="reprobed fallback"), "degraded"),
            "central": (_selection(None, reason="no healthy gateway"), "central base"),
            "gateway equals central": (_selection(_candidate(endpoint=CENTRAL)), "central base"),
            "selector error": (DiscoveryError("discovery unavailable"), "gateway selection failed (DiscoveryError)"),
        }
        for name, (result, reason) in cases.items():
            with self.subTest(name):
                logs = []
                gateway = select_launch_gateway(self.pcfg(), selector_factory=lambda: FakeSelector(result),
                                                log=lambda level, message: logs.append(message))
                self.assertEqual("", gateway.endpoint)
                self.assertIn(reason, gateway.reason)
                self.assertNotIn(SECRET, " ".join([gateway.reason, *logs]))

    def test_selection_off_or_missing_key_never_loads_the_selector(self):
        factory = mock.Mock(side_effect=AssertionError("selector loaded"))
        self.assertIn("off", select_launch_gateway(self.pcfg(gateway_selection="off"), selector_factory=factory).reason)
        self.assertEqual("no API key", select_launch_gateway(self.pcfg(api_key=""), selector_factory=factory).reason)
        factory.assert_not_called()

    def test_real_selector_ranks_discovery_and_rejects_remote_plain_http(self):
        def payload(endpoint):
            now = datetime.now(timezone.utc)
            stamp = now.isoformat().replace("+00:00", "Z")
            until = datetime.fromtimestamp(now.timestamp() + 300, timezone.utc).isoformat().replace("+00:00", "Z")
            return {"object": "list", "protocol_version": 1, "generated_at": stamp, "data": [
                {"id": "n1", "endpoint": endpoint, "status": "online", "health": "healthy",
                 "lastSeenAt": stamp, "availableUntil": until}]}

        seen_keys = []

        def service(endpoint):
            def discovery(base, key, timeout):
                seen_keys.append(key)
                return payload(endpoint)
            return CielAiRouterGatewayService(discovery=discovery,
                                              probe=lambda candidate, timeout: ProbeResult(True, 0.01, 200, candidate.node_id, "gateway", True))

        healthy = select_launch_gateway(self.pcfg(), selector_factory=lambda: service("https://gw1.example/v1"))
        self.assertEqual(("https://gw1.example", "n1"), (healthy.endpoint, healthy.node_id))
        remote_http = select_launch_gateway(self.pcfg(), selector_factory=lambda: service("http://10.1.2.3:20128"))
        self.assertEqual("", remote_http.endpoint)
        self.assertIn("central base", remote_http.reason)
        self.assertEqual([SECRET, SECRET], seen_keys)

    def test_cielairouter_policy_owns_the_capability_and_delegates_selection(self):
        policy = ciel_runtime.provider_configuration_policy("cielairouter", {})
        self.assertTrue(policy.supports_gateway_data_path)
        adapter = ciel_runtime.configured_provider_adapter("cielairouter", {})
        self.assertTrue(adapter.option_presentation_policy(ciel_runtime.provider_contract_config("cielairouter", {})).show_gateway_data_path)
        for provider in OTHER_PROVIDERS:
            other = ciel_runtime.configured_provider_adapter(provider, {})
            self.assertFalse(other.option_presentation_policy(ciel_runtime.provider_contract_config(provider, {})).show_gateway_data_path)
        with mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.select_launch_gateway", return_value="picked") as select:
            self.assertEqual("picked", policy.launch_gateway({"x": 1}, log=print))
        select.assert_called_once_with({"x": 1}, log=print)


class ClaudeEnvironmentTests(unittest.TestCase):
    IDS = ["ASTRA", "cc/claude-opus-5-5", "cc/claude-haiku-4-5", "cc/claude-sonnet-5"]

    def env(self, data_path=None, **pcfg):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        cfg["current_provider"] = "cielairouter"
        cfg["providers"]["cielairouter"].update(api_key=SECRET, current_model="cc/claude-opus-5-5", **pcfg)
        with (mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=self.IDS),
              mock.patch.object(ciel_runtime, "cached_or_configured_model_ids", return_value=self.IDS),
              mock.patch.object(ciel_runtime, "http_json", side_effect=AssertionError("network"))):
            return ciel_runtime.env_vars(cfg) if data_path is None else ciel_runtime.env_vars(cfg, data_path=data_path)

    def test_router_mode_environment_is_unchanged(self):
        routed = self.env()
        self.assertEqual(routed, self.env(ROUTER_PATH))
        self.assertEqual(routed, self.env(LaunchDataPath("claude", DIRECT, ROUTER, reason="discovery unavailable")))
        self.assertEqual(routed, self.env(gateway_data_path="direct"))
        self.assertEqual(ROUTER_BASE, routed["ANTHROPIC_BASE_URL"])
        self.assertTrue(routed["ANTHROPIC_MODEL"].startswith("ciel-runtime-cielairouter-"))

    def test_direct_environment_points_at_the_gateway_with_upstream_models(self):
        routed = self.env()
        direct = self.env(LaunchDataPath("claude", DIRECT, DIRECT, "https://gw1.example", "n1", "central.example",
                                         "ranked", "cc/claude-opus-5-5", SECRET))
        self.assertEqual("https://gw1.example", direct["ANTHROPIC_BASE_URL"])
        self.assertEqual(SECRET, direct["ANTHROPIC_AUTH_TOKEN"])
        self.assertEqual("cc/claude-opus-5-5", direct["ANTHROPIC_MODEL"])
        self.assertEqual("cc/claude-haiku-4-5", direct["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
        self.assertEqual("cc/claude-sonnet-5", direct["ANTHROPIC_DEFAULT_SONNET_MODEL"])
        self.assertFalse([key for key, value in direct.items() if "ciel-runtime-cielairouter-" in value])
        changed = {key for key in set(routed) | set(direct) if routed.get(key) != direct.get(key)}
        self.assertEqual({"ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION",
                          "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                          "CLAUDE_CODE_SUBAGENT_MODEL", "CIEL_RUNTIME_MODEL_ALIAS"}, changed)


class CodexConfigurationTests(unittest.TestCase):
    def test_default_provider_block_is_unchanged_and_direct_names_the_gateway(self):
        routed = ciel_runtime.codex_runtime_config_args()
        self.assertEqual(routed, ciel_runtime.codex_runtime_config_args(None, env_key=None))
        self.assertIn(f'model_providers.ciel-runtime.base_url="{ROUTER_BASE}/v1"', routed)
        direct = ciel_runtime.codex_runtime_config_args("https://gw1.example", env_key=GATEWAY_API_KEY_ENV)
        self.assertIn('model_providers.ciel-runtime.base_url="https://gw1.example/v1"', direct)
        self.assertIn(f'model_providers.ciel-runtime.env_key="{GATEWAY_API_KEY_ENV}"', direct)
        self.assertEqual(len(routed), len(direct))
        self.assertEqual([item for item in routed if "base_url" not in item and "env_key" not in item],
                         [item for item in direct if "base_url" not in item and "env_key" not in item])

    def test_catalog_slug_is_the_upstream_model_in_direct_mode(self):
        service = ciel_runtime._CODEX_LAUNCH_CONFIGURATION
        cfg = {"current_provider": "cielairouter", "providers": {"cielairouter": {"current_model": "ASTRA", "context_window": 100000}}}
        with mock.patch.object(ciel_runtime, "current_alias", return_value="ciel-runtime-cielairouter-astra"):
            self.assertEqual("ciel-runtime-cielairouter-astra", service._launch_model_snapshot(cfg).spec.alias)
            self.assertEqual("ASTRA", service._launch_model_snapshot(cfg, "ASTRA").spec.alias)

    def test_restart_command_rewrites_only_the_provider_block(self):
        cmd = ["codex", "-c", 'model_providers.ciel-runtime.base_url="https://gw1.example/v1"',
               "-c", f'model_providers.ciel-runtime.env_key="{GATEWAY_API_KEY_ENV}"',
               "-c", 'mcp_servers.ciel-runtime-router.url="http://127.0.0.1:1/ca/mcp"', "-m", "ASTRA"]
        out = launch_data_path.codex_provider_command(cmd, "ciel-runtime", "https://gw2.example", GATEWAY_API_KEY_ENV)
        self.assertEqual('model_providers.ciel-runtime.base_url="https://gw2.example/v1"', out[2])
        self.assertEqual(cmd[3:], out[3:])
        back = launch_data_path.codex_provider_command(cmd, "ciel-runtime", ROUTER_BASE, "CIEL_RUNTIME_CODEX_API_KEY")
        self.assertEqual(f'model_providers.ciel-runtime.base_url="{ROUTER_BASE}/v1"', back[2])
        self.assertEqual('model_providers.ciel-runtime.env_key="CIEL_RUNTIME_CODEX_API_KEY"', back[4])
        self.assertEqual(cmd[5:], back[5:])


def _request(runtime, request_id="restart-1"):
    return RuntimeSessionRestartRequest(
        id=request_id, source="ciel-runtime-router-tool", reason="deploy", runtime=runtime,
        target_pid=os.getpid(), resume=True, requested_at=time.time(), expires_at=time.time() + 60,
    )


def _polls(*requests):
    queue = list(requests)
    return lambda: queue.pop(0) if queue else None


class ClaudeLaunchTests(ResolverHarness):
    ROUTED = {
        "ANTHROPIC_BASE_URL": ROUTER_BASE, "ANTHROPIC_AUTH_TOKEN": SECRET,
        "ANTHROPIC_MODEL": "ciel-runtime-cielairouter-astra", "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-cielairouter-astra",
    }

    def services(self, resolver, control, launches, injected, data_path_setting="direct", *,
                 socket="session.sock", stdin_proxy=True):
        def env_vars(_cfg, data_path=None):
            env = dict(self.ROUTED)
            if data_path is not None and data_path.direct:
                env.update(ANTHROPIC_BASE_URL=data_path.endpoint, ANTHROPIC_AUTH_TOKEN=data_path.api_key,
                           ANTHROPIC_MODEL=data_path.model, CIEL_RUNTIME_MODEL_ALIAS=data_path.model)
            return env

        def materialize(_runtime, executable, env, _provider, _config, **kwargs):
            options = kwargs["options"]
            return [executable, "--model", options["model"], *options["extra_args"]], env

        def call_with_wake(command, env, *_record_path, **kwargs):
            launches.append((list(command), dict(env)))
            self.parent_bypass.append(os.environ.get(launch_data_path.ACTIVE_ENV))
            poll, state = kwargs.get("restart_poll"), kwargs.get("restart_state")
            claimed = poll() if poll is not None else None
            if claimed is not None:
                state.mark(claimed)
            return 0

        class WorkspaceMcp:
            def prepare(self, runtime, cfg, env, injected_servers=None, native=False):
                injected.append(injected_servers)
                return WorkspaceMcpLaunch(claude_config_paths=(Path("workspace-mcp.json"),))

            def renew(self, previous, runtime, cfg, env, injected_servers=None, native=False):
                injected.append(injected_servers)
                return previous

            def finish(self, launch):
                return None

        no_op = lambda *_args, **_kwargs: None  # noqa: E731
        pcfg = {"current_model": "ASTRA"}
        if data_path_setting:
            pcfg["gateway_data_path"] = data_path_setting
        cfg = {"current_provider": "cielairouter", "providers": {"cielairouter": pcfg}}
        return runtime_launch.ClaudeLaunchServices(
            constants=runtime_launch.build_default_claude_launch_constants(),
            process=runtime_launch.ClaudeLaunchProcess(no_op, lambda *a, **k: 0, lambda *a, **k: False, env_vars,
                                                      lambda _path: 0, lambda env: env.get("PATH", ""), no_op,
                                                      call_with_wake, call_with_wake),
            installation=runtime_launch.ClaudeLaunchInstallation(lambda _name: "claude", no_op, no_op, lambda: "claude",
                                                                 no_op, no_op, lambda _cfg: [], no_op),
            dispatch=runtime_launch.ClaudeLaunchDispatch(no_op, no_op, no_op, materialize, no_op,
                                                         lambda executable, enabled=True: executable,
                                                         lambda *a, **k: 0, lambda _provider: True),
            config=runtime_launch.ClaudeLaunchConfig(lambda: cfg, no_op, lambda c: ("cielairouter", c["providers"]["cielairouter"]),
                                                     lambda *_a: (True, []), no_op, lambda *_a: [], lambda provider, _c: provider,
                                                     lambda *_a: "routed", lambda: "cwd-key", resolver),
            routing=runtime_launch.ClaudeLaunchRouting(lambda *_a: False, lambda *_a: False, no_op, lambda: True, no_op,
                                                       lambda: "healthy", no_op, mock.Mock(return_value=True),
                                                       lambda callback, _managed: callback(), no_op),
            policy=runtime_launch.ClaudeLaunchPolicy(no_op, lambda _e: False, lambda _a: False,
                                                     lambda args, *options: any(str(v).startswith(options) for v in args),
                                                     lambda *_a: False, lambda *_a: False, lambda *_a: False,
                                                     lambda *_a: (False, ""), lambda *_a: False),
            channel_delivery=runtime_launch.ClaudeLaunchChannelDelivery(lambda *_a: True, lambda *_a: stdin_proxy,
                                                                        lambda: 0.0, lambda: 1, no_op,
                                                                        lambda _c, _a: socket, no_op),
            mcp_config=runtime_launch.ClaudeLaunchMcpConfig(no_op, no_op, WorkspaceMcp()),
            restart=runtime_launch.SessionRestartPorts(lambda: control),
        )

    def launch(self, resolver, *requests, data_path_setting="direct", **channel):
        launches, injected = [], []
        self.parent_bypass = []
        control = RuntimeSessionRestartControl(poll=_polls(*requests))
        services = self.services(resolver, control, launches, injected, data_path_setting, **channel)
        with mock.patch.dict(os.environ), redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime_launch.run_claude([], skip_menu=True, update_check=False,
                                                          self_update_check=False, services=services))
        return launches, injected, services

    def test_router_mode_launch_is_identical_with_or_without_the_resolver(self):
        without, injected_without, _ = self.launch(None)
        with_router, injected_router, _ = self.launch(self.resolver(NoDirectPolicy()))
        self.assertEqual(without, with_router)
        self.assertEqual(injected_without, injected_router)
        self.assertEqual(ROUTER_BASE, without[0][1]["ANTHROPIC_BASE_URL"])

    def test_default_router_setting_never_selects_and_matches_a_launch_without_resolver(self):
        policy = FakeGatewayPolicy(_gateway())
        for setting in ("", "router"):
            with self.subTest(setting=setting):
                baseline = self.launch(None, _request("claude"), data_path_setting=setting)[:2]
                self.assertEqual(baseline, self.launch(self.resolver(policy), _request("claude"), data_path_setting=setting)[:2])
                self.assertEqual([ROUTER_BASE, ROUTER_BASE], [env["ANTHROPIC_BASE_URL"] for _cmd, env in baseline[0]])
        self.assertEqual(0, policy.calls)
        self.assertEqual([], self.output)

    def test_direct_launch_sends_models_to_the_gateway_and_keeps_the_control_plane(self):
        launches, injected, services = self.launch(self.resolver(FakeGatewayPolicy(_gateway())))
        cmd, env = launches[0]
        self.assertEqual("https://gw1.example", env["ANTHROPIC_BASE_URL"])
        self.assertEqual("ASTRA", cmd[cmd.index("--model") + 1])
        self.assertNotIn(SECRET, " ".join(cmd))
        self.assertIn("--mcp-config", cmd)
        self.assertEqual("session.sock", cmd[cmd.index("--messaging-socket-path") + 1])
        self.assertEqual([{"ciel-runtime-router": {"transport": "streamable_http", "url": f"{ROUTER_BASE}/ca/mcp",
                                                   "runtimes": ["claude"]}}], injected)
        services.routing.start_router_if_needed.assert_called_once()

    def test_restart_selects_again_and_falls_back_to_the_router(self):
        policy = FakeGatewayPolicy(_gateway(), _gateway(endpoint="https://gw2.example", node="n2"),
                                   LaunchGateway(reason="central base (no healthy gateway)"))
        launches, injected, _ = self.launch(self.resolver(policy), _request("claude", "r1"), _request("claude", "r2"))
        self.assertEqual(["https://gw1.example", "https://gw2.example", ROUTER_BASE],
                         [env["ANTHROPIC_BASE_URL"] for _cmd, env in launches])
        self.assertEqual([SECRET] * 3, [env["ANTHROPIC_AUTH_TOKEN"] for _cmd, env in launches])
        self.assertEqual(3, len(injected))
        self.assertNotIn(SECRET, self.everything_written())

    def test_router_fallback_at_launch_is_never_upgraded_on_restart(self):
        policy = FakeGatewayPolicy(LaunchGateway(reason="discovery unavailable"), _gateway())
        launches, _injected, _ = self.launch(self.resolver(policy), _request("claude"))
        self.assertEqual([ROUTER_BASE, ROUTER_BASE], [env["ANTHROPIC_BASE_URL"] for _cmd, env in launches])
        self.assertEqual(1, policy.calls)

    def test_marker_only_wakes_without_a_messaging_socket_keep_the_router(self):
        # Session-socket input off: every LLM-delivery wake is a bare [ciel-wake] marker the router fills in.
        policy = FakeGatewayPolicy(_gateway(), _gateway())
        launches, _injected, _ = self.launch(self.resolver(policy), _request("claude"), socket="")
        self.assertEqual(0, policy.calls)
        self.assertEqual([ROUTER_BASE, ROUTER_BASE], [env["ANTHROPIC_BASE_URL"] for _cmd, env in launches])
        self.assertEqual(["ciel-runtime-cielairouter-astra"] * 2, [cmd[cmd.index("--model") + 1] for cmd, _env in launches])
        self.assertEqual([None, None], self.parent_bypass)
        record = launch_data_path.read_records(self.record)["claude"]
        self.assertEqual((DIRECT, ROUTER, launch_data_path.CHANNEL_MARKER_REASON),
                         (record["configured"], record["active"], record["reason"]))
        self.assertIn(launch_data_path.CHANNEL_MARKER_REASON, " ".join(self.output))

    def test_messaging_socket_or_no_terminal_wake_permits_the_gateway(self):
        for channel in ({"socket": "session.sock"}, {"socket": "", "stdin_proxy": False}):
            with self.subTest(**channel):
                policy = FakeGatewayPolicy(_gateway())
                launches, _injected, _ = self.launch(self.resolver(policy), **channel)
                self.assertEqual(1, policy.calls)
                self.assertEqual("https://gw1.example", launches[0][1]["ANTHROPIC_BASE_URL"])
                self.assertEqual(["1"], self.parent_bypass)


class CodexLaunchTests(ResolverHarness):
    def codex(self, resolver, *requests, data_path_setting="direct", delivery_mode="llm"):
        launches = []
        self.parent_bypass = []
        self.wake_options = []
        control = RuntimeSessionRestartControl(poll=_polls(*requests))
        services = mock.MagicMock()
        services.data_path = resolver
        services.constants.CODEX_RUNTIME_API_KEY_ENV = "CIEL_RUNTIME_CODEX_API_KEY"
        services.constants.CONFIG_DIR = Path("config-dir")
        for name, value in (("PRELAUNCH_CANCEL", -1), ("PRELAUNCH_LAUNCH_CLAUDE", 1), ("PRELAUNCH_LAUNCH_CODEX", 2),
                            ("PRELAUNCH_LAUNCH_AGY", 3), ("PRELAUNCH_LAUNCH_CODEX_APP_SERVER", 4), ("PRELAUNCH_LAUNCH_CODEX_REMOTE", 5)):
            setattr(services.constants, name, value)
        services.process._channel_wake_enter_env_is_fixed.return_value = True
        services.process._codex_channel_wake_submit_delay_seconds.return_value = 0.0
        services.process._codex_channel_wake_submit_retries.return_value = 1
        services.process.codex_process_record_path.return_value = None
        services.process.path_with_ciel_runtime_user_dirs.side_effect = lambda env: env.get("PATH", "")

        def call_with_wake(command, env, **kwargs):
            launches.append((list(command), dict(env)))
            self.parent_bypass.append(os.environ.get(launch_data_path.ACTIVE_ENV))
            self.wake_options.append({key: kwargs.get(key) for key in ("wake_for_llm_delivery", "channel_wake_display_body")})
            poll, state = kwargs.get("restart_poll"), kwargs.get("restart_state")
            claimed = poll() if poll is not None else None
            if claimed is not None:
                state.mark(claimed)
            return 0

        services.process.subprocess_call_with_channel_wake_proxy.side_effect = call_with_wake
        pcfg = {"current_model": "ASTRA"}
        if data_path_setting:
            pcfg["gateway_data_path"] = data_path_setting
        services.config.load_config.return_value = {"providers": {"cielairouter": pcfg}}
        services.config.get_current_provider.return_value = ("cielairouter", pcfg)
        services.config.current_alias.return_value = "ciel-runtime-cielairouter-astra"
        services.config.apply_launch_endpoint_policy.return_value = []
        services.config.codex_runtime_model_catalog_args.side_effect = (
            lambda _codex, _cfg, _passthrough, **options: ["-c", f"model_catalog_json=\"{options.get('model_slug', 'alias')}.json\""])
        services.config.workspace_mcp = None
        services.cli_policy.codex_passthrough_args_for_launch.return_value = ([], [])
        services.cli_policy.codex_yolo_launch_args.return_value = ["--yolo"]
        services.cli_policy.codex_current_model_cli_args.side_effect = lambda config, _passthrough: ["-m", config["current_model"]]
        services.cli_policy.codex_runtime_config_args.side_effect = ciel_runtime.codex_runtime_config_args
        services.cli_policy.codex_alternate_screen_compat_args.return_value = []
        services.cli_policy.codex_passthrough_has_command.return_value = False
        services.cli_policy.codex_resume_picker_requested.return_value = False
        services.cli_policy.codex_help_requested.return_value = False

        def materialize(_runtime, executable, env, _provider, _config, **kwargs):
            options = kwargs["options"]
            return [executable, *options["yolo_args"], *options["router_config_args"], *options["model_catalog_args"],
                    *options["mcp_args"], *options["model_alias_args"], *kwargs["passthrough"]], env

        services.dispatch.materialize_runtime_command.side_effect = materialize
        services.dispatch.run_prelaunch_menu.return_value = 0
        services.dispatch.run_codex_update_check.side_effect = lambda executable, enabled=True: executable
        services.installation.find_executable.return_value = "codex"
        services.installation.install_codex_if_missing.return_value = "codex"
        services.routing.native_codex_enabled.return_value = False
        services.routing.direct_native_codex_enabled.return_value = False
        services.routing.codex_routed_enabled.return_value = False
        services.routing.launch_readiness_errors.return_value = []
        services.routing.run_with_router_lifetime.side_effect = lambda callback, _managed: callback()
        services.routing.start_router_if_needed.return_value = False
        services.channel.channel_delivery_mode.return_value = delivery_mode
        services.channel.codex_mcp_native_http_compat_args.return_value = [
            "-c", f'mcp_servers.ciel-runtime-router.url="{ROUTER_BASE}/ca/mcp"']
        services.restart.new_control.return_value = control
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ))
            os.environ.pop(GATEWAY_API_KEY_ENV, None)
            os.environ.pop("CIEL_RUNTIME_CODEX_API_KEY", None)
            stack.enter_context(mock.patch.object(runtime_launch, "codex_native_web_tool_overrides", return_value=[]))
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(0, runtime_launch.run_codex([], skip_menu=True, update_check=False,
                                                         self_update_check=False, services=services))
        return launches, services

    def test_router_mode_launch_is_identical_with_or_without_the_resolver(self):
        without, _ = self.codex(None)
        with_router, _ = self.codex(self.resolver(NoDirectPolicy()))
        self.assertEqual(without, with_router)
        cmd, env = without[0]
        self.assertIn(f'model_providers.ciel-runtime.base_url="{ROUTER_BASE}/v1"', cmd)
        self.assertEqual("ciel-runtime-cielairouter-astra", cmd[cmd.index("-m") + 1])
        self.assertNotIn(GATEWAY_API_KEY_ENV, env)

    def test_default_router_setting_never_selects_and_matches_a_launch_without_resolver(self):
        policy = FakeGatewayPolicy(_gateway())
        for setting in ("", "router"):
            with self.subTest(setting=setting):
                baseline, _ = self.codex(None, _request("codex"), data_path_setting=setting)
                self.assertEqual(baseline, self.codex(self.resolver(policy), _request("codex"), data_path_setting=setting)[0])
                self.assertEqual("ciel-runtime-router-local-key", baseline[0][1]["CIEL_RUNTIME_CODEX_API_KEY"])
        self.assertEqual(0, policy.calls)

    def test_direct_requested_plain_tui_explicitly_falls_back_without_selecting(self):
        policy = FakeGatewayPolicy(_gateway(), _gateway())
        launches, services = self.codex(self.resolver(policy), _request("codex"))
        self.assertEqual(0, policy.calls)
        self.assertEqual(2, len(launches))  # the launch and its session restart
        for cmd, env in launches:
            self.assertIn(f'model_providers.ciel-runtime.base_url="{ROUTER_BASE}/v1"', cmd)
            self.assertEqual("ciel-runtime-cielairouter-astra", cmd[cmd.index("-m") + 1])
            self.assertFalse(any(launch_data_path.DIRECT_PROVIDER_ID in part for part in cmd))
            self.assertNotIn(GATEWAY_API_KEY_ENV, env)
            self.assertEqual("ciel-runtime-router-local-key", env["CIEL_RUNTIME_CODEX_API_KEY"])
        # The wake proxy keeps its router-injection protocol: no direct-delivery flag in the parent.
        self.assertEqual([None, None], self.parent_bypass)
        self.assertEqual([True, True], [options["wake_for_llm_delivery"] for options in self.wake_options])
        record = launch_data_path.read_records(self.record)["codex"]
        self.assertEqual((DIRECT, ROUTER, launch_data_path.CHANNEL_MARKER_REASON),
                         (record["configured"], record["active"], record["reason"]))
        self.assertIn(launch_data_path.CHANNEL_MARKER_REASON, " ".join(self.output))
        self.assertTrue(any(level == "WARN" and "active=router" in message for level, message in self.logs))
        services.routing.start_router_if_needed.assert_called_once()

    def test_the_guard_follows_channel_delivery_not_the_runtime_name(self):
        # Without LLM-delivery wakes the plain TUI has nothing for the router to fill in.
        policy = FakeGatewayPolicy(_gateway(), _gateway(endpoint="https://gw2.example", node="n2"))
        launches, _services = self.codex(self.resolver(policy), _request("codex"), delivery_mode="tty")
        self.assertEqual(2, policy.calls)
        self.assertEqual(['model_providers.ciel-runtime-gateway.base_url="https://gw1.example/v1"',
                          'model_providers.ciel-runtime-gateway.base_url="https://gw2.example/v1"'],
                         [next(part for part in cmd if "base_url" in part) for cmd, _env in launches])


class CodexAppServerLaunchTests(ResolverHarness):
    def run_server(self, resolver, relaunch_requests=()):
        launches = []

        def session(cmd, env, _cwd, run_server=None, relaunch=None, **_kwargs):
            launches.append((list(cmd), dict(env)))
            for request in relaunch_requests:
                cmd = relaunch(request)
                launches.append((list(cmd), dict(env)))
            return 0

        session = mock.Mock(side_effect=session)
        session.launch_mode = "codex-remote-router"
        session.display_name = "TUI + app-server"
        session.relaunches_server = True
        session.split_passthrough = mock.Mock(side_effect=lambda passthrough: (list(passthrough), {}))
        pcfg = {"gateway_data_path": "direct", "current_model": "ASTRA"}
        cfg = {"current_provider": "cielairouter", "providers": {"cielairouter": pcfg}}
        with ExitStack() as stack:
            for name in ("sync_remote_launch_assets", "terminate_existing_codex_processes_for_launch", "terminate_existing_router_clients_for_launch"):
                stack.enter_context(mock.patch.object(ciel_runtime, name))
            stack.enter_context(mock.patch.object(runtime_launch, "codex_native_web_tool_overrides", return_value=[]))
            stack.enter_context(mock.patch.dict(os.environ))
            os.environ.pop(GATEWAY_API_KEY_ENV, None)
            services = ciel_runtime.codex_app_server_launch_services()
            services = replace(services,
                installation=replace(services.installation, warn_if_multiple_ciel_runtime_installs=mock.Mock(), install_codex_if_missing=lambda: "codex", find_executable=lambda _: "codex"),
                dispatch=replace(services.dispatch, run_ciel_runtime_update_check=mock.Mock(), run_codex_update_check=lambda *a, **kw: "codex", run_prelaunch_menu=lambda *a, **kw: 0),
                config=replace(services.config, load_config=lambda: cfg, get_current_provider=lambda _: ("cielairouter", pcfg), apply_launch_endpoint_policy=lambda *a: [],
                               ensure_model_cache_for_launch=mock.Mock(), current_alias=lambda _: "ciel-runtime-cielairouter-astra",
                               codex_runtime_model_catalog_args=lambda *a, **kw: ["-c", f'model_catalog_json="{kw.get("model_slug", "router")}.json"'], record_launch_state_for_cwd=mock.Mock(), workspace_mcp=None),
                routing=replace(services.routing, codex_launch_enabled_for_provider=lambda _: True, launch_readiness_errors=lambda _: [], cleanup_managed_services_for_provider=mock.Mock(),
                                native_codex_enabled=lambda _: False, direct_native_codex_enabled=lambda *a: False, codex_routed_enabled=lambda *a: False,
                                start_router_if_needed=lambda **kw: False, run_with_router_lifetime=lambda runner, _: runner()),
                channel=replace(services.channel, codex_mcp_native_http_compat_args=lambda *a, **kw: []),
                restart=runtime_launch.SessionRestartPorts(),
                data_path=resolver,
            )
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(0, runtime_launch.run_codex_app_server(["--listen", "ws://127.0.0.1:8899"], services=services, desktop=session))
        return launches

    def test_router_mode_server_command_is_identical_with_or_without_the_resolver(self):
        self.assertEqual(self.run_server(None), self.run_server(self.resolver(NoDirectPolicy())))

    def test_direct_server_and_remote_tui_overrides_name_the_gateway(self):
        from ciel_runtime_support.codex_app_server_session import remote_tui_command

        launches = self.run_server(self.resolver(FakeGatewayPolicy(_gateway())))
        cmd, env = launches[0]
        self.assertIn('model_providers.ciel-runtime-gateway.base_url="https://gw1.example/v1"', cmd)
        self.assertIn('model="ASTRA"', cmd)
        self.assertEqual(SECRET, env[GATEWAY_API_KEY_ENV])
        self.assertNotIn(SECRET, " ".join(cmd))
        tui = remote_tui_command(cmd, "ws://127.0.0.1:8899")
        self.assertIn('model_providers.ciel-runtime-gateway.base_url="https://gw1.example/v1"', tui)
        self.assertNotIn(SECRET, " ".join(tui))

    def test_relaunch_selects_again(self):
        policy = FakeGatewayPolicy(_gateway(), _gateway(endpoint="https://gw2.example", node="n2", model="next/model"))
        launches = self.run_server(self.resolver(policy), relaunch_requests=(_request("codex"),))
        self.assertEqual(['model_providers.ciel-runtime-gateway.base_url="https://gw1.example/v1"',
                          'model_providers.ciel-runtime-gateway.base_url="https://gw2.example/v1"'],
                         [next(item for item in cmd if "base_url" in item) for cmd, _env in launches])
        self.assertIn('model="next/model"', launches[1][0])
        self.assertIn('model_catalog_json="next/model.json"', launches[1][0])
        self.assertNotIn('model_catalog_json="ASTRA.json"', launches[1][0])


class GatewayDataPathOptionTests(unittest.TestCase):
    def config(self):
        return copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)

    def test_cli_round_trip_for_cielairouter_only(self):
        cfg = self.config()
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache") as clear,
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity") as cap,
              redirect_stdout(io.StringIO()) as out):
            for value in ("direct", "router"):
                ciel_runtime.cmd_provider_options(argparse.Namespace(values=["cielairouter", f"gateway_data_path={value}"]))
                self.assertEqual(value, cfg["providers"]["cielairouter"]["gateway_data_path"])
                self.assertIn(f"gateway_data_path={value}", out.getvalue())
            self.assertEqual(2, save.call_count)
            clear.assert_not_called()
            cap.assert_not_called()
        self.assertIn("no live re-route", out.getvalue())

    def test_validation_and_rejection_for_other_providers(self):
        pcfg = {}
        for value in ("on", "gateway", "", "Direct-ish"):
            with self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option("cielairouter", pcfg, f"gateway_data_path={value}")
        with self.assertRaises(SystemExit):
            ciel_runtime.apply_provider_option("cielairouter", pcfg, "gateway_data_path")
        for provider in OTHER_PROVIDERS:
            with self.subTest(provider=provider), self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option(provider, {}, "gateway_data_path=direct")
        ciel_runtime.apply_provider_option("cielairouter", pcfg, "gateway_data_path=DIRECT")
        self.assertEqual("direct", pcfg["gateway_data_path"])
        ciel_runtime.apply_provider_option("cielairouter", pcfg, "unset:gateway_data_path")
        self.assertNotIn("gateway_data_path", pcfg)

    def test_portable_panel_setter_and_status(self):
        cfg = self.config()
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache")):
            lines = ciel_runtime.set_llm_option_config("cielairouter", "gateway_data_path", "direct")
            self.assertEqual("direct", cfg["providers"]["cielairouter"]["gateway_data_path"])
            self.assertIn("next launches", lines[0])
            self.assertIn("until it restarts", lines[1])
            save.assert_called_once()
            with self.assertRaises(SystemExit):
                ciel_runtime.set_llm_option_config("vllm", "gateway_data_path", "direct")
            for provider in ("cielairouter", *OTHER_PROVIDERS):
                _rows, keys = ciel_runtime.llm_option_panel_rows(provider, cfg["providers"][provider], "en")
                self.assertEqual(provider == "cielairouter", "gateway_data_path" in keys, provider)
                status = ciel_runtime.provider_options_status(provider, cfg["providers"][provider])
                self.assertEqual(provider == "cielairouter", "gateway_data_path=" in status, provider)
            self.assertIn("gateway_data_path=direct", ciel_runtime.provider_options_status("cielairouter", cfg["providers"]["cielairouter"]))
            self.assertIn("until restart", ciel_runtime.llm_option_description_for_value("cielairouter", cfg["providers"]["cielairouter"], "gateway_data_path"))

    def test_gateway_status_includes_the_launch_data_path(self):
        cfg = self.config()
        cfg["providers"]["cielairouter"]["gateway_data_path"] = "direct"
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "router_health", return_value={"cielairouter_gateway": {"routes": []}}),
              mock.patch.object(ciel_runtime, "router_health_config_matches_current", return_value=True),
              mock.patch.object(launch_data_path, "read_records", return_value={}),
              mock.patch("ciel_runtime_support.cielairouter_gateway_overlay._load_selector") as selector):
            text = "\n".join(ciel_runtime.gateway_status_lines())
        selector.assert_not_called()
        self.assertIn("Gateway data path: direct", text)
        self.assertIn("keeps the gateway it was launched with until it restarts", text)

    def test_external_menu_offers_both_data_paths(self):
        source = Path(ciel_runtime.__file__).with_name("ciel-runtime-menu.py").read_text(encoding="utf-8")
        names = {"build_provider_options_submenu"}
        tree = ast.parse(source)
        code = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        for config, selected in (({}, "router"), ({"gateway_data_path": "direct"}, "direct")):
            namespace = {"current_provider_cfg": lambda config=config: ("cielairouter", config),
                         "provider_option_description": lambda _: "description"}
            exec(compile(code, "ciel-runtime-menu.py", "exec"), namespace)
            items = namespace["build_provider_options_submenu"]()["items"]
            self.assertTrue(any(item["value"] == f"gateway_data_path={selected}" and item["current"] for item in items))
            self.assertEqual(1, sum(item["value"].startswith("gateway_data_path=") and item["current"] for item in items))


if __name__ == "__main__":
    unittest.main()
