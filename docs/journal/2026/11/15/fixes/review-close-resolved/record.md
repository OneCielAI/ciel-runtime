---
type: fix-record
title: Main-review rows for Work items main already resolved now end instead of waiting forever
description: G:\ciel-Walkie's Work pane kept "Awaiting main review" and "Finalizing" rows for items main had completed or removed itself; such reviews now close unreviewed, an approve on an already-done item finalizes, and a blocked saga names its reason.
timestamp: 2026-11-15T17:10:00Z
tags: [work, parallel, review, finalization, mod, pane]
---

# Measured state (G:\ciel-Walkie, read-only, 2026-11-15)

The pane listed 10 `parallel_reviews`. The finalization journal and the board showed these rows:

| Item | Board node | Review state | Decision | Blocked message |
| --- | --- | --- | --- | --- |
| n1869, n1871, n1873 | done | pending | none | — |
| n1536, n1537, n1538 | removed from the board (lease still `submitted`) | pending | none | — |
| n1909 | done | deciding | approve | `board item completed by another result` |
| n1937 | in_progress, has unfinished children | deciding | approve | `n1937 still has unfinished children; finish them first` |
| n1856 | done | deciding | rework | `content baseline entry bound exceeded: 20001/20000` |
| n1971 | done | pending (stranded no_report) | none | code `no_report` |

# Root causes

1. **No path closed a pending review once main resolved the item.** Only main's `approve` or `rework` ends a pending review. When main completes or removes the item itself, neither applies, so the row stayed "Awaiting main review" forever.
2. **Approve refused an item that was already done.** `_board_accept` raised `board item completed by another result` (n1909) when the node was already done. The saga stayed `deciding`, and the pump retried it at every boundary with the same refusal.
3. **The pane showed a blocked saga as "Finalizing".** `parallelReviewLabel` returned the same "Finalizing" text whether a stage was running or blocked, so the reason (n1937, n1856) was visible only in `/work-mode parallel`.

# Change

- **New decision `close`** (`work_parallel_finalization.py`):
  - it shares approve's invariants;
  - it is made only by the host, never by main.
- **`close_resolved_reviews`** (`work_parallel_mcp.py`), run by the new host-internal event `review_close_resolved`:
  - **What it closes:** an undecided pending review in this conversation whose board node is done or missing, and whose lease is still `submitted` with the same artifact.
  - **How:**
    - the lease goes `accepted` → `done`;
    - the coordination archive outcome is `closed unreviewed: Work item resolved on the board`;
    - the board is never edited;
    - the workspace is retained.
  - **Evidence text:** "Closed unreviewed: Work item X was completed on/removed from the board before main reviewed this result. The board was not changed; the workspace is retained at … (head)."
  - **Stranded rows:** an undecided stranded no-report row for such an item is closed too.
  - **Limits:** at most 24 rows per call; conflicts are skipped and retried at the next pump.
- **Who can call it:** only the verified mod host (`internal` host, no actor). A model call is refused, and the mod lists the event among its host-internal events.
- **`_board_accept`:** a node already done, or missing, needs no change, so approve finalizes (n1909).
- **Mod (`register.js`):**
  - **Pump step:** `parallelRollForward` first calls `review_close_resolved` whenever any review is pending.
  - **Older routers:** an older router's `unsupported parallel event` is ignored; any other error is recorded as a `review-close-resolved` failure.
  - **Blocked label:** a `deciding` row with a blocked message is labelled `Blocked: <message>`.
- **Web:** the web dashboard already shows the blocked code as a review field. It keeps hiding the message, because messages can carry host paths.

# Evidence

## Backend unit tests (`tests/test_work_parallel_finalization.py`, 84 passed)

- `test_review_of_an_item_main_completed_on_the_board_closes_unreviewed`:
  - decision `close`, state `finalized`, lease `done`;
  - board evidence unchanged ("main did it");
  - workspace exists, archive outcome recorded;
  - a second call closes nothing.
- `test_review_of_an_item_removed_from_the_board_closes_unreviewed`
- `test_review_of_an_open_item_is_never_closed`
- `test_close_resolved_requires_the_verified_mod_host`
- `test_approve_of_an_item_already_completed_elsewhere_finalizes_instead_of_blocking`: covers n1909.

## Real register.js plus the real router backend (`tests/test_work_mod_real_backend.py`)

The five new scenarios passed (`4 passed, 111 deselected`, then `1 passed, 115 deselected` for the stranded one).

| Scenario | Result |
| --- | --- |
| `close-resolved-stranded` | the n1971 shape: a stranded no-report review (pending, 1 row) for an item main then completes; at the next boundary the stranded row is `done`, 0 pending rows remain, no recovery fork is spawned, and the pane has no "Awaiting main review" |
| `close-resolved-done` | main completes the item; at the next `turn.complete` the review is `finalized`/`close`, the lease `done`, and main made no `work_parallel_item_review` call; the pane has no "Awaiting main review" |
| `close-resolved-removed` | the same for a removed item |
| `close-resolved-open` | an open item still awaits main |
| `blocked-label` | an approve whose board stage fails is shown as `Blocked: n2 still has unfinished children; finish them first`, and "Finalizing" is not shown |

The `blocked-label` scenario injects that board refusal (`fault_board_accept`). Adding a real child would change the approval fingerprint, so the decision itself would be refused.

The whole file on Windows gave `115 passed, 21 subtests passed in 1528.36s`. That run started before the stranded scenario was added, and the stranded scenario passed on its own.

## Linux (Docker `node:24-bookworm-slim`, Python 3.11.2)

- `tests/test_work_parallel_finalization.py`: 79 passed, 5 skipped.
- `tests/test_work_mod_real_backend.py`, with the 5 new scenarios plus `approve`: 6 passed.

## Related Windows suites

These ran together and gave 314 passed, 10 skipped, 245 subtests passed:

- finalization, owner offline, restart terminal, review pin, rework worktree;
- HTTP parallel review and gaps, operation reconcile, receipts, web UI;
- the architecture budget and compaction binding.

## Engine-backed mod suite

`claude plugin test ciel_runtime_support/claude_mods/ciel-work`: 102 pass, 0 fail.

# What still shows after the update

- **n1937** stays `Blocked: n1937 still has unfinished children…`. It finalizes by itself once its children are done.
- **n1856** (a rework decision on an item that is already done) stays `Blocked: content baseline entry bound exceeded: 20001/20000`. This change shows the reason but does not resolve the row. Whether to close a decided rework whose item is already done is the user's decision.
- **Takes effect after a restart:** the change takes effect in G:\ciel-Walkie only after that session restarts on the installed build, which brings the new mod and router.
