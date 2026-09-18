"""Platform dispatch and direct-process lifecycle for channel terminals."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelTerminalDispatchSettings:
    platform_name: str
    stdin_isatty: Callable[[], bool]
    stdout_isatty: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ChannelTerminalProxyPorts:
    windows_supported: Callable[[], bool]
    run_windows: Callable[..., int]
    run_posix: Callable[..., int]
    posix_services: Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ChannelDirectProcessPorts:
    call: Callable[..., int]
    popen: Callable[..., Any]
    write_record: Callable[[Path | None, int, list[str]], None]
    terminate: Callable[[Any, str], None]
    release_record: Callable[[Path | None, int], None]


@dataclass(frozen=True, slots=True)
class ChannelTerminalDispatchService:
    settings: ChannelTerminalDispatchSettings
    proxy: ChannelTerminalProxyPorts
    direct: ChannelDirectProcessPorts
    log: Callable[[str, str], None]

    def dispatch(
        self,
        cmd: list[str],
        env: dict[str, str],
        *,
        inject_channel_messages: bool = True,
        inject_web_chat_only: bool = False,
        wake_for_llm_delivery: bool = False,
        channel_wake_display_body: bool = False,
        synthetic_enter_bytes: str | bytes | None = None,
        normalize_bare_cr_for_synthetic_enter: bool = True,
        channel_wake_submit_retries: int = 1,
        channel_wake_confirm_submit: bool = False,
        channel_wake_bracketed_paste: bool = False,
        channel_wake_submit_delay_seconds: float | None = None,
        tracked_child_pid_path: Path | None = None,
        restart_poll: Callable[[], Any] | None = None,
        restart_state: Any = None,
    ) -> int:
        options = {
            "inject_channel_messages": inject_channel_messages,
            "inject_web_chat_only": inject_web_chat_only,
            "wake_for_llm_delivery": wake_for_llm_delivery,
            "channel_wake_display_body": channel_wake_display_body,
            "synthetic_enter_bytes": synthetic_enter_bytes,
            "normalize_bare_cr_for_synthetic_enter": (
                normalize_bare_cr_for_synthetic_enter
            ),
            "channel_wake_submit_retries": channel_wake_submit_retries,
            "channel_wake_confirm_submit": channel_wake_confirm_submit,
            "channel_wake_bracketed_paste": channel_wake_bracketed_paste,
            "channel_wake_submit_delay_seconds": (
                channel_wake_submit_delay_seconds
            ),
            "tracked_child_pid_path": tracked_child_pid_path,
            "restart_poll": restart_poll,
            "restart_state": restart_state,
        }
        if self.settings.platform_name == "nt" and self.proxy.windows_supported():
            try:
                return self.proxy.run_windows(cmd, env, **options)
            except Exception as exc:
                self.log(
                    "WARN",
                    "channel_windows_console_proxy_failed "
                    f"error={type(exc).__name__}: {exc}; "
                    "using direct subprocess call",
                )
        if (
            self.settings.platform_name != "posix"
            or not self.settings.stdin_isatty()
            or not self.settings.stdout_isatty()
        ):
            self.log(
                "INFO",
                "channel_stdin_proxy_unavailable; using direct subprocess call",
            )
            return self.call_direct(
                cmd,
                env,
                tracked_child_pid_path,
                restart_poll=restart_poll,
                restart_state=restart_state,
            )
        return self.proxy.run_posix(
            cmd,
            env,
            self.proxy.posix_services(),
            **options,
        )

    def call_direct(
        self,
        cmd: list[str],
        env: dict[str, str],
        pid_path: Path | None = None,
        *,
        restart_poll: Callable[[], Any] | None = None,
        restart_state: Any = None,
    ) -> int:
        if pid_path is None and restart_poll is None:
            return self.direct.call(cmd, env=env)
        proc = self.direct.popen(cmd, env=env)
        self.direct.write_record(pid_path, proc.pid, cmd)
        try:
            return self._await_child(proc, restart_poll, restart_state)
        finally:
            self.direct.terminate(proc, "current Codex")
            self.direct.release_record(pid_path, proc.pid)

    def _await_child(
        self,
        proc: Any,
        restart_poll: Callable[[], Any] | None,
        restart_state: Any,
    ) -> int:
        if restart_poll is None:
            return proc.wait()
        while True:
            returncode = proc.poll()
            if returncode is not None:
                return returncode
            request = restart_poll()
            if request is not None:
                if restart_state is not None:
                    restart_state.mark(request)
                self.log(
                    "INFO",
                    "runtime_session_restart_requested transport=direct",
                )
                self.direct.terminate(proc, "cli session restart")
                return proc.wait()
            time.sleep(0.5)
