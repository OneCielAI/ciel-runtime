from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping

CliHandler = Callable[[argparse.Namespace], Any]

SESSION_API_KEY_COMMAND = "session-api-key"
SESSION_API_KEY_ACTIONS = ("list", "inspect", "inject", "replace", "clear", "reset-state")
# Shown instead of argparse's own message for this command: that message would
# echo the offending token ("unrecognized arguments: <token>"), and a key (or
# the reset-state confirmation phrase) typed on the command line by mistake must
# not be printed to a terminal or log.
SESSION_API_KEY_ARGV_REFUSAL = (
    "session-api-key: invalid or unrecognized argument (not shown). A key or the reset-state phrase is "
    "never taken from the command line: use the hidden prompt or --stdin (a key also --key-file - or --fd N).")


@dataclass(frozen=True)
class CliParserLaunch:
    cli: CliHandler
    launch: CliHandler
    launch_codex: CliHandler
    launch_codex_app_server: CliHandler
    launch_agy: CliHandler
    serve: CliHandler
    remote_bridge: CliHandler
    launch_grok: CliHandler = lambda _args: None
    launch_zcode: CliHandler = lambda _args: None
    launch_muse: CliHandler = lambda _args: None


@dataclass(frozen=True)
class CliParserRuntime:
    version: CliHandler
    status: CliHandler
    env: CliHandler
    stop: CliHandler
    test: CliHandler
    restart_session: CliHandler = lambda _args: None
    remote_provision: CliHandler = lambda _args: None


@dataclass(frozen=True)
class CliParserSettings:
    language: CliHandler
    web_search: CliHandler
    web_fetch: CliHandler
    log_level: CliHandler
    external_events: CliHandler
    transcript_events: CliHandler
    usage_events: CliHandler
    usage_api_key: CliHandler
    remote_instructions: CliHandler
    remote_memory: CliHandler


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
    oauth: Mapping[str, CliHandler]


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
    work: CliHandler = lambda _args: None
    session_api_key: CliHandler = lambda _args: 0


class CielCtlArgumentParser(argparse.ArgumentParser):
    """Root ctl parser: a session-api-key command line is never echoed in an error."""

    _redact = False

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        argv = list(sys.argv[1:] if args is None else args)
        self._redact = bool(argv) and argv[0] == SESSION_API_KEY_COMMAND
        return super().parse_known_args(argv, namespace)

    def error(self, message: str):  # type: ignore[override]
        if self._redact:
            self.exit(2, SESSION_API_KEY_ARGV_REFUSAL + "\n")
        super().error(message)


def session_api_key_handler(handler: CliHandler) -> CliHandler:
    """Adapt argv to the session-key CLI: `--key-file -` reads standard input,
    and the handler's integer result becomes the process exit code."""

    def run(args: argparse.Namespace) -> None:
        if getattr(args, "key_file", None) == "-":
            args.stdin = True
        raise SystemExit(handler(args))

    return run


def _add_session_api_key_command(commands: Any, handler: CliHandler) -> None:
    command = commands.add_parser(
        SESSION_API_KEY_COMMAND, allow_abbrev=False,
        help="Memory-only API key for one running session and provider",
        description=("Memory only, this session and provider; never stored. Persistent keys: "
                     "api-key / set-api-key / set-api-keys. The key is read from a hidden prompt, "
                     "--stdin, --key-file - or --fd N, never from the command line. reset-state is "
                     "router-wide; its confirmation phrase is typed at the hidden prompt or given on --stdin."))
    # Errors raised inside this subparser (bad choice, bad integer) are refused
    # without echoing the value either.
    command.error = lambda _message: command.exit(2, SESSION_API_KEY_ARGV_REFUSAL + "\n")  # type: ignore[method-assign]
    command.add_argument("action", nargs="?", choices=SESSION_API_KEY_ACTIONS, default="list")
    command.add_argument("--session", help="exact claude:<uuid> or codex:<id> conversation identity")
    command.add_argument("--provider", help="provider whose key this session uses")
    command.add_argument("--ttl-seconds", type=int, dest="ttl_seconds")
    command.add_argument("--expected-version", type=int, dest="expected_version",
                         help="version shown by inspect; required for replace and clear")
    command.add_argument("--operation-id", "--idempotency-key", dest="idempotency_key",
                         help="idempotency key: a retry with the same id is not applied twice")
    source = command.add_mutually_exclusive_group()
    source.add_argument("--stdin", action="store_true", help="read the key from standard input")
    source.add_argument("--key-file", choices=("-",), dest="key_file",
                        help="'-' reads the key from standard input")
    source.add_argument("--fd", type=int, help="read the key from an inherited file descriptor")
    command.add_argument("--json", action="store_true", help="emit one JSON object (metadata only)")
    command.set_defaults(func=session_api_key_handler(handler))


