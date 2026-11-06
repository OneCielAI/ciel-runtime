"""Compose CLI dispatch and parser services from their bounded port groups."""

from __future__ import annotations

from dataclasses import dataclass

from . import cli_dispatch, cli_parser
from .work_settings_cli import command as work_settings_command  # noqa: F401


@dataclass(frozen=True, slots=True)
class CliServiceAssembly:
    core: cli_dispatch.CliCore
    runtime: cli_dispatch.CliRuntime
    provider_commands: cli_dispatch.CliProviderCommands
    special_commands: cli_dispatch.CliSpecialCommands
    operations: cli_dispatch.CliOperations
    configuration: cli_dispatch.CliConfiguration

    def services(self) -> cli_dispatch.CliServices:
        return cli_dispatch.CliServices(
            core=self.core,
            runtime=self.runtime,
            provider_commands=self.provider_commands,
            special_commands=self.special_commands,
            operations=self.operations,
            configuration=self.configuration,
        )


@dataclass(frozen=True, slots=True)
class CliParserAssembly:
    launch: cli_parser.CliParserLaunch
    runtime: cli_parser.CliParserRuntime
    settings: cli_parser.CliParserSettings
    provider: cli_parser.CliParserProvider
    models: cli_parser.CliParserModels
    work: cli_parser.CliHandler = lambda _args: None

    def services(self) -> cli_parser.CliParserServices:
        return cli_parser.CliParserServices(
            launch=self.launch,
            runtime=self.runtime,
            settings=self.settings,
            provider=self.provider,
            models=self.models,
            work=self.work,
        )


__all__ = ["CliParserAssembly", "CliServiceAssembly"]
