"""Repository and application service for MCP channel capability probes."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelProbePorts:
    read_servers: Callable[[Path, Path], list[tuple[str, dict[str, Any]]]]
    is_stdio: Callable[[dict[str, Any]], bool]
    probe_stdio: Callable[..., dict[str, Any]]
    probe_sse: Callable[..., dict[str, Any]]
    probe_http: Callable[..., dict[str, Any]]
    log: Callable[[str, str], None]


class ChannelProbeCacheRepository:
    def __init__(
        self,
        path: Path,
        version: int,
        save: Callable[[dict[str, Any], str], None],
        log: Callable[[str, str], None],
    ) -> None:
        self.path = path
        self.version = version
        self.save = save
        self.log = log

    def empty(self) -> dict[str, Any]:
        return {"version": self.version, "probed_at": 0.0, "servers": []}

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            self.log(
                "WARN",
                f"channel_probe_cache_read_failed error={type(exc).__name__}: {exc}",
            )
            return self.empty()
        if not isinstance(data, dict):
            return self.empty()
        data.setdefault("version", self.version)
        data.setdefault("probed_at", 0.0)
        servers = data.get("servers")
        data["servers"] = (
            [item for item in servers if isinstance(item, dict)]
            if isinstance(servers, list)
            else []
        )
        return data

    def write(self, cache: dict[str, Any]) -> None:
        self.save(cache, "channel_probe_cache")


class ChannelProbeService:
    def __init__(
        self,
        router_base: str,
        repository: ChannelProbeCacheRepository,
        ports: ChannelProbePorts,
        config_paths: Callable[..., list[Path]],
        dedupe: Callable[[Iterable[str]], list[str]],
        path_key: Callable[[Path], str],
        native_router_names: frozenset[str],
    ) -> None:
        self.router_base = router_base
        self.repository = repository
        self.ports = ports
        self.config_paths = config_paths
        self.dedupe = dedupe
        self.path_key = path_key
        self.native_router_names = native_router_names

    def builtin_record(self) -> dict[str, Any]:
        return {
            "name": "ciel-runtime-router",
            "capable": True,
            "transport": "sse",
            "source_path": "<built-in>",
            "url": f"{self.router_base}/ca/mcp/sse",
            "response_bytes": 0,
            "reason": "built-in",
        }

    @staticmethod
    def transport_label(server: dict[str, Any]) -> str:
        if not isinstance(server, dict):
            return "unknown"
        declared = str(server.get("type") or "").strip().lower()
        if declared in {"http", "streamable-http"}:
            return "streamable-http"
        if declared:
            return declared
        if server.get("url"):
            return "sse"
        if server.get("command"):
            return "stdio"
        return "unknown"

    def probe(
        self,
        paths: Iterable[str],
        cwd: Path,
        *,
        include_router_self: bool = True,
        timeout_per_server: float | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        if include_router_self:
            records.append(self.builtin_record())
            seen.add("ciel-runtime-router")
        for path_text in paths:
            path = Path(path_text) if path_text else Path()
            if not path_text or not path.is_file():
                continue
            for name, server in self.ports.read_servers(path, cwd):
                if name in seen:
                    continue
                seen.add(name)
                if name == "ciel-runtime-router":
                    continue
                record = self._probe_record(
                    name, server, path, timeout_per_server
                )
                records.append(record)
        return records

    def _probe_record(
        self,
        name: str,
        server: dict[str, Any],
        path: Path,
        timeout: float | None,
    ) -> dict[str, Any]:
        transport = self.transport_label(server)
        record: dict[str, Any] = {
            "name": name,
            "capable": False,
            "transport": transport,
            "source_path": str(path),
            "response_bytes": 0,
            "response_received": False,
            "elapsed_ms": 0,
            "exit_code": None,
            "stderr_bytes": 0,
            "stderr_preview": "",
            "stdout_preview": "",
            "reason": "",
        }
        if isinstance(server.get("url"), str):
            record["url"] = str(server["url"])
        probe: Callable[..., dict[str, Any]] | None = None
        if self.ports.is_stdio(server):
            probe = self.ports.probe_stdio
        elif transport == "sse" and isinstance(server.get("url"), str):
            probe = self.ports.probe_sse
        elif transport == "streamable-http" and isinstance(server.get("url"), str):
            probe = self.ports.probe_http
        if probe is None:
            record["reason"] = "transport_not_probed"
            return record
        try:
            detail = probe(name, server, timeout=timeout)
            record["exit_code"] = detail.get("exit_code")
            record["stderr_preview"] = str(detail.get("stderr_preview") or "")
            record["stdout_preview"] = str(detail.get("stdout_preview") or "")
            record.update(
                capable=bool(detail.get("capable")),
                reason=str(detail.get("reason") or ""),
                response_bytes=int(detail.get("response_bytes") or 0),
                response_received=bool(detail.get("response_received")),
                elapsed_ms=int(detail.get("elapsed_ms") or 0),
                stderr_bytes=int(detail.get("stderr_bytes") or 0),
            )
        except Exception as exc:
            record["reason"] = f"probe_exception:{type(exc).__name__}"
            self.ports.log(
                "WARN",
                f"channel_probe_exception server={name} "
                f"error={type(exc).__name__}: {exc}",
            )
        return record

    def refresh(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
        timeout_per_server: float | None = None,
        extra_config_paths: list[Path | str] | None = None,
    ) -> dict[str, Any]:
        cwd = cwd or Path.cwd()
        paths = [str(Path(path).expanduser()) for path in extra_config_paths or []]
        paths.extend(
            str(path) for path in self.config_paths(passthrough or [], cwd, home)
        )
        records = self.probe(paths, cwd, timeout_per_server=timeout_per_server)
        cache = {
            "version": self.repository.version,
            "probed_at": time.time(),
            "servers": records,
        }
        self.repository.write(cache)
        capable = [str(record["name"]) for record in records if record.get("capable")]
        self.ports.log(
            "INFO",
            f"channel_probe_cache_refreshed total={len(records)} "
            f"capable={len(capable)} servers={','.join(capable) or '-'}",
        )
        return cache

    def servers(self) -> list[dict[str, Any]]:
        servers = self.repository.read().get("servers")
        return (
            [item for item in servers if isinstance(item, dict)]
            if isinstance(servers, list)
            else []
        )

    @staticmethod
    def bucket(record: dict[str, Any]) -> str:
        if record.get("capable"):
            return "capable"
        reason = str(record.get("reason") or "").strip()
        if reason == "no_experimental_claude_channel" and record.get(
            "response_received"
        ):
            return "non_capable"
        if reason in {"transport_not_probed", "no_url"}:
            return "skipped"
        return "inconclusive"

    def capable_names(self) -> list[str]:
        names = [
            str(record.get("name"))
            for record in self.servers()
            if record.get("capable") and record.get("name")
        ]
        if "ciel-runtime-router" not in names:
            names.insert(0, "ciel-runtime-router")
        return self.dedupe(names)

    def external_capable_names(self) -> list[str]:
        names = []
        for record in self.servers():
            name = str(record.get("name") or "").strip()
            if (
                record.get("capable")
                and name
                and name.lower() not in self.native_router_names
            ):
                names.append(name)
        return self.dedupe(names)

    def source_paths(self, specs: Iterable[str]) -> list[Path]:
        wanted = {
            str(spec).split(":", 1)[1]
            for spec in specs
            if str(spec).startswith("server:") and str(spec).split(":", 1)[1]
        }
        if not wanted:
            return []
        paths: list[Path] = []
        seen: set[str] = set()
        for record in self.servers():
            if str(record.get("name") or "") not in wanted:
                continue
            source = str(record.get("source_path") or "")
            if not source or source == "<built-in>":
                continue
            path = Path(source).expanduser()
            key = self.path_key(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def server_names_from_specs(self, specs: Iterable[str]) -> list[str]:
        names = []
        for spec in specs:
            text = str(spec or "").strip()
            if text.startswith("server:"):
                name = text.split(":", 1)[1].strip()
                if name:
                    names.append(name)
        return self.dedupe(names)

    def candidate_names(
        self,
        specs: Iterable[str],
        discover: Callable[[], list[str]],
    ) -> list[str]:
        explicit = [
            name
            for name in self.server_names_from_specs(specs)
            if name.lower() not in self.native_router_names
        ]
        try:
            discovered = discover()
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"channel_auto_discovery_failed error={type(exc).__name__}: {exc}",
            )
            discovered = []
        return self.dedupe([*explicit, *discovered])

    @staticmethod
    def needs_refresh(
        cache: dict[str, Any],
        records: list[dict[str, Any]],
        candidate_names: list[str],
    ) -> bool:
        if not cache.get("probed_at") or not records:
            return True
        if not candidate_names:
            return False
        by_name = {
            str(record.get("name") or ""): record
            for record in records
            if record.get("name")
        }
        for name in candidate_names:
            record = by_name.get(name)
            if not record or not record.get("capable"):
                return True
            source = str(record.get("source_path") or "")
            if not source or source == "<built-in>":
                return True
        return False

    def ensure_refresh(
        self,
        refresh_needed: bool,
        refresh: Callable[[], Any],
    ) -> bool:
        if not refresh_needed:
            return False
        try:
            self.ports.log(
                "INFO",
                "channel_probe_launch_refresh "
                "reason=missing_cache_or_selected_server",
            )
            refresh()
            return True
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"channel_probe_launch_refresh_failed "
                f"error={type(exc).__name__}: {exc}",
            )
            return False


@dataclass(frozen=True, slots=True)
class ChannelProbeCompatibilityApi:
    """Typed compatibility and launch-orchestration adapter for channel probes."""

    service_factory: Callable[[], ChannelProbeService]

    def builtin_record(self) -> dict[str, Any]:
        return self.service_factory().builtin_record()

    def transport_label(self, server: dict[str, Any]) -> str:
        return self.service_factory().transport_label(server)

    def probe(
        self,
        paths: Iterable[str],
        cwd: Path,
        *,
        include_router_self: bool = True,
        timeout_per_server: float | None = None,
    ) -> list[dict[str, Any]]:
        return self.service_factory().probe(
            paths,
            cwd,
            include_router_self=include_router_self,
            timeout_per_server=timeout_per_server,
        )

    def read_cache(self) -> dict[str, Any]:
        return self.service_factory().repository.read()

    def write_cache(self, cache: dict[str, Any]) -> None:
        self.service_factory().repository.write(cache)

    def refresh(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
        timeout_per_server: float | None = None,
        extra_config_paths: list[Path | str] | None = None,
    ) -> dict[str, Any]:
        return self.service_factory().refresh(
            passthrough,
            cwd,
            home,
            timeout_per_server,
            extra_config_paths,
        )

    def servers(self) -> list[dict[str, Any]]:
        return self.service_factory().servers()

    def bucket(self, record: dict[str, Any]) -> str:
        return self.service_factory().bucket(record)

    def capable_names(self) -> list[str]:
        return self.service_factory().capable_names()

    def external_capable_names(self) -> list[str]:
        return self.service_factory().external_capable_names()

    def source_paths(self, specs: Iterable[str]) -> list[Path]:
        return self.service_factory().source_paths(specs)

    def server_names_from_specs(self, specs: Iterable[str]) -> list[str]:
        return self.service_factory().server_names_from_specs(specs)
