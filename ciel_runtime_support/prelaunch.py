from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class PrelaunchConstants:
    LANGUAGES: Any
    LLM_OPTION_TOGGLE_KEYS: Any
    MAIN_MENU_ACTIONS: Any
    PRELAUNCH_CANCEL: Any
    PRELAUNCH_LAUNCH_AGY: Any
    PRELAUNCH_LAUNCH_CLAUDE: Any
    PRELAUNCH_LAUNCH_CODEX: Any
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER: Any
    PROVIDER_LABELS: Any


@dataclass(frozen=True, slots=True)
class PrelaunchChannelQuery:
    _channel_panel_first_selectable: Callable[..., Any]
    _channel_panel_step: Callable[..., Any]
    channel_delivery_panel_rows: Callable[..., Any]
    channel_panel_rows: Callable[..., Any]
    channel_panel_rows_for_menu: Callable[..., Any]
    channel_probe_summary_message: Callable[..., Any]
    channel_specs: Callable[..., Any]
    refresh_channel_probe_cache: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchChannelCommands:
    add_channel_spec: Callable[..., Any]
    clear_channel_specs: Callable[..., Any]
    remove_channel_spec: Callable[..., Any]
    set_channel_delivery_config: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchLaunchPolicy:
    agy_launch_enabled_for_provider: Callable[..., Any]
    claude_launch_enabled_for_provider: Callable[..., Any]
    codex_launch_enabled_for_provider: Callable[..., Any]
    launch_blockers_require_api_key: Callable[..., Any]
    launch_readiness_errors: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchConfig:
    clear_model_cache: Callable[..., Any]
    current_provider_panel_choice: Callable[..., Any]
    default_base_url: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    load_config: Callable[..., Any]
    preflight_lines: Callable[..., Any]
    provider_menu_label: Callable[..., Any]
    save_config: Callable[..., Any]
    settings_ready_except_api_key: Callable[..., Any]
    read_model_list_cache: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchTerminal:
    default_prelaunch_action: Callable[..., Any]
    enable_ansi: Callable[..., Any]
    main_menu_rows: Callable[..., Any]
    prelaunch_action_index: Callable[..., Any]
    prompt_menu_multiline_value: Callable[..., Any]
    prompt_menu_value: Callable[..., Any]
    read_clipboard_text: Callable[..., Any]
    read_menu_key: Callable[..., Any]
    render_prelaunch_screen: Callable[..., Any]
    self_cmd: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchPanelRows:
    advisor_model_panel_rows: Callable[..., Any]
    api_key_panel_rows: Callable[..., Any]
    base_url_panel_rows: Callable[..., Any]
    context_setup_panel_rows: Callable[..., Any]
    language_panel_rows: Callable[..., Any]
    llm_option_panel_rows: Callable[..., Any]
    llm_preset_panel_rows: Callable[..., Any]
    log_level_panel_rows: Callable[..., Any]
    model_panel_rows: Callable[..., Any]
    provider_panel_rows: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchSecrets:
    clear_api_key_config: Callable[..., Any]
    mask_secret: Callable[..., Any]
    parse_api_key_list: Callable[..., Any]
    secret_fingerprint: Callable[..., Any]
    store_api_key_input_config: Callable[..., Any]
    store_api_keys_config: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchMutations:
    apply_context_setup_config: Callable[..., Any]
    apply_llm_preset_config: Callable[..., Any]
    apply_timeout_profile_to_provider: Callable[..., Any]
    set_advisor_model_config: Callable[..., Any]
    set_base_url_config: Callable[..., Any]
    set_llm_option_config: Callable[..., Any]
    set_log_level_config: Callable[..., Any]
    set_model_config: Callable[..., Any]
    set_provider_choice_config: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchOptions:
    llm_option_current_bool: Callable[..., Any]
    llm_option_prompt_default: Callable[..., Any]
    timeout_profile_panel_rows: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchServices:
    constants: PrelaunchConstants
    terminal: PrelaunchTerminal
    config: PrelaunchConfig
    launch_policy: PrelaunchLaunchPolicy
    panel_rows: PrelaunchPanelRows
    channel_query: PrelaunchChannelQuery
    channel_commands: PrelaunchChannelCommands
    mutations: PrelaunchMutations
    secrets: PrelaunchSecrets
    options: PrelaunchOptions


