---
type: journal
title: Side decision crash recovery and native pane verification
description: Evidence for indexed intake recovery, committed-decision reconciliation, side pane updates and Work settings selection.
tags: [work, side-intake, recovery, verification, beta]
timestamp: 2026-11-06
---

# Changes under verification

- A missing disposable priority marker is rebuilt from durable pending mail during pickup. Receipt commit precedes pending/index removal; a failed receipt write leaves original and index intact.
- When a Work decision commits but receipt persistence fails, the next expired-lease pickup reconciles the existing decision from the board ledger without asking AI for a different decision or creating another root.
- A coordinator answer committed before follow-up mail creation is recovered on the next host pickup using deterministic `handoff:<event>` identity.
- A real browser correlation-guard test exposed that sending an `advise` question as a final reply would consume the browser's one-shot reply and reject the eventual coordinator answer. Advice now sends correlated `status`; the subsequent answer sends `reply`. The actual production channel dispatcher accepts status→reply and rejects a duplicate final reply. No correlation guard was relaxed.
- Reply preparation persists delivery=`submitting`. Restart does not silently resend `submitting` or `uncertain` messages. This is not exactly-once delivery or automatic recovery of an unknown remote outcome.
- P1 requests naming a child promote/reorder the existing owning goal. The canonical tree stores priorities on goals; active goal and approval phase remain unchanged.
- A repeated unacknowledged side handoff does not create an unlimited Stop-hook continuation loop. Durable advice remains visible; the per-event automatic Stop reminder is bounded.
- Redacted main Work snapshots no longer erase the host-only original/result projection. Side completion/submission also updates an in-memory display-only projection and invalidates pane rendering directly.
- Side model selection now uses the existing provider catalog picker in the primary menu and provider model list in the legacy menu; it does not change the main model. Explicit custom IDs remain available.
- Quiet runtime updates report a cross-channel candidate as rejected, not incorrectly as up-to-date.

# Executed checks

- Side-focused regression suite: 42 tests passed, including crash boundaries, generation fencing, outbox uncertainty, catalog selection, main-status redaction and the actual browser one-shot reply guard.
- Packaged Work hook harness: 40 tests passed, including preserved host pane contents and bounded advice Stop behavior.
- Architecture checks: 263 tests, 42 skipped, no failures.
- Runtime updater service: 5 tests passed.
- Product Ruff and compileall passed. Native `claude plugin validate` passed; directory icon warning only.
- Earlier isolated router gate `bq28sagw9`: 1516 tests passed in 695.916 seconds. It predates the newest edits and is not final-source coverage.
- Full sequence `bozubhlwh` passed: unit3306/50skip, router1516, channel603/80skip, runtime458/19skip. No failures. The snapshot fingerprint predates the subsequent reproduced startup capability-probe correction and real browser advice/final-reply correction. It is not final-source coverage. Final-source unit/channel rerun `br668xvoh` failed one of3308unit tests: cross-process CAS hit PermissionError at Windows lock-byte initialization flush in unchanged `channel_message_repository.py:54`. Because the shell used &&, channel did not run there. A separate unchanged-source rerun passed all24WorkPlanStore tests and channel603/80skip (`bjq6lyikp`). Thirty CAS repetitions `bg045e3ny` reproduced the identical byte-initialization flush PermissionError once. Two deterministic owned initialization probes did not reproduce it; that negative evidence is retained. Microsoft LockFile documentation explicitly permits locking bytes beyond EOF, and an actual empty-file msvcrt probe succeeded without growing the file. Removed the unnecessary pre-lock byte write/flush, retaining the same byte0range lock, acquisition wait and typed contention behavior. After that change, channel repository25tests and60real cross-process CAS repetitions (`bbbl53bjl`) passed. This reduces evidence for the observed initialization failure but is not proof that no other Windows lock race exists. Full all-group validation `bh0yp4ix8` passed on that unchanged source: unit3308/50skip, router1516, channel604/80skip, runtime458/19skip.
- Afterwards, two pane defects observed in native screenshot86c7cfd2 were fixed in the mod only: the receipt label used the Work item ID, and multiline originals produced replacement glyphs in transcript log rows. Pane keeps full original; log rows are single-line and bounded. Native rerun `side-production-44ad8bbf` shows distinct receipt IDs with `Work item: n1`, no glyph corruption, one root, zero pending, both replies submitted. Main requests in that run carried only bounded advice (event ID), not the raw SSE original. Work hook harness40, side suite42 and native plugin validation passed. Unit group rerun `b26k1ih40` passed 3308/50skip on this mod-only delta; router/channel/runtime groups were not rerun because the delta touches only the ciel-work mod files and their tests.
- Python3.13.5actual owned `--help` subprocess reproduced CP949readerthread failure before the capability-probe correction, then detected permission mode with zero reader errors after explicit UTF8/errorsreplace. Eight compatibility tests and nine npm tests passed under Python3.13. Screenshot's exact child command remains uncorrelated.

