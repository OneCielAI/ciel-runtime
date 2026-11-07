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

# Reply through the source system's MCP

- **Before:** an external SSE reply needed a host route (web-chat token or a configured override). With neither, every walkie decision ended `route_unavailable`. The old prompt also told the side AI to use outbound MCP only with a verified original route.
- **Now:** the side AI inherits every valid MCP tool from every MCP server connected to main, through main's existing connections, credentials and native permission prompts. This is not a Walkie-only allowlist and is not limited to reply tools. The prompt tells the side AI to follow the event's own hints and choose whichever inherited MCP is relevant. For example, a Walkie event carries conversation and message ids plus ACK and thread guidance, so the side AI can choose `walkie_send_message`. It answers with `sent_via`, listing the MCP tool names it used for the reply.
- **Host record:** the host stores those names as `reported_sends`, and delivery becomes `side_mcp` instead of `route_unavailable`. The pane shows "sent by the side AI through MCP (reported by the side AI)" and the tool names.
- **Why it is self-reported:** a plugin's `tool.call` and `turn.step` hooks never saw the spawned side agent's MCP calls. In the probe run they saw only the plugin's own calls. That was measured on Claude Code 2.1.310 by writing a file for every hook call. So the receipt cannot claim the host observed the send, and labels it as reported.
- **Same MCP and credentials as main:**
  - Registration starts from the complete valid MCP tool/server snapshot returned by main's `$.tool.list()`; there is no Walkie, server-name, or tool-name allowlist.
  - The side agent receives all inherited non-Work MCP tools. Work MCP tools are present in the snapshot, but mutation tools are denied so an external event cannot bypass Work execution approval; only `work_status` and `work_parallel_status` are directly usable, while `work_side_resolve` remains the authenticated host commit path.
  - Its calls use main's existing MCP connections and credentials under the native permission path. The owned proof server received one `walkie_send_message` while main was busy; arbitrary mail, GitHub, database, calendar and drive MCP names are covered by the registration harness as non-Walkie examples.
- **Native E2E** (333-node board, owned walkie MCP server; `.local/side_production_e2e.py`, `SIDE_WALKIE=1`):
  - 2 side model requests
  - `walkie_send_message(conversation_id=<event conversation>)` called while main was busy
  - delivery `side_mcp`, pending 0
  - Screenshot: [side-walkie-mcp-reply](evidence/side-walkie-mcp-reply.png).

# Pane layout

- **Before:** walkie showed the original JSON of up to ten receipts, wrapped in full, which overflowed the dock and left stale cells. This was reproduced in a real Windows Terminal window on 6e07ee0.
- **Now:** the pane lists one line per receipt, selectable with Enter. Only the selected receipt's detail is shown, and it is paged by the pane's `bodyRows` with Prev and Next. Rows are clipped by terminal cell width, with wide Hangul counted as two cells.

# Installed beta verification

- **Published and installed:** `0.2.51-beta.20261107-015151.8be1f8a`, git head `8be1f8acd6066522f0cf04dfe244ba3fe0851690`.
- **Registry:** the downloaded 1,699,554-byte archive matched npm's SHA-512 integrity; 505 runtime source files matched the commit. `latest=0.2.51` and `nightly=0.2.51-nightly.20261106-020333.c00c8b6` remained unchanged.
- **Local install:** the verified archive was extracted to the new immutable snapshot `C:/Users/djlov/.local/share/ciel-runtime-8be1f8acd6066522f0cf04dfe244ba3fe0851690`. The user-level home and global launcher both report the exact beta. Snapshot and global package contain the same 559 files. No existing session or router was restarted; the nine observed runtime PIDs remained the same.
- **Installed native acceptance:** with the installed snapshot, a 333-node board and an owned Walkie-compatible MCP server, the side model was called twice, called `walkie_send_message` for the conversation named by the event while main was busy, resolved as `side_mcp`, and left pending 0. The same acceptance passed under the supported Python 3.11 interpreter.
- **Installed Windows Terminal capture:** [installed-beta-side-mcp-pane](evidence/installed-beta-side-mcp-pane.png) shows the bounded one-line receipt list, interpreted CloudEvent, hidden `activity_context`, direct `side_mcp` delivery, reported MCP tool, and the 34-tool/two-server parity line.
- **CI:** quality, unit, router, channel, runtime and Python-minimum jobs passed; the npm workflow's complete test and tarball verification also passed.

# Not verified

- **Real walkie model and server:** the owned installed acceptance proves the packaged path and generic MCP inheritance, but not whether the real walkie routed session accepts its selected "API KEYS - SSE" model or whether the production Walkie server accepts the generated call. That requires a normal future relaunch of the real walkie session; this installation did not restart it.
- **Separate guard error:** the `Agent(...)` denial in the walkie screenshot comes from the Work parallel dispatch guard (the main model called Agent without `work_parallel_run`). It is unrelated to side intake and was not changed.
