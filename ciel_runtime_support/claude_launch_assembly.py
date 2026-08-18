"""Assemble Claude Code launch services from bounded, typed dependency groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import runtime_launch


Callback = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeLaunchProcessPorts:
    log_command: Callback
    call_capturing_stderr: Callback
    env_bool: Callback
    env_vars: Callback
    file_size: Callback
    runtime_path: Callback
    print_exit_diagnostics: Callback
    call_with_channel_wake: Callback
    call_with_child_record: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchInstallationPorts:
    find_executable: Callback
    install_commands: Callback
    install_statusline: Callback
    install_claude: Callback
    install_tool_guard: Callback
    disable_commands: Callback
    readiness_errors: Callback
    warn_multiple_installs: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchDispatchPorts:
    launch_agy: Callback
    launch_codex: Callback
    launch_codex_app_server: Callback
    materialize_command: Callback
    run_runtime_update: Callback
    run_claude_update: Callback
    run_prelaunch_menu: Callback
    launch_enabled: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchConfigPorts:
    load_config: Callback
    save_config: Callback
    current_provider: Callback
    ensure_current_model: Callback
    ensure_model_cache: Callback
    apply_endpoint_policy: Callback
    provider_menu_label: Callback
    launch_mode_name: Callback
    current_launch_cwd_key: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchRoutingPorts:
    routed_enabled: Callback
    direct_native_enabled: Callback
    cleanup_services: Callback
    ensure_router_running: Callback
    reset_zai_mcp: Callback
    health_summary: Callback
    log: Callback
    start_router: Callback
    run_with_router_lifetime: Callback
    record_launch_state: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchPolicyPorts:
    append_runtime_settings: Callback
    supports_permission_mode: Callback
    has_noninteractive_args: Callback
    has_passthrough_option: Callback
    should_append_compat_prompt: Callback
    should_attach_web_search: Callback
    should_disallow_server_web_tools: Callback
    should_fork_native_session: Callback
    should_insert_option_boundary: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchDeliveryPorts:
    should_use_llm_delivery: Callback
    should_use_stdin_proxy: Callback
    wake_submit_delay_seconds: Callback
    wake_submit_retries: Callback
    set_transcript_scope: Callback


@dataclass(frozen=True, slots=True)
class ClaudeLaunchMcpConfigPorts:
    write_duckduckgo: Callback
    write_zai: Callback
    workspace: Any = None


@dataclass(frozen=True, slots=True)
class ClaudeLaunchAssembly:
    process: ClaudeLaunchProcessPorts
    installation: ClaudeLaunchInstallationPorts
    dispatch: ClaudeLaunchDispatchPorts
    config: ClaudeLaunchConfigPorts
    routing: ClaudeLaunchRoutingPorts
    policy: ClaudeLaunchPolicyPorts
    delivery: ClaudeLaunchDeliveryPorts
    mcp_config: ClaudeLaunchMcpConfigPorts

    def services(self) -> runtime_launch.ClaudeLaunchServices:
        return runtime_launch.ClaudeLaunchServices(
            constants=runtime_launch.build_default_claude_launch_constants(),
            process=runtime_launch.ClaudeLaunchProcess(
                _log_claude_command_for_diagnostics=self.process.log_command,
                _subprocess_call_capturing_stderr=self.process.call_capturing_stderr,
                env_bool=self.process.env_bool,
                env_vars=self.process.env_vars,
                file_size_or_zero=self.process.file_size,
                path_with_ciel_runtime_user_dirs=self.process.runtime_path,
                print_routed_claude_exit_diagnostics=self.process.print_exit_diagnostics,
                subprocess_call_with_channel_wake_proxy=self.process.call_with_channel_wake,
                subprocess_call_with_child_pid_record=self.process.call_with_child_record,
            ),
            installation=runtime_launch.ClaudeLaunchInstallation(
                find_executable=self.installation.find_executable,
                install_ciel_runtime_slash_commands=self.installation.install_commands,
                install_ciel_runtime_statusline=self.installation.install_statusline,
                install_claude_code_if_missing=self.installation.install_claude,
                install_tool_guard_hooks=self.installation.install_tool_guard,
                disable_ciel_runtime_slash_commands_for_native=self.installation.disable_commands,
                launch_readiness_errors=self.installation.readiness_errors,
                warn_if_multiple_ciel_runtime_installs=self.installation.warn_multiple_installs,
            ),
            dispatch=runtime_launch.ClaudeLaunchDispatch(
                launch_agy=self.dispatch.launch_agy,
                launch_codex=self.dispatch.launch_codex,
                launch_codex_app_server=self.dispatch.launch_codex_app_server,
                materialize_runtime_command=self.dispatch.materialize_command,
                run_ciel_runtime_update_check=self.dispatch.run_runtime_update,
                run_claude_update_check=self.dispatch.run_claude_update,
                run_prelaunch_menu=self.dispatch.run_prelaunch_menu,
                claude_launch_enabled_for_provider=self.dispatch.launch_enabled,
            ),
            config=runtime_launch.ClaudeLaunchConfig(
                load_config=self.config.load_config,
                save_config=self.config.save_config,
                get_current_provider=self.config.current_provider,
                ensure_current_model_from_provider_list=self.config.ensure_current_model,
                ensure_model_cache_for_launch=self.config.ensure_model_cache,
                apply_launch_endpoint_policy=self.config.apply_endpoint_policy,
                provider_menu_label=self.config.provider_menu_label,
                launch_mode_name=self.config.launch_mode_name,
                current_launch_cwd_key=self.config.current_launch_cwd_key,
            ),
            routing=runtime_launch.ClaudeLaunchRouting(
                anthropic_routed_enabled=self.routing.routed_enabled,
                direct_native_anthropic_enabled=self.routing.direct_native_enabled,
                cleanup_managed_services_for_provider=self.routing.cleanup_services,
                ensure_managed_router_running_for_client=self.routing.ensure_router_running,
                reset_zai_mcp_config_if_inactive=self.routing.reset_zai_mcp,
                router_health_summary=self.routing.health_summary,
                router_log=self.routing.log,
                start_router_if_needed=self.routing.start_router,
                run_with_router_lifetime=self.routing.run_with_router_lifetime,
                record_launch_state_for_cwd=self.routing.record_launch_state,
            ),
            policy=runtime_launch.ClaudeLaunchPolicy(
                append_claude_code_runtime_settings_args=self.policy.append_runtime_settings,
                claude_supports_permission_mode_arg=self.policy.supports_permission_mode,
                has_noninteractive_claude_args=self.policy.has_noninteractive_args,
                has_passthrough_option=self.policy.has_passthrough_option,
                should_append_compat_prompt=self.policy.should_append_compat_prompt,
                should_attach_web_search=self.policy.should_attach_web_search,
                should_disallow_claude_server_side_web_tools=self.policy.should_disallow_server_web_tools,
                should_fork_native_session_after_mode_switch=self.policy.should_fork_native_session,
                should_insert_passthrough_option_boundary=self.policy.should_insert_option_boundary,
            ),
            channel_delivery=runtime_launch.ClaudeLaunchChannelDelivery(
                should_use_channel_llm_delivery=self.delivery.should_use_llm_delivery,
                should_use_channel_stdin_proxy=self.delivery.should_use_stdin_proxy,
                channel_wake_submit_delay_seconds=self.delivery.wake_submit_delay_seconds,
                channel_wake_submit_retries=self.delivery.wake_submit_retries,
                set_channel_transcript_scope=self.delivery.set_transcript_scope,
            ),
            mcp_config=runtime_launch.ClaudeLaunchMcpConfig(
                write_duckduckgo_mcp_config=self.mcp_config.write_duckduckgo,
                write_zai_mcp_config=self.mcp_config.write_zai,
                workspace_mcp=self.mcp_config.workspace,
            ),
        )


__all__ = [
    "ClaudeLaunchAssembly",
    "ClaudeLaunchConfigPorts",
    "ClaudeLaunchDeliveryPorts",
    "ClaudeLaunchDispatchPorts",
    "ClaudeLaunchInstallationPorts",
    "ClaudeLaunchMcpConfigPorts",
    "ClaudeLaunchPolicyPorts",
    "ClaudeLaunchProcessPorts",
    "ClaudeLaunchRoutingPorts",
]
