---
type: Defect
title: Duplicate side time-limit main wake
description: One side handoff was prompted into main twice. The first fix was superseded by a fenced, leased intent→started election that Stop also joins, one shared wake redactor, a host-held fence for old-router attempts, no transcript reconciliation (measured unreachable), and per-key idle prompts with a cross-reload pending registry and an idle-anchored release.
timestamp: 2026-11-12T03:35:00Z
tags: [defect, side-intake, main-wake, exact-once, sse]
---

# Observation (owned E2E run-a-20261111-105350)

All of the following is from files under `e2e-source/run-a-20261111-105350/`:

| Evidence | What it shows |
|---|---|
| `claude-debug.txt` 16:54:35.836 and .851 | Two `work_side_wake` calls with `state` and no `attempt_id` (two prepares) for one event. The first follows the `work_side_resolve` answer (the time-limit completion path), the second follows a `work_side_claim` answer (the poll path). |
| `claude-debug.txt` 16:54:36.133 and .141 | Two `$.prompt.submit` calls, both `Ciel side event requires main action` for event `1c7715c73c92…`. |
| `gateway-request-005.json` | The `Ciel side event requires main action` block appears twice. |
| `gateway-request-006.json` | `work_side_handoff` refused: `the plan changed (stored version 4, expected 3)`. |
| `evidence/board-after.json` | Handoff `1c7715c73c92` is still `requested`, `time_limit`, wake `queued`, board version 4. |

# Cause (source-confirmed)

1. **Host.** `scheduleMainWake` added its in-flight key only after `await callRouter(... 'prepare')`, so the completion path and a concurrent poll both passed the check.
2. **Backend.** `side_wake.prepare` returned an already-`submitting` row with its `attempt_id`. The host read `state == 'submitting'` as authorization.
3. **ACK conflict.** Every wake write went through a semantic transaction, which bumped the Work `version`. Main's ACK, still carrying the pre-wake version, was then refused (`gateway-request-006.json`).

# First fix and why it was replaced

The first fix reserved the key before the first await and had a duplicate prepare return a non-authorizing `already_*` state. Independent review found two lost-wake blockers in it:

- **Reload loser.** A held `submitting`. B got `already_submitting` and cached it in `wokenHandoffs`. When A recorded `failed`, claim offered the wake again, but B suppressed it forever.
- **Crash window.** prepare committed `submitting`, then the response or the process was lost before delivery. Claim offered only `pending`/`failed`, and Stop suppressed `submitting`, so the wake never recovered and was never shown.

# Current design

The wake state stays nested in `side_handoffs[event].wake`, with its own `revision`. Top-level states are unchanged (`pending`, `failed`, `submitting`, `queued`, `appended`, `uncertain`), so old readers and the old Stop gate behave as before. The new phases live in `wake.phase`.

| Step | Transition | Authorizes delivery? |
|---|---|---|
| Election, `protocol:2` | `pending`/`failed` → `submitting`/`intent`, with a fresh `attempt_id`, the module-load `owner_nonce`, a `channel` (`prompt`, `append` or `stop`) and `intent_until = now + 5 s` | No |
| Lost response | The same owner adopts its own intent at any time. A different owner gets `already_intent` (no attempt) until `intent_until`, then may supersede with a fresh attempt and lease. | No |
| Start | Owner CAS `intent` → `started`; an old or superseded owner is refused (`stale side wake intent`) and backs off with no timer, rechecked on the next claim/Stop | **Yes, exactly one presentation** |
| Record | Only the exact attempt and owner. `queued` (prompt resolved), `appended` (stores the append `uuid` receipt), `failed` (prompt drop or append deny, which re-elects) or `uncertain` (prompt or append throw). An identical repeat is idempotent. | — |
| Reconcile (backend only) | `side_wake.reconcile` is unchanged: append only, exact `uuid`. The host no longer calls it: in owned probe `state-fence-df88e27b` no `$.session.messages()` row carries a `uuid` (see below), so a transcript lookup can never match. The owner's own delivered-but-unrecorded result is recorded again instead. | Never re-sends |
| Retry | Operator confirmation plus the exact `event`/`attempt_ref`/`revision`; `started` or `uncertain` → `failed`, with an audit row holding the exact attempt id | Then a normal election |
| Legacy prepare (no protocol) | `pending`/`failed` → `submitting`/`started`, `channel=legacy`. Never supersedable. | Old host, as before |

The module-load `owner_nonce` is only a CAS fencing token for start and record. It is never treated as proof that another module or process died, or that a delivery did not happen. Superseding an intent is safe only because nothing is delivered before `started`.

Host changes in `side-intake.js`:
- Only `already_queued` and `already_appended` are cached. A loser keeps no local block.
- A failed record call keeps the delivered outcome locally and retries only the record.
- A `started` row is never sent again. Only the module that delivered it may settle it, by retrying the record of its own result. Every other `started` row stays shown as delivery uncertain.
- The delivered text carries no marker or attempt id.
- The released-wait notice (no acknowledgment needed) keeps its earlier busy-append / idle-prompt delivery.

**Deviation from the review text (approved by team-lead).** A started row with no owner outcome stays top-level `submitting`/`started` and is shown as `delivery_uncertain`. It is not rewritten to top-level `uncertain`. The reasons:
- The owner may be alive and still waiting for main to go idle.
- The installed Stop gate (8e08b97 `register.js:1831`) would present a top-level `uncertain` row.

`delivery_uncertain` is derived at read time and never stored. The owner may still record any definite outcome for its own started attempt.

# Second review pass (five confirmed defects, fixed)

1. **Raw attempt and owner exposure.** `work_status(format=json)` returned `{event_id, **row}` with the nested wake. Fixed by the shared redactor above.
2. **Marker spoofing.** The host accepted the marker as a substring of any transcript row, so an unrelated assistant or user row could settle an uncertain prompt. Fixed: the marker was removed and prompts are never reconciled. The host's later exact-uuid lookup was removed as well: `$.session.messages()` rows carry no uuid (owned probe `state-fence-df88e27b`), so it could never match on the engine.
3. **Intent supersession livelock.** Without a lease, two live owners could replace each other's intent forever before either started. Fixed: a 5 s intent lease. A crashed owner's intent is recoverable after the lease.
4. **New host on an old router.** An old router (installed `side_wake` 4b85569d) ignores `protocol:2` and answers prepare with only `{state:'submitting', attempt_id}`. The new host required `phase:'intent'`, so it returned without delivering and the row stayed `submitting` forever. Fixed:
   - Every new-router prepare/start/record result echoes `protocol:2`.
   - A result without `protocol:2` that is `submitting` with a 32-hex attempt and no phase is the old router's own authorization: the host delivers once and records that attempt.
   - Any other non-protocol-2 answer does not deliver.
   - A malformed protocol-2 `submitting` answer fails closed with one bounded log line.
   - The same attempt seen again in the same module is never delivered twice; only its unrecorded outcome is retried. Across module instances in one process, see the old-router cross-instance fence.
5. **Append receipt dropped.** A row already `appended` without a receipt returned idempotent success to the owner's later `appended` + uuid record, and dropped the uuid. Fixed: the same owner and attempt store that uuid as a wake-only write. A different uuid is refused as conflicting, and the identical uuid is idempotent.

**Wake-only writes.** These use `WorkPlanStore.transaction(..., semantic=False)`. The write runs under the same RLock and file lock and is atomic. It keeps the Work `version` and `updated_at`, and refuses (writing nothing) any change outside `side_handoffs[*].wake`.

**ACK.**
- The first ACK keeps the exact Work CAS.
- A settled identical ACK succeeds even with a stale version; a different payload or an unknown event is refused.
- A real CAS conflict sets the nested `needs_ack_retry`.

**Redaction.** One shared redactor, `side_wake.redact_wake` / `redact_handoff`, is the only path from a stored wake to model, status, REST or browser output. It may expose only `state`, `phase`, `channel`, `revision`, `needs_ack_retry`, timestamps, `attempt_ref` and the derived `delivery_uncertain`. It never exposes `attempt_id`, `owner_nonce`, a marker or a receipt uuid. Consumers:
- `work_status(format=json)`
- `work_side_status`
- the side fork's `handoff_context`
- `work_side_handoff` (MCP) and `side-handoff` (REST) results
- claim `handoff_replies`
- the REST side view

The host claim's `main_wakes` keeps the raw attempt (host-authenticated) and strips the owner fence.

