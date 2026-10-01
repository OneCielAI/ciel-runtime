"""Codex sessions that run on an app-server Ciel Runtime owns.

``run_codex_app_server`` builds the routed ``codex app-server`` command and
hands its final process step to one of these sessions:

- ``CodexBareAppServerSession`` runs the server as before (clients attach
  themselves) and adds the channel client, so channel input, compact and
  new-session requests reach it over JSON-RPC.
- ``CodexRemoteTuiSession`` (runtime ``codex-remote``) starts the server, then
  the Codex TUI attached with ``codex --remote``.  Channel input and compact go
  in over JSON-RPC instead of being typed through the console; the TUI only
  changes conversation through its own ``/new``, so new_session is the one
  command the terminal proxy still types.
- the desktop app session lives in codex_desktop_runtime.

Measured on codex 0.159.3 (journal 2026-10-01 runtime-control): a TUI cannot
``resume`` a thread that has no turns yet, so the TUI starts its own thread and
the channel client follows it from ``thread/started``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Callable

from ciel_runtime_support.codex_app_server import CodexAppServerClient
from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess
from ciel_runtime_support.codex_desktop_injection import (
    CodexAppServerChannelInjector,
    CodexDesktopChannelPorts,
    CodexSessionCommandPorts,
)
from ciel_runtime_support.codex_desktop_runtime import (
    CodexDesktopPorts,
    CodexDesktopSession,
    listen_url_from_command,
    readyz_url,
)

REMOTE_TUI_LAUNCH_MODE = "codex-remote-router"
REMOTE_TUI_SESSION_ACTIONS = frozenset({"new_session"})
REMOTE_TUI_CHANNEL_ACTIONS = frozenset({"compact"})


def remote_tui_command(server_cmd: list[str], listen_url: str) -> list[str]:
    """The TUI command for a server command: same executable and config overrides.

    A ``--remote`` TUI sends the model, approval and sandbox settings from its
    own configuration when it starts a thread, so it takes the same ``-c``
    overrides the routed server was given.
    """

    values = [str(item) for item in server_cmd]
    try:
        start = values.index("app-server")
    except ValueError:
        return [values[0], "--remote", listen_url] if values else []
    config: list[str] = []
    index = start + 1
    while index < len(values):
        value = values[index]
        if value in ("-c", "--config") and index + 1 < len(values):
            config.extend([value, values[index + 1]])
            index += 2
            continue
        if value.startswith("--config="):
            config.append(value)
        index += 1
    return [*values[:start], *config, "--remote", listen_url]


@dataclass(frozen=True, slots=True)
class CodexRemoteTuiPorts:
    """What the TUI step needs from the launcher (all wired in ciel_runtime)."""

    run_terminal: Callable[..., int]
    restart_control: Callable[[], Any]
    set_transcript_scope: Callable[..., Any]
    synthetic_enter: Callable[[], bytes | None] = lambda: b"\r"
    submit_retries: Callable[[], int] = lambda: 1
    submit_delay_seconds: Callable[[], float | None] = lambda: None


def _channel_injector(
    ports: CodexDesktopPorts,
    listen_url: str,
    cwd: Path,
    **options: Any,
) -> CodexAppServerChannelInjector | None:
    if ports.channel is None:
        return None
    return CodexAppServerChannelInjector(
        lambda: CodexAppServerClient(CodexAppServerWebSocketProcess.connect(listen_url)),  # type: ignore[arg-type]
        ports.channel,
        cwd=str(cwd),
        version=ports.version,
        **options,
    )


class CodexBareAppServerSession:
    """Runs the app-server in the foreground and attaches the channel client."""

    display_name = "App Server"
    # Empty: the launcher keeps its own label (router or native provider mode).
    launch_mode = ""

    def __init__(self, ports: CodexDesktopPorts) -> None:
        self.ports = ports

    def __call__(
        self,
        cmd: list[str],
        env: dict[str, str],
        launch_cwd: Path,
        run_server: Callable[[], int] | None = None,
    ) -> int:
        ports = self.ports
        listen_url = listen_url_from_command(cmd)
        if not listen_url.startswith("ws://") or run_server is None:
            # stdio or unix transports carry one client only; nothing to add.
            ports.log("INFO", f"codex_app_server_channel_unavailable listen={listen_url or '-'}")
            return run_server() if run_server is not None else 2
        injector = _channel_injector(ports, listen_url, launch_cwd, wait_ready=self._ready(listen_url))
        if injector is not None:
            injector.start()
        try:
            return run_server()
        finally:
            if injector is not None:
                injector.stop()

    def _ready(self, listen_url: str) -> Callable[[], bool]:
        url = readyz_url(listen_url)
        return lambda: bool(self.ports.wait_ready(url))


class CodexRemoteTuiSession:
    """Starts the app-server, the channel client, then a ``--remote`` TUI."""

    display_name = "TUI (--remote app-server)"
    launch_mode = REMOTE_TUI_LAUNCH_MODE

    def __init__(self, ports: CodexDesktopPorts, tui: CodexRemoteTuiPorts) -> None:
        self.ports = ports
        self.tui = tui

    def __call__(
        self,
        cmd: list[str],
        env: dict[str, str],
        launch_cwd: Path,
        run_server: Callable[[], int] | None = None,
    ) -> int:
        ports = self.ports
        listen_url = listen_url_from_command(cmd)
        if not listen_url.startswith("ws://"):
            print(f"codex-remote needs a ws:// app-server listen address, got {listen_url or 'none'}", flush=True)
            return 2
        logs = ports.config_dir / "codex-remote" / ports.workspace_id
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "app-server.log", "ab") as server_log:
            server = ports.popen(cmd, env=env, cwd=str(launch_cwd), stdout=server_log, stderr=subprocess.STDOUT)
        injector: CodexAppServerChannelInjector | None = None
        try:
            if not ports.wait_ready(readyz_url(listen_url), alive=lambda: server.poll() is None):
                print(f"Codex app-server did not become ready at {listen_url}; see {logs / 'app-server.log'}", flush=True)
                return 1
            ports.log("INFO", f"codex_remote_app_server_ready pid={server.pid} listen={listen_url}")
            injector = _channel_injector(
                ports,
                listen_url,
                launch_cwd,
                start_own_thread=False,
                session_actions=REMOTE_TUI_CHANNEL_ACTIONS,
            )
            if injector is not None:
                injector.open()
                injector.start(opened=True)
            return self._run_tui(remote_tui_command(cmd, listen_url), env, launch_cwd, injector)
        finally:
            if injector is not None:
                injector.stop()
            if server.poll() is None:
                ports.terminate_tree(int(server.pid))
            ports.log("INFO", "codex_remote_session_ended")

    def _run_tui(
        self,
        base_cmd: list[str],
        env: dict[str, str],
        launch_cwd: Path,
        injector: CodexAppServerChannelInjector | None,
    ) -> int:
        tui = self.tui
        control = tui.restart_control()
        handled: set[str] = set()
        cmd = list(base_cmd)
        while True:
            # The server writes the rollout under its CODEX_HOME; the proxy
            # reads it to hold typed commands while a turn runs.
            tui.set_transcript_scope(
                "codex",
                codex_home=Path(env.get("CODEX_HOME") or (Path.home() / ".codex")),
                cwd=launch_cwd,
            )
            control.reset()
            self.ports.log("INFO", f"codex_remote_tui_start cmd={' '.join(cmd[1:])}")
            rc = tui.run_terminal(
                cmd,
                env,
                inject_channel_messages=False,
                synthetic_enter_bytes=tui.synthetic_enter(),
                normalize_bare_cr_for_synthetic_enter=False,
                channel_wake_submit_retries=tui.submit_retries(),
                channel_wake_confirm_submit=True,
                channel_wake_bracketed_paste=True,
                channel_wake_submit_delay_seconds=tui.submit_delay_seconds(),
                restart_poll=control.incoming,
                restart_state=control,
                session_command_runtime="codex",
                session_command_actions=REMOTE_TUI_SESSION_ACTIONS,
            )
            request = control.request
            if request is None or request.id in handled:
                return int(rc or 0)
            handled.add(request.id)
            thread_id = injector.thread_id if injector is not None else ""
            # The server keeps running, so the TUI re-attaches to the thread
            # the channel client last followed.
            cmd = [*base_cmd, "resume", thread_id] if request.resume and thread_id else list(base_cmd)
            print(f"Ciel Runtime: restarting the Codex TUI source={request.source or '-'} reason={request.reason or '-'}", flush=True)
            self.ports.log(
                "INFO",
                f"codex_remote_tui_restart exit_code={rc} source={request.source or '-'} "
                f"resumed_thread={thread_id if request.resume else '-'}",
            )


@dataclass(frozen=True, slots=True)
class CodexAppServerSessions:
    """The three app-server sessions, sharing one set of ports."""

    ports: CodexDesktopPorts
    tui: CodexRemoteTuiPorts

    @property
    def server(self) -> CodexBareAppServerSession:
        return CodexBareAppServerSession(self.ports)

    @property
    def desktop(self) -> CodexDesktopSession:
        return CodexDesktopSession(self.ports)

    @property
    def remote_tui(self) -> CodexRemoteTuiSession:
        return CodexRemoteTuiSession(self.ports, self.tui)


__all__ = [
    "CodexAppServerSessions",
    "CodexBareAppServerSession",
    "CodexDesktopChannelPorts",
    "CodexDesktopPorts",
    "CodexRemoteTuiPorts",
    "CodexRemoteTuiSession",
    "CodexSessionCommandPorts",
    "REMOTE_TUI_LAUNCH_MODE",
    "remote_tui_command",
]
