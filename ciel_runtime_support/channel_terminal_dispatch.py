"""Platform dispatch and direct-process lifecycle for channel terminals."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
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
from ciel_runtime_support.channel_terminal_proxy import terminal_proxy_error_context
from ciel_runtime_support.startup_prompt_answers import STARTUP_AUTO_ANSWER_ENV, startup_auto_answer_enabled

if TYPE_CHECKING:
    from ciel_runtime_support.channel_terminal_proxy import ChannelTerminalPolicy, ChannelTerminalPolling


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
class ChannelDirectDeliveryServices:
    policy: ChannelTerminalPolicy
    polling: ChannelTerminalPolling


@dataclass(frozen=True, slots=True)
class ChannelTerminalDispatchService:
    settings: ChannelTerminalDispatchSettings
    proxy: ChannelTerminalProxyPorts
    direct: ChannelDirectProcessPorts
    log: Callable[[str, str], None]
    delivery: ChannelDirectDeliveryServices | None = None

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
        session_command_runtime: str = "",
        session_command_actions: frozenset[str] | None = None,
    ) -> int:
        if startup_auto_answer_enabled():
            env = {**env, STARTUP_AUTO_ANSWER_ENV: "1"}
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
            "session_command_runtime": session_command_runtime,
            "session_command_actions": session_command_actions,
        }
        pending_state = (
            ChannelPendingPollState(last_id=0)
            if self.delivery is not None and inject_channel_messages
            else None
        )
        if self.settings.platform_name == "nt" and self.proxy.windows_supported():
            try:
                windows_options = dict(options)
                if pending_state is not None:
                    windows_options["pending_poll_state"] = pending_state
                return self.proxy.run_windows(cmd, env, **windows_options)
            except Exception as exc:
                self.log(
                    "WARN",
                    "channel_windows_console_proxy_failed "
                    f"{terminal_proxy_error_context(exc, stage='windows_proxy')}; "
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
                pending_options=ChannelPendingInjectionOptions(
                    enabled=inject_channel_messages,
                    web_chat_only=inject_web_chat_only,
                    wake_for_llm_delivery=wake_for_llm_delivery,
                    display_llm_delivery_body=channel_wake_display_body,
                    submit_retry_count=max(1, min(8, int(channel_wake_submit_retries or 1))),
                    confirm_submit=channel_wake_confirm_submit,
                    bracketed_paste=False,
                    submit_delay_seconds=channel_wake_submit_delay_seconds,
                    terminal_available=False,
                ),
                synthetic_enter_bytes=synthetic_enter_bytes,
                pending_state=pending_state,
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
        pending_options: ChannelPendingInjectionOptions | None = None,
        synthetic_enter_bytes: str | bytes | None = None,
        pending_state: ChannelPendingPollState | None = None,
    ) -> int:
        if startup_auto_answer_enabled():
            env = {**env, STARTUP_AUTO_ANSWER_ENV: "1"}
        unattended_startup = startup_auto_answer_enabled(env)
        if unattended_startup:
            self.log("WARN", "startup_prompt_auto_answer_unavailable transport=direct rendered_screen=false automatic_injection=false")
        poller = None
        if (
            self.delivery is not None
            and pending_options is not None
            and pending_options.enabled
            and not unattended_startup
        ):
            poller = _DirectChannelPoller(
                self.delivery,
                pending_options,
                self.delivery.policy.enter_bytes(synthetic_enter_bytes),
                pending_state,
            )
        if pid_path is None and restart_poll is None and poller is None:
            return self.direct.call(cmd, env=env)
        proc = self.direct.popen(cmd, env=env)
        try:
            self.direct.write_record(pid_path, proc.pid, cmd)
            if poller is not None:
                self.log(
                    "INFO",
                    f"channel_direct_socket_poller_started pid={proc.pid} terminal_available=false",
                )
            return self._await_child(proc, restart_poll, restart_state, poller)
        finally:
            try:
                self.direct.terminate(proc, "current Codex")
            finally:
                self.direct.release_record(pid_path, proc.pid)
                if poller is not None:
                    self.log("INFO", f"channel_direct_socket_poller_stopped pid={proc.pid}")

    def _await_child(
        self,
        proc: Any,
        restart_poll: Callable[[], Any] | None,
        restart_state: Any,
        poller: "_DirectChannelPoller | None" = None,
    ) -> int:
        if restart_poll is None and poller is None:
            return proc.wait()
        while True:
            returncode = proc.poll()
            if returncode is not None:
                return returncode
            request = self._restart_request(restart_poll, restart_state)
            if request is not None:
                if restart_state is not None:
                    restart_state.mark(request)
                self.log(
                    "INFO",
                    "runtime_session_restart_requested transport=direct",
                )
                self.direct.terminate(proc, "cli session restart")
                return proc.wait()
            if poller is not None:
                try:
                    poller.poll(time.time())
                except ChannelFileLockBusy as exc:
                    self.log(
                        "WARN",
                        "channel_direct_socket_poller_deferred "
                        f"phase=lock_acquire errno={exc.errno} retry_after=0.5",
                    )
            time.sleep(0.5)

    def _restart_request(self, restart_poll: Callable[[], Any] | None, restart_state: Any) -> Any:
        if restart_poll is None:
            return None
        if self.delivery is not None and restart_poll == getattr(restart_state, "incoming", None):
            return restart_state.incoming(busy=self.delivery.polling.input_busy)
        return restart_poll()


class _DirectChannelPoller:
    """Keep whole-message channel delivery alive when no terminal proxy exists."""

    def __init__(
        self,
        delivery: ChannelDirectDeliveryServices,
        options: ChannelPendingInjectionOptions,
        enter_bytes: bytes,
        state: ChannelPendingPollState | None = None,
    ) -> None:
        self.delivery = delivery
        self.options = options
        self.enter_bytes = enter_bytes
        self.state = state

    def poll(self, now: float) -> None:
        policy = self.delivery.policy
        polling = self.delivery.polling
        if self.state is None:
            self.state = ChannelPendingPollState(last_id=policy.initial_cursor())
        state = self.state
        paused = state.inflight.hold_for_recovery(
            now, bool(getattr(policy, "compaction", None) and policy.compaction.external_admission_paused())
        )
        if paused:
            state.pending_recheck = True
            return
        if state.inflight_message_id is not None:
            update = advance_channel_inflight(
                ChannelInflightSnapshot(
                    message_id=state.inflight_message_id,
                    cursor=state.inflight_cursor,
                    wake_state=polling.wake_state(state.inflight_message_id),
                    started_at=state.inflight_started_at,
                    logged_at=state.inflight_logged_at,
                    now=now,
                    last_id=state.last_id,
                    recovery_protected=(
                        state.inflight_message_id in state.inflight.recovery_protected
                    ),
                ),
                ChannelInflightPolicy(
                    unseen_retry_seconds=policy.unseen_retry_seconds(),
                    waiting_log_interval=30.0,
                    is_stale=policy.inflight_is_stale,
                    commit_cursor_on_stale=False,
                    log_namespace="channel_direct_socket",
                    stale_event="stale_inflight_retry",
                ),
                polling.inflight_effects(),
            )
            state.inflight_message_id = update.message_id
            state.inflight_cursor = update.cursor
            state.inflight_started_at = update.started_at
            state.inflight_logged_at = update.logged_at
            state.pending_recheck = state.pending_recheck or update.pending_recheck
            state.last_id = update.last_id
            if update.action in {"completed", "failed"}:
                state.inflight.attempts = 0
        self.state = poll_pending_channel_messages(
            now,
            None,
            self.enter_bytes,
            state,
            self.options,
            ChannelPendingPollPolicy("channel_direct_socket", "active_turn"),
            ChannelPendingPollServices(
                file_marker=polling.file_marker,
                should_check=polling.should_check,
                active=polling.input_busy,
                ensure_cursor=policy.initial_cursor,
                inject_pending=polling.inject_pending,
                log=policy.log,
                session_socket_ready=polling.session_socket_ready,
                external_admission_paused=(policy.compaction.external_admission_paused if getattr(policy, "compaction", None) else lambda: False),
            ),
        )
