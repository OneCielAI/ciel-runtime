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

# Unverified / remaining

No full SSE receiver-to-mod end-to-end claim yet. No release/install of these changes yet. External conversational ACK remains not_sent until verified connector integration. Work OFF mail remains pending rather than being falsely marked consumed. Current summaries scan files with bounded result memory; filesystem scan cost is not yet an indexed enterprise-scale query. Work board still uses whole-document persistence and a 32 MiB parser/write bound; that storage design remains to be replaced for scalable board workloads. Queue cleanup audit/restore and actual arrow UI screenshots remain in progress. Live routers/sessions have not been restarted.
