---
type: fix
title: P1 SSE events go first and side decisions survive a moving Work board
description: "Walkie P1 events are indexed and claimed before the aged backlog with one reserved slot. Side decisions commit on the current Work board instead of failing a stale version check. P1 Work is shared with main as a conversation row."
tags: [sse, side-intake, priority, p1, work-cas, walkie]
timestamp: 2026-11-07
---

# Verified production failures

- **Urgency was never indexed.** Walkie carries a message's urgency at `data.message_context.thread_task.priority` (1 = P1, highest, through 5; P3 by default; per Walkie `server/src/thread-tasks.ts`). The inbox read only `data.priority`. A survey of the live inbox found that field in 35 events and `data.priority` in none, so no P1 marker was ever written.
- **Aged events were claimed before priority events.** `claim()` ordered `aged, prioritized, rest`, with `aging_seconds=60`. Almost the whole backlog counts as aged, so urgent #116793 waited behind it while both `max_active=2` slots were busy.
- **Stale-version refusals lost decisions.** `side_work_decision.apply` used compare-and-swap on the snapshot version the fork saw. Main commits while each fork works (live: 1719–1721 against 1721–1723), so the decision was refused and recorded as `invalid_answer`.
- **The refusal cause was not recorded.** Walkie #116841 (15:55) returned a valid `create P1` decision with `expected_version 1725`, and the fork had already sent ACK 116876. The board was at 1727/1728 by then. The decision was quarantined with no cause recorded: `hostJson` dropped the router's error text, and `work_side_fail` had no field to store it.
- The installed old build refuses that exact decision on a copy of the live board with `WorkPlanConflict: the plan changed (stored version 1728, expected 1725)` ([replay](evidence/decision-116841-replay.txt)).

# Fix

**`39f0c97`**

- `source_priority()` maps `data.priority` (`Pn` or 1–5) first, then Walkie `thread_task.priority`, to P1–P5. The result writes the priority marker and is returned on the claim.
- Claim order is now P1, then the aged backlog, then P2–P5, then the rest. Aging still keeps old ordinary events ahead of fresh P2–P5 events.
- The mod reserves one extra slot. When `max_active` ordinary slots are full, it claims with `urgent_only`, and the router hands out only a P1 event. Only one reserved actor runs at a time.
- The fork receives `source_priority` and is told to ACK a P1 message first and to queue any work it cannot answer immediately as P1.
- `apply()` commits under the board lock on the current version. `mutate` re-validates the target (it must exist and not be done) on the board it commits to. The version the fork saw is recorded as `based_on_version`. A missing version defaults to the claim snapshot.
- A P1 `create` or `prioritize` is shared with main in two forms. A user-role conversation row (read by the model) carries the event id, item, title and summary, never the external original. A system notice (shown to the person) reads `Side P1 queued <item> · <title>`. Prioritize and advise handoffs keep their existing row.
- A decision with `sent_via` nested inside `decision`, or with `public_summary` in place of `summary`, is normalized.

**`ac4789d`**

- The router's refusal text travels with the error and is stored on `side_failure.reason`, capped at 300 characters.

# Related observation (not a code defect)

- The selected side model `API KEYS - SSE-1` returns `404 invalid_request_error: Combo has no executable targets` from the cielairouter gateway ([log](evidence/selected-model-404.txt)). Claude Code shows this as "There's an issue with the selected model". The designed fallback then retried each event once on the main model, and those retries completed. For example, the #116793 fork sent ACK 116869 and answer 116874.
- The main session's own "issue with the selected model (…_walkie_opus55-sol61-fast)" at 15:39:43 has not been explained. Router 9505 logged no `/v1/messages` request from that session between 15:39:39 and 15:41:40.

# Verification

- 352 focused tests passed and 1 was skipped, with every `CIEL_RUNTIME_*` variable unset ([output](evidence/focused-tests.txt)).
- Installed `0.2.51-local.20261107-ac4789d`: `npm ls -g` and `ciel-runtime --version` both report it. User `CIEL_RUNTIME_HOME` is `…\ciel-runtime-ac4789d094598b189f764ad4528f84758130f2e5`.
- Real data, installed build, read-only copies of the live inboxes ([replay](evidence/claim-order-replay.txt)):
  - Session 67746a0a has 43 pending events, 6 of them P1. The reserved slot takes #115628 (P1). The ordinary order is #115903, #116780 and #116802 (all P1) before any unprioritized or aged event.
  - Markers written: P1 = 6, P3 = 24.
- On the installed build, the real #116841 decision from a copy of the live board commits as `n873` (P1, `based_on_version 1725`, version 1729). It becomes the first of 12 pending goals behind the active `n766` ([replay](evidence/decision-116841-replay.txt)).
- On the installed `side-intake.js`, the mod harness shows the claims `[false, false, true, true]`: two ordinary, then one reserved P1, then none while the reserved actor runs, then the next P1 once it finishes ([result](evidence/urgency-harness-installed.json)).
  - The `resolve` call's version defaults to the snapshot (7).
  - Main receives one user row and one system notice, without the external note or original.

# Not yet verified

- The running Walkie session (pid 50792, launched 15:50) still runs the earlier build `3a6ea3e`. A live SSE check of this build needs a Walkie relaunch, which was not done automatically.
- #116841 stays quarantined as `invalid_answer`; such events are never replayed, so the ACK is not sent twice. Its Work item was never created.
