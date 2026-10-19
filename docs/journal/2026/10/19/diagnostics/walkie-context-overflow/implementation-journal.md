---
type: Implementation Journal
title: Context recovery and admission safety
description: Restore shared semantic compaction and stop external input admission while the native conversation needs recovery.
timestamp: "2026-10-19T19:39:13Z"
tags: [compaction, context, claude, codex, recovery]
---

# Scope

The user requested an urgent fix for both Claude and Codex, verified local installation and nightly publication. Restarting a full conversation while SSE inputs continue being submitted is not a recovery. Durable incoming events must be preserved while model admission is gated; compaction must have priority and must be confirmed by a native checkpoint before input resumes.

# Preserved incident

The original Walkie session `417ef30b-1289-47a5-8921-a4d45eb93f2e` is 2,014,374,790 bytes. A complete private backup was made outside the repository and verified SHA-256 `6ba4f8a1599b13d3676c8bdb79bcceff3994e33539300a506c93aedb2d73b048`. The original was not changed. Conversation contents and recovery bodies are not publishable evidence.

# Confirmed boundaries

- Native automatic trigger failure is not yet attributed to an exact historical gate. Enabled 872K environment was observed, and native 2.1.303 gate/usage handling is being tested rather than inferred from 2.1.300.
- Runtime ordinary request guarding ran, but `chunks=12` described deterministic excerpts, not complete semantic map/reduce or persistent CLI compaction.
- Current Claude semantic default is disabled for unset context_compact_llm. Codex can report deterministic local completion when map requests fail or exceed eight. Those behaviors are not proof of complete semantic recovery.
- Saved nested gateway 502 contains an input limit rejection and a separate fallback timeout. Current classifier calls the whole response overload; input observation must be distinguished without claiming no generation occurred on every fallback.
- The prompt-only overflow parser now records the actual input/limit pair with zero output reservation and does not use it to justify an output-only retry. Focused tests cover inconsistent values and mixed errors.

# Why native compaction never started (observed)

`evidence/native-usage-since-boundary.json` (counts only, from the private backup):

- After the last boundary (2026-10-18T17:34:48Z) there were 1,582 `api-request` records, all `repl_main_thread`; **zero** compaction requests were sent by Claude Code.
- 1,535 `gpt-6.1-sol` responses reported usage 380,128–700,448 while the persisted history grew to 4,014 messages (Runtime estimate 5,230,119). Only one response (opus, 18:28:31Z, 998,467) was at/above 839k.
- Runtime's ordinary guard reduced outbound requests; the usage Claude Code received described those guarded payloads, not the full persistent history. This is a reproduced mechanism by which history grows without corresponding native usage growth; it does not establish the exact historical native gate decision.
- The exact 2.1.303 gate value for that session was not recorded; the observation is "usage stayed below the comparison line and no compact request was ever sent", not a reconstructed gate trace.

Why Runtime chunking did not recover it: the guard is an outbound projection (first/last excerpts, `chunks=12` = excerpt groups) and never asks the client to compact; Claude semantic summarization was off by default; and the guarded body still exceeded the upstream limit (1,239,398 > 1,000,000) because JSON/4 underestimates this content.

# Fix direction implemented

- Controller observes the **raw** client request before guard/pending append; raw size over input budget queues an automatic native `/compact` (or `thread/compact/start`) and holds external admission. Durable inputs stay queued.
- Release requires a new persisted native checkpoint **and** a smaller below-budget next raw request. A checkpoint whose `postTokens` is still at/over budget, or whose next request is still over budget, is spent proof; retry is bounded (2 attempts) then blocked.
- Semantic map/reduce covers every message losslessly. Offline check on the reconstructed private request: 4,014 messages → 94 map chunks, every message's projection reassembled exactly (`evidence/private-projection-counts.json`; no model calls).

# Verified native integration (isolated)

`evidence/native-e2e/product-verified-09/summary.json`: actual Claude Code 2.1.303 and Codex 0.158.0 against the source Runtime handler and a loopback chat provider. Claude auto/manual: 142 semantic map calls + 1 final reducer, `compact_boundary` 90059→324 / 90050→247 tokens, next raw request 414,617→2,595 / 413,927→2,614 bytes. Codex auto/manual: 71 map calls + 1 reducer, persisted `compacted` replacement history, next raw request ~411 KB→~24 KB. The next native turn succeeded in all four cases. Admission release and the native Responses route are not covered by this run.

`evidence/socket-compact/verified-observations.json`: `/compact` sent through Claude Code's messaging socket is delivered as an ordinary peer prompt (no boundary, no native instruction), idle and mid-turn. Automatic compaction therefore needs the terminal path; the direct (socket-only) launch does not consume compact requests. Walkie's incident router log shows the Windows console proxy path (`channel_windows_console_*`), not the direct fallback.

# Packaging check

`package.json` whitelists `docs/`. Root `.npmignore` alone does not exclude descendants of that explicit whitelist in the local npm 11.12.1 preview. Added nested `evidence/.npmignore` with `**`; dry-run now reports **zero** files from this incident's evidence directory (before: 5,714). Native homes, request bodies and authentication keys must never be staged or shipped. Final release is built from the scoped tracked index, not the dirty workspace.

# Reactive recovery contract

