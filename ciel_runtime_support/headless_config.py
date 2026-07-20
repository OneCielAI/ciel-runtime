"""Application service for applying headless environment configuration."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HeadlessConfigCommands:
    set_language: Callable[[str], None]
    set_web_fetch: Callable[[bool], None]
    set_provider: Callable[[str], None]
    set_api_keys: Callable[[str, list[str]], None]
    set_api_key: Callable[[str, str], None]
    set_base_url: Callable[[str, str], None]
    set_model: Callable[[str], None]
    set_advisor_model: Callable[[str], None]
    set_provider_options: Callable[[list[str]], None]
    set_ollama_options: Callable[[list[str]], None]


@dataclass(frozen=True, slots=True)
class HeadlessChannelCommands:
    add_channel: Callable[[str], None]
    set_delivery: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class HeadlessConfigServices:
    environ: Mapping[str, str]
    parse_bool: Callable[[str | None, bool | None], bool | None]
    current_provider: Callable[[], str]
    commands: HeadlessConfigCommands
    channels: HeadlessChannelCommands


@dataclass(frozen=True, slots=True)
class HeadlessConfigResult:
    skip_menu: bool
    web_search_override: bool | None
    update_check_override: bool | None
    self_update_check_override: bool | None
    force_menu: bool

    def as_tuple(self) -> tuple[bool, bool | None, bool | None, bool | None, bool]:
        return (
            self.skip_menu,
            self.web_search_override,
            self.update_check_override,
            self.self_update_check_override,
            self.force_menu,
        )


@dataclass(frozen=True, slots=True)
class HeadlessEnvFileLoader:
    load: Callable[..., None]

    def pop_args(self, argv: list[str]) -> list[str]:
        cleaned: list[str] = []
        index = 0
        while index < len(argv):
            argument = argv[index]
            if (
                argument == "--ca-env-file"
                or argument.startswith("--ca-env-file=")
            ):
                value = (
                    argument.split("=", 1)[1]
                    if "=" in argument
                    else None
                )
                if value is None:
                    if index + 1 >= len(argv):
                        raise SystemExit(
                            "Missing path for --ca-env-file"
                        )
                    value = argv[index + 1]
                    index += 2
                else:
                    index += 1
                path = Path(value).expanduser()
                if not path.exists():
                    raise SystemExit(
                        f"--ca-env-file not found: {path}"
                    )
                self.load(path, override=True)
                continue
            cleaned.append(argument)
            index += 1
        return cleaned


PROVIDER_OPTION_ENV = {
    "CIEL_RUNTIME_MAX_OUTPUT_TOKENS": "max_output_tokens",
    "CIEL_RUNTIME_CONTEXT_WINDOW": "context_window",
    "CIEL_RUNTIME_REQUEST_TIMEOUT_MS": "request_timeout_ms",
    "CIEL_RUNTIME_STREAM_IDLE_TIMEOUT_MS": "stream_idle_timeout_ms",
    "CIEL_RUNTIME_RATE_LIMIT_RPM": "rate_limit_rpm",
    "CIEL_RUNTIME_RATE_LIMIT_STATUS": "rate_limit_status",
    "CIEL_RUNTIME_STREAM": "stream_enabled",
    "CIEL_RUNTIME_STREAM_WORD_CHUNKING": "stream_word_chunking",
}


def apply_headless_config(services: HeadlessConfigServices) -> HeadlessConfigResult:
    env = services.environ
    commands = services.commands
    skip_menu = env.get("CIEL_RUNTIME_SKIP_MENU") == "1"
    force_menu = bool(services.parse_bool(env.get("CIEL_RUNTIME_FORCE_MENU"), False))
    web_search_override = services.parse_bool(env.get("CIEL_RUNTIME_WEB_SEARCH"), None)
    update_check_override = services.parse_bool(env.get("CIEL_RUNTIME_UPDATE_CHECK"), None)
    self_update_check_override = services.parse_bool(env.get("CIEL_RUNTIME_SELF_UPDATE_CHECK"), None)

    language = env.get("CIEL_RUNTIME_LANGUAGE", "").strip()
    if language:
        commands.set_language(language)
        skip_menu = True
    web_fetch = services.parse_bool(env.get("CIEL_RUNTIME_WEB_FETCH"), None)
    if web_fetch is not None:
        commands.set_web_fetch(web_fetch)
        skip_menu = True
    provider = env.get("CIEL_RUNTIME_PROVIDER", "").strip()
    if provider:
        commands.set_provider(provider)
        skip_menu = True

    current_provider = services.current_provider()
    skip_menu = _apply_api_key_config(env, current_provider, commands) or skip_menu

    base_url = env.get("CIEL_RUNTIME_BASE_URL", "").strip()
    if base_url:
        commands.set_base_url(services.current_provider(), base_url)
        skip_menu = True
    model = env.get("CIEL_RUNTIME_MODEL", "").strip()
    if model:
        commands.set_model(model)
        skip_menu = True
    advisor_model = env.get("CIEL_RUNTIME_ADVISOR_MODEL", "").strip()
    if advisor_model:
        commands.set_advisor_model(advisor_model)
        skip_menu = True

    provider_values = [
        f"{option_key}={env[env_key].strip()}"
        for env_key, option_key in PROVIDER_OPTION_ENV.items()
        if env.get(env_key, "").strip()
    ]
    if provider_values:
        commands.set_provider_options(provider_values)
        skip_menu = True

    ollama_values = _ollama_option_values(env)
    if ollama_values:
        commands.set_ollama_options(ollama_values)
        skip_menu = True

    for channel in _split_values(env.get("CIEL_RUNTIME_CHANNELS", "")):
        services.channels.add_channel(channel)
        skip_menu = True
    for channel in _split_values(env.get("CIEL_RUNTIME_DEV_CHANNELS", "")):
        services.channels.add_channel(channel)
        skip_menu = True
    channel_delivery = env.get("CIEL_RUNTIME_CHANNEL_DELIVERY", "").strip()
    if channel_delivery:
        services.channels.set_delivery(channel_delivery)
        skip_menu = True

    return HeadlessConfigResult(
        skip_menu=skip_menu,
        web_search_override=web_search_override,
        update_check_override=update_check_override,
        self_update_check_override=self_update_check_override,
        force_menu=force_menu,
    )


def _apply_api_key_config(
    env: Mapping[str, str],
    provider: str,
    commands: HeadlessConfigCommands,
) -> bool:
    api_keys_env = env.get("CIEL_RUNTIME_API_KEYS_ENV", "").strip()
    api_keys = env.get("CIEL_RUNTIME_API_KEYS", "").strip()
    api_key_env = env.get("CIEL_RUNTIME_API_KEY_ENV", "").strip()
    api_key = env.get("CIEL_RUNTIME_API_KEY", "").strip()
    if api_keys_env:
        value = env.get(api_keys_env, "")
        if not value:
            raise SystemExit(f"Environment variable {api_keys_env} is empty or not set")
        commands.set_api_keys(provider, [value])
        return True
    if api_keys:
        commands.set_api_keys(provider, [api_keys])
        return True
    if api_key_env:
        value = env.get(api_key_env, "")
        if not value:
            raise SystemExit(f"Environment variable {api_key_env} is empty or not set")
        commands.set_api_key(provider, value)
        return True
    if api_key:
        commands.set_api_key(provider, api_key)
        return True
    return False


def _ollama_option_values(env: Mapping[str, str]) -> list[str]:
    values: list[str] = []
    num_ctx = env.get("CIEL_RUNTIME_OLLAMA_NUM_CTX", "").strip()
    if num_ctx:
        values.append(f"num_ctx={num_ctx}")
    values.extend(item for item in env.get("CIEL_RUNTIME_OLLAMA_OPTIONS", "").replace(",", " ").split() if item)
    return values


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\s,]+", value.strip()) if item.strip()]
