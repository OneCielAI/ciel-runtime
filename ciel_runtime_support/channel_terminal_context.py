"""Terminal channel process orchestration bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .runtime_interaction import RuntimeInteractionEvent

from .channel_terminal_dispatch import (
    ChannelDirectProcessPorts,
    ChannelTerminalDispatchService,
    ChannelTerminalDispatchSettings,
    ChannelTerminalProxyPorts,
)
from .channel_terminal_proxy import (
    ChannelTerminalIO,
    ChannelTerminalPolicy,
    ChannelTerminalPolling,
    ChannelTerminalProcess,
    ChannelTerminalServices,
    ChannelWindowsConsole,
    ChannelWindowsServices,
)


@dataclass(frozen=True, slots=True)
class ChannelTerminalProcessPorts:
    popen: Callable[..., Any]
    write_record: Callable[[Path | None, int, list[str]], None]
    terminate: Callable[[Any, str], None]
    release_record: Callable[[Path | None, int], None]


@dataclass(frozen=True, slots=True)
class ChannelTerminalPolicyPorts:
    initial_cursor: Callable[[], int]
    enter_bytes: Callable[[str | bytes | None], bytes]
    enter_label: Callable[[bytes], str]
    enter_is_fixed: Callable[[], bool]
    unseen_retry_seconds: Callable[[], float]
    inflight_is_stale: Callable[..., bool]
    log: Callable[[str, str], None]
    windows_wake_max_attempts: Callable[[], int]


@dataclass(frozen=True, slots=True)
class ChannelTerminalPollingPorts:
    inject_compact: Callable[..., Any]
    file_marker: Callable[[], Any]
    should_check: Callable[..., bool]
    active_tool_call: Callable[..., bool]
    active_turn: Callable[..., bool]
    inject_pending: Callable[..., Any]
    wake_state: Callable[[int], Any]
    inflight_effects: Callable[[], Any]
    mark_body_fallback: Callable[[int, str], None]
    runtime_interaction: Callable[[], RuntimeInteractionEvent | None] = lambda: None


@dataclass(frozen=True, slots=True)
class ChannelTerminalIoPorts:
    terminal_size: Callable[[int], tuple[int, int]]
    apply_terminal_size: Callable[[int, int, int], bool]
    write_all: Callable[[Any, bytes], None]
    mouse_filter: Callable[[], Any]
    observed_enter: Callable[..., bytes | None]
    reset_input_mode: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ChannelTerminalWindowsPorts:
    run_proxy: Callable[..., int]
    reset_input_mode: Callable[[], None]
    mouse_guard: Callable[[], Any]
    input_writer: Callable[[], Any]
    startup_grace_seconds: Callable[[], float]
    reset_interval_seconds: Callable[[float], float]
    active_turn: Callable[[], bool]
    write_body_fallback: Callable[[Any, int, bytes], None]
    sleep: Callable[[float], None]
    open_conpty: Callable[[list[str], dict[str, str], Callable[[str, str], None]], Any | None]


@dataclass(frozen=True, slots=True)
class ChannelTerminalDispatchPorts:
    platform_name: str
    stdin_isatty: Callable[[], bool]
    stdout_isatty: Callable[[], bool]
    direct_call: Callable[..., int]
    run_windows: Callable[..., int]
    run_posix: Callable[..., int]
    prepare_delivery: Callable[[], Any]
    windows_supported: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ChannelTerminalContext:
    process: ChannelTerminalProcessPorts
    policy: ChannelTerminalPolicyPorts
    polling: ChannelTerminalPollingPorts
    io: ChannelTerminalIoPorts
    windows: ChannelTerminalWindowsPorts
    dispatch_ports: ChannelTerminalDispatchPorts

    def process_services(self) -> ChannelTerminalProcess:
        return ChannelTerminalProcess(
            popen=self.process.popen,
            write_child_record=self.process.write_record,
            terminate_child=self.process.terminate,
            release_child_record=self.process.release_record,
        )

    def policy_services(self) -> ChannelTerminalPolicy:
        return ChannelTerminalPolicy(
            initial_cursor=self.policy.initial_cursor,
            enter_bytes=self.policy.enter_bytes,
            enter_label=self.policy.enter_label,
            enter_is_fixed=self.policy.enter_is_fixed,
            unseen_retry_seconds=self.policy.unseen_retry_seconds,
            inflight_is_stale=self.policy.inflight_is_stale,
            log=self.policy.log,
            windows_wake_max_attempts=self.policy.windows_wake_max_attempts,
        )

    def polling_services(self) -> ChannelTerminalPolling:
        return ChannelTerminalPolling(
            inject_compact=self.polling.inject_compact,
            file_marker=self.polling.file_marker,
            should_check=self.polling.should_check,
            active_tool_call=self.polling.active_tool_call,
            active_turn=self.polling.active_turn,
            inject_pending=self.polling.inject_pending,
            wake_state=self.polling.wake_state,
            inflight_effects=self.polling.inflight_effects,
            mark_body_fallback=self.polling.mark_body_fallback,
            runtime_interaction=self.polling.runtime_interaction,
        )

    def posix_services(self) -> ChannelTerminalServices:
        return ChannelTerminalServices(
            process=self.process_services(),
            io=ChannelTerminalIO(
                terminal_size=self.io.terminal_size,
                apply_terminal_size=self.io.apply_terminal_size,
                write_all=self.io.write_all,
                mouse_filter=self.io.mouse_filter,
                observed_enter=self.io.observed_enter,
                reset_input_mode=self.io.reset_input_mode,
            ),
            policy=self.policy_services(),
            polling=self.polling_services(),
        )

    def windows_services(self) -> ChannelWindowsServices:
        return ChannelWindowsServices(
            process=self.process_services(),
            policy=self.policy_services(),
            polling=self.polling_services(),
            console=ChannelWindowsConsole(
                reset_input_mode=self.windows.reset_input_mode,
                mouse_guard=self.windows.mouse_guard,
                input_writer=self.windows.input_writer,
                startup_grace_seconds=self.windows.startup_grace_seconds,
                reset_interval_seconds=self.windows.reset_interval_seconds,
                active_turn=self.windows.active_turn,
                write_body_fallback=self.windows.write_body_fallback,
                sleep=self.windows.sleep,
                open_conpty=self.windows.open_conpty,
            ),
        )

    def run_windows(
        self,
        cmd: list[str],
        env: dict[str, str],
        **options: Any,
    ) -> int:
        options.pop("normalize_bare_cr_for_synthetic_enter", None)
        return self.windows.run_proxy(
            cmd,
            env,
            self.windows_services(),
            **options,
        )

    def dispatch_service(self) -> ChannelTerminalDispatchService:
        return ChannelTerminalDispatchService(
            settings=ChannelTerminalDispatchSettings(
                platform_name=self.dispatch_ports.platform_name,
                stdin_isatty=self.dispatch_ports.stdin_isatty,
                stdout_isatty=self.dispatch_ports.stdout_isatty,
            ),
            proxy=ChannelTerminalProxyPorts(
                windows_supported=self.dispatch_ports.windows_supported,
                run_windows=self.dispatch_ports.run_windows,
                run_posix=self.dispatch_ports.run_posix,
                posix_services=self.posix_services,
            ),
            direct=ChannelDirectProcessPorts(
                call=self.dispatch_ports.direct_call,
                popen=self.process.popen,
                write_record=self.process.write_record,
                terminate=self.process.terminate,
                release_record=self.process.release_record,
            ),
            log=self.policy.log,
        )

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
    ) -> int:
        if inject_channel_messages:
            self.dispatch_ports.prepare_delivery()
        return self.dispatch_service().dispatch(
            cmd,
            env,
            inject_channel_messages=inject_channel_messages,
            inject_web_chat_only=inject_web_chat_only,
            wake_for_llm_delivery=wake_for_llm_delivery,
            channel_wake_display_body=channel_wake_display_body,
            synthetic_enter_bytes=synthetic_enter_bytes,
            normalize_bare_cr_for_synthetic_enter=(
                normalize_bare_cr_for_synthetic_enter
            ),
            channel_wake_submit_retries=channel_wake_submit_retries,
            channel_wake_confirm_submit=channel_wake_confirm_submit,
            channel_wake_bracketed_paste=channel_wake_bracketed_paste,
            channel_wake_submit_delay_seconds=channel_wake_submit_delay_seconds,
            tracked_child_pid_path=tracked_child_pid_path,
        )

    def call_direct(
        self,
        cmd: list[str],
        env: dict[str, str],
        pid_path: Path | None = None,
    ) -> int:
        return self.dispatch_service().call_direct(cmd, env, pid_path)


@dataclass(frozen=True, slots=True)
class ChannelTerminalCompatibilityApi:
    context: Callable[[], ChannelTerminalContext]

    def process_services(self) -> ChannelTerminalProcess:
        return self.context().process_services()

    def policy_services(self) -> ChannelTerminalPolicy:
        return self.context().policy_services()

    def polling_services(self) -> ChannelTerminalPolling:
        return self.context().polling_services()

    def posix_services(self) -> ChannelTerminalServices:
        return self.context().posix_services()

    def windows_services(self) -> ChannelWindowsServices:
        return self.context().windows_services()

    def dispatch_service(self) -> ChannelTerminalDispatchService:
        return self.context().dispatch_service()

    def run_windows(
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
    ) -> int:
        return self.context().run_windows(
            cmd,
            env,
            inject_channel_messages=inject_channel_messages,
            inject_web_chat_only=inject_web_chat_only,
            wake_for_llm_delivery=wake_for_llm_delivery,
            channel_wake_display_body=channel_wake_display_body,
            synthetic_enter_bytes=synthetic_enter_bytes,
            normalize_bare_cr_for_synthetic_enter=(
                normalize_bare_cr_for_synthetic_enter
            ),
            channel_wake_submit_retries=channel_wake_submit_retries,
            channel_wake_confirm_submit=channel_wake_confirm_submit,
            channel_wake_bracketed_paste=channel_wake_bracketed_paste,
            channel_wake_submit_delay_seconds=channel_wake_submit_delay_seconds,
            tracked_child_pid_path=tracked_child_pid_path,
        )

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
    ) -> int:
        return self.context().dispatch(
            cmd,
            env,
            inject_channel_messages=inject_channel_messages,
            inject_web_chat_only=inject_web_chat_only,
            wake_for_llm_delivery=wake_for_llm_delivery,
            channel_wake_display_body=channel_wake_display_body,
            synthetic_enter_bytes=synthetic_enter_bytes,
            normalize_bare_cr_for_synthetic_enter=(
                normalize_bare_cr_for_synthetic_enter
            ),
            channel_wake_submit_retries=channel_wake_submit_retries,
            channel_wake_confirm_submit=channel_wake_confirm_submit,
            channel_wake_bracketed_paste=channel_wake_bracketed_paste,
            channel_wake_submit_delay_seconds=channel_wake_submit_delay_seconds,
            tracked_child_pid_path=tracked_child_pid_path,
        )

    def call_direct(
        self,
        cmd: list[str],
        env: dict[str, str],
        pid_path: Path | None = None,
    ) -> int:
        return self.context().call_direct(cmd, env, pid_path)


__all__ = [
    "ChannelTerminalCompatibilityApi",
    "ChannelTerminalContext",
    "ChannelTerminalDispatchPorts",
    "ChannelTerminalIoPorts",
    "ChannelTerminalPolicyPorts",
    "ChannelTerminalPollingPorts",
    "ChannelTerminalProcessPorts",
    "ChannelTerminalWindowsPorts",
]
