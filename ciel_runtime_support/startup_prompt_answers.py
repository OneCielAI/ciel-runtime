"""Answers the CLI's startup questions after an unattended restart.

A machine that restarts unattended has nobody at the keyboard. Legacy dialogs
use the output-stream rules below. The paused-goal modal instead uses a current
rendered viewport: select Leave paused, confirm only its selected row, and hold
channel input until the modal is absent and a ready composer is stably visible.
Silence, a background footer, and an append-only output tail prove none of this.

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
    # Legacy dialogs which need Enter after a digit. Paused-goal uses the
    # rendered-state controller instead of this delayed Enter path.
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
# Legacy dialogs retain their historical quiet fallback. Paused-goal never
# releases on silence: current rendered absence plus readiness is required.
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
    phase: str = "select"
    acted_at: float = 0.0
    warned: bool = False


@dataclass(slots=True)
class StartupPromptResponder:
    log: Callable[[str, str], None] = lambda _level, _message: None
    clock: Callable[[], float] = time.monotonic
    prompts: tuple[StartupPrompt, ...] = STARTUP_PROMPTS
    started_at: float = 0.0
    screen_snapshot: Callable[[], str | None] | None = None
    _goal_unresolved: bool = False
    _screen: str | None = None
    _screen_changed_at: float = 0.0
    _goal_signature: tuple[str, ...] = ()
    _goal_changed_at: float = 0.0
    _ready_signature: tuple[str, ...] = ()
    _ready_changed_at: float = 0.0
    _screen_unavailable_logged: bool = False
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
    _enter_prompt: str = ""
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
            if self._enter_at and prompt.trigger != self._enter_prompt:
                self._enter_at = 0.0
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
        if self._screen is not None:
            compact = re.sub(r"\s+", "", self._screen).lower()
            latest = compact.rfind(prompt.trigger)
            if latest < 0:
                return b""
            frame = compact[latest:]
        else:
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
        return (ARROW_DOWN if steps > 0 else ARROW_UP) * abs(steps) + bytes([13])

    def observe_screen(self, snapshot: str | None) -> None:
        """Replace current rendered state; an output tail is not a screen."""
        compact = re.sub(r"\s+", "", snapshot or "").lower()
        modal = compact[compact.rfind("resumepausedgoal?"):] if "resumepausedgoal?" in compact else ""
        signature = ("resumepausedgoal?", *re.findall(r"[›❯]?[1-9]\.(?:leavepaused|resumegoal)", modal)) if modal else ()
        if signature != self._goal_signature:
            self._goal_signature = signature
            self._goal_changed_at = self.clock()
        ready = tuple(marker for marker in READY_MARKERS if marker in compact)
        blockers = tuple(prompt.trigger for prompt in self.prompts if prompt.trigger in compact)
        ready_signature = (*ready, *blockers) if snapshot is not None else ("unavailable",)
        if ready_signature != self._ready_signature:
            self._ready_signature = ready_signature
            self._ready_changed_at = self.clock()
        changed = snapshot != self._screen
        if changed:
            self._screen_changed_at = self.clock()
        self._screen = snapshot
        if snapshot is not None and changed and self.clock() - self.started_at < GIVE_UP_SECONDS:
            compact = re.sub(r"\s+", "", snapshot).lower()
            for prompt in self.prompts:
                if prompt.trigger in compact and prompt.name not in self._pending and self._attempts.get(prompt.name, 0) < MAX_ATTEMPTS:
                    self._pending[prompt.name] = _Pending(prompt, 0, self.clock())
            if "resumepausedgoal?" in compact:
                self._goal_unresolved = True

    def _paused_goal_due(self, pending: _Pending, now: float) -> bytes:
        self._goal_unresolved = True
        self._enter_at = 0.0
        if self._screen is None:
            if not pending.warned:
                self.log("WARN", "startup_prompt_unresolved prompt=codex_paused_goal reason=rendered_screen_unavailable input_held=true")
                pending.warned = True
            return b""
        compact = re.sub(r"\s+", "", self._screen).lower()
        if pending.prompt.trigger not in compact:
            # Absence alone (blank screen or next redraw) is not composer readiness.
            if now - self._ready_changed_at >= SETTLE_SECONDS and any(marker in compact for marker in READY_MARKERS) and not any(p.trigger in compact for p in self.prompts):
                self._goal_unresolved = False
                del self._pending[pending.prompt.name]
                self._handled[pending.prompt.name] = self._offset + len(self._text)
                self._last_dialog = -1
                self._last_ready = self._offset + len(self._text)
                self.answered.append(pending.prompt.name)
                self.log("INFO", "startup_prompt_dismissed prompt=codex_paused_goal input_released=true")
            return b""
        if now - max(self._goal_changed_at, pending.acted_at) < SETTLE_SECONDS:
            return b""
        compact = compact[compact.rfind(pending.prompt.trigger):]
        selected = re.search(r"[›❯]([1-9])\.(leavepaused|resumegoal)", compact)
        target = re.search(r"([1-9])\.leavepaused", compact)
        other = re.search(r"([1-9])\.resumegoal", compact)
        if not target or not other or not selected:
            return b""
        if pending.phase == "dismiss" and now - pending.acted_at < OPTION_WAIT_SECONDS:
            return b""
        if pending.attempts >= MAX_ATTEMPTS:
            if not pending.warned:
                self.log("WARN", "startup_prompt_unresolved prompt=codex_paused_goal reason=retry_limit input_held=true")
                pending.warned = True
            return b""
        if now - self.started_at >= GIVE_UP_SECONDS:
            return b""
        if pending.phase == "select" and pending.attempts and now - pending.acted_at < OPTION_WAIT_SECONDS and selected.group(2) != "leavepaused":
            return b""
        pending.attempts += 1
        pending.acted_at = now
        if selected.group(2) == "leavepaused":
            pending.phase = "dismiss"
            self.log("INFO", "startup_prompt_confirm prompt=codex_paused_goal option=leavepaused")
            return bytes([13])
        pending.phase = "select"
        # Try the displayed digit, then one arrow from the observed Resume row.
        # Never combine navigation and Enter in an unverified batch.
        keys = target.group(1).encode("ascii") if pending.attempts == 1 else (ARROW_DOWN if int(target.group(1)) > int(other.group(1)) else ARROW_UP)
        self.log("INFO", f"startup_prompt_select prompt=codex_paused_goal attempt={pending.attempts}")
        return keys

    def due(self) -> bytes:
        """Keys to send now (empty when nothing is due)."""

        now = self.clock()
        if self.screen_snapshot is not None:
            try:
                self.observe_screen(self.screen_snapshot())
            except (OSError, ValueError):
                self.observe_screen(None)
            if self._screen is None and not self._screen_unavailable_logged:
                self._screen_unavailable_logged = True
                self.log("WARN", "startup_prompt_auto_answer_unavailable reason=rendered_screen_unavailable input_held=true")
        goal = self._pending.get("codex_paused_goal")
        if goal is not None:
            keys = self._paused_goal_due(goal, now)
            if keys or "resumepausedgoal?" in re.sub(r"\s+", "", self._screen or "").lower():
                return keys
        if now - self.started_at >= GIVE_UP_SECONDS:
            return b""
        if self.screen_snapshot is not None and self._screen is None:
            return b""
        if self._enter_at and self._screen is not None:
            # A delayed Enter may not accept a different chained dialog.
            if self._enter_prompt not in re.sub(r"\s+", "", self._screen).lower():
                self._enter_at = 0.0
        if self._enter_at and now >= self._enter_at:
            self._enter_at = 0.0
            self._answered_at = now
            return b"\r"
        if self._enter_at or now - self._output_at < SETTLE_SECONDS:
            return b""
        for name, pending in list(self._pending.items()):
            if name == "codex_paused_goal":
                continue
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
                    self._enter_prompt = pending.prompt.trigger
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

    def rendered_input_ready(self) -> bool:
        """Strict startup proof for a second RPC client, never silence-based."""
        compact = re.sub(r"\s+", "", self._screen or "").lower()
        return bool(
            self._screen is not None
            and self.clock() - self._ready_changed_at >= SETTLE_SECONDS
            and any(marker in compact for marker in READY_MARKERS)
            and not any(prompt.trigger in compact for prompt in self.prompts)
            and not self._pending and not self._enter_at and not self._goal_unresolved
        )

    def input_ready(self) -> bool:
        """Whether typed input may go to the CLI (no dialog left on screen)."""

        now = self.clock()
        if self.screen_snapshot is not None and self._screen is None:
            return False
        if self._goal_unresolved or "codex_paused_goal" in self._pending:
            return False
        if self._screen is not None and any(p.trigger in re.sub(r"\s+", "", self._screen).lower() for p in self.prompts):
            return False
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


# CHAR_INFO attribute bits of a wide (two-cell) glyph's halves.
_LEADING_CELL, _TRAILING_CELL = 0x0100, 0x0200


def viewport_row_text(cells: list[tuple[str, int]]) -> str:
    """One viewport row from its (char, attributes) cells; a wide glyph's trailing cell repeats it."""
    return "".join(char for char, attributes in cells if not attributes & _TRAILING_CELL)


