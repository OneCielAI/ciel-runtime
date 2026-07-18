from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClaudeLaunchServices:
    CLAUDE_SERVER_SIDE_WEB_TOOLS: Any
    LOG_PATH: Any
    PRELAUNCH_CANCEL: Any
    PRELAUNCH_LAUNCH_AGY: Any
    PRELAUNCH_LAUNCH_CLAUDE: Any
    PRELAUNCH_LAUNCH_CODEX: Any
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER: Any
    ROUTED_COMPAT_PROMPT: Any
    _NATIVE_ROUTER_CHANNEL_NAMES: Any
    _log_claude_command_for_diagnostics: Callable[..., Any]
    _subprocess_call_capturing_stderr: Callable[..., Any]
    anthropic_routed_enabled: Callable[..., Any]
    append_claude_code_runtime_settings_args: Callable[..., Any]
    apply_launch_endpoint_policy: Callable[..., Any]
    auto_import_passthrough_channels: Callable[..., Any]
    auto_start_sse_channels_from_mcp_configs: Callable[..., Any]
    cached_channel_capable_server_names: Callable[..., Any]
    cached_channel_source_paths_for_specs: Callable[..., Any]
    channel_candidate_server_names_for_launch: Callable[..., Any]
    channel_specs_for_launch: Callable[..., Any]
    claude_channel_args: Callable[..., Any]
    claude_channels_requested: Callable[..., Any]
    claude_code_channels_auth_available: Callable[..., Any]
    claude_launch_enabled_for_provider: Callable[..., Any]
    claude_supports_permission_mode_arg: Callable[..., Any]
    cleanup_managed_services_for_provider: Callable[..., Any]
    current_launch_cwd_key: Callable[..., Any]
    direct_native_anthropic_enabled: Callable[..., Any]
    disable_ciel_runtime_slash_commands_for_native: Callable[..., Any]
    ensure_channel_probe_cache_for_launch: Callable[..., Any]
    ensure_current_model_from_provider_list: Callable[..., Any]
    ensure_managed_router_running_for_client: Callable[..., Any]
    ensure_model_cache_for_launch: Callable[..., Any]
    env_bool: Callable[..., Any]
    env_vars: Callable[..., Any]
    external_mcp_channel_server_names_from_configs: Callable[..., Any]
    file_size_or_zero: Callable[..., Any]
    find_executable: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    has_noninteractive_claude_args: Callable[..., Any]
    has_passthrough_option: Callable[..., Any]
    install_ciel_runtime_slash_commands: Callable[..., Any]
    install_ciel_runtime_statusline: Callable[..., Any]
    install_claude_code_if_missing: Callable[..., Any]
    install_tool_guard_hooks: Callable[..., Any]
    launch_agy: Callable[..., Any]
    launch_codex: Callable[..., Any]
    launch_codex_app_server: Callable[..., Any]
    launch_mode_name: Callable[..., Any]
    launch_readiness_errors: Callable[..., Any]
    load_config: Callable[..., Any]
    materialize_runtime_command: Callable[..., Any]
    native_channel_passthrough_requested: Callable[..., Any]
    normalize_channel_passthrough: Callable[..., Any]
    path_with_ciel_runtime_user_dirs: Callable[..., Any]
    prepare_channel_llm_delivery_for_launch: Callable[..., Any]
    print_routed_claude_exit_diagnostics: Callable[..., Any]
    provider_menu_label: Callable[..., Any]
    read_channel_probe_cache: Callable[..., Any]
    record_launch_state_for_cwd: Callable[..., Any]
    reset_zai_mcp_config_if_inactive: Callable[..., Any]
    router_health_summary: Callable[..., Any]
    router_log: Callable[..., Any]
    run_ciel_runtime_update_check: Callable[..., Any]
    run_claude_update_check: Callable[..., Any]
    run_prelaunch_menu: Callable[..., Any]
    run_with_router_lifetime: Callable[..., Any]
    save_config: Callable[..., Any]
    should_append_compat_prompt: Callable[..., Any]
    should_attach_web_search: Callable[..., Any]
    should_disallow_claude_server_side_web_tools: Callable[..., Any]
    should_fork_native_session_after_mode_switch: Callable[..., Any]
    should_insert_passthrough_option_boundary: Callable[..., Any]
    should_launch_process_start_channel_sse: Callable[..., Any]
    should_use_channel_llm_delivery: Callable[..., Any]
    should_use_channel_stdin_proxy: Callable[..., Any]
    should_use_native_channel_bridge: Callable[..., Any]
    start_router_if_needed: Callable[..., Any]
    strip_mcp_config_passthrough: Callable[..., Any]
    subprocess_call_with_channel_wake_proxy: Callable[..., Any]
    warn_if_multiple_ciel_runtime_installs: Callable[..., Any]
    write_channel_mcp_config: Callable[..., Any]
    write_duckduckgo_mcp_config: Callable[..., Any]
    write_mcp_proxy_config: Callable[..., Any]
    write_native_mcp_config_from_discovery: Callable[..., Any]
    write_zai_mcp_config: Callable[..., Any]


