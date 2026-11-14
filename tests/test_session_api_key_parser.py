"""ciel-runtimectl session-api-key parser: the key never comes from argv.

The parser is the only place argv is interpreted. These tests pin that no
positional or option carries a key, that a key typed on the command line by
mistake is refused without being echoed, and that the hidden prompt, --stdin,
--key-file - and --fd are the only ways a key reaches the session-key CLI.
"""
import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields

from ciel_runtime_support import cli_parser
from ciel_runtime_support.cli_assembly import CliParserAssembly

SECRET = "sk-test-NEVER-ON-ARGV-0123456789"
PHRASE = "RESET-TEST-PHRASE-0123"
SESSION = "claude:aaaaaaaa-1111-4111-8111-111111111111"
ALLOWED_DESTS = {"help", "action", "session", "provider", "ttl_seconds", "expected_version", "idempotency_key",
                 "stdin", "key_file", "fd", "json"}


def build(handler):
    noop = lambda _args: None  # noqa: E731
    services = cli_parser.CliParserServices(
        launch=cli_parser.CliParserLaunch(*(noop,) * 7), runtime=cli_parser.CliParserRuntime(*(noop,) * 5),
        settings=cli_parser.CliParserSettings(*(noop,) * 10),
        provider=cli_parser.CliParserProvider(*(noop,) * 9, {"copilot": noop, "zai": noop}),
        models=cli_parser.CliParserModels(noop, noop, noop), work=noop, session_api_key=handler)
    return cli_parser.build_cli_parser(services)


def subparser(parser):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices[cli_parser.SESSION_API_KEY_COMMAND]


class Recorder:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, args):
        self.calls.append(vars(args).copy())
        return self.code


class SessionApiKeyParserTests(unittest.TestCase):
    def run_argv(self, argv, handler=None):
        handler = handler or Recorder()
        parser = build(handler)
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            try:
                args = parser.parse_args(argv)
                args.func(args)
                code = 0
            except SystemExit as stop:
                code = stop.code
        return code, handler, err.getvalue() + out.getvalue()

    def test_every_action_and_option_parses_to_the_handler(self):
        for action in cli_parser.SESSION_API_KEY_ACTIONS:
            code, handler, _ = self.run_argv(["session-api-key", action])
            self.assertEqual(0, code)
            self.assertEqual(action, handler.calls[0]["action"])
        code, handler, _ = self.run_argv(["session-api-key"])
        self.assertEqual("list", handler.calls[0]["action"])
        code, handler, _ = self.run_argv([
            "session-api-key", "replace", "--session", SESSION, "--provider", "anthropic", "--ttl-seconds", "600",
            "--expected-version", "3", "--operation-id", "op-1", "--key-file", "-", "--json"])
        call = handler.calls[0]
        self.assertEqual((SESSION, "anthropic", 600, 3, "op-1", True, "-", True),
                         (call["session"], call["provider"], call["ttl_seconds"], call["expected_version"],
                          call["idempotency_key"], call["stdin"], call["key_file"], call["json"]))
        code, handler, _ = self.run_argv(["session-api-key", "inject", "--idempotency-key", "op-2", "--fd", "5"])
        self.assertEqual(("op-2", 5, False), (handler.calls[0]["idempotency_key"], handler.calls[0]["fd"],
                                              handler.calls[0]["stdin"]))

    def test_handler_result_is_the_exit_code(self):
        code, _, _ = self.run_argv(["session-api-key", "inspect", "--session", SESSION], Recorder(1))
        self.assertEqual(1, code)

    def test_no_option_or_positional_can_carry_a_key(self):
        command = subparser(build(Recorder()))
        dests = {action.dest for action in command._actions}
        self.assertEqual(ALLOWED_DESTS, dests)
        positionals = [action for action in command._actions if not action.option_strings]
        self.assertEqual(["action"], [action.dest for action in positionals])
        self.assertEqual(cli_parser.SESSION_API_KEY_ACTIONS, tuple(positionals[0].choices))
        key_file = next(action for action in command._actions if action.dest == "key_file")
        self.assertEqual(("-",), tuple(key_file.choices))
        for action in command._actions:
            for option in action.option_strings:
                self.assertNotRegex(option, r"(?i)(^--key$|api-?key$|secret|token|password)", option)

    def test_a_key_on_argv_is_refused_without_echo_and_reaches_nothing(self):
        attempts = [
            ["session-api-key", "inject", SECRET],
            ["session-api-key", "inject", "--session", SESSION, "--provider", "anthropic", SECRET],
            ["session-api-key", "inject", "--api-key", SECRET],
            ["session-api-key", "inject", "--api-key=" + SECRET],
            ["session-api-key", "inject", "--key", SECRET],
            ["session-api-key", "inject", "--key-file", SECRET],
            ["session-api-key", "inject", "--key-file=" + SECRET],
            ["session-api-key", "inject", "--secret", SECRET],
            ["session-api-key", SECRET],
            ["session-api-key", "inject", "--expected-version", SECRET],
            ["session-api-key", "inject", "--ttl-seconds", SECRET],
            ["session-api-key", "inject", "--stdin", "--key-file", "-", "--fd", "3", SECRET],
        ]
        for argv in attempts:
            with self.subTest(argv=argv[1:3]):
                code, handler, printed = self.run_argv(argv)
                self.assertEqual(2, code)
                self.assertEqual([], handler.calls)
                self.assertNotIn(SECRET, printed)
                self.assertIn(cli_parser.SESSION_API_KEY_ARGV_REFUSAL, printed)

    def test_option_abbreviations_are_not_accepted(self):
        for argv in (["session-api-key", "inject", "--sess", SESSION],
                     ["session-api-key", "inject", "--key-f", "-"]):
            code, handler, printed = self.run_argv(argv)
            self.assertEqual(2, code)
            self.assertEqual([], handler.calls)

    def test_only_one_key_source_may_be_named(self):
        code, handler, _ = self.run_argv(["session-api-key", "inject", "--stdin", "--fd", "4"])
        self.assertEqual((2, []), (code, handler.calls))

    def test_other_commands_still_report_their_own_errors(self):
        code, _, printed = self.run_argv(["work", "nope-action"])
        self.assertEqual(2, code)
        self.assertIn("nope-action", printed)
        self.assertNotIn(cli_parser.SESSION_API_KEY_ARGV_REFUSAL, printed)

    def test_reset_state_phrase_on_argv_is_refused_without_echo(self):
        for argv in (["session-api-key", "reset-state", PHRASE],
                     ["session-api-key", "reset-state", "--phrase", PHRASE],
                     ["session-api-key", "reset-state", "--phrase=" + PHRASE],
                     ["session-api-key", "reset-state", "--confirm", PHRASE],
                     ["session-api-key", "reset-state", "--stdin", PHRASE]):
            with self.subTest(argv=argv[2:3]):
                code, handler, printed = self.run_argv(argv)
                self.assertEqual(2, code)
                self.assertEqual([], handler.calls)
                self.assertNotIn(PHRASE, printed)
                self.assertIn(cli_parser.SESSION_API_KEY_ARGV_REFUSAL, printed)

    def test_parser_actions_match_the_session_key_cli(self):
        from ciel_runtime_support.session_api_key_cli import ACTIONS
        self.assertEqual(tuple(ACTIONS), cli_parser.SESSION_API_KEY_ACTIONS)

    def test_assembly_carries_the_handler(self):
        names = {field.name for field in fields(CliParserAssembly)}
        self.assertIn("session_api_key", names)
        self.assertIn("session_api_key", {field.name for field in fields(cli_parser.CliParserServices)})