Actual native probe (`evidence/overflow-reaction/run-02/verified-outcomes.json`): Claude 2.1.303 runs reactive native compaction and automatically retries the original turn after HTTP 400 prompt-too-long or 413. HTTP 200 SSE error instead caused nonstreaming fallback, not compaction. Codex 0.158 HTTP400/SSE context-length failures ended the turn without compaction; app-server/terminal control remains necessary.

A local **truthful** HTTP400 message, with the prefix `prompt is too long` but **no invented numeric provider token pair**, also triggered Claude reactive compact → persisted boundary → original-turn retry (`evidence/local-pressure/run-02/summary.json`). The source router now invokes this preflight before pending append, deterministic guard or upstream generation for an exactly attributed non-Anthropic main request over configured local budget. Native compact and subagent requests bypass it. Attempts are bounded and a blocked cycle uses an error without the reactive prefix. Mixed historical 502 responses remain unchanged; no claim of safe replay for their other fallback attempts.

# Identity integration fixes

- Claude launches persist the exact messaging socket. Claude Code 2.1.303 publishes `pid`, `sessionId`, socket and process metadata in `<config>/sessions/<pid>.json`; the binding accepts one matching, newer, same-workspace record and follows native session switches within the launch epoch. It does not choose the newest transcript.
- Custom Claude profiles: the transcript projects root now follows the inherited `CLAUDE_CONFIG_DIR`, matching where the pid record and transcript are actually written.
- Launches whose requests never pass the router (native/direct paths) pass `request_identity=False`; they are not held indefinitely waiting for request headers that cannot arrive.
- Facade transcript-delivery scope ignores binding-only options (`messaging_socket_path`, `request_identity`).

# Fixes found by real-run verification

- **Late checkpoint persistence (observed in reactive-product run-03):** Claude Code 2.1.303 wrote `compact_boundary` only after its automatic retry had already been sent (raw 408,228 → 2,389 bytes). The strict controller therefore saw the smaller request before any checkpoint and stayed paused. The controller now records a smaller main raw request seen during `dispatching`/`awaiting_checkpoint`/`compacting` as that cycle's shrink evidence; release still requires the newer persisted checkpoint (and `postTokens` below budget). A new compaction, a dispatch, or a spent/full checkpoint clears it.
- **Bounded client-started compactions:** reactive or manual native compactions now count toward the attempt bound; previously only Runtime-queued commands did, so non-shrinking reactive cycles could repeat indefinitely (bounded only by the rejection helper's in-memory count).
- **Compact-poll lock contention:** a Windows `ChannelFileLockBusy` while reading the compact request could escape the poll and stop the terminal proxy. Pre-submission contention now defers; contention after submission may have begun is never silently retried.

# End-to-end verification (isolated native clients, source Runtime)

- `evidence/reactive-product/run-04/summary.json` — Claude 2.1.303 print/stream-json: raw 408,228 B was rejected locally (HTTP 400, no invented token pair); the native compact request (414,535 B) ran **142 semantic map calls + 1 reducer**; the persisted `compact_boundary` reported 85→322 tokens; the automatic retry was 2,389 B; the controller reached `verified` and released admission. Synthetic provider usage stayed 50/20 throughout, so this was not proactive native auto-compaction.
- `evidence/reactive-product/direct-03/summary.json` — the same flow with an **interactive, no-PTY** Claude launched with only its messaging socket (`terminal_commands_sent=[]`): local 400 at 411,167 B → native compact → 142 maps + 1 reducer → persisted boundary 231→470 → retry 2,978 B / 753 estimated tokens → `verified` → the held durable synthetic event was delivered once over the real session socket (cursor 0→1) and observed upstream on the next request. No injection happened while queued or compacting.
- `evidence/native-e2e/product-verified-09/summary.json` (counts in `evidence/native-e2e/product-verified-counts.json`) — both clients, automatic and manual: Claude 142 maps + 1 reducer each (90059→324 / 90050→247); Codex 71 maps + 1 reducer each with a persisted `compacted` record; next native turns completed.

# Release gates

- Unit group isolated: 2,183 passed (49 skipped). Channel group isolated: 523 passed (80 skipped). Router group's four errors were a stale injected-core shape in `test_responses_provider_auth_boundary.py`; the router now treats absent observer callbacks as no-ops and the focused set (21) passes. A local full router/runtime sweep was not repeated while live routers serve (9385/9489/9512 healthy); hosted CI/Publish runs all four groups.
- CI-pinned Ruff 0.15.22 passes over every scoped file; architecture budget/contracts 267 passed; facade stays 4,979 lines (ratchet 4,980).
- npm dry-run shows zero incident-evidence files (nested `evidence/.npmignore`); the release stages an explicit file list only, never `docs/journal` subtrees.

# Pending

Private Walkie fork recovery is prepared (`evidence/scripts/recover_private_fork.py`, synthetic self-tests passed) but **not executed**. It does not copy the 4.8 GB sidecar directory; historical tool-result references may be unavailable in the fork.

Semantic full-coverage chunking, native CLI before/after harnesses, session-scoped admission/compaction control and router composition are separate implementation units. Completion requires native checkpoint plus next-turn verification on both CLIs, not a written command, accepted RPC, guard projection or unit-only pass.
