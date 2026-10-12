"""Answers the CLI's startup questions after an unattended restart.

A machine that restarts its session on its own (``restart_session`` or a
reboot script running ``--ca-resume-last``) has nobody at the keyboard, so a
startup dialog would leave the session waiting forever.  The responder watches
the child's screen output (ANSI sequences and all whitespace removed, as in
terminal_modal_guard) for a dialog title, finds the wanted option's number on
screen ("2.leavepaused") and sends that digit.  Reading the number from the
screen keeps the answer right when a CLI version reorders the options.

The dialog texts are strings from the installed binaries (checked 2026-10-12:
codex.exe 0.158/0.161 and Claude Code 2.1.296); "Leave paused" was seen on the
screen of codex 0.162 (journal 2026/10/10 session-restart e2e-codex-remote).
"""

from __future__ import annotations

import codecs
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ciel_runtime_support.terminal_modal_guard import READY_MARKERS, _COMPLETE_SEQUENCE, _ESCAPE

STARTUP_AUTO_ANSWER_ENV = "CIEL_RUNTIME_STARTUP_AUTO_ANSWER"


@dataclass(frozen=True, slots=True)
class StartupPrompt:
    name: str
    # Compacted dialog text that identifies the dialog.
    trigger: str
    # Compacted label of the option to choose; its number is read from the screen.
    option: str
    # Codex's trust dialog only highlights on a digit; Enter accepts.
    enter: bool = False
    # Every option label of the dialog.  Claude Code draws its options without
    # numbers ("❯ No, exit"), so the responder moves the "❯" marker with arrow
    # keys from the drawn order and accepts with Enter.
    labels: tuple[str, ...] = ()


STARTUP_PROMPTS = (
    # Codex
    StartupPrompt("codex_paused_goal", "resumepausedgoal?", "leavepaused"),
    StartupPrompt("codex_trust", "trustthisfolder?codex", "trustandcontinue", enter=True),
    StartupPrompt("codex_update", "updateavailable", "skip"),
    StartupPrompt("codex_resume_cwd", "usecurrentdirectory", "usesessiondirectory"),
    StartupPrompt("codex_model_migration", "trynewmodel", "useexistingmodel"),
    StartupPrompt("codex_hooks_review", "continuewithouttrusting", "trustallandcontinue"),
    # Shown after a trust answer on Windows (codex 0.162.1). The admin choice
    # needs a UAC prompt nobody can answer and 3 quits; Enter follows in case
    # the digit only highlights (an Enter on the empty composer does nothing).
    StartupPrompt("codex_windows_sandbox", "setupthecodexagentsandbox", "usenon-adminsandbox", enter=True),
    # Claude Code (option labels in the order of the 2.1.296 binary; the drawn order decides)
    StartupPrompt("claude_trust", "accessingworkspace:", "yes,itrustthisfolder",
                  labels=("yes,itrustthisfolder", "no,continuewithoutthesepermissions", "no,exit")),
    StartupPrompt("claude_bypass_permissions", "bypasspermissionsmode", "yes,iaccept",
                  labels=("no,exit", "yes,iaccept")),
    StartupPrompt("claude_resume_summary", "resumefromsummary", "resumefullsessionas-is",
                  labels=("resumefromsummary(recommended)", "resumefullsessionas-is", "don'taskmeagain")),
    StartupPrompt("claude_project_mcp", "newmcpserverfoundinthisproject", "usethismcpserver",
                  labels=("usethismcpserver", "usethisandallfuturemcpserversinthisproject", "continuewithoutusingthismcpserver")),
    StartupPrompt("claude_external_imports", "allowexternalclaude.mdfileimports", "yes,allowexternalimports",
                  labels=("yes,allowexternalimports", "no,disableexternalimports")),
    StartupPrompt("claude_api_key", "detectedacustomapikey", "yes", labels=("yes", "no(recommended)")),
)
SELECTED_MARKERS = ("\u276f", "\u203a")  # ❯ (Claude Code), › (Codex)
ARROW_UP, ARROW_DOWN = b"\x1b[A", b"\x1b[B"

# Claude Code drops keys pressed within 150 ms of a dialog appearing (2.1.296);
# answer once the screen has been quiet a little longer than that.
SETTLE_SECONDS = 0.5
ENTER_DELAY_SECONDS = 0.3
# Options can render a moment after the title.
OPTION_WAIT_SECONDS = 5.0
MAX_ATTEMPTS = 3
# After the last answer, a quiet screen means the input screen is back even
# when a diff-rendering TUI does not redraw its footer.
QUIET_AFTER_ANSWER_SECONDS = 3.0
# Without any dialog or ready footer, input may go ahead after this, as before.
STARTUP_HOLD_SECONDS = 15.0
GIVE_UP_SECONDS = 180.0
_KEEP = 16384


def startup_auto_answer_enabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(STARTUP_AUTO_ANSWER_ENV) or "").strip() == "1"


@dataclass(slots=True)
class _Pending:
    prompt: StartupPrompt
    position: int
    seen_at: float
    attempts: int = 0