class SessionApiKeySourcesTests(unittest.TestCase):
    """End to end through the parser into continue-pty's SessionApiKeyCli with a fake client."""

    def cli(self, argv, *, tty=False, stdin="", prompt="", fd_bytes=b""):
        from ciel_runtime_support.session_api_key_cli import SessionApiKeyCli, SessionApiKeyCliIO
        sent, lines = [], []

        class Client:
            def action(self, body, operation_id):
                sent.append((dict(body), operation_id))
                return {"action": body["action"], "was_unreadable": False,
                        "key": {"session": body.get("session"), "provider": body.get("provider"),
                                "source": "ctl", "version": 1, "fingerprint": "f" * 12}}

            def state(self):
                return {"sessions": [], "keys": [], "reset": {"confirm": PHRASE, "warning": "router-wide reset"}}

        io_ports = SessionApiKeyCliIO(write_line=lines.append, stdin_isatty=lambda: tty,
                                      prompt_secret=lambda _label: prompt, read_stdin=lambda _limit: stdin,
                                      read_fd=lambda _fd, _limit, data=[fd_bytes]: data.pop() if data else b"")
        handler = SessionApiKeyCli(lambda: Client(), io_ports).run
        parser = build(handler)
        args = parser.parse_args(argv)
        try:
            args.func(args)
        except SystemExit as stop:
            code = stop.code
        return code, sent, lines

    def test_hidden_prompt_stdin_key_file_and_fd_deliver_the_key_only_in_the_body(self):
        base = ["session-api-key", "inject", "--session", SESSION, "--provider", "anthropic", "--operation-id", "op"]
        for extra, kwargs in (([], {"tty": True, "prompt": SECRET}), (["--stdin"], {"stdin": SECRET + "\n"}),
                              (["--key-file", "-"], {"stdin": SECRET + "\n"}),
                              (["--fd", "7"], {"fd_bytes": SECRET.encode()})):
            with self.subTest(source=extra or "prompt"):
                code, sent, lines = self.cli(base + extra, **kwargs)
                self.assertEqual(0, code)
                self.assertEqual(SECRET, sent[0][0]["api_key"])
                self.assertEqual("op", sent[0][1])
                self.assertNotIn(SECRET, "\n".join(lines))

    def test_reset_state_phrase_from_stdin_or_hidden_prompt_reaches_only_the_body(self):
        base = ["session-api-key", "reset-state", "--operation-id", "op-reset"]
        for extra, kwargs in ((["--stdin"], {"stdin": PHRASE + "\n"}), ([], {"tty": True, "prompt": PHRASE})):
            with self.subTest(source=extra or "prompt"):
                code, sent, lines = self.cli(base + extra, **kwargs)
                self.assertEqual(0, code)
                self.assertEqual(({"action": "reset_state", "source": "ctl", "confirm": PHRASE}, "op-reset"),
                                 sent[0])
        code, sent, lines = self.cli(base + ["--stdin"], stdin="wrong phrase\n")
        self.assertEqual((1, []), (code, sent))
        self.assertIn("Not confirmed; nothing changed.", lines)

    def test_without_a_terminal_or_source_nothing_is_sent(self):
        code, sent, _ = self.cli(["session-api-key", "inject", "--session", SESSION, "--provider", "anthropic"])
        self.assertNotEqual(0, code)
        self.assertEqual([], sent)


if __name__ == "__main__":
    unittest.main()