# Actual native CLI evidence

Owned model/MCP loopback fixtures, native Claude Code 2.1.310, production gateway/socket admission and packaged side controller:

- `side-production-27fefc5c`: `resolved_before_main_end=true`, Work nodes=1, pending=0, receipt processed, correlated fixture reply delivery=submitted. Parsed pane screenshot explicitly shows original, decision, action, reply, delivery and main-handoff state. A submitted fixture MCP result is not proof of real recipient delivery or real-model semantic correctness.
- Prior snapshots `06f20126`, `891ecfec`, `4cad0a35`, `0481d376` are retained. Incorrect terminal geometry was a capture defect, then the stale pane was observed and corrected; those older frames are not accepted pane proof.
- `settings-description-2486f38c`: all 22 highlighted Work rows displayed their descriptions. Side model picker opened with inherit, refresh, custom and back without changing live configuration. The fixture provider had no catalog entries, so this frame does not prove a live provider catalog refresh.
- The strengthened native run `side-production-86c7cfd2` passed through the actual production channel dispatcher and private browser reply-token check: persisted message kinds exactly `web_chat`, `status`, `reply`; one Work root, no pending mail. This is an owned channel repository fixture, not a live browser recipient delivery.
- Actual main-advice round-trip rerun `bveg2rgn0` passed: `side-production-62489e9f`, one root, zero pending, two processed receipts with submitted fixture replies. Side advice occurs during the initial main stream; final answer requires the subsequent main acknowledgment and therefore the full round trip is not claimed complete before the initial main turn ends.

# Additional measured evidence

- `side-production-cf020e16`: two independent events processed and submitted while the same main stream remained active; no additional Work roots. Three side model requests used identical system and tool fingerprints. This is stable-prefix evidence only, not a measured backend cache hit.
- `side-production-5e275792`: actual subsequent main model request captured after side completion; routine side original text absent from both initial and subsequent main requests. Separate side model=`fixture-side-model`, main=`claude-opus-5-5`. This is one controlled event, not thousands-event isolation proof.
- Owned disk-only1201event workload: registration2.671seconds; P1pickup0.602seconds; newest10summary projection0.166seconds and exactly10record JSON reads. After one processed event,1200pending records remained, no Work roots created. Directory enumeration still scales with mailbox size; no enterprise throughput guarantee.
- Native Agent fork wire probe `side-actor-3c208b89` completed two persistent MCP-enabled turns during main. Parent request inherited, two substantive system blocks matched; leading native billing block and appended worker/environment context differed. This does not fulfill selected token-tail/custom-model or exact whole-prefix claims.

# Earlier incomplete status (superseded where noted)

This section preserves the earlier native-only investigation. [Routed context](routed-context.md) implements full/recent and shadow comparisons; [completion pass](completion-pass.md) verifies external binding and explicit retry. The authorized live combo was subsequently attempted but returned no executable targets. [Release validation](release-validation.md) is the current acceptance record.

Full/recent token-selected parent conversation and exact parent system sharing are not connected. Dedicated native agents replace the parent system; `model.fork` preserves the exact last prefix but denies tools; Agent `fork` inherits context/model but offers no documented history fraction or spawn-time model override. API inspection found no faithful assistant/tool/thinking history import or per-message token counting surface. Quoting JSON history as user text is not treated as exact replay or shared cache.

No paid live `API KEYS - SSE` comparison was performed. Source-specific external reply connectors beyond protected browser correlation, comprehensive all-MCP behavior, interrupted actor resumption, uncertain-send operator flow, startup screenshot child-command correlation and the reported Work router-call root cause remain unverified. No beta release, commit, installation, live configuration change or live router/session restart was performed.

# Related evidence

- Prior implementation journal: `docs/journal/2026/11/05/features/side-decision/progress.md`.
- [Current screenshot](evidence/native-side.png)
- [Side model picker](evidence/side-model-picker.png)