**Visibility.**
- `work_side_status` reports `delivery_uncertain`, `needs_ack_retry`, the phase, the revision and an `attempt_ref`. The raw attempt id and the owner fence are never exposed.
- The web Side tab shows "Main delivery uncertain" and a confirmed "Retry main wake (may duplicate)" action (`side-wake-retry`).
- The event stream emits `side.changed` on wake revisions.

# Owned probe evidence (Stop, `$.state`, append uuid)

Every probe ran a real `claude.exe` with its own `CLAUDE_CONFIG_DIR`, a loopback fixture gateway as the provider, `CIEL_RUNTIME_TEST_ISOLATED=1`, a temp `CIEL_RUNTIME_CONFIG_DIR`, and every inherited `CIEL_RUNTIME_*`/`CLAUDE*`/`ANTHROPIC*`/`CODEX*`/`OPENAI*` variable removed. Files are under `e2e-source/stop-wake/`.

**Stop block continuation** (`stop_block_probe.py`, sha256 `8c5a8196…`; Claude Code 2.1.321; runs `stopblock-838268bd` and `stopblock-d9595ffa`). Both runs recorded the same order in `hook-events.jsonl`:

| Seq | Event |
|---|---|
| 2 | `turn.start` T |
| 3 | `turn.step` T, index 0 |
| 4 | `classic.Stop` → block (`stop_hook_active` false) |
| 5 | `turn.step` T, index 1, before the model request that carries the block reason |
| 6 | `classic.Stop` → pass, `stop_hook_active` true |
| 7 | `turn.complete` T, `answer` |

No new `turn.start` fired after the block. `classic.Stop` input carries no `turnId`.

**`$.state` and append uuid** (`state_fence_probe.py`, sha256 `969061b2…`; Claude Code 2.1.322; run `state-fence-df88e27b`). An earlier run, `state-fence-74f330ba`, used a version of the script that differed only in line splits for ruff (sha256 `2772bb9b…`, not kept) and gave the same results.

| Question | Measured |
|---|---|
| Two concurrent `$.state.set` with one `ifVersion`, one instance | `[isSet true, isSet false]` in generation A and again in B |
| A's `ifVersion` write seen after a same-process hot reload | B read `["attempt-X"]` at version 1; B's write with `ifVersion: 0` returned `isSet false, version 1` |
| Writers across the reload | A's task read version 0 before the reload, then waited on a loopback request. In this run that held connection was reset (`ConnectionResetError` when released), and A made no later host call (no report, no event). B read version 0 after the reload and its write landed (`isSet true, version 1`). Exactly one write landed. |
| `$.session.append` result | `{message, uuid}`, uuid `33ee3927-…`; the `session.append` hook saw the same uuid with door `note` |
| uuid in `$.session.messages()`, `messages({})`, `messages({as:'api'})` | Absent from all three. Rows have keys `role`, `text`, `toolUses` only. |
| uuid in the session transcript file | Present (`transcript-rows.json`) |

**Independent `$.state` evidence.** review-restart-terminal's probe `race-lifetime-probe-cbdc86cb` (Claude Code 2.1.322, isolated) found:
- `$.state` keeps its value and version across a same-process hot reload.
- `/clear` resets it and moves to a new session id. It is absent after `--continue` and after `--resume`.
- Two concurrent `set(ifVersion: 0)` in one instance: exactly one `isSet:true`. The loser read the winner's value.
- One hooks module per plugin.
- After a reload the old instance made one more successful host call, about 650 ms after the reload was written (`hb_after_reload`); its pending `$.clock.after` was cancelled.

So "no later host call" above holds only for the held call in these runs. Old-instance activity just after a reload is possible, and the compare-and-set is what fences it.

**Append and the next request (not relied on).** In both state-fence runs `model_request_carried_append` was false. The probe appended inside `turn.step` index 0, before `next(e)`. That step's request was sent about 50 ms after the append (`model_requests`, `message_count` 2) and did not carry the marker. The model answered `end_turn`, so no later request was made in the run. So an append made inside a step is not in that step's request. Whether a later request carries it was not measured here. The earlier owned probe `delivery-4f4c90b8` measured an append made while a tool ran: it reached main's next request, inside the tool-result message. Nothing in this fix depends on the in-step case.

The live-profile guard is in each `result.json`; see Remaining uncertainty for one live file change.

# Stop joins the election (`register.js`, `side-intake.js`)

- Stop elects only on a new router's redacted row: one that carries `delivery_uncertain`, in `pending` or `failed`. An old-router row gets no Stop presentation; the pump owns its fallback.
- Stop elects only when the main turn is known. Only `turn.start` binds a turn: `e.turnId`, if an engine ever supplies one on Stop, otherwise the last main `turn.start`. `sideStopWake` prepares with `channel:'stop'`. It starts only if the answer is this owner's own intent with channel `stop`; a start failure returns no block. A Stop-first, pump-first or interleaved order in one process gives exactly one delivery, and no stale-intent text is shown.
- **Superseded Stop (review defect A).** The turn epoch, `turnId`, step index, lifecycle and compact epoch are captured when the Stop begins. After the election's router calls, the existing stale guards run again. A Stop that a new turn, lifecycle, compact or conversation overtook returns no block, matching `stop-superseded`. Its started row stays unsettled and shows as delivery uncertain; nothing is recorded.
- The block keeps the established wording ("…not execution approval and must not cancel running tools. Advice data: …") and the redacted handoff view.
- The pending presentation stores the captured epoch, `turnId`, step index and lifecycle. It records `queued` only on a main `turn.step` with that epoch and `turnId` and a greater index, per the probe. A step of another turn never settles it. If the record fails, only the record is retried (at the next Stop), never the presentation.
- **Turn end before proof.** A main `turn.complete` (an interrupt is `reason: 'aborted'`) drops every unproven entry and clears the bound turn. A later stray step without `turn.start` cannot settle it, and the row stays `started` (delivery uncertain). This is the documented crash window.
- **`stop_hook_active` (team-lead decision).** A Stop-block continuation adds no uncertainty or ACK-retry notice; those wait for the next ordinary Stop, so they cannot loop. A NEW pending event is still elected there, because time-limit events need same-turn presentation. It is bounded, since each event is elected at most once.
- The uncertainty notice and the `needs_ack_retry` notice each appear at most once per identity:event:attempt_ref:revision per process, with redacted fields only.
- **Exact-attempt knowledge (review defect C).** A row is not called uncertain while this process is delivering it, or knows the exact attempt the row names (`attempt_ref`, the sha256 prefix of the id), as an unrecorded outcome, a Stop election it never presented, or a Stop presentation awaiting proof. Knowledge of any other attempt is stale and is cleared, so a foreign started row shows as uncertain and offers the retry.
- **Record landing in a Stop (review defect B).** A proven record that lands inside a Stop's retry loop excludes that event from the same Stop's uncertainty pass, which reads a snapshot taken before the record.
- **ACK conflict (decision).** `needs_ack_retry` means main already called `work_side_handoff` for the event, so it has the event. Such a row gets only the ACK notice, never a fresh "delivery uncertain" notice or a pane retry, although the conflict bumped the revision.
- **Lost Stop start answer (approved).** If the start call threw or answered unexpectedly, Stop returned no block, so this module knows the attempt was never presented. The pump then delivers it once as a prompt. For an own `stop` intent, it first starts that same attempt. For a `started` `stop` row with that exact attempt, it delivers directly. Either way it records with that attempt id and this owner and never starts a second attempt.
- An `appended` wake still gets the existing one-time reminder at Stop. When a pump wins the race while main is busy, the wake is appended into the running turn and Stop shows that reminder. It is not a second election.
- Pane: an uncertain wake row offers "Retry main wake…". It asks before acting and sends only `event_id`, `attempt_ref`, `revision` and `confirmed:true`.

# Old-router cross-instance fence

**Measurement (review-side-wake).** With the old `side_wake` contract and two module instances, the old router returned the same `{state:'submitting', attempt_id:A}` to both prepares, and two prompts were submitted. The module-local `legacyWakeAttempts` fenced one instance only. The harness reproduces this on the unchanged host (`dca981a2`): mode `old-router-reload` gave 2 submits.

