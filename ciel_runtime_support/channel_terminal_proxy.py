from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support.channel_compact_poll import (
    ChannelCompactInjectionOptions,
    ChannelCompactPollServices,
    ChannelCompactPollState,
    poll_pending_compaction,
)
from ciel_runtime_support.channel_inflight import (
    ChannelInflightPolicy,
    ChannelInflightSnapshot,
    advance_channel_inflight,
)
from ciel_runtime_support.channel_pending_poll import (
    ChannelPendingInjectionOptions,
    ChannelPendingPollPolicy,
    ChannelPendingPollServices,
    ChannelPendingPollState,
    poll_pending_channel_messages,
)


@dataclass(frozen=True)
class ChannelTerminalProcess:
    popen: Callable[..., Any]
    write_child_record: Callable[[Path | None, int, list[str]], None]
    terminate_child: Callable[[Any, str], None]
    release_child_record: Callable[[Path | None, int], None]


@dataclass(frozen=True)
class ChannelTerminalIO:
    terminal_size: Callable[[int], tuple[int, int]]
    apply_terminal_size: Callable[[int, int, int], bool]
    write_all: Callable[[Any, bytes], None]
    mouse_filter: Callable[[], Any]
    observed_enter: Callable[..., bytes | None]
    reset_input_mode: Callable[[], None]


@dataclass(frozen=True)
class ChannelTerminalPolicy:
    initial_cursor: Callable[[], int]
    enter_bytes: Callable[[str | bytes | None], bytes]
    enter_label: Callable[[bytes], str]
    enter_is_fixed: Callable[[], bool]
    unseen_retry_seconds: Callable[[], float]
    inflight_is_stale: Callable[..., bool]
    log: Callable[[str, str], None]


@dataclass(frozen=True)
class ChannelTerminalPolling:
    inject_compact: Callable[..., Any]
    file_marker: Callable[[], Any]
    should_check: Callable[..., bool]
    active_tool_call: Callable[..., bool]
    inject_pending: Callable[..., Any]
    wake_state: Callable[[int], Any]
    inflight_effects: Callable[[], Any]


@dataclass(frozen=True)
class ChannelTerminalServices:
    process: ChannelTerminalProcess
    io: ChannelTerminalIO
    policy: ChannelTerminalPolicy
    polling: ChannelTerminalPolling


