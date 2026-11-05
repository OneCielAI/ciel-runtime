---
type: implementation-journal
title: Per-event mailbox redesign in progress
description: Verified mailbox persistence, mod consumption, queue changes and remaining integration boundaries.
tags: [sse, mailbox, work, verification]
timestamp: 2026-11-05
---

# Verified changes

- Queue registration no longer rejects at 100 unfinished nodes. Receipt normalization/admission no longer rejects at 10,000 receipts.
- Receiver admission now also writes each external event envelope to external-mail/<prefix>/<hash>.json before appending the legacy runtime queue; failure aborts admission without ACK. These raw envelopes are recovery evidence, not yet an automatic replay authority. Repository tests verify successful preservation and disk-write failure rejection (24 tests pass).
- Each side request is atomically persisted under pending/<hash-prefix>/<event-hash>.json; per-event locks permit unrelated writers without rewriting the inbox.
- A committed Work item is linked by writing a durable receipts file before pending mail deletion. Original text and stable event key remain in the receipt for deduplication and audit.
- Recovery after receipt commit but before unlink removes the obsolete pending copy. Receipt-write failure retains the pending original.
- Legacy singleton migration preserves inbox.json unchanged and writes a migration marker only after validating/copying its records.
- Automatic runtime admission now stages mail without directly editing Work; mod timer calls the authenticated drain operation outside synchronous PostToolUse. It checks compaction state before consumption and polls during idle and busy periods.

# Evidence

An isolated scoped run using scripts.run_test_group.isolate_runtime_state passed 74 tests (tree, inbox, mailbox, settings, isolated CLI asset guards). The historical CI isolation failure did not reproduce in that scoped local run; its CI stderr is still required before attributing a cause.

The actual-mod-source suite now passes 37 tests, including deferred consumption during compact and resumption after compact failure. Work storage passes 24 tests including archive-before-removal and archive-write-failure preservation.

Real owned native Claude Code 2.1.318 probe `.local/side-busy-5ed9eed3/result.json` confirms gateway admission via the exact native socket during main streaming, disk inbox consumption by mod-authenticated MCP, exactly one new Work node after duplicate delivery, durable receipt and empty pending inbox. `mod-mailbox.png` is a screenshot of captured real ConPTY output. This probe uses a local MCP/model fixture, not the production SSE receiver or external ACK connector.

Real primary settings interaction `.local/choice-menu-1c7a6cf1/result.json` verifies Down+Enter persists work_mode=false in workspace configuration. `arrow-choice.png` captures actual ConPTY choice UI. Two earlier probe failures were incorrect assertions against the global config instead of workspace override, not a failed save.

The first whole unit run executed 3249 tests with 4 errors from renamed constant references and 1 compact-boundary assertion failure. References were corrected. All 50 related MCP/launch/compact tests passed on targeted rerun; the compact failure cause is not established. Subsequent whole unit run passed 3254 tests in 342.997 seconds, skipped 50 (`b1ocfcace.output`). No compaction logic was changed based on the unproven failure; diagnostic assertion context was added.

Channel suite after receiver spool integration passed 602 tests in 15.218 seconds (80 skipped). Fair drain keyset rotation and concurrent duplicate receipt tests also passed. Primary menu now retains validation feedback after returning to its parent panel.

# Latest user-requested settings revision

The latest user request supersedes separate first-screen controls. Main menu now has one `Work · SSE settings` entry. The panel exposes one SSE injection selector (`session` / `btw_with_mcp`); the old enabled/type pair is hidden and selection updates both persisted fields atomically. Each selectable setting has an explanation shown in its choice panel. Real ConPTY `.local/choice-menu-2748fb2a/result.json` verifies Work off and btw_with_mcp selection persist, and `unified-sse-explanation.png` confirms one menu and explanatory text.

Real loopback SSE + production receiver + production repository/gateway + exact native socket + actual native mod probe `.local/side-busy-1ee2fc70/result.json` passed. It verifies receiver event spool, side pending consumption/deletion, retained receipt, duplicate admission yielding one new Work node during main streaming. Screenshot `real-sse-mailbox.png` captured. One fixture HTTP response encountered ConnectionResetError during client shutdown; assertions passed, and this is not claimed as a clean shutdown trace. External conversational ACK remains unconnected.

# Publishing checkpoint

Implementation checkpoint committed as 0798f73. Two fast-forward pushes to the existing feature branch were rejected by the remote with `fatal error in commit_refs`. Read-only remote inspection confirms all three branch heads remain 7e7ccd9 and repository push permission=true, archived=false, disabled=false. Cause is unconfirmed. GitHub status API fetch was blocked by robots policy; the public status homepage did not provide sufficient status detail in the fetched excerpt. No nightly update or local production install has occurred for this checkpoint.

Mailbox status/drain calls now have a two-second UI-side wait bound and rearm on timeout while preserving durable mail. Actual-mod-source tests pass 38 cases including a never-resolving MCP call and compact failure settlement.

# Corrected release verification

Linux CI for c9700c2 rejected a static import cycle: channel_message_repository → side_intake_inbox → work_session_lineage → channel_message_repository. Commit 0c3b177 extracts dependency-free durable_json_file; architecture-cycle check plus 55 persistence tests pass. Corrected CI42131271422 has quality, Python3.10, unit, runtime and channel jobs successful; router job still running at this entry. Publish42131271444 and gated exact-commit installer remain pending. The original 7e7ccd9 import-subprocess failures do not recur in corrected CI unit/Python3.10 jobs; their original cause remains unconfirmed.

Corrected real SSE/native test `.local/side-busy-fd076a0a` passes without the prior fixture shutdown exception. Observed receiver_spool_files=1, pending_files=0, receipt_files=1, work_nodes=2 (one existing + one incoming), intake_before_main_completed=true. Inspected screenshot and results preserved under evidence/corrected-real-sse-*. The pending external event envelope is retained as recovery evidence, not falsely reported deleted.

# Unverified / remaining

Real loopback SSE receiver-to-mod integration and actual arrow UI screenshots are verified above, not a production remote validation. Corrected nightly push is complete; CI/publish/install remain pending at this entry. External conversational ACK remains not_sent until verified connector integration. Work OFF mail remains pending rather than being falsely marked consumed. Current summaries scan files with bounded result memory; filesystem scan cost is not yet an indexed enterprise-scale query. Work board still uses whole-document persistence and a 32 MiB parser/write bound; that storage design remains to be replaced for scalable board workloads. Queue removals now archive original records; a dedicated restoration API/UI remains unimplemented. Live routers/sessions have not been restarted.

Local pure-tree probe registered 2000 nodes in 0.056 seconds. A separate temporary mailbox probe durably wrote 2000 events in 4.432 seconds and returned a 10-row page. These are local measurements, not an enterprise throughput or whole-board persistence benchmark.
