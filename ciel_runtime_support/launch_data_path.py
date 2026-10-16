"""Launch-time model data path: the local router (default) or a provider gateway.

``gateway_data_path`` is a provider option.  ``router`` (the default, and any
value other than ``direct``) leaves a launch exactly as before: the CLI sends
its model requests to the local Ciel Runtime router.  ``direct`` is accepted
only for providers whose configuration policy reports
``supports_gateway_data_path`` (CielAiRouter): right before Claude Code or
Codex starts, the provider picks a gateway (``launch_gateway``) and, when it
names a healthy one, the CLI's model HTTP goes to that gateway with the
provider key and the upstream model id.

The router still starts in direct mode and keeps the router MCP endpoint,
channel delivery, the external event receiver and session restart.  Requests
that never reach it get none of the router's request handling: key rotation,
provider failover, per-request gateway reselection, catalog output caps and
effort lowering, and the body injection behind ``[ciel-wake]`` markers.

Channel delivery decides first.  A launch whose channel messages reach the
CLI only as typed ``[ciel-wake]`` markers, with no whole-message transport
(Claude Code's messaging socket, app-server RPC), stays on the router: the
router appends those bodies to the next model request, and a direct request
would carry the marker alone.  The launcher states this in ``ChannelDelivery``;
plain Codex TUI and Claude Code with session-socket input off are such
launches.  They fall back with a recorded reason and no gateway selection.
On Windows the plain Codex TUI types only the marker (its composer drops
characters of typed bodies); elsewhere it types the body after the marker but
the router still replaces that turn, and a run without the router there is
unverified, so it stays on the router on every platform.

A running CLI keeps the gateway it was launched with; nothing re-routes it
while it runs.  A session restart selects again: a direct launch moves to the
new gateway (or back to the router when none is healthy, which accepts the
upstream model ids).  A launch that fell back to the router stays there until
the next launch.

Any selection problem (selection off, discovery or probe failure, degraded or
central result, plain HTTP to a non-loopback host, no key or model) keeps the
router data path and logs one secret-free line.  The key is never logged,
printed, recorded or put on a command line: Claude Code receives it in
``ANTHROPIC_AUTH_TOKEN`` (as in router mode), Codex in ``GATEWAY_API_KEY_ENV``,
which the provider block names as its ``env_key``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .codex_config import toml_string

SETTING = "gateway_data_path"
ROUTER = "router"
DIRECT = "direct"
VALUES = (ROUTER, DIRECT)
DIRECT_PROVIDER_ID = "ciel-runtime-gateway"
ACTIVE_ENV = "CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE"
GATEWAY_API_KEY_ENV = "CIEL_RUNTIME_GATEWAY_API_KEY"
RECORD_FILE_NAME = "launch-data-path.json"
LIVE_REROUTE_NOTE = (
    "A running CLI keeps the gateway it was launched with until it restarts; "
    "direct mode has no live re-route."
)
_CLAUDE_ROUTE_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")

Log = Callable[[str, str], Any]


def configured_data_path(pcfg: Any) -> str:
    """``direct`` only when explicitly configured; everything else is ``router``."""

    if not isinstance(pcfg, Mapping):
        return ROUTER
    return DIRECT if str(pcfg.get(SETTING) or "").strip().lower() == DIRECT else ROUTER


CHANNEL_MARKER_REASON = (
    "channel wakes are typed [ciel-wake] markers whose bodies only router model HTTP injects "
    "(no whole-message transport)"
)


@dataclass(frozen=True)
class ChannelDelivery:
    """How a launch hands channel messages to its CLI, as far as the data path depends on it.

    ``terminal_wake``: a terminal proxy types LLM-delivery wakes, a short
    ``[ciel-wake]`` marker whose body the router appends to the model request.
    ``whole_message``: the transport that carries a whole message without model
    HTTP (``session_socket``, ``app_server``), or empty when there is none.
    """

    terminal_wake: bool = False
    whole_message: str = ""

    def router_reason(self) -> str:
        """Why model HTTP must stay on the router; empty when a gateway may take it."""

        return CHANNEL_MARKER_REASON if self.terminal_wake and not self.whole_message else ""


@dataclass(frozen=True)
class LaunchGateway:
    """A provider's launch-time gateway pick; an empty ``endpoint`` means none (``reason`` says why)."""

    endpoint: str = ""
    node_id: str = ""
    reason: str = ""
    central: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class LaunchDataPath:
    """The data path one launch uses; ``api_key`` is excluded from repr, records and text."""

    runtime: str = ""
    configured: str = ROUTER
    active: str = ROUTER
    endpoint: str = ""
    node_id: str = ""
    central: str = ""
    reason: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False, compare=False)

    @property
    def direct(self) -> bool:
        return self.active == DIRECT and bool(self.endpoint and self.api_key and self.model)

    def summary(self) -> str:
        if self.direct:
            return (f"direct {display_endpoint(self.endpoint)} (node {self.node_id or '-'}; "
                    f"{self.reason or 'selected'})")
        if self.configured == DIRECT:
            return f"router (direct unavailable: {self.reason or 'no gateway'})"
        return "router"

    def record(self, at: float, pid: int) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "active": DIRECT if self.direct else ROUTER,
            "endpoint": display_endpoint(self.endpoint) if self.direct else None,
            "node": self.node_id or None,
            "central": self.central or None,
            "reason": self.reason or None,
            "at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "pid": pid,
        }