def run_prelaunch_menu(passthrough: list[str] | None = None,
    *,
    services: PrelaunchServices,
) -> int:
    LANGUAGES = services.constants.LANGUAGES
    LLM_OPTION_TOGGLE_KEYS = services.constants.LLM_OPTION_TOGGLE_KEYS
    MAIN_MENU_ACTIONS = services.constants.MAIN_MENU_ACTIONS
    PRELAUNCH_CANCEL = services.constants.PRELAUNCH_CANCEL
    PRELAUNCH_LAUNCH_AGY = services.constants.PRELAUNCH_LAUNCH_AGY
    PRELAUNCH_LAUNCH_CLAUDE = services.constants.PRELAUNCH_LAUNCH_CLAUDE
    PRELAUNCH_LAUNCH_CODEX = services.constants.PRELAUNCH_LAUNCH_CODEX
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER = services.constants.PRELAUNCH_LAUNCH_CODEX_APP_SERVER
    PROVIDER_LABELS = services.constants.PROVIDER_LABELS
    _channel_panel_first_selectable = services.channel_query._channel_panel_first_selectable
    _channel_panel_step = services.channel_query._channel_panel_step
    add_channel_spec = services.channel_commands.add_channel_spec
    advisor_model_panel_rows = services.panel_rows.advisor_model_panel_rows
    agy_launch_enabled_for_provider = services.launch_policy.agy_launch_enabled_for_provider
    api_key_panel_rows = services.panel_rows.api_key_panel_rows
    apply_context_setup_config = services.mutations.apply_context_setup_config
    apply_llm_preset_config = services.mutations.apply_llm_preset_config
    apply_timeout_profile_to_provider = services.mutations.apply_timeout_profile_to_provider
    base_url_panel_rows = services.panel_rows.base_url_panel_rows
    channel_delivery_panel_rows = services.channel_query.channel_delivery_panel_rows
    channel_panel_rows = services.channel_query.channel_panel_rows
    channel_panel_rows_for_menu = services.channel_query.channel_panel_rows_for_menu
    channel_probe_summary_message = services.channel_query.channel_probe_summary_message
    channel_specs = services.channel_query.channel_specs
    claude_launch_enabled_for_provider = services.launch_policy.claude_launch_enabled_for_provider
    clear_api_key_config = services.secrets.clear_api_key_config
    clear_channel_specs = services.channel_commands.clear_channel_specs
    clear_model_cache = services.config.clear_model_cache
    codex_launch_enabled_for_provider = services.launch_policy.codex_launch_enabled_for_provider
    context_setup_panel_rows = services.panel_rows.context_setup_panel_rows
    current_provider_panel_choice = services.config.current_provider_panel_choice
    default_base_url = services.config.default_base_url
    default_prelaunch_action = services.terminal.default_prelaunch_action
    enable_ansi = services.terminal.enable_ansi
    get_current_provider = services.config.get_current_provider
    language_panel_rows = services.panel_rows.language_panel_rows
    launch_blockers_require_api_key = services.launch_policy.launch_blockers_require_api_key
    launch_readiness_errors = services.launch_policy.launch_readiness_errors
    llm_option_current_bool = services.options.llm_option_current_bool
    llm_option_panel_rows = services.panel_rows.llm_option_panel_rows
    llm_option_prompt_default = services.options.llm_option_prompt_default
    llm_preset_panel_rows = services.panel_rows.llm_preset_panel_rows
    load_config = services.config.load_config
    log_level_panel_rows = services.panel_rows.log_level_panel_rows
    main_menu_rows = services.terminal.main_menu_rows
    mask_secret = services.secrets.mask_secret
    model_panel_rows = services.panel_rows.model_panel_rows
    parse_api_key_list = services.secrets.parse_api_key_list
    preflight_lines = services.config.preflight_lines
    prelaunch_action_index = services.terminal.prelaunch_action_index
    prompt_menu_multiline_value = services.terminal.prompt_menu_multiline_value
    prompt_menu_value = services.terminal.prompt_menu_value
    provider_menu_label = services.config.provider_menu_label
    provider_panel_rows = services.panel_rows.provider_panel_rows
    read_clipboard_text = services.terminal.read_clipboard_text
    read_menu_key = services.terminal.read_menu_key
    read_model_list_cache = services.config.read_model_list_cache
    refresh_channel_probe_cache = services.channel_query.refresh_channel_probe_cache
    remove_channel_spec = services.channel_commands.remove_channel_spec
    render_prelaunch_screen = services.terminal.render_prelaunch_screen
    save_config = services.config.save_config
    secret_fingerprint = services.secrets.secret_fingerprint
    self_cmd = services.terminal.self_cmd
    set_advisor_model_config = services.mutations.set_advisor_model_config
    set_base_url_config = services.mutations.set_base_url_config
    set_channel_delivery_config = services.channel_commands.set_channel_delivery_config
    set_llm_option_config = services.mutations.set_llm_option_config
    set_log_level_config = services.mutations.set_log_level_config
    set_model_config = services.mutations.set_model_config
    set_provider_choice_config = services.mutations.set_provider_choice_config
    settings_ready_except_api_key = services.config.settings_ready_except_api_key
    store_api_key_input_config = services.secrets.store_api_key_input_config
    store_api_keys_config = services.secrets.store_api_keys_config
    timeout_profile_panel_rows = services.options.timeout_profile_panel_rows
    passthrough = list(passthrough or [])
    enable_ansi()
    cfg = load_config()
    provider, _pcfg = get_current_provider(cfg)
    main_idx = prelaunch_action_index(default_prelaunch_action(provider)) if settings_ready_except_api_key() else 0
    panel: str | None = None
    panel_idx = 0
    panel_rows: list[str] = []
    panel_values: list[str] = []
    panel_last_idx: dict[str, int] = {}
    checks = preflight_lines()
    messages: list[str] = []
    first_render = True

    def open_panel(name: str) -> None:
        nonlocal panel, panel_idx, panel_rows, panel_values, messages, first_render
        cfg = load_config()
        provider, pcfg = get_current_provider(cfg)
        panel = name
        panel_idx = panel_last_idx.get(name, 0)
        if name == "language":
            panel_rows, panel_values = language_panel_rows(cfg)
            panel_idx = panel_values.index(cfg.get("language", "en"))
        elif name == "provider":
            panel_rows, panel_values = provider_panel_rows(cfg)
            current_choice = current_provider_panel_choice(provider, pcfg)
            panel_idx = panel_values.index(current_choice) if current_choice in panel_values else 0
        elif name == "api-key":
            panel_rows, panel_values = api_key_panel_rows(provider, pcfg)
        elif name == "base-url":
            panel_rows, panel_values = base_url_panel_rows(provider, pcfg)
        elif name == "model":
            try:
                panel_rows, panel_values = model_panel_rows(
                    provider,
                    pcfg,
                    fetch=provider == "anthropic" and read_model_list_cache(provider, pcfg) is None,
                )
            except Exception as exc:
                panel_rows, panel_values = [f"Model list failed: {type(exc).__name__}: {exc}", "+ Custom model id..."], []
        elif name == "advisor-model":
            try:
                panel_rows, panel_values = advisor_model_panel_rows(
                    provider,
                    pcfg,
                    fetch=provider == "anthropic" and read_model_list_cache(provider, pcfg) is None,
                )
            except Exception as exc:
                panel_rows, panel_values = [f"Advisor model list failed: {type(exc).__name__}: {exc}", "+ Custom advisor model id..."], []
        elif name == "test":
            panel_rows, panel_values = ["Run compatibility test", "Back"], ["run", "back"]
        elif name == "options":
            panel_rows, panel_values = llm_option_panel_rows(provider, pcfg, cfg.get("language", "en"))
        elif name == "channel-delivery":
            panel_rows, panel_values = channel_delivery_panel_rows(cfg)
        elif name == "log-level":
            panel_rows, panel_values = log_level_panel_rows(cfg)
        elif name == "channels":
            panel_rows, panel_values, probe_messages = channel_panel_rows_for_menu(cfg, passthrough)
            if probe_messages:
                messages = probe_messages
            if panel_values:
                panel_idx = _channel_panel_first_selectable(panel_values)
        elif name == "context":
            panel_rows, panel_values = context_setup_panel_rows(provider, pcfg, cfg.get("language", "en"))
        elif name == "preset":
            panel_rows, panel_values = llm_preset_panel_rows(provider, pcfg, cfg.get("language", "en"))
        elif name == "timeout":
            panel_rows, panel_values = timeout_profile_panel_rows(pcfg, cfg.get("language", "en"))
        if panel_rows:
            panel_idx = max(0, min(panel_idx, len(panel_rows) - 1))

    def close_panel(next_idx: int | None = None) -> None:
        nonlocal panel, panel_idx, panel_rows, panel_values, main_idx
        if panel:
            panel_last_idx[panel] = panel_idx
        panel = None
        panel_idx = 0
        panel_rows = []
        panel_values = []
        if next_idx is not None:
            main_idx = next_idx

    def refresh_checks() -> None:
        nonlocal checks
        checks = preflight_lines()

    fd = sys.stdin.fileno()
    old_settings = None
    if os.name != "nt" and os.isatty(fd):
        try:
            import termios
            old_settings = termios.tcgetattr(fd)
            new = termios.tcgetattr(fd)
            new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
            new[6][termios.VMIN] = 1
            new[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new)
        except Exception:
            fd = -1
    if sys.stdout.isatty():
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
    def restore_line_mode() -> None:
        if old_settings is not None and fd >= 0:
            try:
                import termios
                termios.tcsetattr(fd, termios.TCSANOW, old_settings)
            except Exception:
                pass

    def restore_raw_mode() -> None:
        if old_settings is not None and fd >= 0:
            try:
                import termios
                new = termios.tcgetattr(fd)
                new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
                new[6][termios.VMIN] = 1
                new[6][termios.VTIME] = 0
                termios.tcsetattr(fd, termios.TCSANOW, new)
            except Exception:
                pass

    try:
        while True:
            first_render = render_prelaunch_screen(main_idx, panel, panel_idx, panel_rows, checks, messages, first_render)
            key = read_menu_key(fd) if fd >= 0 else read_menu_key()
            if panel:
                panel_name = panel
                if key in ("up", "k"):
                    if panel == "channels":
                        panel_idx = _channel_panel_step(panel_values, panel_idx, -1)
                    else:
                        panel_idx = (panel_idx - 1) % max(1, len(panel_rows))
                    panel_last_idx[panel_name] = panel_idx
                    continue
                if key in ("down", "j"):
                    if panel == "channels":
                        panel_idx = _channel_panel_step(panel_values, panel_idx, 1)
                    else:
                        panel_idx = (panel_idx + 1) % max(1, len(panel_rows))
                    panel_last_idx[panel_name] = panel_idx
                    continue
                if key in ("esc", "left", "q"):
                    close_panel()
                    continue
                if key != "enter":
                    continue
                cfg = load_config()
                provider, pcfg = get_current_provider(cfg)
                value = panel_values[panel_idx] if panel_idx < len(panel_values) else ""
                if panel == "language" and value:
                    cfg["language"] = value
                    save_config(cfg)
                    messages = [f"Language set to {value} ({LANGUAGES[value]})."]
                    refresh_checks()
                    close_panel(1)
                elif panel == "provider" and value:
                    messages = set_provider_choice_config(value)
                    refresh_checks()
                    cfg = load_config()
                    provider, _pcfg = get_current_provider(cfg)
                    if provider == "codex":
                        close_panel(prelaunch_action_index(default_prelaunch_action(provider)))
                    else:
                        main_idx = 4
                        open_panel("model")
                elif panel == "model":
                    if value == "back":
                        close_panel()
                        continue
                    if value == "__refresh_models__":
                        panel_rows, panel_values = ["Refreshing provider model list..."], []
                        first_render = render_prelaunch_screen(main_idx, panel, 0, panel_rows, checks, messages, first_render)
                        try:
                            panel_rows, panel_values = model_panel_rows(provider, pcfg, fetch=True, force_refresh=True)
                            messages = [f"Model list refreshed: {max(0, len(panel_values) - 3)} model(s)."]
                        except Exception as exc:
                            messages = [f"Model list refresh failed: {type(exc).__name__}: {exc}"]
                            panel_rows, panel_values = model_panel_rows(provider, pcfg, fetch=False)
                        panel_idx = 0
                        panel_last_idx["model"] = 0
                        refresh_checks()
                        continue
                    if value == "__custom__" or panel_idx >= len(panel_values):
                        model_value = prompt_menu_value("Model id or alias", restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                    else:
                        model_value = value
                    if model_value:
                        messages = set_model_config(model_value)
                        refresh_checks()
                    close_panel(5)
                elif panel == "advisor-model":
                    if value == "back":
                        close_panel()
                        continue
                    if value == "__refresh_models__":
                        panel_rows, panel_values = ["Refreshing provider model list..."], []
                        first_render = render_prelaunch_screen(main_idx, panel, 0, panel_rows, checks, messages, first_render)
                        try:
                            panel_rows, panel_values = advisor_model_panel_rows(provider, pcfg, fetch=True, force_refresh=True)
                            messages = [f"Model list refreshed: {max(0, len(panel_values) - 4)} advisor model(s)."]
                        except Exception as exc:
                            messages = [f"Model list refresh failed: {type(exc).__name__}: {exc}"]
                            panel_rows, panel_values = advisor_model_panel_rows(provider, pcfg, fetch=False)
                        panel_idx = 0
                        panel_last_idx["advisor-model"] = 0
                        refresh_checks()
                        continue
                    if value == "__custom__" or panel_idx >= len(panel_values):
                        advisor_value = prompt_menu_value("Advisor model id", "deepseek-v4-pro", restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                    else:
                        advisor_value = value
                    messages = set_advisor_model_config(advisor_value)
                    refresh_checks()
                    close_panel(6)
                elif panel == "api-key":
                    if value == "back":
                        close_panel()
                    elif value == "input":
                        key_value = prompt_menu_value(f"API key for {provider}", secret=True, restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        if key_value:
                            messages = store_api_key_input_config(provider, key_value)
                            refresh_checks()
                        close_panel(3)
                    elif value == "multi-input":
                        key_value = prompt_menu_multiline_value(
                            f"API keys for {provider} (comma/newline separated)",
                            restore_tty=restore_line_mode,
                            raw_tty=restore_raw_mode,
                        )
                        if key_value:
                            messages = store_api_keys_config(provider, parse_api_key_list(key_value))
                            refresh_checks()
                        close_panel(3)
                    elif value == "env":
                        default_env = {
                            "anthropic": "ANTHROPIC_API_KEY",
                            "deepseek": "DEEPSEEK_API_KEY",
                            "opencode": "OPENCODE_API_KEY",
                            "opencode-go": "OPENCODE_API_KEY",
                            "kimi": "KIMI_API_KEY",
                            "nvidia-hosted": "NVIDIA_API_KEY",
                            "ollama-cloud": "OLLAMA_API_KEY",
                            "openrouter": "OPENROUTER_API_KEY",
                            "fireworks": "FIREWORKS_API_KEY",
                        }.get(provider, "API_KEY")
                        env_name = prompt_menu_value("Environment variable name", default_env, restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        key_value = os.environ.get(env_name, "").strip()
                        if key_value:
                            messages = store_api_key_input_config(provider, key_value)
                        else:
                            messages = [f"Environment variable {env_name} is empty or not set."]
                        refresh_checks()
                        close_panel(3)
                    elif value == "multi-env":
                        default_env = {
                            "anthropic": "ANTHROPIC_API_KEYS",
                            "deepseek": "DEEPSEEK_API_KEYS",
                            "opencode": "OPENCODE_API_KEYS",
                            "opencode-go": "OPENCODE_API_KEYS",
                            "kimi": "KIMI_API_KEYS",
                            "nvidia-hosted": "NVIDIA_API_KEYS",
                            "ollama-cloud": "OLLAMA_API_KEYS",
                            "openrouter": "OPENROUTER_API_KEYS",
                            "fireworks": "FIREWORKS_API_KEYS",
                        }.get(provider, "API_KEYS")
                        env_name = prompt_menu_value("Environment variable name", default_env, restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        key_value = os.environ.get(env_name, "").strip()
                        if key_value:
                            messages = store_api_keys_config(provider, parse_api_key_list(key_value))
                        else:
                            messages = [f"Environment variable {env_name} is empty or not set."]
                        refresh_checks()
                        close_panel(3)
                    elif value == "clipboard":
                        key_value = read_clipboard_text()
                        if not key_value:
                            messages = ["Clipboard did not contain readable text."]
                        else:
                            confirm = prompt_menu_value(f"Clipboard contains {mask_secret(key_value)}. Store it? y/N", restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                            if confirm.lower().startswith("y"):
                                messages = store_api_key_input_config(provider, key_value)
                            else:
                                messages = ["Clipboard API key was not stored."]
                        refresh_checks()
                        close_panel(3)
                    elif value == "multi-clipboard":
                        key_value = read_clipboard_text()
                        keys = parse_api_key_list(key_value)
                        if not keys:
                            messages = ["Clipboard did not contain readable API keys."]
                        else:
                            primary = f"{mask_secret(keys[0])}; fp {secret_fingerprint(keys[0])}"
                            confirm = prompt_menu_value(
                                f"Clipboard contains {len(keys)} key(s); primary {primary}. Store with round-robin? y/N",
                                restore_tty=restore_line_mode,
                                raw_tty=restore_raw_mode,
                            )
                            if confirm.lower().startswith("y"):
                                messages = store_api_keys_config(provider, keys)
                            else:
                                messages = ["Clipboard API keys were not stored."]
                        refresh_checks()
                        close_panel(3)
                    elif value == "clear":
                        messages = clear_api_key_config(provider)
                        refresh_checks()
                        close_panel(3)
                elif panel == "base-url":
                    if value == "back":
                        close_panel()
                    elif value == "default":
                        messages = set_base_url_config(provider, default_base_url(provider))
                        refresh_checks()
                        close_panel(4)
                    elif value == "edit":
                        default = pcfg.get("base_url") or default_base_url(provider)
                        url = prompt_menu_value(f"Base URL for {provider}", default, restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        if url:
                            messages = set_base_url_config(provider, url)
                            refresh_checks()
                        close_panel(4)
                elif panel == "test":
                    if value == "back":
                        close_panel()
                    else:
                        panel_rows, panel_values = ["Testing current provider/model..."], []
                        first_render = render_prelaunch_screen(main_idx, panel, 0, panel_rows, checks, messages, first_render)
                        _, out = self_cmd(["test"])
                        lines = [line for line in out.splitlines() if line.strip()]
                        messages = lines[-8:] if lines else ["Test produced no output."]
                        test_ok = "Compatibility: OK" in out
                        refresh_checks()
                        close_panel(9 if test_ok else 4)
                elif panel == "log-level":
                    if value == "back":
                        close_panel()
                    elif value:
                        messages = set_log_level_config(value)
                        refresh_checks()
                        cfg = load_config()
                        panel_rows, panel_values = log_level_panel_rows(cfg)
                        panel_idx = max(0, min(panel_idx, len(panel_rows) - 1))
                elif panel == "channel-delivery":
                    if value == "back":
                        close_panel()
                    elif value:
                        messages = set_channel_delivery_config(value)
                        refresh_checks()
                        cfg = load_config()
                        panel_rows, panel_values = channel_delivery_panel_rows(cfg)
                        panel_idx = max(0, min(panel_idx, len(panel_rows) - 1))
                elif panel == "channels":
                    if value == "back":
                        close_panel()
                    elif value in ("__heading__", "__noop__"):
                        continue
                    elif value == "__reprobe__":
                        panel_rows, panel_values = ["Re-probing MCP channel capability..."], []
                        first_render = render_prelaunch_screen(main_idx, panel, 0, panel_rows, checks, messages, first_render)
                        try:
                            result = refresh_channel_probe_cache(passthrough)
                            messages = [channel_probe_summary_message("Probe complete", result)]
                        except Exception as exc:
                            messages = [f"Re-probe failed: {type(exc).__name__}: {exc}"]
                        cfg = load_config()
                        panel_rows, panel_values = channel_panel_rows(cfg)
                        if panel_values:
                            panel_idx = _channel_panel_first_selectable(panel_values)
                    elif value == "__add_custom__":
                        spec = prompt_menu_value("Channel spec (for example plugin:ainet@local or server:ainet)", restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        if spec:
                            messages = add_channel_spec(spec)
                            cfg = load_config()
                            panel_rows, panel_values = channel_panel_rows(cfg)
                            if panel_values:
                                panel_idx = _channel_panel_first_selectable(panel_values)
                    elif value == "__remove__":
                        spec = prompt_menu_value("Channel spec to remove", "", restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        if spec:
                            messages = remove_channel_spec(spec)
                            cfg = load_config()
                            panel_rows, panel_values = channel_panel_rows(cfg)
                            if panel_values:
                                panel_idx = _channel_panel_first_selectable(panel_values)
                    elif value == "__clear__":
                        messages = clear_channel_specs()
                        cfg = load_config()
                        panel_rows, panel_values = channel_panel_rows(cfg)
                        if panel_values:
                            panel_idx = _channel_panel_first_selectable(panel_values)
                    elif value:
                        if value in channel_specs(cfg):
                            messages = remove_channel_spec(value)
                        else:
                            messages = add_channel_spec(value)
                        cfg = load_config()
                        panel_rows, panel_values = channel_panel_rows(cfg)
                    refresh_checks()
                elif panel == "options":
                    if value == "back":
                        close_panel()
                    elif value == "context_setup":
                        open_panel("context")
                    elif value == "preset":
                        open_panel("preset")
                    elif value == "timeout_profile":
                        open_panel("timeout")
                    elif value in LLM_OPTION_TOGGLE_KEYS:
                        # Boolean toggles flip on Enter — no input prompt.
                        current = llm_option_current_bool(provider, pcfg, value)
                        try:
                            messages = set_llm_option_config(provider, value, "false" if current else "true")
                        except Exception as exc:
                            messages = [f"Option update failed: {type(exc).__name__}: {exc}"]
                        refresh_checks()
                        cfg = load_config()
                        provider, pcfg = get_current_provider(cfg)
                        old_idx = panel_idx
                        panel_rows, panel_values = llm_option_panel_rows(provider, pcfg, cfg.get("language", "en"))
                        panel_idx = max(0, min(old_idx, len(panel_rows) - 1))
                        panel_last_idx["options"] = panel_idx
                    else:
                        default = llm_option_prompt_default(provider, pcfg, value)
                        entered = prompt_menu_value(f"{value} for {provider} (default/unset clears)", default, restore_tty=restore_line_mode, raw_tty=restore_raw_mode)
                        try:
                            messages = set_llm_option_config(provider, value, entered)
                        except Exception as exc:
                            messages = [f"Option update failed: {type(exc).__name__}: {exc}"]
                        refresh_checks()
                        cfg = load_config()
                        provider, pcfg = get_current_provider(cfg)
                        old_idx = panel_idx
                        panel_rows, panel_values = llm_option_panel_rows(provider, pcfg, cfg.get("language", "en"))
                        panel_idx = max(0, min(old_idx, len(panel_rows) - 1))
                        panel_last_idx["options"] = panel_idx
                elif panel == "context":
                    if value == "back":
                        open_panel("options")
                    elif value == "__info__":
                        continue
                    else:
                        try:
                            messages = apply_context_setup_config(provider, value)
                        except Exception as exc:
                            messages = [f"Context setup failed: {type(exc).__name__}: {exc}"]
                        refresh_checks()
                        cfg = load_config()
                        provider, pcfg = get_current_provider(cfg)
                        panel = "options"
                        panel_idx = panel_last_idx.get("options", 0)
                        panel_rows, panel_values = llm_option_panel_rows(provider, pcfg, cfg.get("language", "en"))
                        panel_idx = max(0, min(panel_idx, len(panel_rows) - 1))
                        panel_last_idx["options"] = panel_idx
                elif panel == "preset":
                    if value == "back":
                        open_panel("options")
                    elif value == "__info__":
                        continue
                    else:
                        try:
                            messages = apply_llm_preset_config(provider, value)
                        except Exception as exc:
                            messages = [f"Preset failed: {type(exc).__name__}: {exc}"]
                        refresh_checks()
                        cfg = load_config()
                        provider, pcfg = get_current_provider(cfg)
                        panel = "options"
                        panel_idx = panel_last_idx.get("options", 0)
                        panel_rows, panel_values = llm_option_panel_rows(provider, pcfg, cfg.get("language", "en"))
                        panel_idx = max(0, min(panel_idx, len(panel_rows) - 1))
                        panel_last_idx["options"] = panel_idx
                elif panel == "timeout":
                    if value == "back":
                        open_panel("options")
                    elif value == "__info__":
                        continue
                    else:
                        try:
                            cfg = load_config()
                            provider, pcfg = get_current_provider(cfg)
                            messages = apply_timeout_profile_to_provider(pcfg, value, cfg.get("language", "en"))
                            save_config(cfg)
                            clear_model_cache()
                        except Exception as exc:
                            messages = [f"Timeout preset failed: {type(exc).__name__}: {exc}"]
                        refresh_checks()
                        cfg = load_config()
                        provider, pcfg = get_current_provider(cfg)
                        panel = "options"
                        panel_idx = panel_last_idx.get("options", 0)
                        panel_rows, panel_values = llm_option_panel_rows(provider, pcfg, cfg.get("language", "en"))
                        panel_idx = max(0, min(panel_idx, len(panel_rows) - 1))
                        panel_last_idx["options"] = panel_idx
                continue

            if key in ("up", "k"):
                cfg = load_config()
                provider, pcfg = get_current_provider(cfg)
                main_idx = (main_idx - 1) % len(main_menu_rows(cfg, provider, pcfg, cfg.get("language", "en")))
            elif key in ("down", "j"):
                cfg = load_config()
                provider, pcfg = get_current_provider(cfg)
                main_idx = (main_idx + 1) % len(main_menu_rows(cfg, provider, pcfg, cfg.get("language", "en")))
            elif key in ("esc", "q"):
                return PRELAUNCH_CANCEL
            elif key == "enter":
                actions = list(MAIN_MENU_ACTIONS)
                action = actions[main_idx]
                if action == "launch":
                    cfg = load_config()
                    provider, _ = get_current_provider(cfg)
                    if not claude_launch_enabled_for_provider(provider):
                        provider_label = provider_menu_label(provider, cfg.get("providers", {}).get(provider, {}))
                        messages = [f"Launch Claude Code is disabled while {provider_label} provider is selected."]
                        refresh_checks()
                        continue
                    blockers = launch_readiness_errors()
                    if blockers:
                        messages = blockers
                        if launch_blockers_require_api_key(blockers):
                            cfg = load_config()
                            provider, _ = get_current_provider(cfg)
                            main_idx = actions.index("api-key")
                            open_panel("api-key")
                            if "input" in panel_values:
                                panel_idx = panel_values.index("input")
                            messages = [
                                *blockers,
                                f"Opening API key setup for {PROVIDER_LABELS.get(provider, provider)}.",
                            ]
                        refresh_checks()
                        continue
                    return PRELAUNCH_LAUNCH_CLAUDE
                if action == "launch-agy":
                    cfg = load_config()
                    provider, _ = get_current_provider(cfg)
                    if not agy_launch_enabled_for_provider(provider):
                        messages = ["Launch AGY is disabled until you select AGY or AGY Routed as the provider."]
                        refresh_checks()
                        continue
                    blockers = launch_readiness_errors()
                    if blockers:
                        messages = blockers
                        refresh_checks()
                        continue
                    return PRELAUNCH_LAUNCH_AGY
                if action == "launch-codex":
                    cfg = load_config()
                    provider, pcfg = get_current_provider(cfg)
                    if not codex_launch_enabled_for_provider(provider):
                        messages = [f"Launch Codex is disabled while {provider_menu_label(provider, pcfg)} provider is selected."]
                        refresh_checks()
                        continue
                    blockers = launch_readiness_errors()
                    if blockers:
                        messages = blockers
                        refresh_checks()
                        continue
                    return PRELAUNCH_LAUNCH_CODEX
                if action == "launch-codex-app-server":
                    cfg = load_config()
                    provider, pcfg = get_current_provider(cfg)
                    if not codex_launch_enabled_for_provider(provider):
                        messages = [f"Launch Codex App Server is disabled while {provider_menu_label(provider, pcfg)} provider is selected."]
                        refresh_checks()
                        continue
                    blockers = launch_readiness_errors()
                    if blockers:
                        messages = blockers
                        refresh_checks()
                        continue
                    return PRELAUNCH_LAUNCH_CODEX_APP_SERVER
                if action == "quit":
                    return PRELAUNCH_CANCEL
                open_panel(action)
    finally:
        if old_settings is not None:
            try:
                import termios
                termios.tcsetattr(fd, termios.TCSANOW, old_settings)
            except Exception:
                pass
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


__all__ = [
    "PrelaunchChannelCommands",
    "PrelaunchChannelQuery",
    "PrelaunchConfig",
    "PrelaunchConstants",
    "PrelaunchLaunchPolicy",
    "PrelaunchMutations",
    "PrelaunchOptions",
    "PrelaunchPanelRows",
    "PrelaunchSecrets",
    "PrelaunchServices",
    "PrelaunchTerminal",
    "run_prelaunch_menu",
]
