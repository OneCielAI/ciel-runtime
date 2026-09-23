"""Run the Codex desktop app (Windows Store package OpenAI.Codex) through Ciel Runtime.

The desktop app takes no `-c` overrides, but it honours its environment
(measured 2026-09-23, journal research/codex-desktop/launch-probes):

- ``CODEX_APP_SERVER_WS_URL`` makes it attach to an app-server we start instead
  of spawning its own, so every routing ``-c`` override stays on our
  ``codex app-server`` command line;
- ``CODEX_HOME`` isolates config, auth and sessions from ``~/.codex``;
- ``CODEX_ELECTRON_USER_DATA_PATH`` plus the ``--user-data-dir`` switch give a
  second instance beside the one the person already has open (without the
  switch Chromium's process singleton hands the launch to that instance).

The app shows "failed to start" and keeps its window hidden when the
app-server is not listening yet, so the launcher waits for ``/readyz`` first.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
import urllib.request

from ciel_runtime_support.codex_app_server import CodexAppServerClient
from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess
from ciel_runtime_support.codex_desktop_injection import (
    CodexDesktopChannelInjector,
    CodexDesktopChannelPorts,
)

DESKTOP_PACKAGE_NAME = "OpenAI.Codex"
DESKTOP_EXECUTABLE_ENV = "CIEL_RUNTIME_CODEX_DESKTOP_EXE"
LAUNCH_MODE_LABEL = "codex-desktop-router"
READY_TIMEOUT_SECONDS = 60.0
# Files copied from the person's ~/.codex. config.toml only seeds a new home
# (later edits made in the desktop app stay); auth.json follows the source so
# a refreshed ChatGPT sign-in reaches the isolated home.
SEED_ONCE = ("config.toml",)
FOLLOW_SOURCE = ("auth.json",)


class CodexDesktopLaunchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CodexDesktopPaths:
    root: Path

    @property
    def codex_home(self) -> Path:
        return self.root / "codex-home"

    @property
    def user_data(self) -> Path:
        return self.root / "electron-user-data"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


def codex_desktop_paths(config_dir: Path, workspace_id: str) -> CodexDesktopPaths:
    return CodexDesktopPaths(Path(config_dir) / "codex-desktop" / workspace_id)


def find_codex_desktop_app(
    env: dict[str, str],
    *,
    run: Callable[..., Any] = subprocess.run,
    platform_name: str = os.name,
) -> Path | None:
    override = str(env.get(DESKTOP_EXECUTABLE_ENV) or "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    if platform_name != "nt":
        return None
    try:
        completed = run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-AppxPackage -Name {DESKTOP_PACKAGE_NAME} | Sort-Object Version -Descending "
                "| Select-Object -First 1).InstallLocation",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    location = str(getattr(completed, "stdout", "") or "").strip()
    if not location:
        return None
    executable = Path(location) / "app" / "ChatGPT.exe"
    return executable if executable.is_file() else None


def prepare_codex_home(paths: CodexDesktopPaths, source_home: Path) -> list[str]:
    for directory in (paths.codex_home, paths.user_data, paths.logs):
        directory.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in SEED_ONCE:
        source, target = source_home / name, paths.codex_home / name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
            copied.append(name)
    for name in FOLLOW_SOURCE:
        source, target = source_home / name, paths.codex_home / name
        if source.is_file() and (not target.exists() or source.stat().st_mtime > target.stat().st_mtime):
            shutil.copy2(source, target)
            copied.append(name)
    return copied


def listen_url_from_command(cmd: Iterable[str]) -> str:
    values = [str(item) for item in cmd]
    for index, value in enumerate(values):
        if value == "--listen" and index + 1 < len(values):
            return values[index + 1]
        if value.startswith("--listen="):
            return value.split("=", 1)[1]
    return ""


def readyz_url(listen_url: str) -> str:
    parts = urlsplit(listen_url)
    if parts.scheme != "ws" or not parts.hostname or not parts.port:
        raise CodexDesktopLaunchError(
            f"the Codex desktop app needs a ws:// app-server listen address, got {listen_url or 'none'}"
        )
    return f"http://{parts.hostname}:{parts.port}/readyz"


def wait_until_ready(
    url: str,
    *,
    timeout: float = READY_TIMEOUT_SECONDS,
    alive: Callable[[], bool] = lambda: True,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], Any] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = now() + timeout
    while now() < deadline:
        if not alive():
            return False
        try:
            with urlopen(url, timeout=2) as response:
                if int(getattr(response, "status", 0) or 0) == 200:
                    return True
        except OSError:
            pass
        sleep(0.25)
    return False


def desktop_app_env(env: dict[str, str], paths: CodexDesktopPaths, listen_url: str) -> dict[str, str]:
    app_env = dict(env)
    app_env["CODEX_HOME"] = str(paths.codex_home)
    app_env["CODEX_ELECTRON_USER_DATA_PATH"] = str(paths.user_data)
    app_env["CODEX_APP_SERVER_WS_URL"] = listen_url
    # FORCE_CLI would make the app ignore the WebSocket URL and spawn its own
    # app-server without our routing overrides.
    app_env.pop("CODEX_APP_SERVER_FORCE_CLI", None)
    app_env.pop("CODEX_CLI_PATH", None)
    return app_env


def desktop_app_command(executable: Path, paths: CodexDesktopPaths) -> list[str]:
    return [str(executable), f"--user-data-dir={paths.user_data}"]


@dataclass(frozen=True, slots=True)
class CodexDesktopPorts:
    config_dir: Path
    workspace_id: str
    source_codex_home: Path
    version: str
    log: Callable[[str, str], Any]
    terminate_tree: Callable[[int], Any]
    channel: CodexDesktopChannelPorts | None = None
    find_app: Callable[[dict[str, str]], Path | None] = find_codex_desktop_app
    popen: Callable[..., Any] = subprocess.Popen
    wait_ready: Callable[..., bool] = wait_until_ready


class CodexDesktopSession:
    """Starts app-server, then the desktop app, then channel delivery; waits for the app."""

    def __init__(self, ports: CodexDesktopPorts) -> None:
        self.ports = ports

    def __call__(self, cmd: list[str], env: dict[str, str], launch_cwd: Path) -> int:
        ports = self.ports
        executable = ports.find_app(env)
        if executable is None:
            print(
                "Codex desktop app not found. Install it from the Microsoft Store (OpenAI Codex) "
                f"or set {DESKTOP_EXECUTABLE_ENV} to its ChatGPT.exe.",
                flush=True,
            )
            return 2
        listen_url = listen_url_from_command(cmd)
        ready_url = readyz_url(listen_url)
        paths = codex_desktop_paths(ports.config_dir, ports.workspace_id)
        copied = prepare_codex_home(paths, ports.source_codex_home)
        ports.log(
            "INFO",
            f"codex_desktop_home_prepared home={paths.codex_home} copied={','.join(copied) or '-'}",
        )
        server_env = dict(env)
        server_env["CODEX_HOME"] = str(paths.codex_home)
        with open(paths.logs / "app-server.log", "ab") as server_log:
            server = ports.popen(cmd, env=server_env, cwd=str(launch_cwd), stdout=server_log, stderr=subprocess.STDOUT)
        injector: CodexDesktopChannelInjector | None = None
        app: Any = None
        try:
            if not ports.wait_ready(ready_url, alive=lambda: server.poll() is None):
                print(f"Codex app-server did not become ready at {ready_url}; see {paths.logs / 'app-server.log'}", flush=True)
                return 1
            ports.log("INFO", f"codex_desktop_app_server_ready pid={server.pid} listen={listen_url}")
            print(f"Codex app-server ready: {listen_url}", flush=True)
            with open(paths.logs / "desktop-app.log", "ab") as app_log:
                app = ports.popen(
                    desktop_app_command(executable, paths),
                    env=desktop_app_env(env, paths, listen_url),
                    cwd=str(launch_cwd),
                    stdout=app_log,
                    stderr=subprocess.STDOUT,
                )
            ports.log("INFO", f"codex_desktop_app_started pid={app.pid} exe={executable}")
            print("Codex desktop app started. Close its window to end this session.", flush=True)
            if ports.channel is not None:
                injector = CodexDesktopChannelInjector(
                    lambda: CodexAppServerClient(CodexAppServerWebSocketProcess.connect(listen_url)),  # type: ignore[arg-type]
                    ports.channel,
                    cwd=str(launch_cwd),
                    version=ports.version,
                )
                injector.start()
            return int(app.wait() or 0)
        finally:
            if injector is not None:
                injector.stop()
            if app is not None and app.poll() is None:
                ports.terminate_tree(int(app.pid))
            if server.poll() is None:
                ports.terminate_tree(int(server.pid))
            ports.log("INFO", "codex_desktop_session_ended")


__all__ = [
    "CodexDesktopChannelPorts",
    "CodexDesktopLaunchError",
    "CodexDesktopPaths",
    "CodexDesktopPorts",
    "CodexDesktopSession",
    "DESKTOP_EXECUTABLE_ENV",
    "LAUNCH_MODE_LABEL",
    "codex_desktop_paths",
    "desktop_app_command",
    "desktop_app_env",
    "find_codex_desktop_app",
    "listen_url_from_command",
    "prepare_codex_home",
    "readyz_url",
    "wait_until_ready",
]
