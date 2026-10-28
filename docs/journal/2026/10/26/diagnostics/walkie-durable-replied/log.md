# Walkie durable_replied log

## 2026-10-26
- **Fix verified**: Runtime input ids now start above every id in the status history; the queue append is fsynced; delivery health reports reused ids. Unit tests (776 on Python 3.14 and 3.10) and real-CLI runs: snapshot 2faae14 skipped ids 2-4 as `durable_replied`, the worktree issued id 5 and injected it. See [fix-verification.md](fix-verification.md).
- **SendMessage and EOF recorded**: SendMessage is removed by the default non-Anthropic block list; the SSE stream ends server-side about every 60 s and the client reconnects. Neither was changed.
- **Investigation**: Unclean reboot at 13:32Z lost queue rows 14877-14907; reissued ids were skipped as `durable_replied` until 14908. See [findings.md](findings.md).