def run_posix_channel_terminal_proxy(
    cmd: list[str],
    env: dict[str, str],
    services: ChannelTerminalServices,
    *,
    inject_channel_messages: bool = True,
    inject_web_chat_only: bool = False,
    wake_for_llm_delivery: bool = False,
    synthetic_enter_bytes: str | bytes | None = None,
    normalize_bare_cr_for_synthetic_enter: bool = True,
    channel_wake_submit_retries: int = 1,
    channel_wake_confirm_submit: bool = False,
    channel_wake_bracketed_paste: bool = False,
    channel_wake_submit_delay_seconds: float | None = None,
    tracked_child_pid_path: Path | None = None,
) -> int:
    import pty
    import select
    import termios
    import tty

    process = services.process
    terminal = services.io
    policy = services.policy
    polling = services.polling
    pending_poll_state = ChannelPendingPollState(last_id=policy.initial_cursor())
    master_fd, slave_fd = pty.openpty()
    stdout_fd = sys.stdout.fileno()
    rows, cols = terminal.terminal_size(stdout_fd)
    if terminal.apply_terminal_size(slave_fd, rows, cols):
        policy.log("INFO", f"channel_stdin_proxy_winsize_init rows={rows} cols={cols}")
    proc = process.popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=env, close_fds=True)
    process.write_child_record(tracked_child_pid_path, proc.pid, cmd)
    os.close(slave_fd)
    stdin_fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(stdin_fd)
    old_sigwinch = None
    sigwinch_installed = False
    mouse_input_filter = terminal.mouse_filter()
    channel_enter_bytes = policy.enter_bytes(synthetic_enter_bytes)
    submit_retry_count = max(1, min(8, int(channel_wake_submit_retries or 1)))
    compact_injection_options = ChannelCompactInjectionOptions(
        submit_retry_count=submit_retry_count,
        confirm_submit=channel_wake_confirm_submit,
        bracketed_paste=channel_wake_bracketed_paste,
        submit_delay_seconds=channel_wake_submit_delay_seconds,
    )
    compact_poll_services = ChannelCompactPollServices(inject_pending=polling.inject_compact)
    compact_poll_state = ChannelCompactPollState()
    pending_injection_options = ChannelPendingInjectionOptions(
        enabled=inject_channel_messages,
        web_chat_only=inject_web_chat_only,
        wake_for_llm_delivery=wake_for_llm_delivery,
        submit_retry_count=submit_retry_count,
        confirm_submit=channel_wake_confirm_submit,
        bracketed_paste=channel_wake_bracketed_paste,
        submit_delay_seconds=channel_wake_submit_delay_seconds,
    )
    pending_poll_services = ChannelPendingPollServices(
        file_marker=polling.file_marker,
        should_check=polling.should_check,
        active=polling.active_tool_call,
        ensure_cursor=policy.initial_cursor,
        inject_pending=polling.inject_pending,
        log=policy.log,
    )
    pending_poll_policy = ChannelPendingPollPolicy("channel_stdin_proxy", "active_tool_call")
    policy.log(
        "INFO",
        "channel_stdin_proxy_enter_default "
        f"enter={policy.enter_label(channel_enter_bytes)} os={os.name} platform={sys.platform} "
        f"submit_retries={submit_retry_count} confirm_submit={bool(channel_wake_confirm_submit)} "
        f"bracketed_paste={bool(channel_wake_bracketed_paste)}",
    )
    terminal.reset_input_mode()

    def handle_terminal_resize(signum: int, frame: Any) -> None:
        new_rows, new_cols = terminal.terminal_size(stdout_fd)
        if terminal.apply_terminal_size(master_fd, new_rows, new_cols):
            policy.log("INFO", f"channel_stdin_proxy_winsize_resize rows={new_rows} cols={new_cols}")
        if callable(old_sigwinch):
            old_sigwinch(signum, frame)

    try:
        try:
            old_sigwinch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, handle_terminal_resize)
            sigwinch_installed = True
        except Exception as exc:
            policy.log("WARN", f"channel_sigwinch_install_failed error={type(exc).__name__}: {exc}")
        tty.setraw(stdin_fd)
        while proc.poll() is None:
            try:
                readable, _, _ = select.select([stdin_fd, master_fd], [], [], 0.2)
            except OSError:
                break
            if stdin_fd in readable:
                data = os.read(stdin_fd, 4096)
                if data:
                    filtered_data = mouse_input_filter.feed(data)
                    if filtered_data:
                        observed_enter = terminal.observed_enter(
                            filtered_data,
                            normalize_bare_cr=normalize_bare_cr_for_synthetic_enter,
                        )
                        if observed_enter and not policy.enter_is_fixed():
                            if observed_enter != channel_enter_bytes:
                                policy.log(
                                    "INFO",
                                    f"channel_stdin_proxy_enter_observed enter={policy.enter_label(observed_enter)}",
                                )
                            channel_enter_bytes = observed_enter
                        terminal.write_all(master_fd, filtered_data)
            if master_fd in readable:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if data:
                    terminal.write_all(stdout_fd, data)
            now = time.time()
            if pending_poll_state.inflight_message_id is not None:
                inflight_update = advance_channel_inflight(
                    ChannelInflightSnapshot(
                        message_id=pending_poll_state.inflight_message_id,
                        cursor=pending_poll_state.inflight_cursor,
                        wake_state=polling.wake_state(pending_poll_state.inflight_message_id),
                        started_at=pending_poll_state.inflight_started_at,
                        logged_at=pending_poll_state.inflight_logged_at,
                        now=now,
                        last_id=pending_poll_state.last_id,
                    ),
                    ChannelInflightPolicy(
                        unseen_retry_seconds=policy.unseen_retry_seconds(),
                        waiting_log_interval=30.0,
                        is_stale=policy.inflight_is_stale,
                        commit_cursor_on_stale=True,
                        log_namespace="channel_stdin_proxy",
                        stale_event="stale_inflight_skipped",
                    ),
                    polling.inflight_effects(),
                )
                pending_poll_state.inflight_message_id = inflight_update.message_id
                pending_poll_state.inflight_cursor = inflight_update.cursor
                pending_poll_state.inflight_started_at = inflight_update.started_at
                pending_poll_state.inflight_logged_at = inflight_update.logged_at
                pending_poll_state.pending_recheck = (
                    pending_poll_state.pending_recheck or inflight_update.pending_recheck
                )
                pending_poll_state.last_id = inflight_update.last_id
            compact_poll_state = poll_pending_compaction(
                now,
                master_fd,
                channel_enter_bytes,
                pending_poll_state.inflight_message_id,
                compact_poll_state,
                compact_injection_options,
                compact_poll_services,
            )
            pending_poll_state = poll_pending_channel_messages(
                now,
                master_fd,
                channel_enter_bytes,
                pending_poll_state,
                pending_injection_options,
                pending_poll_policy,
                pending_poll_services,
            )
        while True:
            try:
                readable, _, _ = select.select([master_fd], [], [], 0)
                if master_fd not in readable:
                    break
                data = os.read(master_fd, 4096)
                if not data:
                    break
                terminal.write_all(stdout_fd, data)
            except OSError:
                break
        return proc.returncode if proc.returncode is not None else 0
    finally:
        if sigwinch_installed:
            try:
                signal.signal(signal.SIGWINCH, old_sigwinch)
            except Exception as exc:
                policy.log("WARN", f"channel_sigwinch_restore_failed error={type(exc).__name__}: {exc}")
        try:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        except Exception as exc:
            policy.log("WARN", f"channel_terminal_restore_failed error={type(exc).__name__}: {exc}")
        terminal.reset_input_mode()
        try:
            os.close(master_fd)
        except Exception as exc:
            policy.log("WARN", f"channel_pty_close_failed error={type(exc).__name__}: {exc}")
        process.terminate_child(proc, "current Codex")
        process.release_child_record(tracked_child_pid_path, proc.pid)