ROUTER_PATH = LaunchDataPath()


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def endpoint_allowed(url: str) -> bool:
    """HTTPS, or plain HTTP only to a loopback host; no credentials, query or fragment."""

    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        host = parts.hostname or ""
        if not host or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            return False
        _ = parts.port  # raises ValueError for an invalid port
    except ValueError:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and _loopback(host))


def display_endpoint(url: Any) -> str:
    """scheme://host[:port][/path] of a gateway URL; never credentials, query or fragment."""

    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            return "-"
        host = f"[{host}]" if ":" in host else host
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}{parts.path.rstrip('/')}"
    except ValueError:
        return "-"


def resolve(resolver: Any, runtime: str, provider: str, pcfg: Any, *,
            channel: ChannelDelivery | None = None) -> LaunchDataPath:
    """``resolver(runtime, provider, pcfg, channel=...)``, or the router path when a launcher has none."""

    if resolver is None:
        return ROUTER_PATH
    route = resolver(runtime, provider, pcfg, channel=channel)
    return route if isinstance(route, LaunchDataPath) else ROUTER_PATH


class LaunchDataPathResolver:
    """Resolves and records each launch's data path (``configuration_policy(provider, pcfg)``)."""

    def __init__(
        self,
        configuration_policy: Callable[[str, Any], Any],
        log: Log,
        *,
        output: Callable[[str], Any] | None = None,
        record_path: Path | None = None,
        upstream_model: Callable[[str, Any, str], str] | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.upstream_model = upstream_model
        self.configuration_policy = configuration_policy
        self.log = log
        self.output = output or (lambda line: print(line, flush=True))
        self._record_path = record_path
        self.clock = clock
        self.monotonic = monotonic

    @property
    def record_path(self) -> Path:
        return self._record_path or default_record_path()

    def __call__(self, runtime: str, provider: str, pcfg: Any, *,
                 channel: ChannelDelivery | None = None) -> LaunchDataPath:
        configured = configured_data_path(pcfg)
        policy = self.configuration_policy(provider, pcfg)
        supported = bool(getattr(policy, "supports_gateway_data_path", False))
        if configured != DIRECT:
            # Router mode adds nothing to the launch; only a stale direct record is replaced.
            if supported:
                self._record(runtime, LaunchDataPath(runtime), create=False)
            return LaunchDataPath(runtime)
        if not supported:
            return self._finish(LaunchDataPath(runtime, DIRECT, ROUTER, reason="provider has no direct gateway data path"),
                                provider, 0, record=False)
        channel_reason = channel.router_reason() if isinstance(channel, ChannelDelivery) else ""
        if channel_reason:
            # Decided before any gateway selection: no discovery or probe for a launch that cannot use it.
            return self._finish(LaunchDataPath(runtime, DIRECT, ROUTER, reason=channel_reason), provider, 0)
        started = self.monotonic()
        try:
            gateway = policy.launch_gateway(pcfg, log=self.log)
        except Exception as error:  # noqa: BLE001 - a selection problem keeps the router data path
            gateway = LaunchGateway(reason=f"gateway selection failed ({type(error).__name__})")
        if isinstance(gateway, LaunchGateway) and gateway.endpoint and self.upstream_model is not None:
            from dataclasses import replace
            gateway = replace(gateway, model=self.upstream_model(provider, pcfg, gateway.model))
        elapsed_ms = int(max(0.0, self.monotonic() - started) * 1000)
        return self._finish(self._route(runtime, gateway), provider, elapsed_ms)

    @staticmethod
    def _route(runtime: str, gateway: Any) -> LaunchDataPath:
        if not isinstance(gateway, LaunchGateway):
            return LaunchDataPath(runtime, DIRECT, ROUTER, reason="no gateway selected")
        reason = " ".join(str(gateway.reason or "").split())[:160]
        fallback = LaunchDataPath(runtime, DIRECT, ROUTER, node_id=gateway.node_id, central=gateway.central,
                                  reason=reason or "no gateway selected")
        if not gateway.endpoint:
            return fallback
        if not endpoint_allowed(gateway.endpoint):
            return LaunchDataPath(runtime, DIRECT, ROUTER, node_id=gateway.node_id, central=gateway.central,
                                  reason="gateway URL rejected (HTTPS, or HTTP only to a loopback host)")
        if gateway.model.startswith("ciel-runtime-"):
            return LaunchDataPath(runtime, DIRECT, ROUTER, reason="upstream model identity could not be resolved")
        if not gateway.api_key or not gateway.model:
            return LaunchDataPath(runtime, DIRECT, ROUTER, node_id=gateway.node_id, central=gateway.central,
                                  reason="no API key" if not gateway.api_key else "no model selected")
        return LaunchDataPath(runtime, DIRECT, DIRECT, gateway.endpoint.rstrip("/"), gateway.node_id,
                              gateway.central, reason or "selected", gateway.model, gateway.api_key)

    def _finish(self, route: LaunchDataPath, provider: str, elapsed_ms: int, *, record: bool = True) -> LaunchDataPath:
        if route.direct:
            self.log(
                "INFO",
                f"launch_data_path runtime={route.runtime} provider={provider} configured=direct active=direct "
                f"endpoint={display_endpoint(route.endpoint)} node={route.node_id or '-'} "
                f"central={route.central or '-'} reason={route.reason or '-'} elapsed_ms={elapsed_ms}; "
                "model requests go to the gateway, the router keeps MCP, channels and restart",
            )
            self.output(f"Ciel Runtime: model requests go directly to gateway {display_endpoint(route.endpoint)} "
                        f"(node {route.node_id or '-'}); the local router keeps MCP, channels and restart.")
        else:
            self.log(
                "WARN",
                f"launch_data_path runtime={route.runtime} provider={provider} configured=direct active=router "
                f"central={route.central or '-'} reason={route.reason or '-'} elapsed_ms={elapsed_ms}; "
                "model requests use the local router",
            )
            self.output(f"Ciel Runtime: direct gateway unavailable ({route.reason or 'no gateway'}); "
                        "model requests use the local router.")
        if record:
            self._record(route.runtime, route, create=True)
        return route

    def _record(self, runtime: str, route: LaunchDataPath, *, create: bool) -> None:
        path = self.record_path
        try:
            records = read_records(path)
            if not create and not records:
                return
            records[runtime or "-"] = route.record(self.clock(), os.getpid())
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps({"version": 1, "launches": records}, indent=2, sort_keys=True),
                                 encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            self.log("WARN", f"launch_data_path_record_failed path={path} error={type(error).__name__}")


def default_record_path() -> Path:
    from .runtime_paths import WORKSPACE_STATE_DIR

    return WORKSPACE_STATE_DIR / RECORD_FILE_NAME


def read_records(path: Path | None = None) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads((path or default_record_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    launches = payload.get("launches") if isinstance(payload, Mapping) else None
    if not isinstance(launches, Mapping):
        return {}
    return {str(name): dict(entry) for name, entry in launches.items() if isinstance(entry, Mapping)}


def status_lines(pcfg: Any, path: Path | None = None) -> list[str]:
    """Configured and last-launched data paths for status output; read-only and secret-free."""

    if configured_data_path(pcfg) == DIRECT:
        lines = ["Gateway data path: direct (opt-in) - at launch Claude Code/Codex send model requests "
                 "straight to the selected gateway; the local router keeps MCP, channels, events and restart."]
    else:
        lines = ["Gateway data path: router (default) - CLI model requests go through the local router."]
    lines.append(LIVE_REROUTE_NOTE)
    lines.append("A CLI whose channel messages arrive only as typed [ciel-wake] markers (plain Codex TUI; Claude Code "
                 "with session-socket input off) stays on the router, which injects their bodies into model HTTP.")
    lines.append("Direct mode has no request-level TUI response observation; transcript agent.turn_ended remains available.")
    records = read_records(path)
    if not records:
        return lines + ["No CLI launch data path recorded in this workspace yet."]
    lines.append("Last launch data path per CLI (this workspace; recorded at launch):")
    for runtime, entry in sorted(records.items()):
        active = DIRECT if entry.get("active") == DIRECT else ROUTER
        reason = " ".join(str(entry.get("reason") or "").split())[:160]
        if active == DIRECT:
            detail = f"direct {display_endpoint(entry.get('endpoint'))} (node {entry.get('node') or '-'}; {reason or 'selected'})"
        elif entry.get("configured") == DIRECT:
            detail = f"router (direct unavailable: {reason or 'no gateway'})"
        else:
            detail = "router"
        lines.append(f"  {runtime}: {detail} at {entry.get('at') or '-'}")
    return lines


def claude_restart_environment(route: LaunchDataPath, router_environment: Mapping[str, str]) -> dict[str, str]:
    """ANTHROPIC_* values a restarted direct Claude Code launch uses; the router's when no gateway."""

    if route.direct:
        return {"ANTHROPIC_BASE_URL": route.endpoint, "ANTHROPIC_AUTH_TOKEN": route.api_key}
    return {key: router_environment[key] for key in _CLAUDE_ROUTE_KEYS if key in router_environment}


def codex_provider_command(cmd: list[str], provider_id: str, base_url: str, env_key: str) -> list[str]:
    """``cmd`` with the provider block's ``base_url``/``env_key`` overrides replaced."""

    values = {
        f"model_providers.{provider_id}.base_url": toml_string(base_url.rstrip("/") + "/v1"),
        f"model_providers.{provider_id}.env_key": toml_string(env_key),
    }
    out = list(cmd)
    for index in range(len(out) - 1):
        if out[index] not in ("-c", "--config"):
            continue
        key, separator, _value = str(out[index + 1]).partition("=")
        if separator and key.strip() in values:
            out[index + 1] = f"{key.strip()}={values[key.strip()]}"
    return out


__all__ = [
    "CHANNEL_MARKER_REASON",
    "ChannelDelivery",
    "DIRECT",
    "GATEWAY_API_KEY_ENV",
    "LIVE_REROUTE_NOTE",
    "RECORD_FILE_NAME",
    "ROUTER",
    "ROUTER_PATH",
    "SETTING",
    "VALUES",
    "LaunchDataPath",
    "LaunchDataPathResolver",
    "LaunchGateway",
    "claude_restart_environment",
    "codex_provider_command",
    "configured_data_path",
    "display_endpoint",
    "endpoint_allowed",
    "read_records",
    "resolve",
    "status_lines",
]


def model_http_bypassed() -> bool:
    return os.environ.get(ACTIVE_ENV) == "1"


def run_with_delivery_path(route: LaunchDataPath, callback: Callable[[], Any]) -> Any:
    """Tell parent-process channel delivery that model HTTP cannot append bodies."""
    if not route.direct:
        return callback()
    previous = os.environ.get(ACTIVE_ENV)
    os.environ[ACTIVE_ENV] = "1"
    try:
        return callback()
    finally:
        if previous is None:
            os.environ.pop(ACTIVE_ENV, None)
        else:
            os.environ[ACTIVE_ENV] = previous


def replace_codex_model_settings(cmd: list[str], settings: list[str]) -> list[str]:
    """Replace generated model snapshot, retaining MCP/listen/session arguments."""
    out = [cmd[0]]
    index = 1
    while index < len(cmd):
        if cmd[index] in ("-c", "--config") and index + 1 < len(cmd):
            key = cmd[index + 1].partition("=")[0]
            if (key in {"model", "model_provider", "model_catalog_json", "model_reasoning_effort",
                        "model_auto_compact_token_limit"}
                    or key.startswith("model_providers.ciel-runtime." )
                    or key.startswith("model_providers.ciel-runtime-gateway.")):
                index += 2
                continue
        out.append(cmd[index])
        index += 1
    at = out.index("app-server") + 1 if "app-server" in out else 1
    out[at:at] = settings
    return out
