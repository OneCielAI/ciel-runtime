---
type: journal
title: External reply and lifecycle completion pass
description: Additional implementation and actual runtime evidence after the request to finish all remaining work.
tags: [sse, work, reply, heartbeat, verification]
timestamp: 2026-11-06
---

# Implemented

## Active side processing lease

Added authenticated `work_side_renew`. Polling extends only an unexpired, matching processing generation. Disabled side processing refuses renewal and resolution. Expired/stale actors cannot revive a claim. Host-only heartbeat does not grant queue execution approval. Failed spawn bookkeeping is cleared in finally, and conversation partition keys use JSON tuples to avoid delimiter collisions.

Actual native CLI run `side-production-2b65a7e5`: main streaming170seconds, first side model response delayed125seconds, four durable heartbeats, one external reply connector call, one existing Work root, pending0, answer committed/submitted before main end. Fixture model responses prove lifecycle/concurrency, not semantic correctness.

## Source-specific external reply connector

Canonical `work_side_intake.reply_connectors` is shared by ctl, initial/legacy menus and live settings. Each operator binding contains exact `source`, `receiver_id`, `server`, `tool`, and an `arguments` object. Event fields cannot choose a tool or arbitrary destination. Credentials are rejected from arguments and remain in native MCP connections. Whole-value placeholders are `$reply`, `$kind`, `$event_id`, `$receiver_id`, `$source`, `$receipt_id`. A source on a different receiver does not match. No Walkie endpoint/tool contract was guessed.

The parent prepares a durable submitting intent, calls the configured MCP through the native connection, and records submitted/failed/uncertain. Each send uses a private attempt ID; a late completion from a prior send cannot overwrite the new attempt. Browser advice remains interim status followed by one final reply. Source without an operator binding remains route_unavailable.

Native production ingress and fixture external connector runs89be53f8 and e8345713 passed: gateway external_event→verified native socket identity→mailbox→side actor→decision→external MCP reply before main end, root1/pending0. e8345713 includes final attempt fencing. This does not prove delivery to a live Walkie recipient; no Walkie MCP server exists in this session.

## Explicit reply retry

Pane provides Retry reply for failed/route_unavailable/uncertain/submitting. Actual native failed-state screenshot88498ded shows the control. Two subsequent terminal mouse automation attempts (78b444aa,8a9643d8) did not open a confirmation dialog or submit; their assertions failed and screenshots are retained. A third keyboard site-focus attempt b96vhrvpn also failed its submitted assertion. The native API documents no default ring target without autoFocus. User-invoked receipts now opens focused and the retry control defines autoFocus. Native run284ae00c then passed: real confirmation dialog captured, one authenticated reset, two connector calls total (initial failure and successful retry), final delivery=submitted. Before/after/dialog screenshots are retained alongside the earlier failed probes; no silent auto retry was enabled. Unknown outcomes require the person's explicit duplicate-send warning acceptance. Backend checks observed state, retains previous delivery audit and fences later outcomes by attempt ID. Cancel does not change state. Submitted replies cannot be reset. Harness proves cancel→zero calls and confirm→one authenticated reset. Durable tests prove old attempt completion is rejected.

## Work connection failures

Work read gate distinguishes fixed secret-safe router-disconnected/router-auth/router-timeout/router-call categories. For a disconnected connection only, one verified native reconnect permits one pure work_status read. Auth/policy/refused/unlisted paths remain closed, identity is rechecked, and Edit/Write is never replayed or authorized from cache. Reconnect and failure tests pass; the historical user correlation wg-mw4dy55o-1 is still not located, so its root cause is not asserted fixed.

# Validation