def run_claude(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    web_search_override: bool | None = None,
    update_check: bool = True,
    self_update_check: bool = True,
    *,
    services: ClaudeLaunchServices,
) -> int:
    CLAUDE_SERVER_SIDE_WEB_TOOLS = services.CLAUDE_SERVER_SIDE_WEB_TOOLS
    LOG_PATH = services.LOG_PATH
    PRELAUNCH_CANCEL = services.PRELAUNCH_CANCEL
    PRELAUNCH_LAUNCH_AGY = services.PRELAUNCH_LAUNCH_AGY
    PRELAUNCH_LAUNCH_CLAUDE = services.PRELAUNCH_LAUNCH_CLAUDE
    PRELAUNCH_LAUNCH_CODEX = services.PRELAUNCH_LAUNCH_CODEX
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER = services.PRELAUNCH_LAUNCH_CODEX_APP_SERVER
    ROUTED_COMPAT_PROMPT = services.ROUTED_COMPAT_PROMPT
    _NATIVE_ROUTER_CHANNEL_NAMES = services._NATIVE_ROUTER_CHANNEL_NAMES
    _log_claude_command_for_diagnostics = services._log_claude_command_for_diagnostics
    _subprocess_call_capturing_stderr = services._subprocess_call_capturing_stderr
    anthropic_routed_enabled = services.anthropic_routed_enabled
    append_claude_code_runtime_settings_args = services.append_claude_code_runtime_settings_args
    apply_launch_endpoint_policy = services.apply_launch_endpoint_policy
    auto_import_passthrough_channels = services.auto_import_passthrough_channels
    auto_start_sse_channels_from_mcp_configs = services.auto_start_sse_channels_from_mcp_configs
    cached_channel_capable_server_names = services.cached_channel_capable_server_names
    cached_channel_source_paths_for_specs = services.cached_channel_source_paths_for_specs
    channel_candidate_server_names_for_launch = services.channel_candidate_server_names_for_launch
    channel_specs_for_launch = services.channel_specs_for_launch
    claude_channel_args = services.claude_channel_args
    claude_channels_requested = services.claude_channels_requested
    claude_code_channels_auth_available = services.claude_code_channels_auth_available
    claude_launch_enabled_for_provider = services.claude_launch_enabled_for_provider
    claude_supports_permission_mode_arg = services.claude_supports_permission_mode_arg
    cleanup_managed_services_for_provider = services.cleanup_managed_services_for_provider
    current_launch_cwd_key = services.current_launch_cwd_key
    direct_native_anthropic_enabled = services.direct_native_anthropic_enabled
    disable_ciel_runtime_slash_commands_for_native = services.disable_ciel_runtime_slash_commands_for_native
    ensure_channel_probe_cache_for_launch = services.ensure_channel_probe_cache_for_launch
    ensure_current_model_from_provider_list = services.ensure_current_model_from_provider_list
    ensure_managed_router_running_for_client = services.ensure_managed_router_running_for_client
    ensure_model_cache_for_launch = services.ensure_model_cache_for_launch
    env_bool = services.env_bool
    env_vars = services.env_vars
    external_mcp_channel_server_names_from_configs = services.external_mcp_channel_server_names_from_configs
    file_size_or_zero = services.file_size_or_zero
    find_executable = services.find_executable
    get_current_provider = services.get_current_provider
    has_noninteractive_claude_args = services.has_noninteractive_claude_args
    has_passthrough_option = services.has_passthrough_option
    install_ciel_runtime_slash_commands = services.install_ciel_runtime_slash_commands
    install_ciel_runtime_statusline = services.install_ciel_runtime_statusline
    install_claude_code_if_missing = services.install_claude_code_if_missing
    install_tool_guard_hooks = services.install_tool_guard_hooks
    launch_agy = services.launch_agy
    launch_codex = services.launch_codex
    launch_codex_app_server = services.launch_codex_app_server
    launch_mode_name = services.launch_mode_name
    launch_readiness_errors = services.launch_readiness_errors
    load_config = services.load_config
    materialize_runtime_command = services.materialize_runtime_command
    native_channel_passthrough_requested = services.native_channel_passthrough_requested
    normalize_channel_passthrough = services.normalize_channel_passthrough
    path_with_ciel_runtime_user_dirs = services.path_with_ciel_runtime_user_dirs
    prepare_channel_llm_delivery_for_launch = services.prepare_channel_llm_delivery_for_launch
    print_routed_claude_exit_diagnostics = services.print_routed_claude_exit_diagnostics
    provider_menu_label = services.provider_menu_label
    read_channel_probe_cache = services.read_channel_probe_cache
    record_launch_state_for_cwd = services.record_launch_state_for_cwd
    reset_zai_mcp_config_if_inactive = services.reset_zai_mcp_config_if_inactive
    router_health_summary = services.router_health_summary
    router_log = services.router_log
    run_ciel_runtime_update_check = services.run_ciel_runtime_update_check
    run_claude_update_check = services.run_claude_update_check
    run_prelaunch_menu = services.run_prelaunch_menu
    run_with_router_lifetime = services.run_with_router_lifetime
    save_config = services.save_config
    should_append_compat_prompt = services.should_append_compat_prompt
    should_attach_web_search = services.should_attach_web_search
    should_disallow_claude_server_side_web_tools = services.should_disallow_claude_server_side_web_tools
    should_fork_native_session_after_mode_switch = services.should_fork_native_session_after_mode_switch
    should_insert_passthrough_option_boundary = services.should_insert_passthrough_option_boundary
    should_launch_process_start_channel_sse = services.should_launch_process_start_channel_sse
    should_use_channel_llm_delivery = services.should_use_channel_llm_delivery
    should_use_channel_stdin_proxy = services.should_use_channel_stdin_proxy
    should_use_native_channel_bridge = services.should_use_native_channel_bridge
    start_router_if_needed = services.start_router_if_needed
    strip_mcp_config_passthrough = services.strip_mcp_config_passthrough
    subprocess_call_with_channel_wake_proxy = services.subprocess_call_with_channel_wake_proxy
    warn_if_multiple_ciel_runtime_installs = services.warn_if_multiple_ciel_runtime_installs
    write_channel_mcp_config = services.write_channel_mcp_config
    write_duckduckgo_mcp_config = services.write_duckduckgo_mcp_config
    write_mcp_proxy_config = services.write_mcp_proxy_config
    write_native_mcp_config_from_discovery = services.write_native_mcp_config_from_discovery
    write_zai_mcp_config = services.write_zai_mcp_config
    if has_noninteractive_claude_args(passthrough):
        self_update_check = False
    warn_if_multiple_ciel_runtime_installs()
    run_ciel_runtime_update_check(enabled=self_update_check)
    auto_import_passthrough_channels(passthrough)
    rc = run_prelaunch_menu(passthrough, skip_menu=skip_menu, force_menu=force_menu)
    if rc == PRELAUNCH_LAUNCH_CODEX:
        return launch_codex(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_AGY:
        return launch_agy(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_CODEX_APP_SERVER:
        return launch_codex_app_server(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_CANCEL:
        return 0
    if rc not in (0, PRELAUNCH_LAUNCH_CLAUDE):
        return rc
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    for line in apply_launch_endpoint_policy(cfg, "claude"):
        print(line, flush=True)
    provider, pcfg = get_current_provider(cfg)
    if not claude_launch_enabled_for_provider(provider):
        print(f"Ciel Runtime launch blocked: Launch Claude Code is disabled while {provider_menu_label(provider, pcfg)} provider is selected.", flush=True)
        print("Use the matching Launch menu item or --ca-runtime agy|codex|codex-app-server.", flush=True)
        return 2
    blockers = launch_readiness_errors(cfg)
    if blockers:
        print("Ciel Runtime launch blocked:", flush=True)
        for line in blockers:
            print(f"- {line}", flush=True)
        return 2
    use_native_anthropic = direct_native_anthropic_enabled(provider, pcfg)
    use_router_mode = not use_native_anthropic
    launch_cwd_key = current_launch_cwd_key()
    fork_native_session, previous_launch_mode = should_fork_native_session_after_mode_switch(
        provider,
        pcfg,
        use_native_anthropic,
        passthrough,
        launch_cwd_key,
    )
    cleanup_managed_services_for_provider(provider, pcfg, cfg, quiet=True)
    env = os.environ.copy()
    env["PATH"] = path_with_ciel_runtime_user_dirs(env)
    launch_passthrough = normalize_channel_passthrough(passthrough)
    native_channel_bridge = should_use_native_channel_bridge(use_router_mode, cfg, launch_passthrough)
    stdin_channel_proxy = should_use_channel_stdin_proxy(use_router_mode, launch_passthrough, cfg)
    llm_channel_delivery = should_use_channel_llm_delivery(use_router_mode, launch_passthrough, cfg)
    native_auto_channel_specs: list[str] = []
    if use_native_anthropic and not native_channel_bridge and not native_channel_passthrough_requested(launch_passthrough):
        try:
            auto_channel_names = external_mcp_channel_server_names_from_configs(launch_passthrough)
            native_auto_channel_specs = [f"server:{name}" for name in auto_channel_names]
            if native_auto_channel_specs:
                router_log(
                    "INFO",
                    "channel_native_auto_specs servers=%s" % ",".join(auto_channel_names),
                )
        except Exception as exc:
            router_log("WARN", f"channel_native_auto_probe_failed error={type(exc).__name__}: {exc}")
    manage_router_lifetime = False
    if use_router_mode or llm_channel_delivery:
        manage_router_lifetime = bool(start_router_if_needed())
    if not use_native_anthropic:
        ensure_model_cache_for_launch(provider, pcfg)
        selected, selection_lines = ensure_current_model_from_provider_list(provider, pcfg)
        if selection_lines:
            for line in selection_lines:
                print(line)
            save_config(cfg)
        if not selected:
            raise RuntimeError(
                f"No concrete model is selected for provider {provider}; choose a model from the provider model list before launching Claude Code."
            )
    launch_env = env_vars(cfg)
    if claude_channels_requested(cfg, launch_passthrough) or native_channel_bridge or llm_channel_delivery or native_auto_channel_specs:
        env.pop("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", None)
        launch_env.pop("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", None)
    if use_native_anthropic:
        # Claude Native guarantee — strip every env var ciel-runtime (or a
        # prior ciel-runtime session) might have left behind that would change
        # Claude Code's default model selection, backend, advisor flow, or
        # other behavior. See env_vars() docstring for the contract.
        for key in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_CUSTOM_MODEL_OPTION",
            "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_EFFORT_LEVEL",
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE",
            "CLAUDE_CODE_ATTRIBUTION_HEADER",
            "CIEL_RUNTIME_ADVISOR_MODEL",
            "CIEL_RUNTIME_BYPASS_PERMISSIONS",
            "CIEL_RUNTIME_MODEL_ALIAS",
        ):
            env.pop(key, None)
            launch_env.pop(key, None)
        if "ANTHROPIC_API_KEY" in launch_env:
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
        router_log(
            "INFO",
            "claude_native_launch model=<defer-to-claude-code> advisor=off backend=<default-anthropic>",
        )
        disable_ciel_runtime_slash_commands_for_native()
    elif anthropic_routed_enabled(provider, pcfg):
        router_log(
            "INFO",
            "claude_anthropic_routed_launch backend=ciel-runtime-router upstream=anthropic",
        )
    env.update(launch_env)
    if not use_native_anthropic:
        preserve_anthropic_auth = anthropic_routed_enabled(provider, pcfg)
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if key not in launch_env and not preserve_anthropic_auth:
                env.pop(key, None)
        install_ciel_runtime_slash_commands(include_advisor=provider != "anthropic")
        install_tool_guard_hooks()
        install_ciel_runtime_statusline()
    claude = install_claude_code_if_missing()
    if not claude:
        raise RuntimeError(
            "claude executable was not found in PATH or the Ciel Runtime user bin directories, "
            "and automatic install of @anthropic-ai/claude-code did not make it available"
        )
    updated_claude = run_claude_update_check(claude, enabled=update_check)
    if isinstance(updated_claude, str) and updated_claude:
        claude = updated_claude
    claude = find_executable("claude") or claude
    if native_channel_bridge or native_auto_channel_specs:
        auth_ok, auth_reason = claude_code_channels_auth_available(claude)
        if not auth_ok:
            if native_channel_bridge:
                router_log("WARN", f"channel_native_unavailable_fallback reason={auth_reason} delivery=llm")
                native_channel_bridge = False
                llm_channel_delivery = True
            else:
                router_log("WARN", f"channel_native_auto_disabled reason={auth_reason}")
                native_auto_channel_specs = []
    extra_args: list[str] = []
    mcp_config_paths: list[str] = []
    reset_zai_mcp_config_if_inactive(provider)
    zai_mcp_config = write_zai_mcp_config(provider, pcfg)
    if zai_mcp_config:
        mcp_config_paths.append(str(zai_mcp_config))
    if should_attach_web_search(provider, cfg, web_search_override):
        mcp_config_paths.append(str(write_duckduckgo_mcp_config(cfg)))
    if llm_channel_delivery:
        mcp_config_paths.append(str(write_channel_mcp_config()))
    native_direct_mcp_config_paths: list[str] = []
    if use_native_anthropic:
        native_mcp_config = write_native_mcp_config_from_discovery(launch_passthrough)
        if native_mcp_config:
            native_direct_mcp_config_paths = [str(native_mcp_config)]
    detected_channel_specs: list[str] = []
    detected_channel_capable_names: list[str] = []
    channel_probe_source_paths: list[Path] = []
    if stdin_channel_proxy or llm_channel_delivery:
        try:
            candidate_channel_names = channel_candidate_server_names_for_launch(cfg, launch_passthrough)
            ensure_channel_probe_cache_for_launch(cfg, launch_passthrough)
            capable_names = cached_channel_capable_server_names()
            capable_name_set = set(capable_names)
            detected_channel_capable_names = [
                name for name in candidate_channel_names
                if name in capable_name_set and name.strip().lower() not in _NATIVE_ROUTER_CHANNEL_NAMES
            ]
            detected_channel_specs = [f"server:{name}" for name in detected_channel_capable_names]
            channel_launch_specs = channel_specs_for_launch(cfg, launch_passthrough, detected_channel_specs)
            channel_probe_source_paths = cached_channel_source_paths_for_specs(channel_launch_specs)
            if channel_probe_source_paths:
                mcp_config_paths.extend(str(path) for path in channel_probe_source_paths)
            cache_age = read_channel_probe_cache().get("probed_at") or 0
            router_log(
                "INFO",
                "channel_probe_loaded source=cache cache_age_ts=%d count=%d servers=%s sources=%s"
                % (
                    int(cache_age),
                    len(detected_channel_capable_names),
                    ",".join(detected_channel_capable_names) or "-",
                    ",".join(str(path) for path in channel_probe_source_paths) or "-",
                ),
            )
        except Exception as exc:
            router_log("WARN", f"channel_probe_cache_load_failed error={type(exc).__name__}: {exc}")
    claude_passthrough = list(launch_passthrough)
    if use_native_anthropic:
        if native_direct_mcp_config_paths:
            mcp_config_paths.extend(native_direct_mcp_config_paths)
            claude_passthrough = strip_mcp_config_passthrough(launch_passthrough)
    elif stdin_channel_proxy or llm_channel_delivery or native_auto_channel_specs:
        if llm_channel_delivery:
            prepare_channel_llm_delivery_for_launch()
        if should_launch_process_start_channel_sse(stdin_channel_proxy, native_channel_bridge, llm_channel_delivery):
            auto_start_sse_channels_from_mcp_configs(
                launch_passthrough,
                extra_config_paths=[Path(path) for path in mcp_config_paths],
            )
        else:
            router_log("INFO", "channel_sse_auto_start_skipped reason=router_managed_llm_delivery")
        # Channel-capable streamable-HTTP backends (e.g. ai-net-http) are forced
        # through ciel-runtime's own mcp-proxy so there is exactly ONE backend
        # connection: the proxy serves Claude Code's tool calls AND owns the
        # notification stream + idle-death wake handling. Because the proxy now
        # OWNS the stream, it must NOT also be in the disable set -- forcing a
        # server while disabling its stream would leave zero notification owners
        # and the agent would never wake. force and disable are mutually
        # exclusive per server, so the disable set excludes anything we force.
        forced_channel_names = (
            set(detected_channel_capable_names)
            if (stdin_channel_proxy or llm_channel_delivery)
            else set()
        )
        proxy_config = write_mcp_proxy_config(
            launch_passthrough,
            extra_config_paths=[Path(path) for path in mcp_config_paths],
            force_proxy_server_names=forced_channel_names or None,
            disable_proxy_notification_stream_names=None,
        )
        if proxy_config:
            mcp_config_paths = [str(proxy_config)]
            claude_passthrough = strip_mcp_config_passthrough(launch_passthrough)
    if mcp_config_paths:
        extra_args.extend(["--mcp-config", *mcp_config_paths])
    if should_append_compat_prompt(provider, pcfg, cfg) and not has_passthrough_option(launch_passthrough, "--system-prompt"):
        extra_args.extend(["--append-system-prompt", ROUTED_COMPAT_PROMPT])
    extra_args.extend(
        claude_channel_args(
            cfg,
            launch_passthrough,
            extra_specs=native_auto_channel_specs if native_auto_channel_specs else detected_channel_specs,
            native_channel_bridge=bool(native_channel_bridge or native_auto_channel_specs),
        )
    )
    append_claude_code_runtime_settings_args(extra_args, launch_passthrough, provider, pcfg)
    if fork_native_session:
        session_id = str(uuid.uuid4())
        extra_args.extend(["--session-id", session_id])
        router_log(
            "INFO",
            f"claude_native_session_boundary previous_mode={previous_launch_mode} cwd={launch_cwd_key} session_id={session_id}",
        )
    bypass_permission_mode = (
        not use_native_anthropic
        and not has_passthrough_option([*extra_args, *claude_passthrough], "--permission-mode")
        and claude_supports_permission_mode_arg(claude)
    )
    disallowed_tools = ""
    if (
        should_disallow_claude_server_side_web_tools(provider, pcfg, use_native_anthropic)
        and not has_passthrough_option([*extra_args, *claude_passthrough], "--disallowedTools", "--disallowed-tools")
    ):
        disallowed_tools = ",".join(CLAUDE_SERVER_SIDE_WEB_TOOLS)
    model = env.get("CIEL_RUNTIME_MODEL_ALIAS")
    cmd, env = materialize_runtime_command(
        "claude",
        claude,
        env,
        provider,
        pcfg,
        mode="native" if use_native_anthropic else "routed",
        protocol="anthropic_messages",
        cwd=Path.cwd(),
        enable_channels=bool(stdin_channel_proxy or native_channel_bridge or llm_channel_delivery),
        passthrough=claude_passthrough,
        options={
            "bypass_permission_mode": bypass_permission_mode,
            "disallowed_tools": disallowed_tools,
            "model": model or "",
            "extra_args": tuple(extra_args),
            "passthrough_boundary": should_insert_passthrough_option_boundary(extra_args, claude_passthrough),
        },
    )
    _log_claude_command_for_diagnostics(cmd, env)
    record_launch_state_for_cwd(
        launch_cwd_key,
        provider,
        launch_mode_name(provider, pcfg, use_native_anthropic),
        str(pcfg.get("current_model") or env.get("CIEL_RUNTIME_MODEL_ALIAS") or ""),
    )
    launch_log_offset = file_size_or_zero(LOG_PATH)
    capture_stderr = env_bool(os.environ.get("CIEL_RUNTIME_CAPTURE_CC_STDERR"), False)
    def run_claude_process() -> int:
        rc = 1
        try:
            if use_router_mode and not ensure_managed_router_running_for_client():
                print(
                    "Ciel Runtime warning: local router health check failed immediately before launching Claude Code.",
                    flush=True,
                )
                print(f"  {router_health_summary()}", flush=True)
            if stdin_channel_proxy:
                rc = subprocess_call_with_channel_wake_proxy(cmd, env, wake_for_llm_delivery=llm_channel_delivery)
            elif capture_stderr:
                rc = _subprocess_call_capturing_stderr(cmd, env)
            else:
                rc = subprocess.call(cmd, env=env)
            return rc
        finally:
            if use_router_mode:
                print_routed_claude_exit_diagnostics(rc, provider, pcfg, log_offset=launch_log_offset)

    return run_with_router_lifetime(run_claude_process, manage_router_lifetime)



@dataclass(frozen=True, slots=True)
class CodexLaunchServices:
    CODEX_RUNTIME_API_KEY_ENV: Any
    CONFIG_DIR: Any
    PRELAUNCH_CANCEL: Any
    PRELAUNCH_LAUNCH_AGY: Any
    PRELAUNCH_LAUNCH_CLAUDE: Any
    PRELAUNCH_LAUNCH_CODEX: Any
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER: Any
    _channel_wake_enter_env_is_fixed: Callable[..., Any]
    _codex_channel_wake_submit_delay_seconds: Callable[..., Any]
    _codex_channel_wake_submit_retries: Callable[..., Any]
    _log_codex_command_for_diagnostics: Callable[..., Any]
    _set_channel_transcript_scope: Callable[..., Any]
    apply_launch_endpoint_policy: Callable[..., Any]
    auto_import_passthrough_channels: Callable[..., Any]
    channel_delivery_mode: Callable[..., Any]
    cleanup_managed_services_for_provider: Callable[..., Any]
    codex_alternate_screen_compat_args: Callable[..., Any]
    codex_channel_capable_mcp_server_names: Callable[..., Any]
    codex_current_model_cli_args: Callable[..., Any]
    codex_help_requested: Callable[..., Any]
    codex_mcp_native_http_compat_args: Callable[..., Any]
    codex_mcp_split_proxy_enabled: Callable[..., Any]
    codex_native_routed_config_args: Callable[..., Any]
    codex_passthrough_args_for_launch: Callable[..., Any]
    codex_passthrough_has_command: Callable[..., Any]
    codex_process_record_path: Callable[..., Any]
    codex_resume_picker_requested: Callable[..., Any]
    codex_resume_with_session_id: Callable[..., Any]
    codex_routed_enabled: Callable[..., Any]
    codex_runtime_config_args: Callable[..., Any]
    codex_runtime_model_catalog_args: Callable[..., Any]
    codex_yolo_launch_args: Callable[..., Any]
    current_alias: Callable[..., Any]
    current_launch_cwd_key: Callable[..., Any]
    direct_native_codex_enabled: Callable[..., Any]
    disable_ciel_runtime_codex_prompts_for_native: Callable[..., Any]
    ensure_model_cache_for_launch: Callable[..., Any]
    find_executable: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    has_passthrough_option: Callable[..., Any]
    install_ciel_runtime_codex_prompts: Callable[..., Any]
    install_codex_if_missing: Callable[..., Any]
    launch_agy: Callable[..., Any]
    launch_claude: Callable[..., Any]
    launch_codex_app_server: Callable[..., Any]
    launch_readiness_errors: Callable[..., Any]
    load_config: Callable[..., Any]
    log_codex_passthrough_mapping: Callable[..., Any]
    materialize_runtime_command: Callable[..., Any]
    native_codex_enabled: Callable[..., Any]
    path_with_ciel_runtime_user_dirs: Callable[..., Any]
    provider_mode_label: Callable[..., Any]
    record_launch_state_for_cwd: Callable[..., Any]
    run_ciel_runtime_update_check: Callable[..., Any]
    run_codex_update_check: Callable[..., Any]
    run_prelaunch_menu: Callable[..., Any]
    run_with_router_lifetime: Callable[..., Any]
    select_codex_resume_session: Callable[..., Any]
    start_codex_mcp_channel_sse_for_launch: Callable[..., Any]
    start_router_if_needed: Callable[..., Any]
    subprocess_call_with_channel_wake_proxy: Callable[..., Any]
    terminate_existing_codex_processes_for_launch: Callable[..., Any]
    terminate_existing_router_clients_for_launch: Callable[..., Any]
    warn_if_multiple_ciel_runtime_installs: Callable[..., Any]
    write_codex_mcp_config_for_channel_discovery: Callable[..., Any]


def run_codex(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
    *,
    services: CodexLaunchServices,
) -> int:
    CODEX_RUNTIME_API_KEY_ENV = services.CODEX_RUNTIME_API_KEY_ENV
    CONFIG_DIR = services.CONFIG_DIR
    PRELAUNCH_CANCEL = services.PRELAUNCH_CANCEL
    PRELAUNCH_LAUNCH_AGY = services.PRELAUNCH_LAUNCH_AGY
    PRELAUNCH_LAUNCH_CLAUDE = services.PRELAUNCH_LAUNCH_CLAUDE
    PRELAUNCH_LAUNCH_CODEX = services.PRELAUNCH_LAUNCH_CODEX
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER = services.PRELAUNCH_LAUNCH_CODEX_APP_SERVER
    _channel_wake_enter_env_is_fixed = services._channel_wake_enter_env_is_fixed
    _codex_channel_wake_submit_delay_seconds = services._codex_channel_wake_submit_delay_seconds
    _codex_channel_wake_submit_retries = services._codex_channel_wake_submit_retries
    _log_codex_command_for_diagnostics = services._log_codex_command_for_diagnostics
    _set_channel_transcript_scope = services._set_channel_transcript_scope
    apply_launch_endpoint_policy = services.apply_launch_endpoint_policy
    auto_import_passthrough_channels = services.auto_import_passthrough_channels
    channel_delivery_mode = services.channel_delivery_mode
    cleanup_managed_services_for_provider = services.cleanup_managed_services_for_provider
    codex_alternate_screen_compat_args = services.codex_alternate_screen_compat_args
    codex_channel_capable_mcp_server_names = services.codex_channel_capable_mcp_server_names
    codex_current_model_cli_args = services.codex_current_model_cli_args
    codex_help_requested = services.codex_help_requested
    codex_mcp_native_http_compat_args = services.codex_mcp_native_http_compat_args
    codex_mcp_split_proxy_enabled = services.codex_mcp_split_proxy_enabled
    codex_native_routed_config_args = services.codex_native_routed_config_args
    codex_passthrough_args_for_launch = services.codex_passthrough_args_for_launch
    codex_passthrough_has_command = services.codex_passthrough_has_command
    codex_process_record_path = services.codex_process_record_path
    codex_resume_picker_requested = services.codex_resume_picker_requested
    codex_resume_with_session_id = services.codex_resume_with_session_id
    codex_routed_enabled = services.codex_routed_enabled
    codex_runtime_config_args = services.codex_runtime_config_args
    codex_runtime_model_catalog_args = services.codex_runtime_model_catalog_args
    codex_yolo_launch_args = services.codex_yolo_launch_args
    current_alias = services.current_alias
    current_launch_cwd_key = services.current_launch_cwd_key
    direct_native_codex_enabled = services.direct_native_codex_enabled
    disable_ciel_runtime_codex_prompts_for_native = services.disable_ciel_runtime_codex_prompts_for_native
    ensure_model_cache_for_launch = services.ensure_model_cache_for_launch
    find_executable = services.find_executable
    get_current_provider = services.get_current_provider
    has_passthrough_option = services.has_passthrough_option
    install_ciel_runtime_codex_prompts = services.install_ciel_runtime_codex_prompts
    install_codex_if_missing = services.install_codex_if_missing
    launch_agy = services.launch_agy
    launch_claude = services.launch_claude
    launch_codex_app_server = services.launch_codex_app_server
    launch_readiness_errors = services.launch_readiness_errors
    load_config = services.load_config
    log_codex_passthrough_mapping = services.log_codex_passthrough_mapping
    materialize_runtime_command = services.materialize_runtime_command
    native_codex_enabled = services.native_codex_enabled
    path_with_ciel_runtime_user_dirs = services.path_with_ciel_runtime_user_dirs
    provider_mode_label = services.provider_mode_label
    record_launch_state_for_cwd = services.record_launch_state_for_cwd
    run_ciel_runtime_update_check = services.run_ciel_runtime_update_check
    run_codex_update_check = services.run_codex_update_check
    run_prelaunch_menu = services.run_prelaunch_menu
    run_with_router_lifetime = services.run_with_router_lifetime
    select_codex_resume_session = services.select_codex_resume_session
    start_codex_mcp_channel_sse_for_launch = services.start_codex_mcp_channel_sse_for_launch
    start_router_if_needed = services.start_router_if_needed
    subprocess_call_with_channel_wake_proxy = services.subprocess_call_with_channel_wake_proxy
    terminate_existing_codex_processes_for_launch = services.terminate_existing_codex_processes_for_launch
    terminate_existing_router_clients_for_launch = services.terminate_existing_router_clients_for_launch
    warn_if_multiple_ciel_runtime_installs = services.warn_if_multiple_ciel_runtime_installs
    write_codex_mcp_config_for_channel_discovery = services.write_codex_mcp_config_for_channel_discovery
    warn_if_multiple_ciel_runtime_installs()
    run_ciel_runtime_update_check(enabled=self_update_check)
    env = os.environ.copy()
    env["PATH"] = path_with_ciel_runtime_user_dirs(env)
    codex = install_codex_if_missing()
    if not codex:
        raise RuntimeError(
            "codex executable was not found in PATH or the Ciel Runtime user bin directories, "
            "and automatic install of @openai/codex did not make it available"
        )
    updated_codex = run_codex_update_check(codex, enabled=update_check)
    if isinstance(updated_codex, str) and updated_codex:
        codex = updated_codex
    codex = find_executable("codex") or codex
    codex_passthrough, codex_passthrough_notes = codex_passthrough_args_for_launch(passthrough)
    if codex_help_requested(codex_passthrough):
        log_codex_passthrough_mapping(codex_passthrough_notes)
        return subprocess.call([codex, *codex_passthrough], env=env)
    if codex_passthrough_has_command(codex_passthrough):
        skip_menu = True
    auto_import_passthrough_channels(passthrough)
    rc = run_prelaunch_menu(passthrough, skip_menu=skip_menu, force_menu=force_menu)
    if rc == PRELAUNCH_LAUNCH_CLAUDE:
        return launch_claude(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_AGY:
        return launch_agy(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_CODEX_APP_SERVER:
        return launch_codex_app_server(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_CANCEL:
        return 0
    if rc not in (0, PRELAUNCH_LAUNCH_CODEX):
        return rc
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    for line in apply_launch_endpoint_policy(cfg, "codex"):
        print(line, flush=True)
    provider, pcfg = get_current_provider(cfg)
    blockers = launch_readiness_errors(cfg)
    if blockers:
        print("Ciel Runtime Codex launch blocked:", flush=True)
        for line in blockers:
            print(f"- {line}", flush=True)
        return 2
    cleanup_managed_services_for_provider(provider, pcfg, cfg, quiet=True)
    use_native_codex = direct_native_codex_enabled(provider, pcfg)
    use_codex_routed = codex_routed_enabled(provider, pcfg)
    mapped_continue = any(note.startswith("--continue -> resume --last") for note in codex_passthrough_notes)
    if not use_native_codex and mapped_continue:
        try:
            resume_index = codex_passthrough.index("resume")
        except ValueError:
            resume_index = -1
        if resume_index >= 0 and resume_index + 1 < len(codex_passthrough):
            if codex_passthrough[resume_index + 1] == "--last":
                del codex_passthrough[resume_index + 1]
                codex_passthrough_notes.append("routed --continue -> provider-independent session picker")
    if not use_native_codex and codex_resume_picker_requested(codex_passthrough):
        session_id = select_codex_resume_session(
            env,
            include_non_interactive="--include-non-interactive" in codex_passthrough,
            passthrough=codex_passthrough,
        )
        if session_id == "":
            return 0
        if session_id:
            codex_passthrough = codex_resume_with_session_id(codex_passthrough, session_id)
            codex_passthrough_notes.append("resume picker -> selected local Codex session")
    launch_cwd = Path.cwd()
    if use_native_codex:
        disable_ciel_runtime_codex_prompts_for_native(env)
    else:
        install_ciel_runtime_codex_prompts(env)
    codex_mcp_config = write_codex_mcp_config_for_channel_discovery(codex_passthrough, env=env)
    env["CIEL_RUNTIME_CODEX_MANAGED"] = "1"
    env["CIEL_RUNTIME_CONFIG_DIR"] = str(CONFIG_DIR)
    env["CIEL_RUNTIME_LAUNCH_CWD"] = str(launch_cwd)
    terminate_existing_codex_processes_for_launch("codex_prelaunch_processes", cwd=launch_cwd, quiet=True)
    if not use_native_codex:
        terminate_existing_router_clients_for_launch("codex_prelaunch_active_clients", quiet=True)
    manage_router_lifetime = False if use_native_codex else bool(start_router_if_needed())
    if not native_codex_enabled(provider):
        ensure_model_cache_for_launch(provider, pcfg)
    codex_channel_owned_names = (
        codex_channel_capable_mcp_server_names(cfg, codex_mcp_config)
        if not use_native_codex and channel_delivery_mode(cfg) == "llm"
        else []
    )
    codex_mcp_compat_args = codex_mcp_native_http_compat_args(
        codex_mcp_config,
        split_http_proxy=(not use_native_codex and codex_mcp_split_proxy_enabled()),
        channel_owned_server_names=codex_channel_owned_names,
    )
    codex_yolo_args = codex_yolo_launch_args(codex_passthrough)
    if not use_native_codex and not use_codex_routed:
        env[CODEX_RUNTIME_API_KEY_ENV] = env.get(CODEX_RUNTIME_API_KEY_ENV) or "ciel-runtime-router-local-key"
    log_codex_passthrough_mapping(codex_passthrough_notes)
    model_alias_args: list[str] = []
    if not native_codex_enabled(provider) and not has_passthrough_option(codex_passthrough, "-m", "--model"):
        model = current_alias(cfg)
        if model:
            model_alias_args.extend(["-m", model])
    codex_mode = "native" if use_native_codex else ("routed" if use_codex_routed else "router")
    cmd, env = materialize_runtime_command(
        "codex",
        codex,
        env,
        provider,
        pcfg,
        mode=codex_mode,
        protocol="openai_responses",
        cwd=launch_cwd,
        enable_channels=bool(codex_channel_owned_names),
        passthrough=codex_passthrough,
        options={
            "yolo_args": tuple(codex_yolo_args),
            "model_args": tuple(codex_current_model_cli_args(pcfg, codex_passthrough)),
            "routed_config_args": tuple(codex_native_routed_config_args()),
            "router_config_args": tuple(codex_runtime_config_args()),
            "model_catalog_args": tuple(codex_runtime_model_catalog_args(codex, cfg)),
            "alternate_screen_args": tuple(codex_alternate_screen_compat_args(codex_passthrough, env=env)),
            "mcp_args": tuple(codex_mcp_compat_args),
            "model_alias_args": tuple(model_alias_args),
        },
    )
    _log_codex_command_for_diagnostics(cmd, env)
    record_launch_state_for_cwd(
        current_launch_cwd_key(),
        provider,
        provider_mode_label(provider, pcfg) if native_codex_enabled(provider) else "codex-router",
        str(pcfg.get("current_model") or ("" if native_codex_enabled(provider) else current_alias(cfg)) or ""),
    )

    def run_codex_process() -> int:
        _set_channel_transcript_scope(
            "codex",
            codex_home=Path(env.get("CODEX_HOME") or (Path.home() / ".codex")),
        )
        if not use_native_codex:
            start_codex_mcp_channel_sse_for_launch(cfg, codex_mcp_config, allowed_server_names=codex_channel_owned_names)
        codex_synthetic_enter = None if _channel_wake_enter_env_is_fixed() else b"\r"
        return subprocess_call_with_channel_wake_proxy(
            cmd,
            env,
            wake_for_llm_delivery=channel_delivery_mode(cfg) == "llm",
            synthetic_enter_bytes=codex_synthetic_enter,
            normalize_bare_cr_for_synthetic_enter=False,
            channel_wake_submit_retries=_codex_channel_wake_submit_retries(),
            channel_wake_confirm_submit=True,
            channel_wake_bracketed_paste=True,
            channel_wake_submit_delay_seconds=_codex_channel_wake_submit_delay_seconds(),
            tracked_child_pid_path=codex_process_record_path("client"),
        )

    return run_with_router_lifetime(run_codex_process, manage_router_lifetime)



@dataclass(frozen=True, slots=True)
class CodexAppServerLaunchServices:
    CODEX_RUNTIME_API_KEY_ENV: Any
    CONFIG_DIR: Any
    PRELAUNCH_CANCEL: Any
    PRELAUNCH_LAUNCH_AGY: Any
    PRELAUNCH_LAUNCH_CLAUDE: Any
    PRELAUNCH_LAUNCH_CODEX: Any
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER: Any
    _log_codex_app_server_command_for_diagnostics: Callable[..., Any]
    apply_launch_endpoint_policy: Callable[..., Any]
    auto_import_passthrough_channels: Callable[..., Any]
    channel_delivery_mode: Callable[..., Any]
    cleanup_managed_services_for_provider: Callable[..., Any]
    codex_app_server_default_listen_url: Callable[..., Any]
    codex_app_server_launch_args: Callable[..., Any]
    codex_channel_capable_mcp_server_names: Callable[..., Any]
    codex_current_model_config_args: Callable[..., Any]
    codex_launch_enabled_for_provider: Callable[..., Any]
    codex_mcp_native_http_compat_args: Callable[..., Any]
    codex_mcp_split_proxy_enabled: Callable[..., Any]
    codex_native_routed_config_args: Callable[..., Any]
    codex_passthrough_has_model_override: Callable[..., Any]
    codex_process_record_path: Callable[..., Any]
    codex_routed_enabled: Callable[..., Any]
    codex_runtime_config_args: Callable[..., Any]
    current_alias: Callable[..., Any]
    current_launch_cwd_key: Callable[..., Any]
    direct_native_codex_enabled: Callable[..., Any]
    ensure_model_cache_for_launch: Callable[..., Any]
    find_executable: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    install_codex_if_missing: Callable[..., Any]
    launch_agy: Callable[..., Any]
    launch_claude: Callable[..., Any]
    launch_codex: Callable[..., Any]
    launch_readiness_errors: Callable[..., Any]
    load_config: Callable[..., Any]
    native_codex_enabled: Callable[..., Any]
    path_with_ciel_runtime_user_dirs: Callable[..., Any]
    provider_mode_label: Callable[..., Any]
    record_launch_state_for_cwd: Callable[..., Any]
    run_ciel_runtime_update_check: Callable[..., Any]
    run_codex_update_check: Callable[..., Any]
    run_prelaunch_menu: Callable[..., Any]
    run_with_router_lifetime: Callable[..., Any]
    start_codex_mcp_channel_sse_for_launch: Callable[..., Any]
    start_router_if_needed: Callable[..., Any]
    subprocess_call_with_child_pid_record: Callable[..., Any]
    terminate_existing_codex_processes_for_launch: Callable[..., Any]
    terminate_existing_router_clients_for_launch: Callable[..., Any]
    toml_string: Callable[..., Any]
    warn_if_multiple_ciel_runtime_installs: Callable[..., Any]
    write_codex_mcp_config_for_channel_discovery: Callable[..., Any]


def run_codex_app_server(
    passthrough: list[str],
    skip_menu: bool = True,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
    *,
    services: CodexAppServerLaunchServices,
) -> int:
    CODEX_RUNTIME_API_KEY_ENV = services.CODEX_RUNTIME_API_KEY_ENV
    CONFIG_DIR = services.CONFIG_DIR
    PRELAUNCH_CANCEL = services.PRELAUNCH_CANCEL
    PRELAUNCH_LAUNCH_AGY = services.PRELAUNCH_LAUNCH_AGY
    PRELAUNCH_LAUNCH_CLAUDE = services.PRELAUNCH_LAUNCH_CLAUDE
    PRELAUNCH_LAUNCH_CODEX = services.PRELAUNCH_LAUNCH_CODEX
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER = services.PRELAUNCH_LAUNCH_CODEX_APP_SERVER
    _log_codex_app_server_command_for_diagnostics = services._log_codex_app_server_command_for_diagnostics
    apply_launch_endpoint_policy = services.apply_launch_endpoint_policy
    auto_import_passthrough_channels = services.auto_import_passthrough_channels
    channel_delivery_mode = services.channel_delivery_mode
    cleanup_managed_services_for_provider = services.cleanup_managed_services_for_provider
    codex_app_server_default_listen_url = services.codex_app_server_default_listen_url
    codex_app_server_launch_args = services.codex_app_server_launch_args
    codex_channel_capable_mcp_server_names = services.codex_channel_capable_mcp_server_names
    codex_current_model_config_args = services.codex_current_model_config_args
    codex_launch_enabled_for_provider = services.codex_launch_enabled_for_provider
    codex_mcp_native_http_compat_args = services.codex_mcp_native_http_compat_args
    codex_mcp_split_proxy_enabled = services.codex_mcp_split_proxy_enabled
    codex_native_routed_config_args = services.codex_native_routed_config_args
    codex_passthrough_has_model_override = services.codex_passthrough_has_model_override
    codex_process_record_path = services.codex_process_record_path
    codex_routed_enabled = services.codex_routed_enabled
    codex_runtime_config_args = services.codex_runtime_config_args
    current_alias = services.current_alias
    current_launch_cwd_key = services.current_launch_cwd_key
    direct_native_codex_enabled = services.direct_native_codex_enabled
    ensure_model_cache_for_launch = services.ensure_model_cache_for_launch
    find_executable = services.find_executable
    get_current_provider = services.get_current_provider
    install_codex_if_missing = services.install_codex_if_missing
    launch_agy = services.launch_agy
    launch_claude = services.launch_claude
    launch_codex = services.launch_codex
    launch_readiness_errors = services.launch_readiness_errors
    load_config = services.load_config
    native_codex_enabled = services.native_codex_enabled
    path_with_ciel_runtime_user_dirs = services.path_with_ciel_runtime_user_dirs
    provider_mode_label = services.provider_mode_label
    record_launch_state_for_cwd = services.record_launch_state_for_cwd
    run_ciel_runtime_update_check = services.run_ciel_runtime_update_check
    run_codex_update_check = services.run_codex_update_check
    run_prelaunch_menu = services.run_prelaunch_menu
    run_with_router_lifetime = services.run_with_router_lifetime
    start_codex_mcp_channel_sse_for_launch = services.start_codex_mcp_channel_sse_for_launch
    start_router_if_needed = services.start_router_if_needed
    subprocess_call_with_child_pid_record = services.subprocess_call_with_child_pid_record
    terminate_existing_codex_processes_for_launch = services.terminate_existing_codex_processes_for_launch
    terminate_existing_router_clients_for_launch = services.terminate_existing_router_clients_for_launch
    toml_string = services.toml_string
    warn_if_multiple_ciel_runtime_installs = services.warn_if_multiple_ciel_runtime_installs
    write_codex_mcp_config_for_channel_discovery = services.write_codex_mcp_config_for_channel_discovery
    warn_if_multiple_ciel_runtime_installs()
    run_ciel_runtime_update_check(enabled=self_update_check)
    env = os.environ.copy()
    env["PATH"] = path_with_ciel_runtime_user_dirs(env)
    codex = install_codex_if_missing()
    if not codex:
        raise RuntimeError(
            "codex executable was not found in PATH or the Ciel Runtime user bin directories, "
            "and automatic install of @openai/codex did not make it available"
        )
    updated_codex = run_codex_update_check(codex, enabled=update_check)
    if isinstance(updated_codex, str) and updated_codex:
        codex = updated_codex
    codex = find_executable("codex") or codex
    auto_import_passthrough_channels(passthrough)
    rc = run_prelaunch_menu(passthrough, skip_menu=skip_menu, force_menu=force_menu)
    if rc == PRELAUNCH_LAUNCH_CLAUDE:
        return launch_claude(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_CODEX:
        return launch_codex(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_AGY:
        return launch_agy(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_CANCEL:
        return 0
    if rc not in (0, PRELAUNCH_LAUNCH_CODEX_APP_SERVER):
        return rc
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    for line in apply_launch_endpoint_policy(cfg, "codex-app-server"):
        print(line, flush=True)
    provider, pcfg = get_current_provider(cfg)
    codex_mcp_config = write_codex_mcp_config_for_channel_discovery(passthrough, env=env)
    if not codex_launch_enabled_for_provider(provider):
        print("Ciel Runtime Codex App Server launch blocked:", flush=True)
        print("- Select Codex or Codex routed as the provider before launching Codex App Server.", flush=True)
        return 2
    blockers = launch_readiness_errors(cfg)
    if blockers:
        print("Ciel Runtime Codex App Server launch blocked:", flush=True)
        for line in blockers:
            print(f"- {line}", flush=True)
        return 2
    cleanup_managed_services_for_provider(provider, pcfg, cfg, quiet=True)
    use_native_codex = direct_native_codex_enabled(provider, pcfg)
    use_codex_routed = codex_routed_enabled(provider, pcfg)
    launch_cwd = Path.cwd()
    env["CIEL_RUNTIME_CODEX_MANAGED"] = "1"
    env["CIEL_RUNTIME_CONFIG_DIR"] = str(CONFIG_DIR)
    env["CIEL_RUNTIME_LAUNCH_CWD"] = str(launch_cwd)
    terminate_existing_codex_processes_for_launch("codex_app_server_prelaunch_processes", cwd=launch_cwd, quiet=True)
    if not use_native_codex:
        terminate_existing_router_clients_for_launch("codex_app_server_prelaunch_active_clients", quiet=True)
    manage_router_lifetime = False if use_native_codex else bool(start_router_if_needed())
    if use_codex_routed:
        config_args = codex_native_routed_config_args()
    elif use_native_codex:
        config_args = []
    else:
        env[CODEX_RUNTIME_API_KEY_ENV] = env.get(CODEX_RUNTIME_API_KEY_ENV) or "ciel-runtime-router-local-key"
        config_args = codex_runtime_config_args()
    if native_codex_enabled(provider):
        config_args = [*config_args, *codex_current_model_config_args(pcfg, passthrough)]
    if not native_codex_enabled(provider):
        ensure_model_cache_for_launch(provider, pcfg)
        model = current_alias(cfg)
        if model and not codex_passthrough_has_model_override(passthrough):
            config_args = [*config_args, "-c", f"model={toml_string(model)}"]
    codex_channel_owned_names = (
        codex_channel_capable_mcp_server_names(cfg, codex_mcp_config)
        if not use_native_codex and channel_delivery_mode(cfg) == "llm"
        else []
    )
    codex_mcp_compat_args = codex_mcp_native_http_compat_args(
        codex_mcp_config,
        split_http_proxy=(not use_native_codex and codex_mcp_split_proxy_enabled()),
        channel_owned_server_names=codex_channel_owned_names,
    )
    config_args = [*config_args, *codex_mcp_compat_args]
    listen_url = codex_app_server_default_listen_url()
    app_server_args = codex_app_server_launch_args(
        passthrough,
        config_args=config_args,
        default_listen_url=listen_url,
    )
    cmd = [codex, *app_server_args]
    print("Launching Codex App Server through Ciel Runtime.", flush=True)
    if "--listen" in cmd:
        try:
            print(f"Codex App Server listen: {cmd[cmd.index('--listen') + 1]}", flush=True)
        except Exception:
            pass
    _log_codex_app_server_command_for_diagnostics(cmd, env)
    record_launch_state_for_cwd(
        current_launch_cwd_key(),
        provider,
        provider_mode_label(provider, pcfg) if native_codex_enabled(provider) else "codex-app-server-router",
        str(pcfg.get("current_model") or ("" if native_codex_enabled(provider) else current_alias(cfg)) or ""),
    )

    split_proxy_enabled = bool(not use_native_codex and (codex_mcp_split_proxy_enabled() or codex_channel_owned_names))

    def run_codex_app_server_process() -> int:
        if split_proxy_enabled:
            start_codex_mcp_channel_sse_for_launch(cfg, codex_mcp_config, allowed_server_names=codex_channel_owned_names)
        return subprocess_call_with_child_pid_record(cmd, env, codex_process_record_path("app-server"))

    return run_with_router_lifetime(run_codex_app_server_process, manage_router_lifetime)



@dataclass(frozen=True, slots=True)
class AgyLaunchServices:
    PRELAUNCH_CANCEL: Any
    PRELAUNCH_LAUNCH_AGY: Any
    PRELAUNCH_LAUNCH_CLAUDE: Any
    PRELAUNCH_LAUNCH_CODEX: Any
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER: Any
    _codex_channel_wake_submit_delay_seconds: Callable[..., Any]
    _codex_channel_wake_submit_retries: Callable[..., Any]
    _log_agy_command_for_diagnostics: Callable[..., Any]
    agy_dangerous_launch_args: Callable[..., Any]
    agy_help_requested: Callable[..., Any]
    agy_passthrough_args_for_launch: Callable[..., Any]
    agy_passthrough_has_command: Callable[..., Any]
    agy_routed_enabled: Callable[..., Any]
    auto_import_passthrough_channels: Callable[..., Any]
    channel_delivery_mode: Callable[..., Any]
    cleanup_managed_services_for_provider: Callable[..., Any]
    current_launch_cwd_key: Callable[..., Any]
    find_executable: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    install_agy_if_missing: Callable[..., Any]
    launch_claude: Callable[..., Any]
    launch_codex: Callable[..., Any]
    launch_codex_app_server: Callable[..., Any]
    launch_readiness_errors: Callable[..., Any]
    load_config: Callable[..., Any]
    log_agy_passthrough_mapping: Callable[..., Any]
    materialize_runtime_command: Callable[..., Any]
    native_agy_enabled: Callable[..., Any]
    path_with_ciel_runtime_user_dirs: Callable[..., Any]
    provider_mode_label: Callable[..., Any]
    record_launch_state_for_cwd: Callable[..., Any]
    run_agy_update_check: Callable[..., Any]
    run_ciel_runtime_update_check: Callable[..., Any]
    run_prelaunch_menu: Callable[..., Any]
    run_with_router_lifetime: Callable[..., Any]
    start_router_if_needed: Callable[..., Any]
    subprocess_call_with_channel_wake_proxy: Callable[..., Any]
    warn_if_multiple_ciel_runtime_installs: Callable[..., Any]


def run_agy(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
    *,
    services: AgyLaunchServices,
) -> int:
    PRELAUNCH_CANCEL = services.PRELAUNCH_CANCEL
    PRELAUNCH_LAUNCH_AGY = services.PRELAUNCH_LAUNCH_AGY
    PRELAUNCH_LAUNCH_CLAUDE = services.PRELAUNCH_LAUNCH_CLAUDE
    PRELAUNCH_LAUNCH_CODEX = services.PRELAUNCH_LAUNCH_CODEX
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER = services.PRELAUNCH_LAUNCH_CODEX_APP_SERVER
    _codex_channel_wake_submit_delay_seconds = services._codex_channel_wake_submit_delay_seconds
    _codex_channel_wake_submit_retries = services._codex_channel_wake_submit_retries
    _log_agy_command_for_diagnostics = services._log_agy_command_for_diagnostics
    agy_dangerous_launch_args = services.agy_dangerous_launch_args
    agy_help_requested = services.agy_help_requested
    agy_passthrough_args_for_launch = services.agy_passthrough_args_for_launch
    agy_passthrough_has_command = services.agy_passthrough_has_command
    agy_routed_enabled = services.agy_routed_enabled
    auto_import_passthrough_channels = services.auto_import_passthrough_channels
    channel_delivery_mode = services.channel_delivery_mode
    cleanup_managed_services_for_provider = services.cleanup_managed_services_for_provider
    current_launch_cwd_key = services.current_launch_cwd_key
    find_executable = services.find_executable
    get_current_provider = services.get_current_provider
    install_agy_if_missing = services.install_agy_if_missing
    launch_claude = services.launch_claude
    launch_codex = services.launch_codex
    launch_codex_app_server = services.launch_codex_app_server
    launch_readiness_errors = services.launch_readiness_errors
    load_config = services.load_config
    log_agy_passthrough_mapping = services.log_agy_passthrough_mapping
    materialize_runtime_command = services.materialize_runtime_command
    native_agy_enabled = services.native_agy_enabled
    path_with_ciel_runtime_user_dirs = services.path_with_ciel_runtime_user_dirs
    provider_mode_label = services.provider_mode_label
    record_launch_state_for_cwd = services.record_launch_state_for_cwd
    run_agy_update_check = services.run_agy_update_check
    run_ciel_runtime_update_check = services.run_ciel_runtime_update_check
    run_prelaunch_menu = services.run_prelaunch_menu
    run_with_router_lifetime = services.run_with_router_lifetime
    start_router_if_needed = services.start_router_if_needed
    subprocess_call_with_channel_wake_proxy = services.subprocess_call_with_channel_wake_proxy
    warn_if_multiple_ciel_runtime_installs = services.warn_if_multiple_ciel_runtime_installs
    warn_if_multiple_ciel_runtime_installs()
    run_ciel_runtime_update_check(enabled=self_update_check)
    env = os.environ.copy()
    env["PATH"] = path_with_ciel_runtime_user_dirs(env)
    agy = install_agy_if_missing()
    if not agy:
        raise RuntimeError(
            "agy executable was not found in PATH or the Ciel Runtime/AGY user bin directories, "
            "and automatic install from Google's official AGY manifest did not make it available"
        )
    updated_agy = run_agy_update_check(agy, enabled=update_check)
    if isinstance(updated_agy, str) and updated_agy:
        agy = updated_agy
    agy = find_executable("agy") or agy
    agy_passthrough, agy_passthrough_notes = agy_passthrough_args_for_launch(passthrough)
    if agy_help_requested(agy_passthrough):
        log_agy_passthrough_mapping(agy_passthrough_notes)
        return subprocess.call([agy, *agy_passthrough], env=env)
    if agy_passthrough_has_command(agy_passthrough):
        skip_menu = True
    auto_import_passthrough_channels(passthrough)
    rc = run_prelaunch_menu(passthrough, skip_menu=skip_menu, force_menu=force_menu)
    if rc == PRELAUNCH_LAUNCH_CLAUDE:
        return launch_claude(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_CODEX:
        return launch_codex(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_LAUNCH_CODEX_APP_SERVER:
        return launch_codex_app_server(
            passthrough,
            skip_menu=True,
            force_menu=False,
            update_check=update_check,
            self_update_check=False,
        )
    if rc == PRELAUNCH_CANCEL:
        return 0
    if rc not in (0, PRELAUNCH_LAUNCH_AGY):
        return rc
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    if not native_agy_enabled(provider):
        print("Ciel Runtime AGY launch blocked:", flush=True)
        print("- Select AGY or AGY Routed as the provider before launching AGY.", flush=True)
        return 2
    blockers = launch_readiness_errors(cfg)
    if blockers:
        print("Ciel Runtime AGY launch blocked:", flush=True)
        for line in blockers:
            print(f"- {line}", flush=True)
        return 2
    cleanup_managed_services_for_provider(provider, pcfg, cfg, quiet=True)
    use_agy_routed = agy_routed_enabled(provider, pcfg)
    manage_router_lifetime = bool(start_router_if_needed()) if use_agy_routed and channel_delivery_mode(cfg) == "llm" else False
    agy_dangerous_args = agy_dangerous_launch_args(agy_passthrough)
    cmd, env = materialize_runtime_command(
        "agy",
        agy,
        env,
        provider,
        pcfg,
        mode="routed" if use_agy_routed else "native",
        protocol="anthropic_messages",
        cwd=Path.cwd(),
        enable_channels=use_agy_routed,
        passthrough=agy_passthrough,
        options={"dangerous_args": tuple(agy_dangerous_args)},
    )
    log_agy_passthrough_mapping(agy_passthrough_notes)
    _log_agy_command_for_diagnostics(cmd, env)
    record_launch_state_for_cwd(
        current_launch_cwd_key(),
        provider,
        provider_mode_label(provider, pcfg),
        str(pcfg.get("current_model") or ""),
    )

    def run_agy_process() -> int:
        if use_agy_routed:
            return subprocess_call_with_channel_wake_proxy(
                cmd,
                env,
                wake_for_llm_delivery=channel_delivery_mode(cfg) == "llm",
                synthetic_enter_bytes=None,
                normalize_bare_cr_for_synthetic_enter=False,
                channel_wake_submit_retries=_codex_channel_wake_submit_retries(),
                channel_wake_confirm_submit=True,
                channel_wake_bracketed_paste=True,
                channel_wake_submit_delay_seconds=_codex_channel_wake_submit_delay_seconds(),
            )
        return subprocess.call(cmd, env=env)

    return run_with_router_lifetime(run_agy_process, manage_router_lifetime)



__all__ = ['run_claude','ClaudeLaunchServices','run_codex','CodexLaunchServices','run_codex_app_server','CodexAppServerLaunchServices','run_agy','AgyLaunchServices']
