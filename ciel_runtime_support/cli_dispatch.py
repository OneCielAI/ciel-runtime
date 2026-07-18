"""Command-line application dispatcher with explicit dependencies."""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CliServices:
    VERSION: Any
    add_channel_spec: Any
    agy_passthrough_has_command: Any
    apply_auto_llm_options_config: Any
    apply_headless_env_config: Any
    channel_delivery_mode: Any
    clear_channel_specs: Any
    cli_usage: Any
    cmd_advisor_model: Any
    cmd_api_key: Any
    cmd_base_url: Any
    cmd_channels: Any
    cmd_language: Any
    cmd_log_level: Any
    cmd_mcp_proxy: Any
    cmd_model: Any
    cmd_models: Any
    cmd_ollama_catalog: Any
    cmd_ollama_native: Any
    cmd_ollama_options: Any
    cmd_provider: Any
    cmd_provider_options: Any
    cmd_set_api_key: Any
    cmd_set_api_keys: Any
    cmd_status: Any
    cmd_stop: Any
    cmd_test: Any
    cmd_web_fetch: Any
    cmd_web_search: Any
    codex_passthrough_has_command: Any
    find_executable: Any
    get_current_provider: Any
    last_launch_runtime: Any
    launch_agy: Any
    launch_claude: Any
    launch_codex: Any
    launch_codex_app_server: Any
    load_config: Any
    native_agy_enabled: Any
    native_codex_enabled: Any
    pop_headless_env_file_args: Any
    portable_provider_menu: Any
    run_external_menu: Any
    run_quiet_upgrade_and_exit: Any
    set_advisor_model_config: Any
    set_channel_delivery_config: Any
    set_channel_development_enabled: Any
    set_log_level_config: Any


