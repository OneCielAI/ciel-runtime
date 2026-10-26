# Investigation log

## 2026-10-26
- Probe scripts are stored as `evidence/scripts/*.py.txt` (verbatim, renamed so the repository lint does not treat them as project code); evidence headers name them by their original `.py` names.
- Read-only diagnosis of Kobe u24 on .14 after the user's screenshot. No remote files written; probes ran from stdin over SSH. Confirmed the error shape is outside the existing stream recovery and was not retried by Claude Code. No fix made.