@dataclass(slots=True)
class StartupPromptResponder:
    log: Callable[[str, str], None] = lambda _level, _message: None
    clock: Callable[[], float] = time.monotonic
    prompts: tuple[StartupPrompt, ...] = STARTUP_PROMPTS
    started_at: float = 0.0
    _decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(errors="replace")
    )
    _pending_escape: str = ""
    _text: str = ""
    _offset: int = 0
    _output_at: float = 0.0
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _handled: dict[str, int] = field(default_factory=dict)
    _attempts: dict[str, int] = field(default_factory=dict)
    _enter_at: float = 0.0
    _answered_at: float = 0.0
    _last_dialog: int = -1
    _last_ready: int = -1
    answered: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = self.clock()

    def feed(self, data: bytes) -> None:
        if not data or self.clock() - self.started_at >= GIVE_UP_SECONDS:
            # Only startup dialogs: later output (the model's own text) is never read.
            return
        text = self._pending_escape + self._decoder.decode(data)
        cut = text.rfind("\x1b")
        if cut != -1 and len(text) - cut < 512 and not _COMPLETE_SEQUENCE.match(text, cut):
            text, self._pending_escape = text[:cut], text[cut:]
        else:
            self._pending_escape = ""
        compact = re.sub(r"\s+", "", _ESCAPE.sub("", text)).lower()
        if not compact:
            return
        now = self.clock()
        self._output_at = now
        search_from = max(0, len(self._text) - 64)
        self._text += compact
        for prompt in self.prompts:
            found = self._text.rfind(prompt.trigger, search_from)
            if found == -1:
                continue
            position = self._offset + found
            self._last_dialog = max(self._last_dialog, position)
            if position < self._handled.get(prompt.name, -1) or prompt.name in self._pending:
                continue
            if self._attempts.get(prompt.name, 0) >= MAX_ATTEMPTS:
                continue
            self._pending[prompt.name] = _Pending(prompt, position, now)
            self.log("INFO", f"startup_prompt_seen prompt={prompt.name}")
        for marker in READY_MARKERS:
            found = self._text.rfind(marker, search_from)
            if found != -1:
                self._last_ready = max(self._last_ready, self._offset + found)
        if len(self._text) > 2 * _KEEP:
            drop = len(self._text) - _KEEP
            self._text = self._text[drop:]
            self._offset += drop

    def _option_keys(self, pending: _Pending) -> bytes:
        """The keys that choose the option in the dialog's latest frame ("" when not drawn yet)."""

        prompt = pending.prompt
        latest = self._text.rfind(prompt.trigger)
        start = latest if latest != -1 else max(0, pending.position - self._offset)
        frame = self._text[start:]
        match = re.search(r"(\d)\." + re.escape(prompt.option), frame)
        if match:
            return match.group(1).encode("ascii")
        if not prompt.labels:
            return b""
        drawn = sorted((frame.find(label), label) for label in prompt.labels if frame.find(label) != -1)
        order = [label for _, label in drawn]
        if prompt.option not in order:
            return b""
        marked = max((frame.rfind(marker) for marker in SELECTED_MARKERS), default=-1)
        if marked == -1:
            return b""
        after = frame[marked + 1 :]
        current = next((label for label in order if after.startswith(label)), "")
        if not current:
            return b""
        steps = order.index(prompt.option) - order.index(current)
        return (ARROW_DOWN if steps > 0 else ARROW_UP) * abs(steps) + b"\r"

    def due(self) -> bytes:
        """Keys to send now (empty when nothing is due)."""

        now = self.clock()
        if self._enter_at and now >= self._enter_at:
            self._enter_at = 0.0
            self._answered_at = now
            return b"\r"
        if self._enter_at or now - self._output_at < SETTLE_SECONDS:
            return b""
        for name, pending in list(self._pending.items()):
            if now - pending.seen_at < SETTLE_SECONDS:
                continue
            keys = self._option_keys(pending)
            if not keys:
                if now - pending.seen_at < OPTION_WAIT_SECONDS:
                    continue
                del self._pending[name]
                self._handled[name] = self._offset + len(self._text)
                self.log("WARN", f"startup_prompt_option_not_found prompt={name} option={pending.prompt.option}")
                continue
            del self._pending[name]
            self._handled[name] = self._offset + len(self._text)
            self._attempts[name] = self._attempts.get(name, 0) + 1
            self._answered_at = now
            self.answered.append(name)
            if keys.isdigit():
                key = keys.decode("ascii")
                if pending.prompt.enter:
                    self._enter_at = now + ENTER_DELAY_SECONDS
            else:
                moves = keys.count(b"\x1b")
                key = f"{'down' if ARROW_DOWN in keys else 'up'}x{moves}+enter" if moves else "enter"
            self.log(
                "INFO",
                f"startup_prompt_answered prompt={name} option={pending.prompt.option} key={key} "
                f"enter={str(bool(self._enter_at)).lower()} attempt={self._attempts[name]}",
            )
            return keys
        return b""

    def input_ready(self) -> bool:
        """Whether typed input may go to the CLI (no dialog left on screen)."""

        now = self.clock()
        if now - self.started_at >= GIVE_UP_SECONDS:
            return True
        if self._pending or self._enter_at:
            return False
        if self._last_dialog < 0:
            return self._last_ready >= 0 or now - self.started_at >= STARTUP_HOLD_SECONDS
        if self._last_ready > self._last_dialog:
            return True
        return bool(self._answered_at) and now - max(self._answered_at, self._output_at) >= QUIET_AFTER_ANSWER_SECONDS


def startup_prompt_responder(
    log: Callable[[str, str], None],
    environ: dict[str, str] | None = None,
) -> StartupPromptResponder | None:
    """A responder when this launch restarts unattended, else None."""

    if not startup_auto_answer_enabled(environ):
        return None
    log("INFO", "startup_prompt_auto_answer_enabled")
    return StartupPromptResponder(log=log)


__all__ = [
    "STARTUP_AUTO_ANSWER_ENV",
    "STARTUP_PROMPTS",
    "StartupPrompt",
    "StartupPromptResponder",
    "startup_auto_answer_enabled",
    "startup_prompt_responder",
]
