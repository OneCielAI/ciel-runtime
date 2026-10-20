"""Unattended restart: startup dialogs answered, --ca-resume-last, the continue message."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import unattended_restart_menu
from ciel_runtime_support.codex_app_server_resume import AppServerResume, resolve_resume_thread
from ciel_runtime_support.codex_cli import codex_without_resume_command
from ciel_runtime_support.session_resume_last import (
    RESUME_LAST_ENV,
    claude_has_conversation,
    enable_resume_last,
    resume_last_passthrough,
)
from ciel_runtime_support.startup_prompt_answers import (
    STARTUP_AUTO_ANSWER_ENV,
    StartupPromptResponder,
    startup_prompt_responder,
    viewport_row_text,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def screen(*lines: str) -> bytes:
    # The TUIs place words with cursor moves; the responder ignores them and spaces.
    return "".join(f"\x1b[{row};3H{text}" for row, text in enumerate(lines, start=1)).encode("utf-8")


class TerminalStartupGateTests(unittest.TestCase):
    def test_default_open_and_scoped_state_is_fresh_and_restored(self):
        from ciel_runtime_support.terminal_startup_gate import (
            TerminalStartupGate, current_terminal_startup_gate, terminal_startup_scope,
        )
        self.assertTrue(TerminalStartupGate().ready())
        self.assertIsNone(current_terminal_startup_gate())
        first = TerminalStartupGate(require_screen=True)
        with terminal_startup_scope(first):
            self.assertIs(first, current_terminal_startup_gate())
            first.observe(rendered_ready=False)
            self.assertFalse(first.ready())
            first.observe(rendered_ready=True)
            self.assertTrue(first.ready())
            with terminal_startup_scope(TerminalStartupGate(require_screen=True)):
                self.assertFalse(current_terminal_startup_gate().ready())
            self.assertIs(first, current_terminal_startup_gate())
        self.assertIsNone(current_terminal_startup_gate())

    def test_closed_gate_does_not_even_poll_competing_restart(self):
        from ciel_runtime_support.channel_terminal_proxy import _runtime_session_restart_requested
        from ciel_runtime_support.terminal_startup_gate import TerminalStartupGate, terminal_startup_scope
        poll, state = mock.Mock(return_value=None), mock.Mock()
        gate = TerminalStartupGate(require_screen=True)
        with terminal_startup_scope(gate):
            self.assertFalse(_runtime_session_restart_requested(lambda *_: None, poll, state, transport="conpty", pid=1))
            poll.assert_not_called()
            state.mark.assert_not_called()
            gate.observe(rendered_ready=True)
            self.assertFalse(_runtime_session_restart_requested(lambda *_: None, poll, state, transport="conpty", pid=1))
            poll.assert_called_once_with()


class StartupPromptResponderTests(unittest.TestCase):
    def responder(self) -> tuple[StartupPromptResponder, Clock, list[str]]:
        clock = Clock()
        logs: list[str] = []
        return StartupPromptResponder(log=lambda level, message: logs.append(f"{level} {message}"), clock=clock), clock, logs

    def goal_frame(self, selected="resume", reversed=False):
        rows = [("Resume goal", "resume"), ("Leave paused", "leave")]
        if reversed:
            rows.reverse()
        return "Resume paused goal?\n" + "\n".join(
            f"{'›' if key == selected else ' '} {i}. {label}"
            for i, (label, key) in enumerate(rows, 1)
        ) + "\nenter select · esc back\n? for shortcuts"

    def rendered(self, responder, frame):
        responder.observe_screen(frame)

    def test_rendered_readiness_never_uses_silence_or_stream_footer(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("? for shortcuts"))
        clock.now += 181
        self.assertFalse(responder.rendered_input_ready())
        responder.observe_screen("loading")
        self.assertFalse(responder.rendered_input_ready())
        responder.observe_screen("Ask Codex to do anything\n? for shortcuts")
        self.assertFalse(responder.rendered_input_ready())
        clock.now += 0.6
        self.assertTrue(responder.rendered_input_ready())

    def test_ready_footer_is_stable_across_unrelated_redraws(self):
        responder, clock, _ = self.responder()
        frame = "noise 1" + chr(10) + "Ask Codex to do anything" + chr(10) + "? for shortcuts"
        responder.observe_screen(frame)
        for tick in range(1, 7):
            clock.now += 0.1
            responder.observe_screen("noise " + str(tick + 1) + chr(10) + "Ask Codex to do anything" + chr(10) + "? for shortcuts")
        self.assertTrue(responder.rendered_input_ready())

    def test_ready_footer_change_restarts_the_settle_window(self):
        responder, clock, _ = self.responder()
        ready = "Ask Codex to do anything" + chr(10) + "? for shortcuts"
        responder.observe_screen(ready)
        self.assertFalse(responder.rendered_input_ready())
        clock.now += 0.4
        responder.observe_screen("still loading")
        self.assertFalse(responder.rendered_input_ready())
        clock.now += 0.4
        responder.observe_screen(ready)
        self.assertFalse(responder.rendered_input_ready())
        clock.now += 0.6
        self.assertTrue(responder.rendered_input_ready())

    def test_modal_stability_ignores_unrelated_redraw_but_not_selected_row(self):
        responder, clock, _ = self.responder()
        responder.observe_screen("clock 1\n" + self.goal_frame())
        for tick in range(1, 7):
            clock.now += 0.1
            responder.observe_screen(f"clock {tick + 1}\n" + self.goal_frame())
        self.assertEqual(b"2", responder.due())
        responder.observe_screen("clock 8\n" + self.goal_frame("leave"))
        self.assertEqual(b"", responder.due())
        clock.now += 0.6
        self.assertEqual(bytes([13]), responder.due())
        self.assertFalse(responder.rendered_input_ready())

    def test_codex_paused_goal_is_left_paused(self):
        responder, clock, logs = self.responder()
        self.rendered(responder, self.goal_frame())
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        self.assertFalse(responder.input_ready())
        self.rendered(responder, self.goal_frame("leave"))
        clock.now += 0.6
        self.assertEqual(bytes([13]), responder.due())
        self.assertFalse(responder.input_ready())
        self.rendered(responder, "Ask Codex to do anything\n? for shortcuts")
        clock.now += 0.6
        self.assertEqual(b"", responder.due())
        self.assertTrue(responder.input_ready())
        self.assertTrue(any("startup_prompt_dismissed" in line for line in logs))

    def test_digit_ignored_never_confirms_resume_default(self):
        responder, clock, _ = self.responder()
        responder.observe_screen(self.goal_frame())
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        clock.now += 0.6
        self.assertEqual(b"", responder.due())
        clock.now += 5
        self.assertEqual(bytes([27]) + b"[B", responder.due())
        responder.observe_screen(self.goal_frame("leave"))
        clock.now += 0.6
        self.assertEqual(bytes([13]), responder.due())

    def test_reordered_options_selects_label_not_fixed_digit(self):
        responder, clock, _ = self.responder()
        responder.observe_screen(self.goal_frame(reversed=True))
        clock.now += 0.6
        self.assertEqual(b"1", responder.due())
        responder.observe_screen(self.goal_frame("leave", reversed=True))
        clock.now += 0.6
        self.assertEqual(bytes([13]), responder.due())

    def test_background_footer_does_not_prove_dismissal(self):
        responder, clock, _ = self.responder()
        responder.observe_screen(self.goal_frame("leave"))
        clock.now += 0.6
        self.assertEqual(bytes([13]), responder.due())
        clock.now += 181
        self.assertEqual(b"", responder.due())
        self.assertFalse(responder.input_ready())

    def test_digit_directly_dismisses_no_enter_leaks_to_composer(self):
        responder, clock, _ = self.responder()
        responder.observe_screen(self.goal_frame())
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        responder.observe_screen("? for shortcuts")
        self.assertEqual(b"", responder.due())
        self.assertFalse(responder.input_ready())  # wait for stable rendered state
        clock.now += 0.6
        self.assertEqual(b"", responder.due())
        self.assertTrue(responder.input_ready())

    def test_missing_rendered_screen_keeps_gate_closed(self):
        responder, clock, logs = self.responder()
        responder.screen_snapshot = lambda: None
        clock.now += 16
        self.assertEqual(b"", responder.due())
        self.assertFalse(responder.input_ready())
        self.assertTrue(any("rendered_screen_unavailable" in line for line in logs))

    def test_delayed_trust_enter_cancelled_when_goal_replaces_it(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("Trust this folder? Codex", "› 1. Trust and continue", "2. Quit"))
        clock.now += 0.6
        self.assertEqual(b"1", responder.due())
        responder.observe_screen(self.goal_frame())
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        responder.observe_screen("? for shortcuts")
        clock.now += 0.6
        self.assertEqual(b"", responder.due())
        self.assertTrue(responder.input_ready())

    def test_stream_only_is_not_rendered_state_proof(self):
        responder, clock, logs = self.responder()
        responder.feed(screen("Resume paused goal?", "› 1. Resume goal", "2. Leave paused", "? for shortcuts"))
        clock.now += 0.6
        self.assertEqual(b"", responder.due())
        clock.now += 181
        self.assertFalse(responder.input_ready())
        self.assertTrue(any("rendered_screen_unavailable" in line for line in logs))

    def test_chain_dialog_does_not_receive_goal_confirmation(self):
        responder, clock, _ = self.responder()
        responder.observe_screen(self.goal_frame())
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        responder.observe_screen("Update available\n› 1. Update now\n2. Skip")
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())  # update's own answer, not Enter
        self.assertFalse(responder.input_ready())
        responder.observe_screen("? for shortcuts")
        clock.now += 0.6
        responder.due()
        self.assertTrue(responder.input_ready())

    def test_the_option_number_comes_from_the_screen(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("Accessing workspace:", "G:\\work", "Quick safety check: ...",
                              "❯ 1. No, exit", "  2. No, continue without these permissions", "  3. Yes, I trust this folder"))
        clock.now += 0.6
        self.assertEqual(b"3", responder.due())

    def test_claude_trust_dialog_default_order(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("Accessing workspace:", "❯ 1. Yes, I trust this folder", "  2. No, exit", "Enter to confirm · Esc to cancel"))
        clock.now += 0.6
        self.assertEqual(b"1", responder.due())

    def test_codex_trust_needs_enter_after_the_digit(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("Trust this folder? Codex can read, edit, and run files here", "› 1. Trust and continue", "  2. Quit"))
        clock.now += 0.6
        self.assertEqual(b"1", responder.due())
        self.assertFalse(responder.input_ready())
        clock.now += 0.31
        self.assertEqual(b"\r", responder.due())

    def test_codex_update_prompt_is_skipped(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("✨ Update available! 0.161.0 -> 0.162.0", "› 1. Update now (runs `npm install -g @openai/codex`)",
                              "  2. Skip", "  3. Skip until next version"))
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())

    def test_claude_unnumbered_trust_dialog_moves_the_marker_and_accepts(self):
        # Claude Code 2.1.296 on screen (E2E 2026-10-12): no option numbers, "❯" on the default.
        responder, clock, logs = self.responder()
        responder.feed(screen("Accessing workspace:", "G:\\ws", "Quick safety check: Is this a project you created",
                              "Security guide", "❯ No, exit", "  Yes, I trust this folder", "Enter to confirm · Esc to cancel"))
        clock.now += 0.6
        self.assertEqual(b"\x1b[B\r", responder.due())
        self.assertTrue(any("prompt=claude_trust option=yes,itrustthisfolder key=downx1+enter" in line for line in logs))

    def test_claude_marker_already_on_the_option_just_accepts(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("WARNING: Claude Code running in Bypass Permissions mode", "  No, exit", "❯ Yes, I accept"))
        clock.now += 0.6
        self.assertEqual(b"\r", responder.due())

    def test_claude_option_above_the_marker_moves_up(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("Accessing workspace:", "  Yes, I trust this folder", "  No, continue without these permissions",
                              "❯ No, exit"))
        clock.now += 0.6
        self.assertEqual(b"\x1b[A\x1b[A\r", responder.due())

    def test_codex_windows_sandbox_setup_takes_the_non_admin_choice(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("Set up the Codex agent sandbox to protect your files and control network access.",
                              "› 1. Set up default sandbox (requires Administrator permissions)",
                              "  2. Use non-admin sandbox (higher risk if prompt injected)", "  3. Quit", "enter select · esc back"))
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        clock.now += 0.31
        self.assertEqual(b"\r", responder.due())

    def test_claude_bypass_and_resume_summary(self):
        responder, clock, _ = self.responder()
        responder.feed(screen("WARNING: Claude Code running in Bypass Permissions mode", "❯ 1. No, exit", "  2. Yes, I accept"))
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())
        responder.feed(screen("This session is 3h old and 250k tokens.", "❯ 1. Resume from summary (recommended)",
                              "  2. Resume full session as-is", "  3. Don't ask me again"))
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())

    def test_retry_limit_keeps_unresolved_modal_held(self):
        responder, clock, logs = self.responder()
        responder.observe_screen(self.goal_frame())
        keys = []
        for _ in range(6):
            clock.now += 5.1
            keys.append(responder.due())
        self.assertEqual([b"2", bytes([27]) + b"[B", bytes([27]) + b"[B", b"", b"", b""], keys)
        self.assertFalse(responder.input_ready())
        self.assertEqual(1, sum("reason=retry_limit" in line for line in logs))

    def test_missing_goal_option_keeps_input_held(self):
        responder, clock, logs = self.responder()
        responder.feed(screen("Resume paused goal?", "something else"))
        clock.now += 5.1
        self.assertEqual(b"", responder.due())
        self.assertTrue(any("rendered_screen_unavailable" in line for line in logs))
        self.assertFalse(responder.input_ready())

    def test_input_waits_for_the_ready_footer_or_the_hold(self):
        responder, clock, _ = self.responder()
        self.assertFalse(responder.input_ready())
        responder.feed(screen("› Ask Codex to do anything", "? for shortcuts"))
        self.assertTrue(responder.input_ready())
        other, other_clock, _ = self.responder()
        other_clock.now += 15.0
        self.assertTrue(other.input_ready())

    def test_output_after_the_startup_window_is_never_read(self):
        responder, clock, _ = self.responder()
        clock.now += 181.0
        responder.feed(screen("Resume paused goal?", "1. Resume goal", "2. Leave paused"))
        clock.now += 1.0
        self.assertEqual(b"", responder.due())

    def test_wide_glyph_trailing_cell_is_not_a_second_character(self):
        # ReadConsoleOutputW cells measured in Windows Terminal and conhost (journal 2026/10/16
        # unattended-restart screen-reader-probe): a Hangul glyph fills a 0x100 and a 0x200 cell.
        cells = [("ㄱ", 0x100), ("ㄱ", 0x200), (" ", 0), ("계", 0x107), ("계", 0x207), ("-", 7), (" ", 7), (" ", 7)]
        self.assertEqual("ㄱ 계-  ", viewport_row_text(cells))

    @unittest.skipUnless(os.name == "nt", "requires a native Windows console")
    def test_native_viewport_with_korean_row_still_shows_the_goal_modal(self):
        import json
        import subprocess
        import sys

        frame = ["› 계속-세션재시작됨", "  Resume paused goal?", "› 1. Resume goal   Mark it active",
                 "  2. Leave paused  Keep it paused; use /goal resume later", "  enter select · esc back"]
        child = (
            "import json,sys,time; sys.path.insert(0, sys.argv[1]); "
            "from ciel_runtime_support.startup_prompt_answers import windows_startup_screen; "
            "sys.stdout.write('\\n'.join(json.loads(sys.argv[3])) + '\\n'); sys.stdout.flush(); time.sleep(0.5); "
            "open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(windows_startup_screen()))"
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screen.json"
            # A console of its own (no window), so the viewport is this frame, not the test runner's.
            # No std handles are passed: the child's stdout must be that console, not the runner's pipe.
            completed = subprocess.run(
                [sys.executable, "-c", child, str(Path(__file__).resolve().parents[1]), str(out), json.dumps(frame)],
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
            )
            self.assertEqual(0, completed.returncode)
            snapshot = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNotNone(snapshot)
        self.assertEqual(frame, [row.rstrip() for row in snapshot.split("\n")[: len(frame)]])
        responder, clock, _ = self.responder()
        responder.screen_snapshot = lambda: snapshot
        responder.due()
        clock.now += 0.6
        self.assertEqual(b"2", responder.due())

    def test_responder_only_for_unattended_launches(self):
        self.assertIsNone(startup_prompt_responder(lambda *_: None, {}))
        self.assertIsNotNone(startup_prompt_responder(lambda *_: None, {STARTUP_AUTO_ANSWER_ENV: "1"}))
        with mock.patch.dict(os.environ, {STARTUP_AUTO_ANSWER_ENV: "1"}):
            self.assertIsNone(startup_prompt_responder(lambda *_: None, {}))
            self.assertIsNotNone(startup_prompt_responder(lambda *_: None))


class ResumeLastTests(unittest.TestCase):
    def test_enable_marks_resume_last_and_auto_answer(self):
        environ: dict[str, str] = {}
        enable_resume_last(environ)
        self.assertEqual({RESUME_LAST_ENV: "1", STARTUP_AUTO_ANSWER_ENV: "1"}, environ)

    def test_claude_continues_only_when_the_folder_has_a_conversation(self):
        with tempfile.TemporaryDirectory() as root:
            home, cwd = Path(root) / "home", Path(root) / "work dir"
            self.assertFalse(claude_has_conversation(cwd, {}, home))
            self.assertEqual(["--model", "x"], resume_last_passthrough(["--model", "x"], "claude", cwd=cwd, environ={}, home=home))
            folder = home / ".claude" / "projects" / ("".join(c if c.isalnum() or c in "._-" else "-" for c in str(cwd)))
            folder.mkdir(parents=True)
            (folder / "abc.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertTrue(claude_has_conversation(cwd, {}, home))
            self.assertEqual(["--continue", "--model", "x"],
                             resume_last_passthrough(["--model", "x"], "claude", cwd=cwd, environ={}, home=home))

    def test_codex_runtimes_get_continue_unless_a_session_is_chosen(self):
        for runtime in ("codex", "codex-remote"):
            self.assertEqual(["--continue"], resume_last_passthrough([], runtime, cwd=Path("."), environ={}, home=Path(".")))
            self.assertEqual(["resume", "abc"], resume_last_passthrough(["resume", "abc"], runtime, cwd=Path("."), environ={}, home=Path(".")))

    def test_codex_without_resume_command(self):
        self.assertEqual(["-c", "a=b"], codex_without_resume_command(["-c", "a=b", "resume"]))
        self.assertEqual(["exec", "x"], codex_without_resume_command(["exec", "x"]))

    def test_app_server_last_without_a_thread_opens_a_new_one_only_when_unattended(self):
        resume = AppServerResume("last")
        select = mock.Mock(return_value=None)
        self.assertIsNone(resolve_resume_thread(select, resume, {}, Path(".")))
        self.assertEqual("", resolve_resume_thread(select, resume, {RESUME_LAST_ENV: "1"}, Path(".")))
        found = mock.Mock(return_value="thread-1")
        self.assertEqual("thread-1", resolve_resume_thread(found, resume, {RESUME_LAST_ENV: "1"}, Path(".")))


class ContinueMessageTests(unittest.TestCase):
    def test_typed_into_the_prompt_as_is(self):
        from ciel_runtime_support.channel_message_policy import message_input_transport, message_raw_injection
        from ciel_runtime_support.runtime_session_restart import restart_notice_body

        body = restart_notice_body("claude", source="resume-last")
        self.assertEqual("계속-세션재시작됨", body["message"])
        self.assertTrue(message_raw_injection(body))
        self.assertEqual("tty", message_input_transport(body))


class PrimedNoticeTests(unittest.TestCase):
    def test_cursor_is_initialised_before_the_notice_is_queued(self):
        from ciel_runtime_support.runtime_session_restart import primed_notice

        calls: list[str] = []
        notice = primed_notice(lambda: calls.append("cursor"), lambda body: calls.append(body["message"]) or {"id": 7})
        self.assertEqual({"id": 7}, notice({"message": "계속-세션재시작됨"}))
        self.assertEqual(["cursor", "계속-세션재시작됨"], calls)


class UnattendedRestartMenuTests(unittest.TestCase):
    def test_panel_writes_the_script_for_this_folder(self):
        with tempfile.TemporaryDirectory() as root:
            state, cwd = Path(root) / "state", Path(root) / "work"
            rows, values = unattended_restart_menu.panel_rows(state, cwd)
            self.assertIn("--ca-resume-last", rows[0])
            self.assertIn("not written", rows[values.index("write")])
            self.assertEqual("no script", unattended_restart_menu.summary(state))
            messages = unattended_restart_menu.apply(state, cwd, "write")
            path = unattended_restart_menu.script_path(state)
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn(str(cwd), text)
            self.assertIn("ciel-runtime --ca-resume-last", text)
            self.assertIn(str(path), messages[0])
            self.assertIn(path.name, unattended_restart_menu.summary(state))


class CliDispatchResumeLastTests(unittest.TestCase):
    def test_flag_selects_last_runtime_without_menu_or_update_checks(self):
        from ciel_runtime_support.cli_dispatch import dispatch_cli

        launched = {}
        services = mock.MagicMock()
        services.runtime.last_launch_runtime.return_value = "codex"
        services.runtime.codex_passthrough_has_command.return_value = False
        services.runtime.agy_passthrough_has_command.return_value = False
        services.core.pop_headless_env_file_args.side_effect = lambda argv: argv
        services.configuration.apply_headless_env_config.return_value = (False, None, None, None, False)

        def launch_codex(passthrough, **kwargs):
            launched.update(passthrough=passthrough, **kwargs)
            return 0

        services.runtime.launch_codex.side_effect = launch_codex
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RESUME_LAST_ENV, None)
            os.environ.pop(STARTUP_AUTO_ANSWER_ENV, None)
            rc = dispatch_cli(["--ca-resume-last"], services)
            self.assertEqual("1", os.environ.get(RESUME_LAST_ENV))
            self.assertEqual("1", os.environ.get(STARTUP_AUTO_ANSWER_ENV))
        self.assertEqual(0, rc)
        self.assertEqual(["--continue"], launched["passthrough"])
        self.assertTrue(launched["skip_menu"])
        self.assertFalse(launched["update_check"])
        self.assertFalse(launched["self_update_check"])


if __name__ == "__main__":
    unittest.main()
