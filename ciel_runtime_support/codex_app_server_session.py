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

A saved conversation (``--continue``, ``resume [id]``) is resumed by the channel
client first, with the permissions Ciel's Codex TUI launch uses (``--yolo``),
and the TUI then attaches with ``--remote URL resume <id>``.  On codex 0.160.0
a remote TUI that resumes with ``--yolo`` is refused, and a thread saved by a
``--yolo`` TUI runs its commands "blocked by policy" unless the first resume
sets the permissions (journal 2026-10-03 research/codex-app-server).
Transcripts are shared both ways with the plain TUI: same rollout file, same
thread id.  The listener takes a per-launch capability token, so other local
accounts (the sandboxes of one host) cannot drive the session.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import subprocess
import threading
from typing import Any, Callable, Iterable

from ciel_runtime_support.codex_app_server import CodexAppServerClient, CodexAppServerError
from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess
from ciel_runtime_support.codex_cli import (
    codex_passthrough_args_for_launch,
    codex_passthrough_first_non_option_index,
)
from ciel_runtime_support.codex_desktop_injection import (
    FULL_ACCESS,
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
REMOTE_TOKEN_ENV = "CIEL_RUNTIME_CODEX_REMOTE_TOKEN"
# Permissions are the server's: the TUI may not send them when it resumes.
SERVER_ONLY_CONFIG_KEYS = ("approval_policy", "sandbox_mode")
PERMISSION_FLAGS = frozenset({"--yolo", "--dangerously-bypass-approvals-and-sandbox", "--full-auto"})
PICKER_FLAGS = ("--all", "--include-non-interactive")


@dataclass(frozen=True, slots=True)
class RemoteTuiResume:
    """The conversation a codex-remote launch asked for: a new one by default."""

    mode: str = ""  # "", "last", "pick" or "id"
    session_id: str = ""
    picker_args: tuple[str, ...] = ()


def split_remote_tui_passthrough(passthrough: Iterable[str]) -> tuple[list[str], RemoteTuiResume]:
    """Server arguments, and the resume request that belongs to the TUI.

    ``--continue``/``--resume`` map the way the Codex TUI launch maps them
    (``resume --last`` / ``resume <id>``).  Permission flags are dropped: the
    session sets permissions over the protocol.
    """

    args, _notes = codex_passthrough_args_for_launch([str(item) for item in passthrough])
    index = codex_passthrough_first_non_option_index(args)
    if index < 0 or args[index] != "resume":
        return [arg for arg in args if arg not in PERMISSION_FLAGS], RemoteTuiResume()
    server = [arg for arg in args[:index] if arg not in PERMISSION_FLAGS]
    rest = args[index + 1 :]
    picker = tuple(arg for arg in rest if arg in PICKER_FLAGS)
    positional = [arg for arg in rest if not arg.startswith("-")]
    if "--last" in rest:
        return server, RemoteTuiResume("last", picker_args=picker)
    if positional:
        return server, RemoteTuiResume("id", positional[0])
    return server, RemoteTuiResume("pick", picker_args=picker)


def _config_key(setting: str) -> str:
    return setting.split("=", 1)[0].strip()


def remote_server_command(cmd: list[str], token_file: Path) -> list[str]:
    """The routed server command with full-access defaults and a ws token."""

    values = [str(item) for item in cmd]
    try:
        start = values.index("app-server") + 1
    except ValueError:
        return values
    defaults = [
        "-c", f'approval_policy="{FULL_ACCESS.approval_policy}"',
        "-c", f'sandbox_mode="{FULL_ACCESS.sandbox}"',
    ]
    out = [*values[:start], *defaults, *values[start:]]
    if "--ws-auth" not in out:
        out.extend(["--ws-auth", "capability-token", "--ws-token-file", str(token_file)])
    return out


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
            if _config_key(values[index + 1]) not in SERVER_ONLY_CONFIG_KEYS:
                config.extend([value, values[index + 1]])
            index += 2
            continue
        if value.startswith("--config=") and _config_key(value.split("=", 1)[1]) not in SERVER_ONLY_CONFIG_KEYS:
            config.append(value)
        index += 1
    remote = ["--remote", listen_url]
    if "--ws-auth" in values:
        remote.extend(["--remote-auth-token-env", REMOTE_TOKEN_ENV])
    return [*values[:start], *config, *remote]


@dataclass(frozen=True, slots=True)
class CodexRemoteTuiPorts:
    """What the TUI step needs from the launcher (all wired in ciel_runtime)."""

    run_terminal: Callable[..., int]
    restart_control: Callable[[], Any]
    set_transcript_scope: Callable[..., Any]
    synthetic_enter: Callable[[], bytes | None] = lambda: b"\r"
    submit_retries: Callable[[], int] = lambda: 1
    submit_delay_seconds: Callable[[], float | None] = lambda: None
    # select_codex_resume_session(env, include_non_interactive=, passthrough=, cwd=, select_latest=)
    select_resume: Callable[..., str | None] | None = None


def _channel_injector(
    ports: CodexDesktopPorts,
    listen_url: str,
    cwd: Path,
    bearer_token: str = "",
    **options: Any,
) -> CodexAppServerChannelInjector | None:
    if ports.channel is None:
        return None
    return CodexAppServerChannelInjector(
        lambda: CodexAppServerClient(  # type: ignore[arg-type]
            CodexAppServerWebSocketProcess.connect(listen_url, bearer_token=bearer_token)
        ),
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
        server_done = threading.Event()
        injector = _channel_injector(
            ports, listen_url, launch_cwd, wait_ready=self._ready(listen_url, server_done)
        )
        if injector is not None:
            injector.start()
        try:
            return run_server()
        finally:
            # Ends the /readyz wait too: a server that exits before it is ready
            # must not leave the client polling (and logging) behind it.
            server_done.set()
            if injector is not None:
                injector.stop()

    def _ready(self, listen_url: str, server_done: threading.Event) -> Callable[[], bool]:
        url = readyz_url(listen_url)
        return lambda: bool(self.ports.wait_ready(url, alive=lambda: not server_done.is_set()))


class CodexRemoteTuiSession:
    """Starts the app-server, the channel client, then a ``--remote`` TUI."""

    display_name = "TUI (--remote app-server)"
    launch_mode = REMOTE_TUI_LAUNCH_MODE

    def __init__(self, ports: CodexDesktopPorts, tui: CodexRemoteTuiPorts) -> None:
        self.ports = ports
        self.tui = tui

    @staticmethod
    def split_passthrough(passthrough: list[str]) -> tuple[list[str], dict[str, Any]]:
        """run_codex_app_server: server arguments, and this session's own."""

        server, resume = split_remote_tui_passthrough(passthrough)
        return server, {"resume": resume}

    def __call__(
        self,
        cmd: list[str],
        env: dict[str, str],
        launch_cwd: Path,
        run_server: Callable[[], int] | None = None,
        resume: RemoteTuiResume | None = None,
    ) -> int:
        ports = self.ports
        listen_url = listen_url_from_command(cmd)
        if not listen_url.startswith("ws://"):
            print(f"codex-remote needs a ws:// app-server listen address, got {listen_url or 'none'}", flush=True)
            return 2
        thread_id = self._resume_thread_id(resume or RemoteTuiResume(), env, launch_cwd)
        if thread_id is None:
            return 0
        logs = ports.config_dir / "codex-remote" / ports.workspace_id
        logs.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        token_file = logs / "ws-token"
        _write_private(token_file, token)
        server_cmd = remote_server_command(cmd, token_file)
        with open(logs / "app-server.log", "ab") as server_log:
            server = ports.popen(server_cmd, env=env, cwd=str(launch_cwd), stdout=server_log, stderr=subprocess.STDOUT)
        injector: CodexAppServerChannelInjector | None = None
        try:
            if not ports.wait_ready(readyz_url(listen_url), alive=lambda: server.poll() is None):
                print(f"Codex app-server did not become ready at {listen_url}; see {logs / 'app-server.log'}", flush=True)
                return 1
            ports.log("INFO", f"codex_remote_app_server_ready pid={server.pid} listen={listen_url} resume={thread_id or '-'}")
            injector = _channel_injector(
                ports,
                listen_url,
                launch_cwd,
                bearer_token=token,
                start_own_thread=False,
                session_actions=REMOTE_TUI_CHANNEL_ACTIONS,
                initial_thread_id=thread_id,
                permissions=FULL_ACCESS,
            )
            if injector is not None:
                try:
                    injector.open()
                except (CodexAppServerError, OSError) as exc:
                    ports.log("ERROR", f"codex_remote_channel_open_failed resume={thread_id or '-'} error={str(exc)[:300]}")
                    print(f"Codex app-server could not open conversation {thread_id or '(new)'}: {exc}", flush=True)
                    return 1
                injector.start(opened=True)
            tui_env = dict(env)
            tui_env[REMOTE_TOKEN_ENV] = token
            return self._run_tui(remote_tui_command(server_cmd, listen_url), tui_env, launch_cwd, injector, thread_id)
        finally:
            if injector is not None:
                injector.stop()
            if server.poll() is None:
                ports.terminate_tree(int(server.pid))
            try:
                token_file.unlink()
            except OSError:
                pass
            ports.log("INFO", "codex_remote_session_ended")

    def _resume_thread_id(self, resume: RemoteTuiResume, env: dict[str, str], launch_cwd: Path) -> str | None:
        """The saved conversation to open ("" for a new one), None when none was chosen."""

        if resume.mode in ("", "id"):
            return resume.session_id
        select = self.tui.select_resume
        if select is None:
            print("codex-remote cannot list saved Codex sessions here; pass resume <session id>.", flush=True)
            return None
        selected = select(
            env,
            include_non_interactive="--include-non-interactive" in resume.picker_args,
            passthrough=["resume", *resume.picker_args],
            cwd=launch_cwd,
            select_latest=resume.mode == "last",
        )
        return str(selected or "").strip() or None

    def _run_tui(
        self,
        base_cmd: list[str],
        env: dict[str, str],
        launch_cwd: Path,
        injector: CodexAppServerChannelInjector | None,
        thread_id: str = "",
    ) -> int:
        tui = self.tui
        control = tui.restart_control()
        handled: set[str] = set()
        cmd = [*base_cmd, "resume", thread_id] if thread_id else list(base_cmd)
        while True:
            # The server writes the rollout under its CODEX_HOME; the proxy
            # reads it to hold typed commands while a turn runs.
            tui.set_transcript_scope(
                "codex",
                codex_home=Path(env.get("CODEX_HOME") or (Path.home() / ".codex")),
                cwd=launch_cwd,
                # A resumed conversation may have begun in another folder.
                session_id=cmd[-1] if "resume" in cmd[-2:-1] else None,
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


def _write_private(path: Path, text: str) -> None:
    """Create ``path`` readable by its owner only (POSIX mode; Windows ACLs follow the profile)."""

    try:
        path.unlink()
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


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
    "REMOTE_TOKEN_ENV",
    "REMOTE_TUI_LAUNCH_MODE",
    "RemoteTuiResume",
    "remote_server_command",
    "remote_tui_command",
    "split_remote_tui_passthrough",
]
