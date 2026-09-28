"""Tells the PTY relay when the CLI is showing a startup or confirmation dialog.

Typed channel input must not land in such a dialog: Claude Code opens with a
folder trust dialog whose default choice is "No, exit", so the injected
prompt's Enter quits the CLI with code 1 (sarah-ai 2026-09-27; reproduced on
that host with Claude Code 2.1.283: dialog 1.1 s after start, text + Enter ->
exit 1).

The guard watches the child's screen output with ANSI sequences and all
whitespace removed (the TUIs position words with cursor moves, not spaces)
and reports a dialog while the last dialog marker is newer than the last
marker of the normal input screen. The markers are strings from the installed
claude.exe and codex.exe (checked 2026-09-27).
"""

from __future__ import annotations

import codecs
import re

DIALOG_MARKERS = (
    # Claude Code: folder trust dialog and the footer of its selection dialogs.
    "quicksafetycheck",
    "yes,itrustthisfolder",
    "entertoconfirm",
    # Codex: directory trust prompt and onboarding screens.
    "doyoutrustthecontentsofthisdirectory",
    "pressentertocontinue",
)
READY_MARKERS = (
    # Footers of the normal input screen, idle ("? for shortcuts") or busy ("esc to interrupt").
    "forshortcuts",
    "tointerrupt",
    "shift+tabtocycle",
)
_SEQUENCE = (
    r"\x1b\[[0-9;?<>=!]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[P^_X][^\x1b]*\x1b\\"  # DCS / PM / APC / SOS
    r"|\x1b[^\[\]P^_X]"  # two-byte escapes (ESC 7, ESC =, ...)
)
# A sequence cut by a read boundary waits for the next read; other controls are dropped.
_COMPLETE_SEQUENCE = re.compile(_SEQUENCE)
_ESCAPE = re.compile(_SEQUENCE + r"|[\x00-\x08\x0b-\x1f\x7f]")
_LONGEST = max(len(marker) for marker in DIALOG_MARKERS + READY_MARKERS)


class TerminalModalGuard:
    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""  # an escape sequence cut by a read boundary
        self._text = ""  # compacted tail kept for markers split across reads
        self._offset = 0  # absolute position of self._text[0]
        self._last_dialog = -1
        self._last_ready = -1

    @property
    def blocking(self) -> bool:
        return self._last_dialog > self._last_ready

    def feed(self, data: bytes) -> None:
        if not data:
            return
        text = self._pending + self._decoder.decode(data)
        cut = text.rfind("\x1b")
        if cut != -1 and len(text) - cut < 512 and not _COMPLETE_SEQUENCE.match(text, cut):
            text, self._pending = text[:cut], text[cut:]
        else:
            self._pending = ""
        compact = re.sub(r"\s+", "", _ESCAPE.sub("", text)).lower()
        if not compact:
            return
        search_from = max(0, len(self._text) - _LONGEST)
        self._text += compact
        for markers, attribute in ((DIALOG_MARKERS, "_last_dialog"), (READY_MARKERS, "_last_ready")):
            for marker in markers:
                found = self._text.rfind(marker, search_from)
                if found != -1:
                    setattr(self, attribute, max(getattr(self, attribute), self._offset + found))
        if len(self._text) > 4 * _LONGEST:
            drop = len(self._text) - 2 * _LONGEST
            self._text = self._text[drop:]
            self._offset += drop


__all__ = ["DIALOG_MARKERS", "READY_MARKERS", "TerminalModalGuard"]
