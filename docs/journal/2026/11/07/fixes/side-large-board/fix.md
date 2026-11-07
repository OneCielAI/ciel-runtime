---
type: fix
title: Side intake stalled on a large Work board
description: The walkie side actor never processed SSE events because the claim result exceeded the native MCP result limit; host results are now paged and originals render interpreted.
tags: [side-intake, mcp, work, walkie, beta]
timestamp: 2026-11-07
---

# Symptom (G:/ciel-walkie, beta 6e07ee0)

- The side inbox kept receiving SSE events (`channel_side_intake_accepted`), but no reply was sent and no Work item was added or updated. The same receipts showed again and again.
- The status line showed `Side processing retained: SyntaxError: JSON Parse error: Unexpected identifier "Error"`.
- The side model ("API KEYS - SSE") was never called.
- The pane printed each original as raw CloudEvent JSON.

# Cause (reproduced)

- **Live inbox, read-only:** 15 receipts were in `pending`, each with `processing.owner=side-1` and a renewed lease.
- **Copied state:** the same router code called `work_side_claim` on a copy of the walkie Work board and side inbox. The response was 129,171 characters (178,989 bytes). The board projection alone was 125,954 characters: 333 nodes, `truncated=false`.
- **Owned native run (Claude Code 2.1.310):** with a 333-node board, a probe build showed what `$.mcp.call` returned. The text was `Error: result (182,656 characters across 1 line) exceeds maximum allowed tokens…` with `isError=false`.
- **What happened next:** `callRouter` ran `JSON.parse` on that text, so every claim failed with the status-line error. The lease stayed held and the next poll claimed the same event again. Side model requests in the run: 0.
- **Work board status:** `work_status` (JSON) on a fixture board of 333 top-level goals was about 98,000 characters, which made the status line show "Work unavailable". Walkie's real board is about 18,000 characters, so this was not hit there yet. It grows with the number of goals.

# Fix

- **Paging (`host_result_pages.py`):** a successful Work tool result for the authenticated mod (`_host` token verified) that exceeds 8,000 characters or 24,000 bytes is stored as pages for that session. The call returns `{"paged":{"id":sha256,"parts":n}}`.
- **`work_host_page`:** a new host-only tool that returns one page. Pages expire after 1 hour.
- **Unauthenticated callers** (the model's own `work_status`) are answered as before.
- **Mod side:** `hostJson` in `side-intake.js` (also used by `register.js` `parallelCall` and `readSnapshot`) reads every page, checks the part order, and checks the SHA-256 against the id before parsing. Text that isn't JSON now fails with a message naming the tool.
- **`eventView`:**
  - A CloudEvent original shows its kind, time, source, event type and text.
  - The remaining fields are shown as indented JSON.
  - Opaque or credential-like keys such as `activity_context` and `token` are replaced by `‹hidden N chars›`.
  - Log lines show `[type · event] text`.

# Verification

- `tests/test_host_result_pages.py` (6) covers:
  - a 333-root claim and `work_status` paged and reassembled exactly
  - unauthenticated and wrong-token callers not paged
  - a foreign session, a bad id or part, and a wrong host token all refused
  - the multibyte byte budget
  - lone surrogates hashed the way TextEncoder hashes them
- `tests/test_side_intake_mod.py` (new paging/view test) covers:
  - reassembly with the host token
  - the oversize-error text
  - a tampered page caught as a digest mismatch
  - the interpreted view with hidden fields
- **Owned native E2E on the 333-node board** (`.local/side_production_e2e.py`, `SIDE_BOARD_BIG=332 SIDE_EVENT_JSON=1`):

| Build | Side model requests | Claims | Pages read | Resolves | Pending | Reply delivery |
|---|---|---|---|---|---|---|
| Before (installed 6e07ee0) | 0 | retried every poll | n/a | 0 | n/a | none |
| After | 2 | 1 | 49 | 1 | 0 | submitted |

  - After the fix, the status line shows `Work 0/0 … Main active job` instead of "Work unavailable". Screenshot: [large-board-side-pane](evidence/large-board-side-pane.png).
- **Real walkie events:** three CloudEvents from the walkie inbox were rendered read-only with `eventView`. They showed the expected kind, text and hidden `activity_context`.

# Not verified

- **Model call in walkie:** whether the selected side model "API KEYS - SSE" is accepted in the walkie routed session. This is only observable after this build is installed and walkie is relaunched.
- **Separate guard error:** the `Agent(...)` denial in the walkie screenshot comes from the Work parallel dispatch guard (the main model called Agent without `work_parallel_run`). It is unrelated to side intake and was not changed.