**Fix.** Before delivering an old-router attempt, the host adds it to `$.state` `ciel-work.legacyWakes` (the newest 200 ids) with an `ifVersion` compare-and-set.
- Only the instance whose write lands delivers. An instance that finds the id already there delivers nothing.
- A miss re-reads, because a miss means another write landed.
- If all 32 tries miss without the id appearing, the legacy fence still lets the attempt through (unchanged). Its delivery then needs a landed per-key prompt-registry claim like every idle prompt. Measured on the final draft with review-side-wake's `legacy_fence_probe.mjs` "exhaust" case, where a foreign write lands before every compare-and-set on every key: `submits: []`, and the wake stays `submitting` (shown as delivery uncertain). The rejected freeze `cb2f0172` gave one delivery there; the first draft of this fence (`b3ef7d0b`), which delivered nothing on exhaustion, gave 0. The harness row `test_legacy_fence_exhaustion_still_delivers_once` contends only the `legacyWakes` key, so its registry claim lands and it delivers once.
- The module Set stays as well, because `$.state` resets at `/clear` (`nonce-lifetime-probe-44e40778`, `cbdc86cb`).
- The key is declared in `types/index.d.ts`. `claude plugin validate` passes with it, and fails without it: "ciel-work.legacyWakes is not declared".

**Basis.**
- `state-fence-df88e27b` and `cbdc86cb` (real engine): a claim taken before a reload is read by the reloaded module, a write at the stale version misses, and concurrent writers land once.
- The two-module-instance concurrency case is a fake-host harness result (`old-router-reload`). On the real engine no two-instance overlap was reproduced: `cbdc86cb` saw one old-instance host call about 650 ms after the reload, and `74f330ba`/`df88e27b` saw the old instance's held call reset.

# Concurrent plugin prompts (per-key registry)

**Observation.** The main-review E2E `run-20261111-161947` (`second-claude-debug.txt`) shows:
- 22:22:46.501: the mod called `$.prompt.submit` for review `11f824dc`.
- 22:22:47.650: before that prompt's turn started (`turn 1 start` at 47.897), the mod called `$.prompt.submit` for review `30353fd4`, because `working` was still false.
- No turn, `UserPromptSubmit` or transcript row followed for `30353fd4` before the kill 65 s later, and its `review_wake` record was never sent.

The same unguarded pattern was in `register.js` `deliverParallelReview` and in `side-intake.js` `noticeToMain` and `deliverToMain`.

**Owned probes** (Claude Code 2.1.322, isolated; files under `e2e-source/stop-wake/`):

