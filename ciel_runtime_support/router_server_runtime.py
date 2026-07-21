"""Router HTTP server startup and shutdown application service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RouterServerConfig:
    config_dir: Path
    pid_path: Path
    port: int
    client_base: str
    log_level_path: Path
    log_level_names: dict[Any, Any]
    handler: Any


@dataclass(frozen=True, slots=True)
class RouterServerStatePorts:
    load_config: Callable[[], dict[str, Any]]
    reset_api_key_cooldowns: Callable[[], None]
    bind_host: Callable[[dict[str, Any]], str]
    current_log_level: Callable[[], Any]
    current_pid: Callable[[], int]
    env_value: Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class RouterServerEffects:
    chmod: Callable[[Path, int], None]
    stderr: Any
    server_factory: Callable[..., Any]
    start_watchdog: Callable[[Any], None]
    start_channels: Callable[[dict[str, Any]], Any]
    stop_channels: Callable[[None], Any]
    thread_factory: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class RouterServerRuntime:
    config: RouterServerConfig
    state: RouterServerStatePorts
    effects: RouterServerEffects

    def run(self) -> None:
        self.config.config_dir.mkdir(parents=True, exist_ok=True)
        runtime_config = self.state.load_config()
        self.state.reset_api_key_cooldowns()
        bind_host = self.state.bind_host(runtime_config)
        self.config.pid_path.write_text(str(self.state.current_pid()))
        self.effects.chmod(self.config.pid_path, 0o600)
        level = self.state.current_log_level()
        source = self._log_level_source()
        self.effects.stderr.write(
            f"ciel-runtime router starting on {bind_host}:{self.config.port} "
            f"(client base {self.config.client_base}, log level "
            f"{self.config.log_level_names.get(level, level)}, source={source})\n"
        )
        self.effects.stderr.flush()
        server = self.effects.server_factory(
            (bind_host, self.config.port), self.config.handler
        )
        self.effects.start_watchdog(server)
        channel_thread = self.effects.thread_factory(
            target=lambda: self.effects.start_channels(runtime_config),
            daemon=True,
            name="ca-router-channel-sse-start",
        )
        channel_thread.start()
        try:
            server.serve_forever()
        finally:
            self.effects.stop_channels(None)
            try:
                self.config.pid_path.unlink()
            except FileNotFoundError:
                pass

    def _log_level_source(self) -> str:
        if self.config.log_level_path.exists():
            return "file"
        if self.state.env_value("CIEL_RUNTIME_LOG_LEVEL"):
            return "env"
        return "default"


__all__ = [
    "RouterServerConfig",
    "RouterServerEffects",
    "RouterServerRuntime",
    "RouterServerStatePorts",
]
