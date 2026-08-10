"""Startup web endpoint options and optional Tailscale Serve integration."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import urllib.parse
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .workspace_router_selection import workspace_identity


_TRUE = {"1", "true", "yes", "on"}
_WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]"}


@dataclass(frozen=True, slots=True)
class TailscaleNode:
    ipv4: str
    dns_name: str
    online: bool = True


@dataclass(frozen=True, slots=True)
class WebEndpointReport:
    local_url: str
    tailscale_ip_url: str = ""
    tailscale_https_url: str = ""
    tailscale_https_command: str = ""

    def status_lines(self) -> list[str]:
        lines = [f"web: {self.local_url}"]
        if self.tailscale_ip_url:
            lines.append(f"web_tailscale_http: {self.tailscale_ip_url}")
        if self.tailscale_https_url:
            lines.append(f"web_tailscale_https: {self.tailscale_https_url}")
        elif self.tailscale_https_command:
            lines.append(f"web_tailscale_https_recommended: {self.tailscale_https_command}")
        return lines


@dataclass(frozen=True, slots=True)
class WebBackendSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    tailscale_https: bool = False
    workspace: str = ""

    @property
    def client_host(self) -> str:
        return "127.0.0.1" if self.host in _WILDCARD_HOSTS else self.host


def web_backend_settings(config: dict[str, Any] | None) -> WebBackendSettings:
    raw = config.get("web_backend") if isinstance(config, dict) else {}
    values = raw if isinstance(raw, dict) else {}
    host = str(values.get("host") or "127.0.0.1").strip()
    if not host or any(character.isspace() for character in host):
        host = "127.0.0.1"
    try:
        port = int(values.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        port = 0
    tailscale_https = bool(values.get("tailscale_https", False))
    enabled_value = values.get("enabled")
    enabled = (
        bool(enabled_value)
        if enabled_value is not None
        else bool(tailscale_https or host != "127.0.0.1" or port)
    )
    workspace = workspace_identity(values.get("workspace"))
    return WebBackendSettings(enabled, host, port, tailscale_https, workspace)


def current_web_workspace(
    environ: MutableMapping[str, str] | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    return workspace_identity(environment.get("CIEL_RUNTIME_LAUNCH_CWD") or Path.cwd())


def web_backend_owned_by_workspace(
    settings: WebBackendSettings,
    workspace: str | os.PathLike[str] | None = None,
) -> bool:
    if not settings.workspace:
        return True
    return settings.workspace == workspace_identity(workspace or current_web_workspace())


def web_backend_owned_by_instance(
    settings: WebBackendSettings,
    router_port: int,
    workspace: str | os.PathLike[str] | None = None,
) -> bool:
    if not web_backend_owned_by_workspace(settings, workspace):
        return False
    return not settings.port or settings.port == router_port


def load_saved_web_backend(path: os.PathLike[str] | str) -> WebBackendSettings:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    return web_backend_settings(payload if isinstance(payload, dict) else {})


def web_backend_summary(config: dict[str, Any], effective_port: int) -> str:
    settings = web_backend_settings(config)
    port = settings.port or effective_port or int(config.get("_effective_web_port") or 0)
    mode = " + Tailscale HTTPS" if settings.tailscale_https else ""
    state = "on" if settings.enabled else "off"
    return f"{state} · {settings.host}:{port or 'auto'}{mode}"


def web_backend_panel_rows(
    config: dict[str, Any], effective_port: int
) -> tuple[list[str], list[str]]:
    settings = web_backend_settings(config)
    port = settings.port or effective_port
    node = discover_tailscale_node()
    tailscale = "on" if settings.tailscale_https else "off"
    if node and node.dns_name:
        tailscale += f" · {node.dns_name}:{port}"
    else:
        tailscale += " · not detected"
    return (
        [
            f"Web backend  [{'on' if settings.enabled else 'off'}]",
            f"Web bind address  [{settings.host}]",
            f"Web port  [{port}]",
            f"Tailscale HTTPS  [{tailscale}]",
            "Back",
        ],
        ["enabled", "host", "port", "tailscale", "back"],
    )


def update_web_backend_config(
    config: dict[str, Any],
    key: str,
    value: Any,
    effective_port: int,
    workspace: str | os.PathLike[str] | None = None,
) -> list[str]:
    current = web_backend_settings(config)
    enabled = current.enabled
    host = current.host
    port = current.port
    tailscale_https = current.tailscale_https
    if key == "enabled":
        enabled = bool(value)
    elif key == "host":
        host, embedded_port = _normalize_web_address(str(value))
        enabled = True
        if embedded_port is not None:
            port = embedded_port
    elif key == "port":
        port = _valid_port(str(value), "web port")
        enabled = True
    elif key == "tailscale":
        tailscale_https = bool(value)
        enabled = True
    else:
        raise ValueError(f"unknown web backend setting: {key}")
    config["web_backend"] = {
        "enabled": enabled,
        "host": host,
        "port": port or effective_port,
        "tailscale_https": tailscale_https,
        "workspace": workspace_identity(workspace or current_web_workspace()),
    }
    external = host in _WILDCARD_HOSTS or not _is_loopback(host)
    config["router_debug_external_access"] = external
    config["router_debug_external_access_confirmed"] = external
    scheme = "https" if tailscale_https else "http"
    return [
        f"Web backend: {'on' if enabled else 'off'} · {host}:{port or effective_port}.",
        f"Tailscale HTTPS: {'on' if tailscale_https else 'off'}.",
        f"Web instance: {config['web_backend']['workspace']}:{port or effective_port}.",
        f"Runtime is restarting so {scheme} endpoint settings apply now.",
    ]


def apply_startup_web_options(
    argv: list[str],
    environ: MutableMapping[str, str],
) -> list[str]:
    """Consume Ciel-owned web flags before runtime path constants are imported."""
    output = [argv[0]] if argv else []
    index = 1
    passthrough = False
    while index < len(argv):
        argument = argv[index]
        if passthrough:
            output.append(argument)
            index += 1
            continue
        if argument == "--":
            passthrough = True
            output.append(argument)
            index += 1
            continue
        name, separator, inline_value = argument.partition("=")
        if name in {"--ca-web-address", "--ca-web-host"}:
            value, index = _option_value(argv, index, inline_value if separator else "", name)
            host, embedded_port = _normalize_web_address(value)
            _apply_web_host(host, environ)
            environ["CIEL_RUNTIME_WEB_START_REQUESTED"] = "1"
            if embedded_port is not None:
                environ["CIEL_RUNTIME_ROUTER_PORT"] = str(embedded_port)
            continue
        if name == "--ca-web-port":
            value, index = _option_value(argv, index, inline_value if separator else "", name)
            environ["CIEL_RUNTIME_ROUTER_PORT"] = str(_valid_port(value, name))
            environ["CIEL_RUNTIME_WEB_START_REQUESTED"] = "1"
            continue
        if name == "--ca-tailscale-https":
            environ["CIEL_RUNTIME_TAILSCALE_HTTPS"] = "1"
            environ["CIEL_RUNTIME_WEB_START_REQUESTED"] = "1"
            if separator and inline_value:
                environ["CIEL_RUNTIME_TAILSCALE_HTTPS_PORT"] = str(
                    _valid_port(inline_value, name)
                )
            index += 1
            continue
        output.append(argument)
        index += 1
    return output


def _option_value(
    argv: Sequence[str], index: int, inline_value: str, option: str
) -> tuple[str, int]:
    if inline_value:
        return inline_value, index + 1
    if index + 1 >= len(argv) or argv[index + 1] == "--":
        raise SystemExit(f"{option} requires a value")
    return argv[index + 1], index + 2


def _normalize_web_address(value: str) -> tuple[str, int | None]:
    text = value.strip()
    if not text:
        raise SystemExit("--ca-web-address requires a host or URL")
    parsed = urllib.parse.urlparse(text if "://" in text else f"//{text}")
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        raise SystemExit("--ca-web-address supports only http:// or https:// URLs")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SystemExit("--ca-web-address must not include a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"invalid web address port: {exc}") from exc
    host = str(parsed.hostname or "").strip()
    if not host or any(character.isspace() for character in host):
        raise SystemExit("--ca-web-address contains an invalid host")
    return host, port


def _valid_port(value: str, option: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{option} requires a numeric port") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"{option} port must be between 1 and 65535")
    return port


def _apply_web_host(host: str, environ: MutableMapping[str, str]) -> None:
    environ["CIEL_RUNTIME_ROUTER_BIND_HOST"] = host
    environ["CIEL_RUNTIME_ROUTER_CLIENT_HOST"] = (
        "127.0.0.1" if host in _WILDCARD_HOSTS else host
    )
    if host in _WILDCARD_HOSTS or not _is_loopback(host):
        environ["CIEL_RUNTIME_ROUTER_DEBUG_EXTERNAL"] = "1"


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def discover_tailscale_node(*, timeout: float = 2.0) -> TailscaleNode | None:
    executable = shutil.which("tailscale")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if str(payload.get("BackendState") or "").lower() != "running":
        return None
    self_node = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
    addresses = self_node.get("TailscaleIPs") or payload.get("TailscaleIPs") or []
    ipv4 = next((str(item) for item in addresses if _is_ipv4(item)), "")
    dns_name = str(self_node.get("DNSName") or "").strip().rstrip(".")
    if not ipv4 and not dns_name:
        return None
    return TailscaleNode(ipv4=ipv4, dns_name=dns_name, online=bool(self_node.get("Online", True)))


def _is_ipv4(value: Any) -> bool:
    try:
        return ipaddress.ip_address(str(value)).version == 4
    except ValueError:
        return False


def tailscale_https_url_for_target(
    node: TailscaleNode,
    router_port: int,
    *,
    timeout: float = 2.0,
) -> str:
    if not node.dns_name:
        return ""
    executable = shutil.which("tailscale")
    if not executable:
        return ""
    try:
        completed = subprocess.run(
            [executable, "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            return ""
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return ""
    target = f"http://127.0.0.1:{router_port}"
    web = payload.get("Web") if isinstance(payload.get("Web"), dict) else {}
    tcp = payload.get("TCP") if isinstance(payload.get("TCP"), dict) else {}
    for authority, config in web.items():
        handlers = config.get("Handlers") if isinstance(config, dict) else {}
        if not isinstance(handlers, dict):
            continue
        if not any(
            isinstance(handler, dict) and str(handler.get("Proxy") or "").rstrip("/") == target
            for handler in handlers.values()
        ):
            continue
        port_text = str(authority).rsplit(":", 1)[-1]
        port_config = tcp.get(port_text) if isinstance(tcp, dict) else None
        if not isinstance(port_config, dict) or not port_config.get("HTTPS"):
            continue
        port = int(port_text) if port_text.isdigit() else 443
        suffix = "" if port == 443 else f":{port}"
        return f"https://{node.dns_name}{suffix}"
    return ""


def tailscale_proxy_target_for_port(
    public_port: int,
    *,
    executable: str | None = None,
    timeout: float = 2.0,
) -> str:
    command = executable or shutil.which("tailscale")
    if not command:
        return ""
    try:
        completed = subprocess.run(
            [command, "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            return ""
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return ""
    web = payload.get("Web") if isinstance(payload.get("Web"), dict) else {}
    suffix = f":{public_port}"
    for authority, config in web.items():
        if public_port == 443:
            matches = str(authority).rsplit(":", 1)[-1] in {"443", str(authority)}
        else:
            matches = str(authority).endswith(suffix)
        if not matches or not isinstance(config, dict):
            continue
        handlers = config.get("Handlers")
        root = handlers.get("/") if isinstance(handlers, dict) else None
        if isinstance(root, dict):
            return str(root.get("Proxy") or "").rstrip("/")
    return ""


def build_web_endpoint_report(
    client_host: str,
    bind_host: str,
    router_port: int,
) -> WebEndpointReport:
    display_host = _url_host(client_host or "127.0.0.1")
    local_url = f"http://{display_host}:{router_port}/"
    node = discover_tailscale_node()
    if node is None:
        return WebEndpointReport(local_url=local_url)
    exposed = bind_host in _WILDCARD_HOSTS or bind_host in {node.ipv4, node.dns_name}
    tailscale_ip_url = f"http://{node.ipv4}:{router_port}/" if exposed and node.ipv4 else ""
    https_url = tailscale_https_url_for_target(node, router_port)
    command = ""
    if not https_url and node.dns_name:
        command = (
            f"ciel-runtime --ca-web-port {router_port} --ca-tailscale-https={router_port}"
        )
    return WebEndpointReport(
        local_url=local_url,
        tailscale_ip_url=tailscale_ip_url,
        tailscale_https_url=f"{https_url}/" if https_url else "",
        tailscale_https_command=command,
    )


def configure_tailscale_https(router_port: int, https_port: int | None = None) -> list[str]:
    node = discover_tailscale_node()
    if node is None or not node.dns_name:
        return ["Tailscale HTTPS was requested, but no online MagicDNS node was detected."]
    executable = shutil.which("tailscale")
    if not executable:
        return ["Tailscale HTTPS was requested, but the tailscale CLI was not found."]
    public_port = https_port or router_port
    target = f"http://127.0.0.1:{router_port}"
    existing_target = tailscale_proxy_target_for_port(
        public_port,
        executable=executable,
    )
    if (
        existing_target
        and existing_target != target
        and public_port != router_port
    ):
        return [
            "Tailscale HTTPS setup refused: "
            f"public port {public_port} already belongs to {existing_target}; "
            f"this runtime is {target}. Use a unique public port."
        ]
    command = [
        executable,
        "serve",
        f"--https={public_port}",
        "--bg",
        "--yes",
        target,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"Tailscale HTTPS setup failed: {type(exc).__name__}: {exc}"]
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        return [f"Tailscale HTTPS setup failed: {detail}"]
    suffix = "" if public_port == 443 else f":{public_port}"
    return [
        f"Tailscale HTTPS: https://{node.dns_name}{suffix}/",
        f"Tailscale Serve target: http://127.0.0.1:{router_port}",
    ]


def configure_requested_web_endpoints(
    router_port: int,
    client_host: str,
    bind_host: str,
    environ: MutableMapping[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> list[str]:
    environment = os.environ if environ is None else environ
    lines: list[str] = []
    saved = web_backend_settings(config)
    explicit = str(environment.get("CIEL_RUNTIME_TAILSCALE_HTTPS") or "").lower() in _TRUE
    owned = web_backend_owned_by_instance(
        saved,
        router_port,
        current_web_workspace(environment),
    )
    requested = explicit or (saved.enabled and saved.tailscale_https and owned)
    if requested:
        configured_port = str(environment.get("CIEL_RUNTIME_TAILSCALE_HTTPS_PORT") or "").strip()
        https_port = _valid_port(configured_port, "CIEL_RUNTIME_TAILSCALE_HTTPS_PORT") if configured_port else None
        lines.extend(configure_tailscale_https(router_port, https_port))
    elif saved.enabled and saved.tailscale_https and not owned:
        owner = saved.workspace or "legacy port"
        lines.append(
            "web_tailscale_skipped: settings belong to "
            f"{owner}:{saved.port or 'auto'}, current instance is "
            f"{current_web_workspace(environment)}:{router_port}"
        )
    lines.extend(build_web_endpoint_report(client_host, bind_host, router_port).status_lines())
    return lines


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


__all__ = [
    "TailscaleNode",
    "WebBackendSettings",
    "WebEndpointReport",
    "apply_startup_web_options",
    "build_web_endpoint_report",
    "configure_requested_web_endpoints",
    "configure_tailscale_https",
    "current_web_workspace",
    "discover_tailscale_node",
    "load_saved_web_backend",
    "tailscale_https_url_for_target",
    "tailscale_proxy_target_for_port",
    "update_web_backend_config",
    "web_backend_panel_rows",
    "web_backend_owned_by_instance",
    "web_backend_owned_by_workspace",
    "web_backend_settings",
    "web_backend_summary",
]
