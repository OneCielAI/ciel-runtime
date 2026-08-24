"""CLI dispatch, parser construction, and process entrypoint context."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class CliApplicationDispatchPorts:
    dispatch: Callable[..., int]
    services: Callable[[], Any]
    launch_claude: Callable[[list[str]], int]
    launch_codex: Callable[[list[str]], int]
    launch_codex_app_server: Callable[[list[str]], int]
    launch_agy: Callable[[list[str]], int]
    launch_kimi: Callable[[list[str]], int]
    kimi_login: Callable[[], int]
    launch_grok: Callable[[list[str]], int] = lambda _argv: 127
    launch_zcode: Callable[[list[str]], int] = lambda _argv: 127


@dataclass(frozen=True, slots=True)
class CliApplicationPresentationPorts:
    parser_builder: Callable[[Any], argparse.ArgumentParser]
    parser_services: Callable[[], Any]
    version: str
    output: Callable[[str], None]
    arguments: Callable[[], Sequence[str]]


@dataclass(frozen=True, slots=True)
class CliApplicationContext:
    dispatch: CliApplicationDispatchPorts
    presentation: CliApplicationPresentationPorts

    def run_cli(self, argv: list[str]) -> int:
        return self.dispatch.dispatch(argv, self.dispatch.services())

    def build_parser(self) -> argparse.ArgumentParser:
        return self.presentation.parser_builder(
            self.presentation.parser_services()
        )

    def cmd_cli(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.run_cli(args.argv))

    def cmd_launch(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.dispatch.launch_claude(args.argv))

    def cmd_launch_codex(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.dispatch.launch_codex(args.argv))

    def cmd_launch_codex_app_server(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.dispatch.launch_codex_app_server(args.argv))

    def cmd_launch_agy(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.dispatch.launch_agy(args.argv))

    def cmd_launch_grok(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.dispatch.launch_grok(args.argv))

    def cmd_launch_zcode(self, args: argparse.Namespace) -> None:
        raise SystemExit(self.dispatch.launch_zcode(args.argv))

    def cmd_version(self, _: argparse.Namespace) -> None:
        self.presentation.output(f"ciel-runtime {self.presentation.version}")

    def main(self) -> None:
        arguments = list(self.presentation.arguments())
        if len(arguments) >= 2 and arguments[1] == "cli":
            raise SystemExit(self.run_cli(arguments[2:]))
        routes = {
            "launch": self.dispatch.launch_claude,
            "codex": self.dispatch.launch_codex,
            "launch-codex": self.dispatch.launch_codex,
            "codex-app": self.dispatch.launch_codex_app_server,
            "codex-app-server": self.dispatch.launch_codex_app_server,
            "codex-appserver": self.dispatch.launch_codex_app_server,
            "launch-codex-app-server": self.dispatch.launch_codex_app_server,
            "agy": self.dispatch.launch_agy,
            "launch-agy": self.dispatch.launch_agy,
            "antigravity": self.dispatch.launch_agy,
            "kimi": self.dispatch.launch_kimi,
            "kimi-code": self.dispatch.launch_kimi,
            "launch-kimi": self.dispatch.launch_kimi,
            "grok": self.dispatch.launch_grok,
            "grok-build": self.dispatch.launch_grok,
            "launch-grok": self.dispatch.launch_grok,
            "zcode": self.dispatch.launch_zcode,
            "launch-zcode": self.dispatch.launch_zcode,
        }
        if len(arguments) >= 2 and arguments[1] in routes:
            raise SystemExit(routes[arguments[1]](arguments[2:]))
        if len(arguments) >= 2 and arguments[1] in (
            "kimi-login",
            "kimi-oauth-login",
        ):
            raise SystemExit(self.dispatch.kimi_login())
        args = self.build_parser().parse_args(arguments[1:])
        args.func(args)


@dataclass(frozen=True, slots=True)
class CliApplicationCompatibilityApi:
    context: Callable[[], CliApplicationContext]

    def run_cli(self, argv: list[str]) -> int:
        return self.context().run_cli(argv)

    def build_parser(self) -> argparse.ArgumentParser:
        return self.context().build_parser()

    def cmd_cli(self, args: argparse.Namespace) -> None:
        self.context().cmd_cli(args)

    def cmd_launch(self, args: argparse.Namespace) -> None:
        self.context().cmd_launch(args)

    def cmd_launch_codex(self, args: argparse.Namespace) -> None:
        self.context().cmd_launch_codex(args)

    def cmd_launch_codex_app_server(self, args: argparse.Namespace) -> None:
        self.context().cmd_launch_codex_app_server(args)

    def cmd_launch_agy(self, args: argparse.Namespace) -> None:
        self.context().cmd_launch_agy(args)

    def cmd_launch_grok(self, args: argparse.Namespace) -> None:
        self.context().cmd_launch_grok(args)

    def cmd_launch_zcode(self, args: argparse.Namespace) -> None:
        self.context().cmd_launch_zcode(args)

    def cmd_version(self, args: argparse.Namespace) -> None:
        self.context().cmd_version(args)

    def main(self) -> None:
        self.context().main()


__all__ = [
    "CliApplicationCompatibilityApi",
    "CliApplicationContext",
    "CliApplicationDispatchPorts",
    "CliApplicationPresentationPorts",
]
