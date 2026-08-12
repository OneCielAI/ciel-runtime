"""Assemble Codex CLI and app-server launch services from shared typed ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import runtime_launch


Callback = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CodexLaunchSharedConfigPorts:
    apply_endpoint_policy: Callback
    current_alias: Callback
    current_launch_cwd_key: Callback
    ensure_model_cache: Callback
    get_current_provider: Callback
    load_config: Callback
    provider_mode_label: Callback
    record_launch_state: Callback
    model_catalog_args: Callback


@dataclass(frozen=True, slots=True)
class CodexLaunchSharedInstallationPorts:
    find_executable: Callback
    install_codex: Callback
    warn_multiple_installs: Callback
    disable_prompts: Callback
    has_passthrough_option: Callback
    install_prompts: Callback


@dataclass(frozen=True, slots=True)
class CodexLaunchSharedDispatchPorts:
    launch_agy: Callback
    launch_claude: Callback
    launch_codex: Callback
    launch_app_server: Callback
    materialize_command: Callback
    run_runtime_update: Callback
    run_codex_update: Callback
    run_prelaunch_menu: Callback
    log_passthrough_mapping: Callback


@dataclass(frozen=True, slots=True)
class CodexLaunchSharedRoutingPorts:
    cleanup_services: Callback
    routed_enabled: Callback
    direct_native_enabled: Callback
    readiness_errors: Callback
    native_enabled: Callback
    launch_enabled: Callback
    run_with_router_lifetime: Callback
    start_router: Callback


@dataclass(frozen=True, slots=True)
class CodexLaunchSharedChannelPorts:
    delivery_mode: Callback
    native_http_args: Callback
    select_resume_session: Callback


@dataclass(frozen=True, slots=True)
class CodexCliLaunchPorts:
    process: runtime_launch.CodexLaunchProcess
    policy: runtime_launch.CodexLaunchCliPolicy


@dataclass(frozen=True, slots=True)
class CodexAppServerLaunchPorts:
    process: runtime_launch.CodexAppServerProcess
    policy: runtime_launch.CodexAppServerCliPolicy


@dataclass(frozen=True, slots=True)
class CodexLaunchAssembly:
    config: CodexLaunchSharedConfigPorts
    installation: CodexLaunchSharedInstallationPorts
    dispatch: CodexLaunchSharedDispatchPorts
    routing: CodexLaunchSharedRoutingPorts
    channel: CodexLaunchSharedChannelPorts
    cli: CodexCliLaunchPorts
    app_server: CodexAppServerLaunchPorts

    def cli_services(self) -> runtime_launch.CodexLaunchServices:
        return runtime_launch.CodexLaunchServices(
            constants=runtime_launch.build_default_codex_launch_constants(),
            process=self.cli.process,
            cli_policy=self.cli.policy,
            config=runtime_launch.CodexLaunchConfig(
                apply_launch_endpoint_policy=self.config.apply_endpoint_policy,
                current_alias=self.config.current_alias,
                current_launch_cwd_key=self.config.current_launch_cwd_key,
                ensure_model_cache_for_launch=self.config.ensure_model_cache,
                get_current_provider=self.config.get_current_provider,
                load_config=self.config.load_config,
                provider_mode_label=self.config.provider_mode_label,
                record_launch_state_for_cwd=self.config.record_launch_state,
                codex_runtime_model_catalog_args=self.config.model_catalog_args,
            ),
            installation=runtime_launch.CodexLaunchInstallation(
                disable_ciel_runtime_codex_prompts_for_native=self.installation.disable_prompts,
                find_executable=self.installation.find_executable,
                has_passthrough_option=self.installation.has_passthrough_option,
                install_ciel_runtime_codex_prompts=self.installation.install_prompts,
                install_codex_if_missing=self.installation.install_codex,
                warn_if_multiple_ciel_runtime_installs=self.installation.warn_multiple_installs,
            ),
            dispatch=runtime_launch.CodexLaunchDispatch(
                launch_agy=self.dispatch.launch_agy,
                launch_claude=self.dispatch.launch_claude,
                launch_codex_app_server=self.dispatch.launch_app_server,
                log_codex_passthrough_mapping=self.dispatch.log_passthrough_mapping,
                materialize_runtime_command=self.dispatch.materialize_command,
                run_ciel_runtime_update_check=self.dispatch.run_runtime_update,
                run_codex_update_check=self.dispatch.run_codex_update,
                run_prelaunch_menu=self.dispatch.run_prelaunch_menu,
            ),
            routing=runtime_launch.CodexLaunchRouting(
                cleanup_managed_services_for_provider=self.routing.cleanup_services,
                codex_routed_enabled=self.routing.routed_enabled,
                direct_native_codex_enabled=self.routing.direct_native_enabled,
                launch_readiness_errors=self.routing.readiness_errors,
                native_codex_enabled=self.routing.native_enabled,
                run_with_router_lifetime=self.routing.run_with_router_lifetime,
                start_router_if_needed=self.routing.start_router,
            ),
            channel=runtime_launch.CodexLaunchChannel(
                channel_delivery_mode=self.channel.delivery_mode,
                codex_mcp_native_http_compat_args=self.channel.native_http_args,
                select_codex_resume_session=self.channel.select_resume_session,
            ),
        )

    def app_server_services(self) -> runtime_launch.CodexAppServerLaunchServices:
        return runtime_launch.CodexAppServerLaunchServices(
            constants=runtime_launch.build_default_codex_launch_constants(),
            process=self.app_server.process,
            config=runtime_launch.CodexAppServerConfig(
                apply_launch_endpoint_policy=self.config.apply_endpoint_policy,
                current_alias=self.config.current_alias,
                current_launch_cwd_key=self.config.current_launch_cwd_key,
                ensure_model_cache_for_launch=self.config.ensure_model_cache,
                get_current_provider=self.config.get_current_provider,
                load_config=self.config.load_config,
                provider_mode_label=self.config.provider_mode_label,
                record_launch_state_for_cwd=self.config.record_launch_state,
            ),
            cli_policy=self.app_server.policy,
            installation=runtime_launch.CodexAppServerInstallation(
                find_executable=self.installation.find_executable,
                install_codex_if_missing=self.installation.install_codex,
                warn_if_multiple_ciel_runtime_installs=self.installation.warn_multiple_installs,
            ),
            dispatch=runtime_launch.CodexAppServerDispatch(
                launch_agy=self.dispatch.launch_agy,
                launch_claude=self.dispatch.launch_claude,
                launch_codex=self.dispatch.launch_codex,
                run_ciel_runtime_update_check=self.dispatch.run_runtime_update,
                run_codex_update_check=self.dispatch.run_codex_update,
                run_prelaunch_menu=self.dispatch.run_prelaunch_menu,
            ),
            routing=runtime_launch.CodexAppServerRouting(
                cleanup_managed_services_for_provider=self.routing.cleanup_services,
                codex_launch_enabled_for_provider=self.routing.launch_enabled,
                codex_routed_enabled=self.routing.routed_enabled,
                direct_native_codex_enabled=self.routing.direct_native_enabled,
                launch_readiness_errors=self.routing.readiness_errors,
                native_codex_enabled=self.routing.native_enabled,
                run_with_router_lifetime=self.routing.run_with_router_lifetime,
                start_router_if_needed=self.routing.start_router,
            ),
            channel=runtime_launch.CodexAppServerChannel(
                channel_delivery_mode=self.channel.delivery_mode,
                codex_mcp_native_http_compat_args=self.channel.native_http_args,
            ),
        )


__all__ = [
    "CodexAppServerLaunchPorts",
    "CodexCliLaunchPorts",
    "CodexLaunchAssembly",
    "CodexLaunchSharedChannelPorts",
    "CodexLaunchSharedConfigPorts",
    "CodexLaunchSharedDispatchPorts",
    "CodexLaunchSharedInstallationPorts",
    "CodexLaunchSharedRoutingPorts",
]