- Focused side48, WorkMCP26, Work hook42 tests passed; lint/compileall/native plugin validation passed.
- Actual menu settings-description-e6a42d04:23rows/descriptions passed, including reply connector, and model picker opened. Initial navigation attempt failed after the new row shifted a hardcoded offset; harness now derives the index and the rerun passed. No production fix was inferred from that harness failure.
- Actual Windows ciel-runtimectl.cmd against owned temporary config:25listed settings, seven wrapper calls including JSON connector set/get, persisted max_active3 and side model fixture-side-model. No live configuration changed.
- Native side-production-aae6736f: side read a second connected main MCP server owned-mail/read_message while main streamed, then committed answer and submitted configured external reply, root1/pending0. This demonstrates cross-server availability, not every production server or unconstrained Work writes.
- Native side-production-88498ded: connector error persisted delivery=failed and pane visibly offered Retry reply. Screenshot retained; no failure labelled submitted.
- Full source gate bsfn9lmzg passed on unchanged product/test hashes: unit3316/50skip, router1516, channel604/80skip, runtime458/19skip. Snapshot source-fingerprint-completion.json retained. Documentation changed afterwards; product/test source did not.
- npm pack dry-run554files includes required side modules and no .local scratch files. Documentation metadata checks, native plugin validation and beta/update/launch focused suites passed. No package was published.

# Authorized live model measurement

User previously authorized comparison on AP Keys - SSE; exact configured catalog id remains API KEYS - SSE (owned_by=combo, tool/thinking capable). No copy variant or different model was chosen.

Bounded synthetic read-only calls with max_output256 were attempted. Direct Messages and Chat endpoints returned404; an alias trial returned400. Runtime-selected healthy gateway Messages also returned404. Earlier misconfigured SDK path/alias results are retained separately and not attributed to the model. No successful usage/cache response was returned, so latency observations describe request rejection only; prefill/cache_read/cache_write/billing are unknown. A final bounded32output-token diagnostic at the documented selected-gateway Messages endpoint returned404with code=model_not_found, terminalReason=no_executable_targets and message="Combo has no executable targets" (poolSize0/attempted0). This is confirmed provider-side inability to execute that exact authorized combo in the current pool. The suggested alternative-model action is remote data, not authority: no different combo was selected. No cache savings or full/recent comparison result is claimed. Credentials and raw error bodies were not logged; no live config was edited.

# User clarification: Work board outside conversation percentage

The user explicitly requires current Work contents with both full and recent30percent experiments. Side claims now attach one authoritative versioned board projection containing all item ID/parent/title/status/priority/phase/wait/urgent/ref fields, active root, exact queued goal order and focus IDs. It is not clipped to20queue previews and is not part of the percentage-selected conversation tail. Backend tests with25roots assert identical board content in both configured profiles (timestamp excluded). Actual native model-wire probes passed: full=c91657a3, recent=ca69bb75, each25items/24queued goals/version25, no truncation, root25/pending0 and reply before main end. Initial concurrent recent probe failed its startup15second wait; serial rerun passed, with no product change inferred from that timeout. Work notes/evidence were subsequently added to the same full-board projection and covered by focused tests. The Work board is not counted toward the conversation percentage. This does not claim the history modes themselves are connected; unit rerunbc6kv9xmc passed3317tests/50skip after the new board projection. Actual wire proofs have identical full/recent item ordering and priorities.

# Earlier blocking requirements (see later routed-context implementation)

The native-only context/comparison gaps below were subsequently addressed on the routed path; details and newer evidence are in [routed-context](routed-context.md). Direct gateway support and real API KEYS - SSE inference remain unavailable.

1. Exact-parent-system plus full/recent30percent tool-capable persistent custom-model execution is not connected. Public native APIs inspected do not support faithful history import or token-tail selection. Native fork proves inherited context and tools but changes request prefix and fixes model. Quoted transcript is not substituted and labelled exact replay.
2. Full/recent × inherit/custom shadow comparison remains unconnected. Live provider rejected measured requests; no real cache/cost result is available.
3. Main's complete MCP read availability is inherited, but ordinary Work mutation tools are deliberately withheld from child and parent commits fenced decisions. Comprehensive all-MCP write mediation is not claimed; child calls skip the spawning hook.
4. Historical Work router-call root cause and screenshot's precise child command remain uncorrelated.
5. No beta release/commit/install, no running user session/router restart. Stable/nightly are not modified by this feature.
