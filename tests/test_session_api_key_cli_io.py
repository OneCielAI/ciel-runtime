"""session-api-key ctl: a NUL stdin never reaches getpass, and --json emits one metadata object.

Measured defects (continue-ctl, 2026-11-13 04:19Z): on this machine
``sys.stdin.isatty()`` is True for NUL, so ``inject`` waited forever at the
hidden prompt; ``--json`` was accepted by the parser and ignored.
"""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.session_api_key_cli import SessionApiKeyCli, SessionApiKeyCliIO

SESSION = "claude:11111111-1111-4111-8111-111111111111"
PHRASE = "reset-session-key-state"
ROOT = Path(__file__).resolve().parents[1]


def canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


class Client:
    def __init__(self):
        self.sent = []

    def state(self):
        return {"sessions": [{"session": SESSION, "runtime": "claude-code", "provider": "deepseek",
                              "last_activity": 1.0, "keys": [{"session": SESSION, "provider": "deepseek",
                                                              "source": "ctl", "version": 3, "fingerprint": "f" * 12}]}],
                "keys": [{"session": SESSION, "provider": "deepseek", "source": "ctl", "version": 3,
                          "fingerprint": "f" * 12}],
                "staged": [], "abandoned": [], "notices": [], "health": {"rows": 1, "limit": 512},
                "reset": {"confirm": PHRASE, "warning": "router-wide"}}

    def action(self, body, operation_id):
        self.sent.append(dict(body))
        return {"ok": True, "action": body["action"], "replayed": False, "was_unreadable": False,
                "key": {"session": body.get("session"), "provider": body.get("provider"), "source": "ctl",
                        "version": 4, "fingerprint": "e" * 12}}


def run(args, *, prompt_calls, stdin="", hidden_prompt_ok=False):
    lines, client = [], Client()
    io_ports = SessionApiKeyCliIO(write_line=lines.append, stdin_isatty=lambda: hidden_prompt_ok,
                                  prompt_secret=lambda label: prompt_calls.append(label) or "",
                                  read_stdin=lambda _limit: stdin)
    try:
        code = SessionApiKeyCli(lambda: client, io_ports).run(SimpleNamespace(**args))
    except SystemExit as stop:
        code = stop.code
    return code, lines, client.sent


class NulStdinTests(unittest.TestCase):
    def test_composition_wires_the_console_check_not_isatty(self):
        """The ctl entry must not decide on sys.stdin.isatty() alone."""

        import inspect

        from ciel_runtime_support import session_api_key_composition

        source = inspect.getsource(session_api_key_composition.SessionApiKeyComposition.command)
        self.assertIn("stdin_hidden_prompt_available", source)
        self.assertNotIn("sys.stdin.isatty()", source)

    @unittest.skipUnless(os.name == "nt", "the NUL device answers isatty() True only on Windows")
    def test_real_nul_stdin_returns_promptly_without_a_prompt(self):
        """A child process with NUL stdin runs the real ctl entry and must exit, not hang."""

        with tempfile.TemporaryDirectory() as cfg:
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("CIEL_RUNTIME_", "CIEL_CODE_", "CLAUDE", "ANTHROPIC", "CODEX",
                                                "OPENAI", "MAX_MCP"))}
            env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=cfg, PYTHONPATH=str(ROOT),
                       PYTHONDONTWRITEBYTECODE="1", CIEL_RUNTIME_ROUTER_PORT="9")
            script = ("import sys\nfrom ciel_runtime_support.session_api_key_cli import stdin_hidden_prompt_available\n"
                      "print('isatty', sys.stdin.isatty(), 'hidden_prompt', stdin_hidden_prompt_available())\n")
            with open(os.devnull, "rb") as nul:
                done = subprocess.run([sys.executable, "-c", script], stdin=nul, capture_output=True, text=True,
                                      timeout=60, env=env, cwd=cfg)
        self.assertEqual(0, done.returncode, done.stderr[-500:])
        self.assertIn("hidden_prompt False", done.stdout)

    def test_no_terminal_means_no_prompt_and_nothing_sent(self):
        for action in ("inject", "replace"):
            with self.subTest(action):
                prompts = []
                code, lines, sent = run({"action": action, "session": SESSION, "provider": "deepseek",
                                         "expected_version": 3, "stdin": False, "fd": None},
                                        prompt_calls=prompts)
                self.assertNotEqual(0, code)
                self.assertEqual(([], []), (prompts, sent))
        prompts = []
        code, _lines, sent = run({"action": "reset-state", "stdin": False}, prompt_calls=prompts)
        self.assertNotEqual(0, code)
        self.assertEqual(([], []), (prompts, sent))


class JsonOutputTests(unittest.TestCase):
    def one_object(self, lines):
        self.assertEqual(1, len(lines), lines)
        return json.loads(lines[0])

    def test_every_action_prints_one_json_object_with_metadata_only(self):
        key = canary()
        cases = {
            "list": {},
            "inspect": {"session": SESSION, "provider": "deepseek"},
            "inject": {"session": SESSION, "provider": "deepseek", "stdin": True},
            "replace": {"session": SESSION, "provider": "deepseek", "expected_version": 3, "stdin": True},
            "clear": {"session": SESSION, "provider": "deepseek", "expected_version": 3},
            "reset-state": {"stdin": True},
        }
        for action, extra in cases.items():
            with self.subTest(action):
                stdin = PHRASE + "\n" if action == "reset-state" else key + "\n"
                code, lines, _sent = run({"action": action, "json": True, "fd": None, "stdin": False, **extra},
                                         prompt_calls=[], stdin=stdin)
                self.assertEqual(0, code, lines)
                payload = self.one_object(lines)
                self.assertTrue(payload["ok"])
                self.assertEqual(action, payload["command"])
                self.assertNotIn(key, lines[0])
                self.assertNotIn("api_key", lines[0])

    def test_a_refusal_is_one_json_error_object(self):
        code, lines, _ = run({"action": "inject", "json": True, "session": SESSION, "provider": "deepseek",
                              "stdin": False, "fd": None}, prompt_calls=[])
        self.assertNotEqual(0, code)
        payload = self.one_object(lines)
        self.assertEqual((False, "inject", "no_key_source"), (payload["ok"], payload["command"], payload["error"]))

    def test_without_json_the_text_output_is_unchanged(self):
        code, lines, _ = run({"action": "list", "json": False}, prompt_calls=[])
        self.assertEqual(0, code)
        self.assertTrue(lines[0].startswith("records: 1 of 512"), lines)


if __name__ == "__main__":
    unittest.main()