def build_cli_parser(services: CliParserServices) -> argparse.ArgumentParser:
    parser = CielCtlArgumentParser(prog="ciel-runtimectl")
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
    _add_remainder_command(commands, "launch-grok", services.launch.launch_grok)
    _add_remainder_command(commands, "launch-zcode", services.launch.launch_zcode)
    _add_remainder_command(commands, "launch-muse", services.launch.launch_muse)
    commands.add_parser("serve").set_defaults(func=services.launch.serve)
    bridge = commands.add_parser("bridge")
    bridge.add_argument(
        "action",
        nargs="?",
        choices=("status", "enable", "disable", "token", "serve"),
        default="status",
    )
    bridge.add_argument("--host")
    bridge.set_defaults(func=services.launch.remote_bridge)
    commands.add_parser("version").set_defaults(func=services.runtime.version)
    commands.add_parser("status").set_defaults(func=services.runtime.status)
    commands.add_parser("env").set_defaults(func=services.runtime.env)
    commands.add_parser("stop").set_defaults(func=services.runtime.stop)
    restart_session = commands.add_parser("restart-session")
    restart_session.add_argument("--reason", default="")
    restart_session.add_argument("--runtime", default="")
    restart_session.add_argument("--pid", type=int, default=0)
    restart_session.add_argument("--workspace", default="")
    restart_session.add_argument("--no-resume", action="store_true")
    restart_session.set_defaults(func=services.runtime.restart_session)
    _add_optional_value_command(commands, "language", services.settings.language)
    _add_optional_value_command(commands, "web-search", services.settings.web_search)
    _add_optional_value_command(commands, "web-fetch", services.settings.web_fetch)
    _add_optional_value_command(commands, "log-level", services.settings.log_level)
    _add_values_command(commands, "external-events", services.settings.external_events)
    _add_values_command(commands, "transcript-events", services.settings.transcript_events)
    _add_values_command(commands, "usage-events", services.settings.usage_events)
    _add_values_command(commands, "usage-api-key", services.settings.usage_api_key)
    _add_values_command(commands, "remote-instructions", services.settings.remote_instructions)
    _add_values_command(commands, "remote-memory", services.settings.remote_memory)
    work = commands.add_parser("work", help="Inspect and operate Work/SSE for an explicit conversation")
    from .work_ctl import ACTIONS as work_actions
    work.add_argument("action", nargs="?", choices=("status", "list", "get", "set", *work_actions),
                      default="status")
    work.add_argument("key", nargs="?", help="setting key or action target id")
    work.add_argument("value", nargs="?", help="setting value, decision evidence, note or exact retry state")
    work.add_argument("--session", help="exact claude:<uuid> or codex:<id> conversation identity")
    work.add_argument("--json", action="store_true", help="emit one JSON object for reads and actions")
    work.add_argument("--confirm", action="store_true", help="confirm a release or possibly duplicate retry")
    work.add_argument("--expected-version", type=int)
    work.add_argument("--expected-finalization-version", type=int)
    work.add_argument("--expected-review-pin", help="review-approve/rework: the row's review_pin (rp1:<64 hex>)")
    work.add_argument("--preview-digest")
    work.add_argument("--attempt-ref")
    work.add_argument("--revision", type=int)
    work.add_argument("--clear-wait", action="append", default=[])
    work.add_argument("--activate", action="store_true")
    work.add_argument("--operation-id")
    work.add_argument("--expected-settings-fingerprint",
                      help="settings_fingerprint from `work status --json`; `work set` refuses if settings changed")
    work.set_defaults(func=services.work)
    _add_session_api_key_command(commands, services.session_api_key)
    _add_values_command(commands, "remote-provision", services.runtime.remote_provision)
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
    copilot_oauth = commands.add_parser("copilot-oauth")
    copilot_oauth.add_argument(
        "action",
        nargs="?",
        choices=("login", "status", "logout"),
        default="status",
    )
    copilot_oauth.set_defaults(func=services.provider.oauth["copilot"])
    zai_oauth = commands.add_parser("zai-oauth")
    zai_oauth.add_argument(
        "action",
        nargs="?",
        choices=("login", "import", "status", "logout"),
        default="status",
    )
    zai_oauth.add_argument("--no-browser", action="store_true")
    zai_oauth.add_argument(
        "--profile",
        choices=("coding-plan", "start-plan"),
        default="coding-plan",
    )
    zai_oauth.set_defaults(func=services.provider.oauth["zai"])
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
