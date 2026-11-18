# Log

## 2026-11-17

- User question: why Celly's Work pane shows P1 where P0 goals should be.
- Read-only scan of Celly's transcripts. P0 activations happened: 25 `Side P0 queued` notices on 9 goals, 19 of them ending `fork starting`, and no activation error.
- Cause: the board stores P0 as canonical P1 (or keeps the earlier priority), and the pane printed the stored field. The fork line used the lease's canonical priority.
- Fix: `register.js` `shownPriority` (tree, list, rows); `effective_priority` on fork rows in `work_parallel_mcp._fork_projection`.
- Tests fail before and pass after. `claude plugin test` gives 103 pass after the 2.1.327 engine-ref assertion fix. See [record.md](record.md).