| Probe | Measured |
|---|---|
| `prompt-gate-0dd78bf3` (`prompt_delivery_gate_probe.py`) | Two `$.prompt.submit` calls 3 ms apart while idle. P1's turn started at +53 ms and its promise resolved at +55 ms. P2 resolved at +4240 ms, 42 ms after P1's `turn.complete`, and started its own turn. P2 was absent from request 1 and present in request 2. An append invoked after P1's `turn.start` (before its step 0) resolved with a uuid, but was absent from P1's only request and present in turn 2's. The run's first `result.json` had an analysis bug (4260 ms, by event name); it was corrected from the recorded events. |
| `prompt-stop-28f9d226` (`prompt_stop_duplicate_probe.py`) | A Stop block carrying P2's data while P2 was held. Request 2 (the Stop continuation) carried STOP(P2). Request 3 (P2's own turn) carried STOP(P2) and the P2 prompt. Main read the same data twice; the Stop block does not cancel the held prompt. |
| `prompt-fifo-371c36de` (`prompt_fifo_engine_probe.py`, against the superseded FIFO `side-intake.js` `194fa123…`) | The engine logged exactly two `$.prompt.submit` calls, the second 30 ms after P1's `turn.start`. P2 resolved after P1's `turn.complete` and started its own turn. The duplicate `side:B` text reached no request. **Label correction:** the run's `duplicateShared: "distinct-promise"` compares the duplicate's raw return with `p2`, which is a `.then` wrapper of the first call, so it says nothing about promise identity. Identity is shown directly by `test_duplicate_prompt_key_returns_the_exact_promise_and_submits_once` and by `prompt-registry-e2bed500` (`sameAPromise: true`, two raw returns compared). |
| `fifo-review-append-clean-f006e2ae` (`prompt_append_reload_probe.d2e89a12.py`, mode `append-clean`) | An append awaited inside `turn.start` before `next(e)` resolved with a uuid before `next` returned, but was absent from that turn's only request (`request_1_has_bappend: false`). So an append cannot join a turn that is starting; batching held prompts into appends was rejected. |
| `fifo-review-reload-held-prompt-135e1028` (`.a082db8d.py`) and `fifo-review-reload-turn-only-20268a9f` (`.d2e89a12.py`) | With a main turn held open by the gateway, the rewritten module's `session.start` did not run until that turn ended (`b_loaded_while_turn_held: false` in 20268a9f; the held turn was released 30 018 ms after the rewrite). In 135e1028 the old instance's held prompt resolved after its turn ended, and the reloaded instance's own prompt then entered. |
| `fifo-review-append-turn-start-9be02fd8` | Invalid run, kept as such: native `fetch` is undefined in the plugin worker, so its reports were lost (`started: false`), and B used the same marker as a prompt. Replaced by a `$.fs` report channel and distinct markers. |
| `fifo-review-append-turn-start-98c444cc` (`.6c895172.py`) | Mixed run: a B prompt was also submitted, so its append result is not attributable. Re-run clean as `f006e2ae`. |
| `prompt-registry-35a040a8` (driver `driver-A.js`/`driver-B.js` in the run folder; the script bytes, sha256 `106e1d40…`, were not kept) | Registry exports of this draft. Prompts issued from a `$.clock.after` callback did not reach this plugin's own `prompt.submit` hook (no hook record for A, B or R; one for R2, issued inside `session.start`), so the hold in the hook did not apply and every key resolved `queued`. A `$.state` read in a dispatch that slept 3 s saw a write another dispatch made meanwhile (v0, then v1). Cause of the missing hook dispatch not established. |
| `prompt-registry-e2bed500` (`prompt_registry_engine_probe.0c6249d1.py`, against an earlier registry draft `side-intake.js` `fd2f2496…`, which still had the local-marker fallback and the unchecked timer callback; superseded by the later runs below) | Unresolved head: the driver's own `prompt.submit` hook held A for 8 s. Issued inside `session.start`: A, B, then A again, with no await between. `sameAPromise: true`; the registry held `parallel:A` and `side:B` at +335 ms; the duplicate A text reached no request. B's `prompt.submit` hook ran at +8166 ms, after A's hook ended (+8048 ms): **the engine held B behind A's unresolved hook; the module did not.** At +5003 ms (no main turn yet) the idle window resolved A and the duplicate `uncertain`, then B (+5029 ms); the registry was empty at +5636 ms. A's prompt still entered at +8060 ms and B's at +8297 ms: the released prompts were not cancelled. Reload: R was issued at A's `turn.complete` and held 9 s in the hook; the module file was rewritten at +8712 ms and, with no main turn running, the new instance's `session.start` ran at +9285 ms. It hydrated `parallel:R` (registry v5), and its own `sidePromptSubmit('parallel:R')` submitted nothing (no R2 request). The old instance kept running: its idle-window timer resolved R `uncertain` at +13312 ms and its held hook finished at +17324 ms, after which R entered (+17322 ms, `turn.start` in the new instance). The new instance's window resolved its hydrated entry `uncertain` at +14289 ms (registry v6, empty). |
| `prompt-registry-5fe2353d` (same script `0c6249d1…`, against superseded freeze `92a1347a`'s `side-intake.js` `d09ba9f1…`; `result.json` `3450fc65…`) | The same sequence and outcome as `e2bed500` on those superseded bytes: `sameAPromise: true`; registry `[parallel:A, side:B]` at +338 ms; A and its duplicate `uncertain` at +5004 ms, B at +5033 ms, registry empty at +5638 ms; A entered at +8061 ms and B at +8284 ms; the reloaded instance hydrated `parallel:R` (v5) at +9259 ms and raw-submitted nothing for it (no R2 request); its window released it at +14262 ms (v6, empty); the old instance's window fired at +13302 ms and R entered at +17317 ms. Live-profile hashes unchanged; no protected process ended; only the owned `claude.exe` was terminated. |
| `prompt-registry-01148375` (same script `0c6249d1…`, against the accepted final `side-intake.js` `9b955068…`, byte-identical to the final freeze; `result.json` `768b68b0…`) | The same sequence on the final bytes: `sameAPromise: true`; A and its duplicate `uncertain` at +5004/+5005 ms, B at +5035 ms; registry version 2 → 4 with keys `parallel:A`, `side:B` still listed (the driver logs keys only, not `status`; two writes after the claims are consistent with two terminal rows); A entered at +8077 ms and B at +8319 ms; the reloaded instance hydrated `parallel:R` (v5) at +9288 ms and raw-submitted nothing for it (no R2 request); its window released it at +14289 ms (v6); R entered at +17343 ms. Live-profile hashes unchanged; only the owned `claude.exe` (pid 82116) was terminated. Three protected `python.exe` processes outside the owned tree, all created at 02:21:35Z inside the probe window (pids 86500, 86996, 7840), had ended by the end of the run; their cause is not established. |
| `prompt-registry-a2893051` (same script `0c6249d1…`, against rejected draft `side-intake.js` `c5c992ae…`; `result.json` `8a1fabdd…`; retained as rejected evidence) | Same outcome as `01148375`: `sameAPromise: true`; A and its duplicate `uncertain` at +5004 ms, B at +5031 ms; registry version 2 → 4; A entered at +8063 ms and B at +8342 ms; the reloaded instance hydrated `parallel:R` (v5) at +9691 ms and raw-submitted nothing for it (no R2 request); its window released it at +14692 ms (v6); R entered at +17372 ms. Live-profile hashes unchanged; only the owned `claude.exe` (pid 26272) was terminated. One protected `python.exe` outside the owned tree (pid 82624, parent 1188, created 02:41:41Z inside the probe window) had ended by the end of the run; cause not established. This probe does not exercise `/clear`; its rescinded terminal-row restore is not part of the accepted product bytes. |

So a held prompt is held, not lost, and it is the only mechanism measured to start its own turn. A held prompt can enter long after it was issued (`e2bed500`: 3 s after its idle window ended).

**Superseded design.** The single module FIFO (`register.js` `0d1b4afc…`, `side-intake.js` `194fa123…`) let one never-settling submit hide every later wake, and it was per module instance. Team-lead reverted it for the design below.

**Fix (team-lead's final design, `side-intake.js`).**
- **Delivery identity.** One delivery is `key` plus the exact redacted `attempt_ref` (`ref`). Keys: `parallel:<lease>`, `side:<event>`, `notice:<sha256 prefix of the text>`. A durable review or side wake passes the `attempt_ref` of its backend attempt (`register.js` `finishParallelWake` and `side-intake.js` `submitWake`). A notice has no ref, so its hashed key is the whole identity. A new backend attempt (new `attempt_ref`) is a new delivery and never shares an old attempt's live promise or terminal record. No attempt id reaches text or logs; only the 16-hex reference is stored.
- **No global serializer.** Each identity issues its own `$.prompt.submit` at once; the engine queues prompts. Busy-path appends are unchanged and take no identity.
- **Same identity, same module.** A second call returns the exact Promise object of the first (installed before the first await) and never submits.
- **Registry.** `$.state` `ciel-work.promptPending` holds `{key, ref?, generation, status, outcome?, settledAt?}` rows: at most 200 `pending` rows and, capped separately, the newest 200 `terminal` rows. Only the oldest terminal rows are evicted; a pending row is never evicted. A row without `status` (written by the rejected drafts) reads as pending. Declared in `types/index.d.ts`; `claude plugin validate` fails without it: "ciel-work.promptPending is not declared".
- **Claim.** Before the raw submit the identity is claimed with an `ifVersion` compare-and-set. Only a landed claim (`claimed`) raw-submits. `existing` (another instance's pending row) hydrates and submits nothing. `terminal` returns that terminal outcome and submits nothing. `full`, `contended` (32 misses) and `unavailable` (oversized key, a throw, or no `$.state`) fail closed: no raw submit in any instance; the entry stays pending locally and resolves `uncertain` at the next verified idle-window expiry.
- **Terminal, not cleared.** On resolve (`queued`), drop (`failed`), throw (`uncertain`) or idle-window expiry (`uncertain`), the module first installs a bounded local terminal record (200) for the identity, before any await, so a duplicate during the state write gets that terminal promise. It then replaces its own exact pending generation with a terminal row by compare-and-set. A terminal row already present wins while `$.state` is readable: a raw settlement that arrives after an expiry keeps `uncertain` and never re-hides the row. If terminalization throws, one module can retain its local outcome while another module reads the already-durable terminal outcome; `local-vs-durable-terminalize-throws` measured local `queued` versus durable `uncertain`, with no resubmission. Another generation's row is never touched.
- **Same identity after it is terminal** (a duplicate during the write or after it, a pump re-offer, a reproduced released-wait notice with the same text, the old-router compat review whose `noticedParallelReviews` is cleared at session.start): the terminal outcome, 0 raw submits while its bounded terminal record remains in `$.state` or that module. Notices and old-projection compat have no durable backend fence, so they can issue again after process restart, `/clear` plus reload, or eviction by 200 newer terminal identities (`terminal-cap-reissue`: raw 201 → 202). A person-confirmed pane retry is exempt only because the backend turns it into a new attempt with a new `attempt_ref`; an automatic path is never treated as person-confirmed.
- **Idle-anchored anomaly release.** A 5 s window is armed when an idle-path prompt is issued or a row is hydrated, only if main is not busy at that moment, and again for every unresolved entry at each main `turn.complete`. `turn.start` cancels every window. The callback acts only if it is still that entry's current timer (`entry.timer === timer` and the arming counter), the entry is still the identity's local entry with the same generation, and main is not busy; otherwise it does nothing, so an ineffective `cancel()` cannot expire an entry early. There is no wall-clock timeout. If `$.clock.after` throws or returns no usable timer at an idle boundary, the entry is settled `uncertain` at once (no raw submit after that point) instead of staying hidden.
- **Owned and hydrated.** Only the exact owner records the durable side or review outcome. A hydrated foreign side attempt is never recorded here (records are owner-fenced); its release only ends this module's visibility marker, and the started row already projects `delivery_uncertain`. A hydrated parallel review becomes Stop-visible at release; its durable wake stays `submitting`. When an instance that waited on another instance's identity releases it, it returns the terminal outcome that owner wrote, if one exists.
- **While an identity is unresolved** (local entry or hydrated row): the parallel Stop gate leaves that lease's review out (no data, no hold); `sideWakeKnown` treats the exact attempt as known, so the Stop uncertainty notice and the pane retry are suppressed for that attempt only; the durable row stays `submitting`/`started`. An entry for another attempt does not hide uncertainty.
- **Hot reload.** `startSide` (session.start) re-claims, then hydrates pending rows into local entries and terminal rows into local terminal records. It never resubmits. A fresh process starts with no `$.state` (`nonce-lifetime-probe-44e40778`), so nothing is hydrated, and a durable started row is already shown as `delivery_uncertain`.
- **`/clear` in the same instance.** Measured on one run (`nonce-lifetime-probe-44e40778`, `phase1-debug.txt`): at `/clear` the loaded module instance received `classic.SessionStart` with `source: 'clear'` (settled 18:35:33.211Z) and `$.state` was already at version 0 inside that hook; no plugin `session.start` was raised between the reloads at 18:35:28.961Z and 18:35:41.544Z. `register.js` hooks `classic.SessionStart` and, before `next`, calls `sidePromptReassert`, which re-claims every local unresolved raw generation (own or hydrated) with the same compare-and-set; an exact generation already present writes nothing. A failed re-claim never issues a new raw prompt; the existing raw may still enter, and the entry is released `uncertain` at its idle window. A transient failure keeps the entry's own row clearable (it does not forget a generation it owns). Whether `session.end` fires at `/clear` is not measured; `resetParallelHostState` and `session.end` do not touch the prompt entries, terminal records or `$.state` rows, and the harness shows the re-claim still landing after a `session.end`. The re-claim path is harness-only evidence; it was not run on the real engine.

**Liveness table** (rows: path; columns: condition). "Hidden" means hidden from Stop's review gate, Stop's uncertainty notice and the pane retry while unresolved.

| Path | Finite (resolves) | Anomaly (never resolves, idle) | Restart (fresh process) | Reload (same process) |
|---|---|---|---|---|
| Parallel review | Hidden, then recorded `queued`; Stop gate shows it | Hidden for 5 s idle, then recorded `uncertain`; Stop gate shows it; late resolution keeps `uncertain` | Nothing hydrated; Stop gate shows it at once; not resubmitted | Hydrated and hidden; shown after the new instance's 5 s window; not resubmitted; the old environment's late resolution reads the terminal row and records `uncertain` |
| Side wake | Hidden, then `queued` | Hidden for 5 s, then `uncertain`; Stop notice and pane retry offered | Started row shown uncertain at once; pane retry offered | Hydrated (exact `attempt_ref`) and hidden; shown after 5 s; the old owner records the terminal `uncertain` |
| Pane retry (person-confirmed, new attempt) | As side wake (new `attempt_ref` issues) | As side wake: another retry needs the may-duplicate confirmation again; the same `attempt_ref` never re-issues | As side wake | As side wake |
| Released-wait notice | Logged `queued` | Logged `uncertain` | Reproduced notice is issued again (no state) | With the row present: not issued again, logged `uncertain` after 5 s. After `/clear` then reload: reproduced notice issues once again (documented state-loss residual) |

Every cell is a test: `test_prompt_liveness_table_for_review_side_wake_and_retry` (12 cells, real `register.js` + `side-intake.js`, two fresh module copies for restart/reload) and `test_release_notice_liveness_table`.

# Residuals

- **Two `claude.exe` on one session** stay at-least-once on an old router. The old router cannot tell a duplicate prepare from a fresh one, and `$.state` is held by each process's own host. That a claim in one `claude.exe` is invisible to another is not measured; the fence is not relied on there.
- **No `$.state` on the engine.** The old-router fence still falls back to the module Set (unchanged), but every idle prompt now fails closed at the per-key claim: nothing is raw-submitted, and the started row stays visible as delivery uncertain. The harness mode `old-router-reload:nostate` gave two deliveries before the registry and gives none now. The real engine provided `$.state` in every owned probe (`state-fence-df88e27b`, `prompt-registry-e2bed500`).
- **Fence exhaustion.** After 32 missed legacy claims the attempt is let through, so it may be a duplicate across instances, but only if its prompt-registry claim then lands. If `$.state` is contended for that key too, nothing is sent and the wake stays visibly delivery uncertain (measured, `legacy_fence_probe.mjs` exhaust).
- **Superseded Stop.** Its started row is never presented or recorded. It stays delivery uncertain until a person retries or main acknowledges.
- **Held prompt latency.** A second plugin prompt enters only after the running turn ends (`prompt-gate-0dd78bf3`), so a time-limit wake behind a long turn waits for that turn's end. The engine also held a prompt behind another prompt's unresolved `prompt.submit` hook (`prompt-registry-e2bed500`); the module adds no wait of its own.
- **Person-confirmed retry can duplicate.** A retry under a new `attempt_ref` raw-submits while the released prompt of the old attempt can still enter: `prompt-registry-e2bed500` and `5fe2353d` measured a released prompt entering about 3 s after its release. Only a person's confirmation (the pane's may-duplicate prompt) creates that new attempt.
- **Bounded and lost notice tombstones (documented residuals, team-lead ruling).** Notices and old-projection compat have no durable backend fence. Their identity can issue again after a fresh process (no `$.state`), after `/clear` resets `$.state` followed by a reload, or after 200 newer terminal identities evict the oldest tombstone. Harness `test_notice_after_clear_and_reload_is_a_documented_state_loss_residual` measures 1 raw after `/clear` plus reload; reviewer probe `terminal-cap-reissue` measures raw 201 → 202 with 1 pending and 200 terminal rows. Whether the same notice is reproduced in real use is not established (static). Durable review and side wakes remain fenced by their backend rows; the register-level `skip`/`fails` controls show 0 raw submits.
- **Anomaly release can duplicate.** After a key's 5 s idle window it is recorded `uncertain` and shown, but its engine prompt is not cancelled and can still enter (`prompt-registry-e2bed500`: 3 s after the release). A person who confirms the pane retry in that gap can make main read the event twice. A finite prompt resolved in 55 ms in `prompt-gate-0dd78bf3`; a prompt held longer than 5 s of idle main is the anomaly this release exists for. Whether such a hold happens outside a plugin hook is not measured.
- **Registry scope.** `$.state` lives in one `claude.exe`. Two processes on one session are not fenced by it. After `/clear`, a key is fenced again only if its re-claim landed.
- **`/clear` then reload without a landed re-claim** (harness `prompt-key-clear`, variants `skip` and `fails`): the reloaded instance raw-submits nothing, because the durable `submitting` review wake or `started` side wake is never re-offered for delivery. It does not hydrate the key, so it presents the review at Stop, or the side wake's uncertainty and pane retry, at once. If the first instance's prompt later enters, main reads that event twice (a duplicate presentation, not a second raw submit). At the registry level alone (`clear-reload` `skip`/`fails`), a second raw submit of the same key does happen; the durable producers are what prevent it.
- **Old instance after a reload.** With its `prompt.submit` hook still in flight, the old instance kept running after the reload: its `$.clock.after` window fired and its hook finished (`prompt-registry-e2bed500`). Its late clear is fenced by generation, and its late record by owner. `cbdc86cb` (no hook in flight) saw no old-instance timer fire; the cause of the difference is not established.
- **Intent lease liveness.** If every prepare→start gap exceeds `INTENT_LEASE_SECONDS` (5 s), each new owner supersedes the last and the row is never started. The reviewer measured revision 40 and no start. Safety holds: nothing is delivered before `started`. The host calls start immediately after prepare: only synchronous checks lie between the prepare answer and the start call (`side-intake.js` `scheduleMainWake` and `sideStopWake`). No path where that gap exceeds 5 s has been shown, so the code is unchanged.

# Store writer audit (refinement 2)

- Every board write goes through `WorkPlanStore.transaction`, under the in-process RLock plus `exclusive_file_lock(path)`. `_write_unlocked` is the only file write.
- `WorkSessionStore.transaction` also takes the lineage lock. Retention uses `expire_completed`, which also goes through `transaction`.
- The installed 8e08b97 store has the same structure.
- `normalize_document` (current and 8e08b97) deep-copies `side_handoffs` and validates only `row.state`, so nested wake fields survive.
- No lockless writer was found.

# Tests

The first 23 tests below fail on the frozen first-fix snapshot. That snapshot was rebuilt from hash-verified bytes, and the tests ran against it from a temporary copy. The later rows were also run against the unchanged host from a temporary copy: `register.js` `a923bb10…`, which differs from the draft base `135fc5e6…` only in `parallelRestartSubmitted`, and `side-intake.js` `dca981a2…`.
- Every Stop and pane row fails there.
- `test_old_router_attempt_is_delivered_once…` fails there, with 2 deliveries.
- The two existing-wording rows fail there only because their scenarios now drive `turn.step`, which the unchanged `register.js` does not hook.
- `test_legacy_fence_without_host_state…` and `test_record_failure_retries_record_only` pass there. They describe behaviour the unchanged host already has.

The adversarial rows (from `test_lost_stop_start_answer…` onward) were run against the reviewed draft (`register.js` `81949d1a…`, `side-intake.js` `6da3de4c…`, the reviewer's copy):
- Defects A, B (both fault counts) and C, the stray step after `turn.complete`, and the ACK double notice all reproduce there.
- The lost-start and continuation rows pass there too; they guard approved behaviour.
- The exhaustion row's "before" is review-side-wake's own measurement on `b3ef7d0b`: 0 submits.

| Test | Proves |
|---|---|
| `test_side_wake_protocol.py::test_parallel_prepares_elect_exactly_one_started_delivery` | Eight threads on independent stores: exactly one reaches `started` |
| `…::test_crash_matrix_before_intent_lost_intent_old_start_and_started` | Before intent the wake is still offered. A lost intent is adopted by its owner. A superseded owner's start is fenced. `started` is never re-elected and shows as `delivery_uncertain`. The owner fence is not in the claim payload. |
| `…::test_only_exact_append_receipt_settles_and_prompts_never_reconcile` | A prompt is never reconciled. An append needs the exact recorded uuid, and wrong, missing or empty uuids are refused. |
| `…::test_intent_lease_bounds_reviewer_adversarial_schedule` | The reviewer's alternating schedule starts in the first round |
| `…::test_expired_intent_after_crash_is_recoverable` | Refused at 104.9 s, superseded at 105 s, crashed owner's start fenced |
| `…::test_no_model_status_rest_or_browser_output_exposes_wake_secrets` | Walks `work_status` json and text, `work_side_status`, the fork `handoff_context`, REST side/board/state/queue, and the REST and MCP ACK results. None contains the attempt id, the nonce, the receipt uuid, a marker or any standalone 32-hex token. |
| `…::test_appended_without_receipt_keeps_owner_uuid_and_refuses_a_different_one` | Reviewer sequence: the uuid is kept with no Work version change; the identical uuid is idempotent; a different uuid and a non-owner are refused |
| `…::test_every_result_echoes_protocol_two` | prepare/start/record/duplicate/legacy results all carry `protocol:2` |
| `test_side_intake_mod.py::test_new_host_on_old_router_delivers_once_and_records_legacy_attempt` | Fake of the exact 4b85569d contract. A fresh prepare gives one delivery and one record. A repeated prepare returning the same old attempt gives no second delivery; only the failed record is retried. |
| `…::test_malformed_protocol2_prepare_fails_closed_with_bounded_log` | No delivery; one log line under 300 characters with no attempt id |
| `…::test_host_never_writes_wake_secrets_to_prompt_log_pane_or_fork` | Raw attempt, nonce and receipt on the host claim path never reach a prompt, append, pane log, status line or the side fork prompt the host builds. (Confirmation test: it passes on the previous freeze too. A temp copy with an injected leak was detected.) |
| `test_side_intake_mod.py::test_transcript_rows_never_settle_an_uncertain_prompt` | Spoofed assistant and user rows never settle |
| `…::test_intent_lease_ends_reviewer_livelock_schedule` / `…::test_crashed_intent_recovers_only_after_its_lease` | Host-level lease behaviour |
| `…::test_record_is_fenced_idempotent_and_keeps_receipt` | Record needs `started` and the owner. An identical repeat is idempotent and the uuid receipt is kept. |
| `…::test_wake_writes_keep_work_version_and_ack_forms_still_commit` | Wake writes leave the version, `updated_at`, nodes and active root unchanged. Plain and activate+clear ACKs with the pre-wake version commit. |
| `…::test_unrelated_work_change_still_conflicts_first_ack_and_marks_retry` | A real conflict still fails and sets `needs_ack_retry`, which is cleared on success |
| `…::test_duplicate_identical_stale_ack_succeeds_and_changed_payload_fails` | Idempotent settled ACK; a different payload or an unknown event is refused |
| `…::test_manual_retry_needs_confirmation_exact_revision_and_is_audited` | Needs confirmation, the exact ref and the exact revision; the audit row holds the exact attempt; a new election follows |
| `…::test_legacy_prepare_is_started_and_never_superseded` | A no-protocol prepare starts as `legacy`; protocol 2 cannot supersede it; a legacy prepare after an intent does not authorize |
| `…::test_every_transition_keeps_old_reader_states_and_old_normalizer_round_trips` | Top-level states stay familiar. The 8e08b97 `normalize_document` (from git) round-trips the wake exactly. |
| `…::test_concurrent_wake_and_work_writers_preserve_each_other` | 20 wake-only and 20 Work writes run concurrently; all of them survive |
| `test_work_plan_store.py::test_non_semantic_transaction_preserves_version_and_refuses_other_changes` | A non-wake change in `semantic=False` raises and the file bytes stay identical |
| `test_side_handoff_flow.py::test_concurrent_wake_prepares_authorize_exactly_one_attempt` | Barrier race through real prepare and start: one start, the loser fenced, then failed → re-elected → queued |
| `test_side_intake_mod.py` race, reload, `reload-intent`, `reload-failed`/`stale-loser`, `orphan-started`, `lost-prepare`, retry, `record-fail`, drop/throw, `throw-marker`, deny/`append-idle` | Host behaviour for each crash window and delivery outcome (harness `tests/side_time_limit_harness.mjs`) |
| `test_work_http_side_receipts.py::test_uncertain_main_wake_is_visible_and_retry_requires_confirmation` | The REST side view shows the uncertain state without exposing the attempt id or owner. Retry needs confirmation and the exact revision. |
| `test_work_web_ui.py::test_side_and_parallel_tabs_render_operator_controls` | "Main delivery uncertain" text and the confirmed retry command |
| `test_work_mod_write_gate.py::test_stop_presents_only_its_own_started_election_and_records_on_the_next_step` | prepare→start at Stop; row `started` until T#1, then one `queued` record; the continuation Stop and a later Stop add nothing; no attempt id, owner or `attempt_ref` in the block |
| `…::test_stop_proof_is_bound_to_the_blocked_turn_and_a_later_step` | A step of turn U, or T#0 again, records nothing; T#1 records |
| `…::test_turn_end_before_the_proving_step_leaves_the_wake_uncertain` | `turn.complete` (aborted) before T#1: no record, no re-presentation; the next ordinary Stop shows the uncertainty notice |
| `…::test_uncertain_wake_and_ack_retry_are_each_noticed_once` | Each notice once per key; none while `stop_hook_active` |
| `…::test_old_router_row_gets_no_stop_presentation` | No wake call and no block for an old-router row |
| `…::test_stop_and_pump_in_one_process_present_a_wake_exactly_once` | Stop-first, pump-first and interleaved: one delivery, no stale text |
| `…::test_lost_stop_start_is_never_stranded_or_called_uncertain` | Start answer lost before or after commit: no block, no uncertainty notice or pane retry, one pump prompt, `queued` |
| `…::test_pane_wake_retry_needs_confirmation_and_sends_redacted_fields` | Cancel sends nothing; confirm sends only the redacted fields |
| `…::test_high_priority_advice_prompts_main_at_safe_stop_even_without_auto_continue`, `…::test_time_limit_handoff_keeps_bounded_deadline_and_evidence` | Existing wording and time-limit evidence, now through the Stop election on a new-router row |
| `test_side_intake_mod.py::test_old_router_attempt_is_delivered_once_across_module_instances` | Two instances, same old attempt: one delivery; `legacyWakes` holds the attempt at version 1 (2 deliveries on the unchanged host) |
| `…::test_legacy_fence_without_host_state_fails_closed_at_the_prompt_registry` | Changed: without `$.state` neither instance raw-submits (2 deliveries before the registry); the started row stays |
| `…::test_record_failure_retries_record_only` | With real-shaped messages (no uuid) an append's record is retried like a prompt's (4th call commits); no reconcile call |
| `test_side_wake_protocol.py::test_stop_channel_is_a_fenced_protocol_two_delivery_channel` | Backend prepare/start/record for `channel:'stop'` |
| `test_work_mod_write_gate.py::test_lost_stop_start_answer_is_delivered_under_the_same_started_attempt` | Committed and uncommitted: one router attempt; every start and the one `queued` record carry that attempt id and owner |
| `…::test_superseded_stop_election_presents_nothing_and_records_nothing` | Defect A: no block, no record; the row is later shown uncertain |
| `…::test_record_that_lands_late_never_calls_the_wake_uncertain` | Defect B: recordFaults=1 and =2; never "uncertain", one start |
| `…::test_stale_knowledge_of_another_attempt_never_hides_uncertainty` | Defect C plus its control: Stop notice and pane retry shown |
| `…::test_a_step_after_the_turn_ended_never_settles_a_stop_presentation` | `turn.complete` (aborted or answer), then a stray step: no record |
| `…::test_continuation_stop_gates_only_notices_and_elects_a_new_event_once` | `stop_hook_active`: no notices; a new event is presented once, then no more blocks |
| `…::test_ack_conflict_gives_only_the_ack_notice` | The ACK-conflict decision |
| `…::test_pump_between_stop_and_step_and_second_handoff_are_not_presented_twice` | Pump during proof, a second handoff, and the pane retry, from the reviewer's set |
| `test_side_intake_mod.py::test_legacy_fence_exhaustion_still_delivers_once` | 32 foreign writes on `legacyWakes` only: the legacy fence lets it through, the registry claim lands, one delivery, recorded through the legacy path |
| `test_work_mod_write_gate.py::test_both_idle_prompts_are_issued_at_once_and_each_records_its_own_outcome` | The E2E interleave: two reviews, two side wakes, review→side, side→review, and reverse release order. Both `$.prompt.submit` calls happen before either resolves; each key records only when its own promise resolves; the other stays pending |
| `…::test_stop_gate_skips_only_reviews_whose_prompt_is_unresolved` | No review data while both are unresolved; then only the resolved one (either order); then both |
| `…::test_prompt_drop_or_throw_records_that_key_and_leaves_the_other_pending` | drop → `failed`, throw → `uncertain` for that key; the other key stays pending and later records `queued` |
| `…::test_an_issued_prompt_key_is_shared_and_submitted_once` | A duplicate key shares the promise; one submit |
| `…::test_latent_prompt_never_blocks_other_keys_and_is_exposed_after_its_idle_window` | Never-settling A: B and L1 are issued and recorded; A is hidden from Stop and the pane; no window during the main turn; after `turn.complete` and its window, A is recorded `uncertain`, the uncertainty notice names it once and the pane offers retry |
| `…::test_prompt_liveness_table_for_review_side_wake_and_retry` | The 12 review/side/retry cells of the liveness table (finite, anomaly, restart, reload) |
| `…::test_submitted_result_wakes_main_once_without_reviewer_fork` | Changed assertion: the one 5 s idle window is the only new timer (no review polling) |
| `test_side_intake_mod.py::test_pending_main_wake_never_holds_side_claims` | Changed assertion: w1 and w2 each issue their own submit at once: 2 submits. The FIFO freeze `0d1b4afc` asserted 1; before the FIFO (`378f6d73`) it asserted 2. Claims still run |
| `…::test_old_router_attempt_is_delivered_once_across_module_instances` | Changed assertion: the delivery's own identity row is `terminal`/`queued` at version 2 (claim, then terminalize) beside `legacyWakes` |
| `…::test_duplicate_prompt_key_returns_the_exact_promise_and_submits_once` | The exported function's return value is identical (`===`, no wrapper) for the duplicate |
| `…::test_latent_prompt_key_never_blocks_other_keys` | A side key, a review key and a notice key: the latent one blocks neither; its window releases it `uncertain` |
| `…::test_idle_window_never_runs_during_a_main_turn` | No window in a turn; a key issued while busy gets one only at `turn.complete` |
| `…::test_same_prompt_key_from_two_module_instances_is_submitted_once` | Two module instances, one identity: one submit; the waiting instance releases at its own window and returns the owner's terminal `queued` |
| `…::test_reload_expiry_is_terminal_for_the_same_ref_and_a_new_ref_may_issue` | Replaces the rejected `test_reload_hydrates…` (which required a second raw submit after a hydrated expiry). Hydration with the exact ref, no resubmit, terminal `uncertain` row; the same ref in this and a later instance returns `uncertain` with 0 raw; a new ref issues; the old instance's late raw keeps `uncertain` and leaves the new pending row |
| `…::test_duplicate_during_or_after_terminalization_never_resubmits` | A duplicate while the terminal row is being written and after it: `uncertain`, 0 raw; a reproduced notice with the same text: `queued`, 0 raw |
| `…::test_terminal_rows_are_capped_without_evicting_pending_rows` | 3 pending + 200 terminal rows, one more terminal: pending rows kept, 200 terminal rows, the oldest evicted, the new one kept |
| `…::test_clock_failure_at_idle_settles_uncertain_without_raw_submit` | `$.clock.after` throws: `uncertain`, 0 raw, not pending, terminal row written |
| `…::test_transient_reassert_failure_keeps_the_exact_row_clearable` | `$.state` throws during the re-claim: the later settlement still turns its own row terminal (no orphan pending row) |
| `test_work_mod_write_gate.py::test_compat_review_without_durable_wake_stays_terminal_across_session_start` | A review row with no durable wake (older projection): after session.start clears `noticedParallelReviews`, still 1 raw submit |
| `…::test_prompt_drop_or_throw_clears_the_key` | drop/throw clear the row and the marker |
| `…::test_unclaimable_prompt_fails_closed_until_its_idle_window` | Full (200 rows untouched, version unchanged), contended, oversized, state-throw and no `$.state`: no raw submit in either instance, duplicate shares, `uncertain` after the window |
| `…::test_idle_windows_are_anchored_to_turn_events_and_late_raw_resolution_stays_uncertain` | One window at idle issue; none during a turn; three attempted expiries while busy (a turn longer than 15 s) release nothing; `turn.complete` arms one 5000 ms window; a new turn inside it cancels it and its stale callback cannot release; expiry then gives `uncertain`, and the raw resolution after it keeps `uncertain` and the row removed |
| `…::test_clear_reasserts_unresolved_raw_fence_before_reload` | `$.state` reset with a raw prompt unresolved: the re-claim restores the same generation, the reload hydrates and raw-submits 0; controls `skip`/`fails` show the registry-level consequence (1 reload raw submit) |
| `test_work_mod_write_gate.py::test_clear_reasserts_the_prompt_fence_and_a_reload_never_submits_again` | Real `register.js`, review and side: `classic.SessionStart` after the reset re-claims, the reload hydrates (hidden until its window, 0 raw submits); without or with a failing re-claim the reload still raw-submits 0 and presents at once |
| `…::test_release_notice_liveness_table` | The notice row of the liveness table |
| `…::test_notice_after_clear_and_reload_is_a_documented_state_loss_residual` | Labelled residual: resolved notice, `/clear` resets `$.state`, reassert writes no terminal row, reload, same notice: 1 raw. The original instance still returns its local terminal (0 more raw there) |
| `test_work_mod_write_gate.py::test_clear_reasserts_the_prompt_fence_and_a_reload_never_submits_again` (`end` variant) | `session.end` before the `/clear` boundary: the entry survives, the re-claim lands, the reload raw-submits 0 |

Fail-before, on a hash-verified copy of the previous freeze (`register.js` `0d1b4afc…`, `side-intake.js` `194fa123…`, `types/index.d.ts` `986a5d43…`) with the new tests, no import adapter:
- Fail there: the interleave (both submits were not in progress: `['L1'] != ['L1', 'L2']`), the Stop gate, drop/throw, latent, all 12 table cells, the timer row (no 5 s window), `pending_main_wake` (1 submit), `old_router…` (no registry row), identity (no registry row; the identity check itself also holds there), cross-instance (2 submits), notice table (no registry, and the reload instance submitted again), `test_legacy_fence_without_host_state…` (2 deliveries), `test_clear_reasserts_the_prompt_fence…` (no `classic.SessionStart` hook).
- Error there, because the frozen module lacks `sidePromptHydrate`/`sidePromptTurnStart`/`sidePromptTurnComplete`/`sidePromptReassert`: latent-key, idle-window, reload, `unclaimable`, `windows` and `clear-reload` (`prompt_registry_harness.mjs` exits 1).
- Pass there: `test_an_issued_prompt_key_is_shared_and_submitted_once` and `test_prompt_drop_or_throw_clears_the_key`. They guard behaviour the FIFO already had.

Second freeze (`register.js` `cb2f0172…`, `side-intake.js` `09aa2431…`) against the new tests, hash-verified copy, no import adapter:
- `unclaimable`: 2 raw submits per case (the local-marker fallback).
- `windows`: a callback armed before a new `turn.start`, invoked after main was idle again, released the key (`{'value': 'uncertain'}` where `None` is required): its callback checked only `settled` and `mainBusy()`.
- `clear-reload` and `test_clear_reasserts_the_prompt_fence…`: no `sidePromptReassert` and no `classic.SessionStart` hook there.
- `test_legacy_fence_without_host_state…`: 2 deliveries.

Rejected freezes `cb2f0172` and `c5c992ae`, and superseded freeze `92a1347a`. Team-lead rejected `cb2f0172` (`register.js` `cb2f0172…`, `side-intake.js` `09aa2431…`) on three contract mismatches, which review-side-wake confirmed independently: an unclaimable key still raw-submitted; `local.promise.then(() => sidePromptSubmit(...))` could resubmit the same key after an `uncertain` expiry; no `/clear` re-claim. `92a1347a` (`register.js` `92a1347a…`, `side-intake.js` `d09ba9f1…`) was frozen before the review findings were delivered: it fixed fail-closed and the re-claim but had no terminal records, keyed entries by key alone, and left an entry hidden when `$.clock.after` threw. Final product bytes are `eab3ad33` (`register.js` `eab3ad33…`, `side-intake.js` `9b955068…`, `types/index.d.ts` `542ff492…`): terminal records, key + ref identity, the clock-failure release and N1. `c5c992ae` added a `/clear` terminal-row restore, but team-lead rejected it after review because notices are already documented as at-least-once across process restart and terminal-cap eviction; it is kept only as rejected evidence. The final freeze changes journal and tests only. The four earlier snapshots are kept unchanged under `.local/tmp/freeze-sse-cb2f0172/`, `.local/tmp/freeze-sse-92a1347a/`, `.local/tmp/freeze-sse-eab3ad33/` and `.local/tmp/freeze-sse-c5c992ae/`.

Against `92a1347a` (hash-verified copy, no import adapter), the final tests fail as follows:
- terminal cap: the harness exits 1 (no terminal rows);
- clock failure: `{'value': None, 'raw': 1, 'pending': True}`;
- duplicate during/after terminalization: `('uncertain', 'queued', 'queued')` (the duplicates raw-submitted);
- reload: no terminal row, and the same ref raw-submitted again;
- cross-instance: the waiter returned `uncertain` instead of the owner's `queued`;
- transient re-claim failure: an orphan pending row `side:O`;
- the three reload table cells: the late raw recorded `queued` after the window had released it;
- compat review: 2 raw submits after session.start.

Review-side-wake's final three harness-only modes are archived under `e2e-source/stop-wake/registry-adversarial-final-ruling/` with the exact reviewer script `registry_adversarial.mjs` `1efd9ab8…`, accepted `side-intake.js` `9b955068…`, and output `results.jsonl` `3a5a1431…`:
- `local-before-durable-race`: both local promises were `uncertain` after the held read was released; 1 raw submit; durable terminal `uncertain`.
- `local-vs-durable-terminalize-throws`: module A and its duplicate returned local `queued`, module B returned durable `uncertain`; 1 raw submit, so the outcome mismatch did not resubmit.
- `terminal-cap-reissue`: an evicted old notice issued again, raw 201 → 202; 1 pending and 200 terminal rows.

Review-side-wake's probes (`e2e-source/stop-wake/registry-adversarial-final/`: `registry_adversarial.mjs` `cc749936…`, `legacy_fence_probe.mjs` `b70feb67…`, outputs `results.jsonl` `0c9607f8…` and `legacy-results.txt` `03620ff6…`, rerun byte-identically on accepted `side-intake.js` `9b955068…`; harness-only, scripted hosts, not the engine):
- `dup-during-expiry-clear`: 1 raw (`92a1347a`: 2); `dup-after-expiry`: 1 raw with state, 0 without (`92a1347a`: 2 with state);
- `retry-ref`: the same ref adds no raw submit, a new ref issues (`92a1347a`: the same ref raw-submitted again);
- `clock-failure`: `uncertain`, 0 raw, not pending (`92a1347a`: 1 raw, still pending);
- `fail-closed`: full, contended, unavailable and no-state all 0 raw, then `uncertain`;
- `clear-then-reload`: registry restored, 0 raw in the second instance; `stale-timer-after-turn`: the stale callback releases nothing; `busy-expiry`: nothing expires while busy; `late-raw-after-expiry`: stays `uncertain`;
- The reviewer helper `keys()` lists pending and terminal rows alike; read with status, `reassert-orphan` on `9b955068` ends with `side:O/a/terminal:queued` and `side:O/b/terminal:uncertain`; ref `b` is pending only before its idle window (no orphan pending row).
- `hydrated-blocks-new-ref`: a new ref issues at once beside a hydrated old ref; `reassert-registered` and `reassert-orphan`: no orphan pending row, and another instance's row is never removed;
- `legacy_fence_probe.mjs`: `shared` 1 submit; `exhaust` 0 submits (the registry claim is contended too, so it fails closed; before the registry it delivered once); `throws` 0 submits; `record-fail` 1 append, recorded after retries.

Group runs from this revision record the `~/.claude.json` hash before and after each group (`summary.txt`, `claude_json=` and `mtime=`), so a change can be attributed to one group's time window. Final journal/test-only run on product `eab3ad33`/`9b955068`/`542ff492`, private draft `probe/groups-v6-final`: `side` 210 OK and `gate` 120 OK; each group's recorded `~/.claude.json` before/after hashes are identical; other live files and the draft sources are unchanged. Earlier runs recorded it only around the whole sequence; there it changed during the `gate` window (01:46:53Z) and the `restart` window (02:28:34Z) with no test temp prefix or draft path in the file, cause not established.

Every scripted host that drives an idle prompt now has a `$.state` fake (version plus `ifVersion` compare-and-set), as the engine does: `side_intake_harness.mjs`, `side_release_harness.mjs`, `side_secret_walk_harness.mjs`, `side_wake_harness.mjs`, `side_urgency_harness.mjs`, `work_mod_harness.mjs` (`host`, `advHost`, `prompt-key-dedup`) and `work_mod_real_backend_harness.mjs` (reset for a new process, kept for a reload). The run before the fakes were added (`probe/groups-probe1`, private draft) reported side 3 failures and 4 errors, gate 17 failures and real backend 4 failures; it also included registry tests that were being rewritten for the fail-closed rule at the same time, so not every one of those is attributable to a missing fake.

Harness timing changes (no assertion changed): a prompt is now issued after its registry claim, so `side_time_limit_harness.mjs` and `side_intake_harness.mjs` wait for 8 quiet 5 ms ticks instead of one tick; `side_intake_harness.mjs` does not queue the 5 s window (it never elapses in that run); `old-router-exhaust` contends only the `legacyWakes` key; `side_wake_harness.mjs` waits for both submits and releases w1's by its event id.

# Separate pre-existing observation: side inbox claim generation

This was found by the output walk and is not a wake defect. `work_side_status` (a host-authenticated, coordinator-only tool) returns `side_inbox_receipts[*].processing.generation`, a 32-hex side inbox claim generation. That is existing behaviour and was not changed here. The following check that no model-visible or browser path carries it:
- `test_no_model_status_rest_or_browser_output_exposes_wake_secrets` allows a 32-hex token only in `work_side_status`. `work_status` (json and text), the fork `handoff_context`, the REST side/board/state/queue views and both ACK results must contain no 32-hex token at all.
- `test_host_never_writes_wake_secrets_to_prompt_log_pane_or_fork` gives the claim a 32-hex generation. It never reaches the side fork prompt, the main prompt, an append, the pane log or the status line.

# Remaining uncertainty

- No fresh owned E2E run of the whole design. The Stop, `$.state`, append-uuid and prompt-registry behaviour above is from the focused owned probes.
- The unresolved head in `prompt-registry-e2bed500` was made by the probe's own `prompt.submit` hook. A head that stays unresolved without a plugin hook was not reproduced.
- Plugin worker globals: `typeof` is `undefined` for `fetch`, `WebSocket`, `XMLHttpRequest`, `EventSource`, `process`, `require`, `Deno`, `Bun`, `navigator`, `importScripts` and `postMessage`, in both module generations of `fifo-review-reload-held-prompt-135e1028` and in `fifo-review-reload-turn-only-20268a9f` (`native.status` reports). `9be02fd8` lost its reports to the missing `fetch`; later probes report through `$.fs`.
- Probe isolation: live-profile hashes were unchanged in `prompt-registry-35a040a8`, `prompt-registry-e2bed500`, `fifo-review-append-clean-f006e2ae` and `fifo-review-reload-turn-only-20268a9f`. In `fifo-review-reload-held-prompt-135e1028` `~/.claude.json` changed (no owned path or probe id in it) and two protected processes outside the owned tree ended during the window; in `prompt-registry-35a040a8` one protected `python.exe` (pid 83972, parent 72912) outside the owned tree ended. Their cause is not established. Only the owned `claude.exe` tree was terminated in each run.
- Two `claude.exe` on one session stay at-least-once on an old router (Residuals).
- A prompt whose `$.prompt.submit` threw stays uncertain until a person retries or main acknowledges. There is no automatic evidence path for it.
- The lease bound (5 s) is fixed; see Residuals for its liveness limit.
- Live-profile guard, `state-fence-df88e27b`: `~/.claude.json` changed between the before and after hashes (`b713d147…` → `c0fe76da…`, mtime 21:02:00Z). It contains no owned path or probe id. The probe's own debug log records config writes only under its temp `CLAUDE_CONFIG_DIR`. The file had also changed between the two probe runs, while no probe was running (`ff978533…` → `b713d147…`). Who wrote it is not established. `settings.json` (`543860932ffbd84d…`) and the statusline script (`20e823e9…`) are unchanged.
- The probe's `claude.exe` loaded `~/.claude/CLAUDE.md` by ancestor-directory discovery, and that text reached only the loopback fixture gateway and the temp transcript. The journal keeps only `transcript-rows.json` (row ids); the temp run folders were removed.
- No JS harness drives the real Python backend end to end (no bridge was created, by instruction). Real-backend coverage is the Python tests.

# Citations

- [Design and contract](/design.md)
- [Verification](/verification.md)