def windows_startup_screen() -> str | None:
    """Read the rendered viewport, not scrollback or a ConPTY byte-stream tail.

    Cells are read with ReadConsoleOutputW: a CJK glyph fills two cells, so a
    row holds fewer UTF-16 characters than columns (ReadConsoleOutputCharacterW
    returns 111 for a 120-cell row holding nine). Completeness is the returned
    cell rectangle, not a character count. No screen access means unknown,
    never evidence that a modal closed.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Coord(ctypes.Structure):
        _fields_ = [("x", wintypes.SHORT), ("y", wintypes.SHORT)]

    class Rect(ctypes.Structure):
        _fields_ = [(name, wintypes.SHORT) for name in ("left", "top", "right", "bottom")]

    class Info(ctypes.Structure):
        _fields_ = [("size", Coord), ("cursor", Coord), ("attributes", wintypes.WORD), ("window", Rect), ("maximum", Coord)]

    class CharInfo(ctypes.Structure):
        _fields_ = [("char", wintypes.WCHAR), ("attributes", wintypes.WORD)]

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
        kernel.ReadConsoleOutputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(CharInfo), Coord, Coord, ctypes.POINTER(Rect)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW("CONOUT$", 0x80000000, 3, None, 3, 0, None)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            return None
        try:
            info = Info()
            if not kernel.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                return None
            left, right = info.window.left, info.window.right
            width = right - left + 1
            height = info.window.bottom - info.window.top + 1
            if width < 1 or height < 1 or width * height > 65536:
                return None
            lines = []
            for row in range(info.window.top, info.window.bottom + 1):
                cells = (CharInfo * width)()
                region = Rect(left, row, right, row)
                if not kernel.ReadConsoleOutputW(handle, cells, Coord(width, 1), Coord(0, 0), ctypes.byref(region)):
                    return None
                if (region.left, region.top, region.right, region.bottom) != (left, row, right, row):
                    return None
                lines.append(viewport_row_text([(cell.char, cell.attributes) for cell in cells]))
            return "\n".join(lines)
        finally:
            kernel.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None
