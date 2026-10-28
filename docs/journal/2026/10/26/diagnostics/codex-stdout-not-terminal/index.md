# "Error: stdout is not a terminal" window (2026-10-26)

Where the message in the user's screenshot comes from, and what is not recorded.

- [findings.md](findings.md): confirmed source (Codex CLI via the npm shim) and what remains unknown
- [evidence/01-string-search.txt](evidence/01-string-search.txt): which binaries and trees contain the text
- [evidence/02-codex-tty-probe.txt](evidence/02-codex-tty-probe.txt): owned reproduction with both installed Codex versions
- [evidence/03-windows-and-walkie-tab.txt](evidence/03-windows-and-walkie-tab.txt): terminal windows now, and the Walkie tab relaunches
- [evidence/codex_tty_probe.py](evidence/codex_tty_probe.py): the probe script
- [log.md](log.md): change history
