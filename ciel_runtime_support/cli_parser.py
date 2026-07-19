from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable

CliHandler = Callable[[argparse.Namespace], Any]


@dataclass(frozen=True)
class CliParserLaunch:
    cli: CliHandler
    launch: CliHandler
    launch_codex: CliHandler
    launch_codex_app_server: CliHandler
    launch_agy: CliHandler
    serve: CliHandler


@dataclass(frozen=True)
class CliParserRuntime:
    version: CliHandler
    status: CliHandler
    env: CliHandler
    stop: CliHandler
    test: CliHandler


@dataclass(frozen=True)
class CliParserSettings:
    language: CliHandler
    web_search: CliHandler
    web_fetch: CliHandler
    log_level: CliHandler
    channels: CliHandler
    channel_delivery: CliHandler


@dataclass(frozen=True)
class CliParserProvider:
    ollama_native: CliHandler
    ollama_options: CliHandler
    provider_options: CliHandler
    ollama_catalog: CliHandler
    provider: CliHandler
    api_key: CliHandler
    set_api_key: CliHandler
    set_api_keys: CliHandler
    base_url: CliHandler


@dataclass(frozen=True)
class CliParserModels:
    model: CliHandler
    advisor_model: CliHandler
    models: CliHandler


@dataclass(frozen=True)
class CliParserServices:
    launch: CliParserLaunch
    runtime: CliParserRuntime
    settings: CliParserSettings
    provider: CliParserProvider
    models: CliParserModels


def build_cli_parser(services: CliParserServices) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ciel-runtimectl")
    commands = parser.add_subparsers(dest="cmd", required=True)
    _add_remainder_command(commands, "cli", services.launch.cli)
    _add_remainder_command(commands, "launch", services.launch.launch)
    _add_remainder_command(commands, "launch-codex", services.launch.launch_codex)
    _add_remainder_command(
        commands,
        "launch-codex-app-server",
        services.launch.launch_codex_app_server,
    )
    _add_remainder_command(commands, "launch-agy", services.launch.launch_agy)
    commands.add_parser("serve").set_defaults(func=services.launch.serve)
    commands.add_parser("version").set_defaults(func=services.runtime.version)
    commands.add_parser("status").set_defaults(func=services.runtime.status)
    commands.add_parser("env").set_defaults(func=services.runtime.env)
    commands.add_parser("stop").set_defaults(func=services.runtime.stop)
    _add_optional_value_command(commands, "language", services.settings.language)
    _add_optional_value_command(commands, "web-search", services.settings.web_search)
    _add_optional_value_command(commands, "web-fetch", services.settings.web_fetch)
    _add_optional_value_command(commands, "log-level", services.settings.log_level)
    channels = commands.add_parser("channels")
    channels.add_argument("values", nargs="*")
    channels.set_defaults(func=services.settings.channels)
    _add_optional_value_command(commands, "channel-delivery", services.settings.channel_delivery)
    _add_optional_value_command(commands, "ollama-native", services.provider.ollama_native)
    _add_values_command(commands, "ollama-options", services.provider.ollama_options)
    _add_values_command(commands, "provider-options", services.provider.provider_options)
    ollama_catalog = commands.add_parser("ollama-catalog")
    ollama_catalog.add_argument("--no-contexts", action="store_true")
    ollama_catalog.add_argument("--timeout", type=float, default=10.0)
    ollama_catalog.set_defaults(func=services.provider.ollama_catalog)
    test = commands.add_parser("test")
    test.add_argument("timeout", nargs="?", type=float, default=120.0)
    test.add_argument("mode", nargs="?", choices=("auto", "quick", "smoke", "full"), default="auto")
    test.set_defaults(func=services.runtime.test)
    provider = commands.add_parser("provider")
    provider.add_argument("name", nargs="?")
    provider.set_defaults(func=services.provider.provider)
    api_key = commands.add_parser("api-key")
    api_key.add_argument("provider", nargs="?")
    api_key.add_argument("action", nargs="?")
    api_key.set_defaults(func=services.provider.api_key)
    set_api_key = commands.add_parser("set-api-key")
    set_api_key.add_argument("provider")
    set_api_key.add_argument("key")
    set_api_key.set_defaults(func=services.provider.set_api_key)
    set_api_keys = commands.add_parser("set-api-keys")
    set_api_keys.add_argument("provider")
    set_api_keys.add_argument("keys", nargs="+")
    set_api_keys.set_defaults(func=services.provider.set_api_keys)
    base_url = commands.add_parser("base-url")
    base_url.add_argument("provider")
    base_url.add_argument("url")
    base_url.set_defaults(func=services.provider.base_url)
    _add_values_command(commands, "model", services.models.model, argument_name="value")
    _add_values_command(commands, "advisor-model", services.models.advisor_model, argument_name="value")
    models = commands.add_parser("models")
    models.add_argument("provider", nargs="?")
    models.set_defaults(func=services.models.models)
    return parser


def _add_remainder_command(
    commands: Any,
    name: str,
    handler: CliHandler,
) -> None:
    command = commands.add_parser(name, add_help=False)
    command.add_argument("argv", nargs=argparse.REMAINDER)
    command.set_defaults(func=handler)


def _add_optional_value_command(commands: Any, name: str, handler: CliHandler) -> None:
    command = commands.add_parser(name)
    command.add_argument("value", nargs="?")
    command.set_defaults(func=handler)


def _add_values_command(
    commands: Any,
    name: str,
    handler: CliHandler,
    *,
    argument_name: str = "values",
) -> None:
    command = commands.add_parser(name)
    command.add_argument(argument_name, nargs="*")
    command.set_defaults(func=handler)