def dispatch_cli(argv: list[str], services: CliServices) -> int:
    VERSION = services.VERSION
    add_channel_spec = services.add_channel_spec
    agy_passthrough_has_command = services.agy_passthrough_has_command
    apply_auto_llm_options_config = services.apply_auto_llm_options_config
    apply_headless_env_config = services.apply_headless_env_config
    channel_delivery_mode = services.channel_delivery_mode
    clear_channel_specs = services.clear_channel_specs
    cli_usage = services.cli_usage
    cmd_advisor_model = services.cmd_advisor_model
    cmd_api_key = services.cmd_api_key
    cmd_base_url = services.cmd_base_url
    cmd_channels = services.cmd_channels
    cmd_language = services.cmd_language
    cmd_log_level = services.cmd_log_level
    cmd_mcp_proxy = services.cmd_mcp_proxy
    cmd_model = services.cmd_model
    cmd_models = services.cmd_models
    cmd_ollama_catalog = services.cmd_ollama_catalog
    cmd_ollama_native = services.cmd_ollama_native
    cmd_ollama_options = services.cmd_ollama_options
    cmd_provider = services.cmd_provider
    cmd_provider_options = services.cmd_provider_options
    cmd_set_api_key = services.cmd_set_api_key
    cmd_set_api_keys = services.cmd_set_api_keys
    cmd_status = services.cmd_status
    cmd_stop = services.cmd_stop
    cmd_test = services.cmd_test
    cmd_web_fetch = services.cmd_web_fetch
    cmd_web_search = services.cmd_web_search
    codex_passthrough_has_command = services.codex_passthrough_has_command
    find_executable = services.find_executable
    get_current_provider = services.get_current_provider
    last_launch_runtime = services.last_launch_runtime
    launch_agy = services.launch_agy
    launch_claude = services.launch_claude
    launch_codex = services.launch_codex
    launch_codex_app_server = services.launch_codex_app_server
    load_config = services.load_config
    native_agy_enabled = services.native_agy_enabled
    native_codex_enabled = services.native_codex_enabled
    pop_headless_env_file_args = services.pop_headless_env_file_args
    portable_provider_menu = services.portable_provider_menu
    run_external_menu = services.run_external_menu
    run_quiet_upgrade_and_exit = services.run_quiet_upgrade_and_exit
    set_advisor_model_config = services.set_advisor_model_config
    set_channel_delivery_config = services.set_channel_delivery_config
    set_channel_development_enabled = services.set_channel_development_enabled
    set_log_level_config = services.set_log_level_config
    if argv and argv[0] == "mcp-proxy":
        return cmd_mcp_proxy(argv[1:])
    if argv and argv[0] in ("help", "--help", "-h"):
        print(cli_usage())
        return 0
    argv = pop_headless_env_file_args(argv)
    if any(arg in ("--ca-upgrade-and-exit", "--ca-quiet-upgrade", "--ca-upgrade-exit") for arg in argv):
        return run_quiet_upgrade_and_exit()
    if argv:
        head, rest = argv[0], argv[1:]
        if head in ("agy", "launch-agy", "antigravity"):
            return launch_agy(rest)
        if head in ("codex", "launch-codex"):
            return launch_codex(rest)
        if head in ("version", "--version", "-v"):
            print(f"ciel-runtime {VERSION}")
            return 0
        if head in ("language", "lang"):
            cmd_language(argparse.Namespace(value=rest[0] if rest else None))
            return 0
        if head == "provider":
            if not rest:
                rc = run_external_menu("ciel-runtime-provider")
                return portable_provider_menu() if rc is None else rc
            if rest[0] in ("list", "ls"):
                cmd_provider(argparse.Namespace(name=None))
                return 0
            cmd_provider(argparse.Namespace(name=rest[0]))
            return 0
        if head == "model":
            if not rest:
                raise SystemExit("Missing model id")
            cmd_model(argparse.Namespace(value=rest))
            return 0
        if head in ("advisor-model", "advisormodel", "advisor"):
            cmd_advisor_model(argparse.Namespace(value=rest))
            return 0
        if head == "base-url":
            if len(rest) < 2:
                raise SystemExit("Usage: ciel-runtime base-url PROVIDER URL")
            cmd_base_url(argparse.Namespace(provider=rest[0], url=rest[1]))
            return 0
        if head == "models":
            cmd_models(argparse.Namespace(provider=rest[0] if rest else None))
            return 0
        if head in ("api-key", "apikey"):
            if not rest:
                raise SystemExit("Missing provider")
            cmd_api_key(argparse.Namespace(provider=rest[0], action=rest[1] if len(rest) > 1 else None))
            return 0
        if head in ("set-api-key", "set-apikey"):
            if len(rest) < 2:
                raise SystemExit("Usage: ciel-runtime set-api-key PROVIDER KEY")
            cmd_set_api_key(argparse.Namespace(provider=rest[0], key=rest[1]))
            return 0
        if head in ("set-api-keys", "set-apikeys"):
            if len(rest) < 2:
                raise SystemExit("Usage: ciel-runtime set-api-keys PROVIDER KEY1,KEY2")
            cmd_set_api_keys(argparse.Namespace(provider=rest[0], keys=rest[1:]))
            return 0
        if head in ("web-search", "websearch"):
            cmd_web_search(argparse.Namespace(value=rest[0] if rest else None))
            return 0
        if head in ("web-fetch", "webfetch"):
            cmd_web_fetch(argparse.Namespace(value=rest[0] if rest else None))
            return 0
        if head in ("log-level", "loglevel", "logging"):
            cmd_log_level(argparse.Namespace(value=rest[0] if rest else None))
            return 0
        if head in ("channels", "channel"):
            cmd_channels(argparse.Namespace(values=rest))
            return 0
        if head in ("channel-delivery", "channel_delivery"):
            if rest:
                for line in set_channel_delivery_config(rest[0]):
                    print(line)
            else:
                print(f"channel_delivery: {channel_delivery_mode()}")
            return 0
        if head in ("ollama-native", "ollama-compat"):
            cmd_ollama_native(argparse.Namespace(value=rest[0] if rest else None))
            return 0
        if head in ("ollama-options", "ollama-option", "ollama-opts"):
            cmd_ollama_options(argparse.Namespace(values=rest))
            return 0
        if head in ("provider-options", "provider-option", "provider-opts", "vllm-options", "nim-options"):
            cmd_provider_options(argparse.Namespace(values=rest))
            return 0
        if head in ("ollama-catalog", "ollama-catalog-refresh"):
            no_contexts = "--no-contexts" in rest
            timeout = 10.0
            for item in rest:
                if item.startswith("--timeout="):
                    try:
                        timeout = float(item.split("=", 1)[1])
                    except ValueError:
                        raise SystemExit("Usage: ciel-runtime ollama-catalog [--no-contexts] [--timeout=SECONDS]")
            cmd_ollama_catalog(argparse.Namespace(no_contexts=no_contexts, timeout=timeout))
            return 0
        if head in ("test", "compat", "compatibility"):
            timeout = 60.0
            mode = "auto"
            if rest and rest[0] in ("auto", "quick", "smoke", "full"):
                mode = rest[0]
                rest = rest[1:]
            if rest:
                try:
                    timeout = float(rest[0])
                except ValueError:
                    raise SystemExit("Usage: ciel-runtime test [timeout_seconds] [auto|quick|smoke|full]")
                if len(rest) > 1:
                    mode = rest[1]
            if mode not in ("auto", "quick", "smoke", "full"):
                raise SystemExit("Usage: ciel-runtime test [timeout_seconds] [auto|quick|smoke|full]")
            cmd_test(argparse.Namespace(timeout=timeout, mode=mode))
            return 0
        if head == "status":
            cmd_status(argparse.Namespace())
            return 0
        if head == "stop":
            cmd_stop(argparse.Namespace())
            ncp = find_executable("ncp")
            if ncp:
                subprocess.run([ncp, "kill"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 0
        if argv[0] == "resume":
            runtime = last_launch_runtime()
            if runtime == "codex":
                return launch_codex(argv)
            if runtime == "agy":
                return launch_agy(argv)
        if codex_passthrough_has_command(argv):
            cfg = load_config()
            provider, _ = get_current_provider(cfg)
            if native_codex_enabled(provider):
                return launch_codex(argv)
        if argv[0] == "resume":
            cfg = load_config()
            provider, _ = get_current_provider(cfg)
            if native_agy_enabled(provider):
                return launch_agy(argv)
        if agy_passthrough_has_command(argv):
            cfg = load_config()
            provider, _ = get_current_provider(cfg)
            if native_agy_enabled(provider):
                return launch_agy(argv)

    passthrough: list[str] = []
    configure_only = False
    auto_llm_options = False
    auto_llm_model: str | None = None
    runtime = "claude"
    skip_menu, web_search_override, update_check_override, self_update_check_override, force_menu = apply_headless_env_config()
    update_check = True
    if update_check_override is not None:
        update_check = update_check_override
    self_update_check = True
    if self_update_check_override is not None:
        self_update_check = self_update_check_override
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--ca-menu", "--ca-interactive"):
            force_menu = True
            i += 1
        elif arg == "--ca-runtime" or arg.startswith("--ca-runtime="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing runtime for --ca-runtime")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            runtime = str(value or "").strip().lower()
            if runtime in ("codex-app", "codex-appserver"):
                runtime = "codex-app-server"
            if runtime not in ("claude", "codex", "codex-app-server", "agy"):
                raise SystemExit("--ca-runtime must be claude, codex, codex-app-server, or agy")
        elif arg in ("--ca-no-launch", "--ca-configure-only", "--ca-setup-only"):
            configure_only = True
            skip_menu = True
            i += 1
        elif arg == "--ca-language" or arg.startswith("--ca-language="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing language for --ca-language")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_language(argparse.Namespace(value=value))
            skip_menu = True
        elif arg == "--ca-provider" or arg.startswith("--ca-provider="):
            provider_value = arg.split("=", 1)[1] if "=" in arg else None
            if provider_value:
                cmd_provider(argparse.Namespace(name=provider_value))
                skip_menu = True
                i += 1
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                cmd_provider(argparse.Namespace(name=argv[i + 1]))
                skip_menu = True
                i += 2
            else:
                rc = run_external_menu("ciel-runtime-provider")
                if rc is None:
                    rc = portable_provider_menu()
                if rc != 0:
                    return rc
                skip_menu = True
                i += 1
        elif arg == "--ca-base-url" or arg.startswith("--ca-base-url="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing URL for --ca-base-url")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            provider, _ = get_current_provider(load_config())
            cmd_base_url(argparse.Namespace(provider=provider, url=value))
            skip_menu = True
        elif arg == "--ca-model" or arg.startswith("--ca-model="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing model id for --ca-model")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_model(argparse.Namespace(value=[value]))
            skip_menu = True
        elif arg == "--ca-advisor-model" or arg.startswith("--ca-advisor-model="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing model id for --ca-advisor-model")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            for line in set_advisor_model_config(value):
                print(line)
            skip_menu = True
        elif arg in ("--ca-auto-llm-options", "--ca-auto-llm", "--ca-recommended-llm") or arg.startswith(
            ("--ca-auto-llm-options=", "--ca-auto-llm=", "--ca-recommended-llm=")
        ):
            auto_llm_options = True
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            if value:
                auto_llm_model = value
            skip_menu = True
        elif arg == "--ca-models":
            cmd_models(argparse.Namespace(provider=None))
            return 0
        elif arg == "--ca-api-key" or arg.startswith("--ca-api-key="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing key for --ca-api-key")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            provider, _ = get_current_provider(load_config())
            cmd_set_api_key(argparse.Namespace(provider=provider, key=value))
            skip_menu = True
        elif arg == "--ca-api-key-env" or arg.startswith("--ca-api-key-env="):
            env_name = arg.split("=", 1)[1] if "=" in arg else None
            if env_name is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing env var name for --ca-api-key-env")
                env_name = argv[i + 1]
                i += 2
            else:
                i += 1
            value = os.environ.get(env_name, "")
            if not value:
                raise SystemExit(f"Environment variable {env_name} is empty or not set")
            provider, _ = get_current_provider(load_config())
            cmd_set_api_key(argparse.Namespace(provider=provider, key=value))
            skip_menu = True
        elif arg == "--ca-api-keys" or arg.startswith("--ca-api-keys="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing key list for --ca-api-keys")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            provider, _ = get_current_provider(load_config())
            cmd_set_api_keys(argparse.Namespace(provider=provider, keys=[value]))
            skip_menu = True
        elif arg == "--ca-api-keys-env" or arg.startswith("--ca-api-keys-env="):
            env_name = arg.split("=", 1)[1] if "=" in arg else None
            if env_name is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing env var name for --ca-api-keys-env")
                env_name = argv[i + 1]
                i += 2
            else:
                i += 1
            value = os.environ.get(env_name, "")
            if not value:
                raise SystemExit(f"Environment variable {env_name} is empty or not set")
            provider, _ = get_current_provider(load_config())
            cmd_set_api_keys(argparse.Namespace(provider=provider, keys=[value]))
            skip_menu = True
        elif arg == "--ca-set-api-key":
            if i + 2 >= len(argv):
                raise SystemExit("Usage: --ca-set-api-key PROVIDER KEY")
            cmd_set_api_key(argparse.Namespace(provider=argv[i + 1], key=argv[i + 2]))
            skip_menu = True
            i += 3
        elif arg == "--ca-set-api-key-env":
            if i + 2 >= len(argv):
                raise SystemExit("Usage: --ca-set-api-key-env PROVIDER ENVVAR")
            value = os.environ.get(argv[i + 2], "")
            if not value:
                raise SystemExit(f"Environment variable {argv[i + 2]} is empty or not set")
            cmd_set_api_key(argparse.Namespace(provider=argv[i + 1], key=value))
            skip_menu = True
            i += 3
        elif arg == "--ca-set-api-keys":
            if i + 2 >= len(argv):
                raise SystemExit("Usage: --ca-set-api-keys PROVIDER KEY1,KEY2")
            cmd_set_api_keys(argparse.Namespace(provider=argv[i + 1], keys=[argv[i + 2]]))
            skip_menu = True
            i += 3
        elif arg == "--ca-set-api-keys-env":
            if i + 2 >= len(argv):
                raise SystemExit("Usage: --ca-set-api-keys-env PROVIDER ENVVAR")
            value = os.environ.get(argv[i + 2], "")
            if not value:
                raise SystemExit(f"Environment variable {argv[i + 2]} is empty or not set")
            cmd_set_api_keys(argparse.Namespace(provider=argv[i + 1], keys=[value]))
            skip_menu = True
            i += 3
        elif arg in ("--ca-provider-option", "--ca-provider-options") or arg.startswith(
            ("--ca-provider-option=", "--ca-provider-options=")
        ):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing KEY=VALUE for --ca-provider-option")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[value]))
            skip_menu = True
        elif arg == "--ca-set-provider-option":
            if i + 2 >= len(argv):
                raise SystemExit("Usage: --ca-set-provider-option PROVIDER KEY=VALUE")
            cmd_provider_options(argparse.Namespace(values=[argv[i + 1], argv[i + 2]]))
            skip_menu = True
            i += 3
        elif arg == "--ca-ollama-num-ctx" or arg.startswith("--ca-ollama-num-ctx="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing value for --ca-ollama-num-ctx")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_ollama_options(argparse.Namespace(values=[f"num_ctx={value}"]))
            skip_menu = True
        elif arg == "--ca-ollama-ctx-range" or arg.startswith("--ca-ollama-ctx-range="):
            if "=" in arg:
                raw = arg.split("=", 1)[1]
                sep = ":" if ":" in raw else "-"
                parts = [p.strip() for p in raw.split(sep, 1)]
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    raise SystemExit("Usage: --ca-ollama-ctx-range MIN MAX")
                min_value, max_value = parts
                i += 1
            else:
                if i + 2 >= len(argv):
                    raise SystemExit("Usage: --ca-ollama-ctx-range MIN MAX")
                min_value, max_value = argv[i + 1], argv[i + 2]
                i += 3
            cmd_ollama_options(
                argparse.Namespace(values=[f"min={min_value}", f"max={max_value}", "num_ctx=auto"])
            )
            skip_menu = True
        elif arg == "--ca-ollama-option" or arg.startswith("--ca-ollama-option="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing KEY=VALUE for --ca-ollama-option")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_ollama_options(argparse.Namespace(values=[value]))
            skip_menu = True
        elif arg == "--ca-max-output-tokens" or arg.startswith("--ca-max-output-tokens="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing value for --ca-max-output-tokens")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"max_output_tokens={value}"]))
            skip_menu = True
        elif arg == "--ca-context-window" or arg.startswith("--ca-context-window="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing value for --ca-context-window")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"context_window={value}"]))
            skip_menu = True
        elif arg == "--ca-request-timeout-ms" or arg.startswith("--ca-request-timeout-ms="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing value for --ca-request-timeout-ms")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"request_timeout_ms={value}"]))
            skip_menu = True
        elif arg == "--ca-stream-idle-timeout-ms" or arg.startswith("--ca-stream-idle-timeout-ms="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing value for --ca-stream-idle-timeout-ms")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"stream_idle_timeout_ms={value}"]))
            skip_menu = True
        elif arg == "--ca-rate-limit-rpm" or arg.startswith("--ca-rate-limit-rpm="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing value for --ca-rate-limit-rpm")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"rate_limit_rpm={value}"]))
            skip_menu = True
        elif arg == "--ca-rate-limit-status" or arg.startswith("--ca-rate-limit-status="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing on/off for --ca-rate-limit-status")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"rate_limit_status={value}"]))
            skip_menu = True
        elif arg == "--ca-stream" or arg.startswith("--ca-stream="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing on/off for --ca-stream")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"stream_enabled={value}"]))
            skip_menu = True
        elif arg == "--ca-stream-word-chunking" or arg.startswith("--ca-stream-word-chunking="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing on/off for --ca-stream-word-chunking")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            cmd_provider_options(argparse.Namespace(values=[f"stream_word_chunking={value}"]))
            skip_menu = True
        elif arg == "--ca-log-level" or arg.startswith("--ca-log-level="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing level for --ca-log-level")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            for line in set_log_level_config(value):
                print(line)
            skip_menu = True
        elif arg == "--ca-web-search":
            web_search_override = True
            skip_menu = True
            i += 1
        elif arg == "--ca-no-web-search":
            web_search_override = False
            skip_menu = True
            i += 1
        elif arg == "--ca-web-fetch":
            cmd_web_fetch(argparse.Namespace(value="on"))
            skip_menu = True
            i += 1
        elif arg == "--ca-no-web-fetch":
            cmd_web_fetch(argparse.Namespace(value="off"))
            skip_menu = True
            i += 1
        elif arg == "--ca-channel" or arg.startswith("--ca-channel="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing channel spec for --ca-channel")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            for line in add_channel_spec(value):
                print(line)
            skip_menu = True
        elif arg == "--ca-channel-delivery" or arg.startswith("--ca-channel-delivery="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing mode for --ca-channel-delivery")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            for line in set_channel_delivery_config(value):
                print(line)
            skip_menu = True
        elif arg == "--ca-dev-channel" or arg.startswith("--ca-dev-channel="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing channel spec for --ca-dev-channel")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            for line in add_channel_spec(value):
                print(line)
            skip_menu = True
        elif arg == "--ca-development-channels" or arg.startswith("--ca-development-channels="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing on/off for --ca-development-channels")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            for line in set_channel_development_enabled(True):
                print(line)
            skip_menu = True
        elif arg == "--ca-clear-channels":
            for line in clear_channel_specs():
                print(line)
            skip_menu = True
            i += 1
        elif arg == "--ca-no-update-check":
            update_check = False
            skip_menu = True
            i += 1
        elif arg == "--ca-no-self-update-check":
            self_update_check = False
            skip_menu = True
            i += 1
        elif arg == "--ca-status":
            cmd_status(argparse.Namespace())
            return 0
        elif arg == "--ca-stop":
            cmd_stop(argparse.Namespace())
            return 0
        elif arg == "--":
            passthrough.extend(argv[i + 1 :])
            break
        else:
            passthrough.append(arg)
            i += 1
    if auto_llm_options:
        for line in apply_auto_llm_options_config(auto_llm_model):
            print(line)
    if configure_only:
        return 0
    if runtime == "agy":
        return launch_agy(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            update_check=update_check,
            self_update_check=self_update_check,
        )
    if runtime == "codex":
        return launch_codex(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            update_check=update_check,
            self_update_check=self_update_check,
        )
    if runtime == "codex-app-server":
        return launch_codex_app_server(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            update_check=update_check,
            self_update_check=self_update_check,
        )
    return launch_claude(
        passthrough,
        skip_menu=skip_menu,
        force_menu=force_menu,
        web_search_override=web_search_override,
        update_check=update_check,
        self_update_check=self_update_check,
    )


__all__ = ["CliServices", "dispatch_cli"]
