---
type: design-proposal
title: Released wait resume boost lane
description: Durable sidecar and recovery design for exact Walkie wait releases entering a scheduling lane behind P0.
timestamp: 2026-11-14T10:04:40Z
tags: [work, sse, wait, queue, sidecar, compatibility]
---

# Verdict

Use a new strict-CAS sidecar beside `work-parallel.json`, not board-node fields and not the legacy ledger schema.

57dbfb3 `work_plan_tree.normalize_document` reconstructs every node from a fixed key set, so unknown node boost fields are stripped by an ordinary old write. Its ledger validator also has an exact lease field set. A new file is ignored by the old build and can preserve authoritative boost state. Proposed file: `work-parallel-wait-boost-v1.json`.

The board remains the wait/release authority. The sidecar is scheduling and consume-recovery authority. The ledger remains execution authority.

# Exact release mutation

Do not call `tree.update_node(status='pending')`: that API invokes `_reopen_ancestors` for a non-root node and can mutate ancestor status.

Add one exact release helper whose board-lock contract is:

1. validate the named node exists, is unfinished, belongs to the decision target's root, and its `waiting_on` exactly equals the observed bounded text;
2. require the released node to be a **leaf/item**; a blocked non-leaf/ancestor release is refused as `non_leaf_wait` and neither it nor descendants change;
3. copy and replace only that node, setting `waiting_on=''`, setting `status='pending'` only when its prior status is `blocked`, appending the bounded audit line when capacity permits, and setting that node's `updated_at`;
4. leave every ancestor and descendant byte-for-byte unchanged, including status, timestamps, phase and execution approval;
5. separately recompute read-only readiness/display from the resulting tree. An unchanged blocked ancestor still gates the leaf and prevents selection.

This helper must not call `_reopen_ancestors`, `_complete_ancestors`, `_advance_queue`, `set_phase`, or queue-reorder helpers.

# Board recovery receipt

The committed `side_decisions[release_receipt].result` contains one bounded `wait_boost` object per successful release (maximum remains the existing release bound):

```text
wait_boost = {
  item_id: text<=200,
  root_id: text<=200,
  root_created_at: finite nonnegative number,
  release_receipt: lowerhex64,
  release_at: finite nonnegative number,
  wait_digest: lowerhex64,
  parked_lease_id: null | text<=200
}
```

`wait_digest` is SHA-256 of the exact released wait text; the text itself is not copied into this object. It contains no host id, token, event/reply text or model content. `release_receipt` must equal the enclosing receipt key. New readers validate the exact key set, types, lengths, finiteness and identity relationships before recovery. Invalid objects fail closed and are shown as recovery errors; they do not create boosts.

57dbfb3 preserves arbitrary `side_decisions[*].result` fields as a deep copy. Therefore this receipt is the board-first recovery source after a crash between files.

# Sidecar schema

Top-level keys are exactly `schema,version,rows,bind_sagas,workspaces,cleanups,history`. `schema == 1`; version and every store version are integers in `0..2^53-1`; times are finite numbers in `0..1e15`; ids/text are nonempty UTF-8 strings of at most 200 characters without NUL; digests are lowerhex64 and operation ids lowerhex32. Every collection is an object with at most 2,000 entries, every canonical serialized row is at most 65,536 bytes, and the whole canonical file is at most 2 MiB. Unknown keys, wrong scalar types, duplicate JSON keys or broken cross-links fail closed.

Every `BoostRow` has the exact keys below; fields not allowed by its state are present as null, never omitted:

```text
scope,item_id,root_id,root_created_at,release_receipt,release_at,wait_digest,
parked_lease_id,release_store_before_version,state,
attempt_id,attempt_kind,ledger_before_version,lease_id,pre_state,pre_slot,
pre_lease_digest,expected_post_state,expected_post_slot,expected_post_role,
expected_dispatch_name,send_receipt,send_state,slot_acquired_version,
slot_acquire_digest,attempt_request_digest,
launch_state,launch_lease_id,assignment_generation,assignment_digest,
capability_derivation_digest,launch_dispatch_name,reserve_receipt_digest,
launch_manifest_digest,outcome,consumed_at,
completed_attempt_id,completed_attempt_kind,completed_ledger_before_version,
completed_ledger_after_version,completed_lease_id,completed_receipt_digest,
operation_digest,terminal_attempt_id,terminal_transition,terminal_outcome,
terminal_receipt_digest,terminal_operation_digest,terminal_at,terminal_manifest,
updated_at
```

| Boost state | Required values | Required nulls / constraints |
|---|---|---|
| `boosted` | immutable release identity; optional exact retained terminal audit | all open attempt, launch and completion fields null; `outcome,consumed_at` null |
| `consuming` | complete immutable attempt pin and request digest; send identity for reacquire/running; launch identity only for reserve after reservation | completion and terminal audit null; reserve has deterministic dispatch and null send fields; non-reserve has null launch fields |
| `consumed` | `outcome` and `consumed_at`; lease outcomes retain exact completion identity/digest; `p0` retains no lease/completion identity | all open attempt/launch and terminal audit fields null |
| `cancelled` | cancellation outcome plus exact terminal attempt/transition/receipt/operation/manifest and `terminal_at` | all open attempt/launch and completion fields null; `consumed_at` null |

One row exists per `(scope,item_id)` and the map key is exactly `scope + NUL + item_id`. Same receipt/digest is replay; a different receipt may replace only a non-consuming row after its terminal identity is safely archived.

`BindSaga` keys are exactly:

```text
attempt_id,scope,root_id,root_created_at,item_id,lease_id,dispatch_name,
assignment_generation,assignment_digest,state,agent_id,agent_name,
bind_manifest_digest,ledger_before_version,ledger_after_version,ledger_bound_digest,
coord_before_version,coord_after_version,coord_bound_digest,
bind_receipt_digest,operation_digest,rollback_receipt_digest,
rollback_operation_digest,created_at,updated_at,terminal_at
```

| Bind state | Required values | Required nulls / monotonic edge |
|---|---|---|
| `prepared` | immutable identity, agent id/name, assignment, ledger/coord before versions, `bind_manifest_digest` over all known request/before facts | after versions, bound digests, bind receipt, final operation digest, rollback fields and terminal time null; only new saga entry |
| `ledger_bound` | prepared fields + `ledger_after=ledger_before+1` + ledger bound digest | coordination after/digest, bind receipt, final operation digest, rollback fields and terminal time null; only from prepared |
| `coord_bound` | ledger-bound fields + `coord_after=coord_before+1` + coordination bound digest | bind receipt, final operation digest, rollback fields and terminal time null; only from ledger_bound |
| `done` | manifest + both bound proofs + exact bind receipt + final operation digest + terminal time | rollback fields null; only from coord_bound |
| `recovery_required` | immutable identity and every proof known at the prior phase | bind receipt exists only if bind completion committed; rollback fields exist only if rollback commit is known; no proof may be cleared/changed; nonterminal |
| `rolled_back` | immutable identity, distinct rollback receipt digest + rollback operation digest, terminal time, and proofs of every cleanup already committed | `bind_receipt_digest` remains null unless bind had committed; unavailable later-phase proofs null; only exact cleanup roll-forward may enter |

At most one nonterminal saga exists per attempt/lease. Edges are monotonic; exact replay returns the same/current phase and no transition clears a committed before/after/digest.

`WorkspaceChild` keys are exactly:

```text
attempt_id,scope,root_id,root_created_at,item_id,lease_id,workspace_operation_id,
state,repo_identity_digest,base_head,target_ref,target_path_digest,branch_digest,
gitdir_digest,manifest_digest,ready_receipt_digest,lease_attach_receipt_digest,
coord_attach_receipt_digest,rollback_receipt_digest,rollback_operation_digest,
created_at,updated_at,terminal_at
```

`target_ref` and `base_head` are bounded opaque text; paths are stored only as digests. Receipt fields are immutable and phase-specific:

| Workspace state | Required values | Required nulls / monotonic edge |
|---|---|---|
| `prepared` | immutable identity, operation id, repo/base/target policy and manifest digest | all phase/rollback receipts and terminal time null; backend-minted before FS effect |
| `ready` | prepared fields + exact owned-finalization `ready_receipt_digest` | attach/rollback receipts and terminal time null; only from prepared or exact absence-resume |
| `lease_attached` | ready fields + lease attachment receipt | coordination/rollback receipts and terminal time null; only from ready |
| `coord_attached` | lease-attached fields + coordination attachment receipt | rollback receipts and terminal time null; only from lease_attached; nonterminal/protected |
| `recovery_required` | immutable identity and every known phase receipt | no known receipt may be cleared/changed; from any nonterminal state on partial, mismatched or unowned evidence; nonterminal |
| `done` | all three phase receipts and terminal time | rollback fields null; only after exact BindSaga done and parent BoostRow native_bound |
| `rolled_back` | exact rollback receipt + rollback operation digest + terminal time and every earlier receipt that had committed | later uncommitted phase receipts null; only after exact-owned cleanup roll-forward |

A prior-pin retry after a later workspace phase is accepted only by that transition's resolver when operation id, manifest and all request fields equal the committed edge; it returns the current child and never repeats the effect.

`CleanupSaga` entries live in top-level `cleanups` (maximum 2,000). The map key is exactly `cleanup_id`, where `cleanup_id = H("ciel.wait-boost.cleanup-id.v1", {scope,item_id,release_receipt,attempt_id,cleanup_trigger})`; `cleanup_trigger` is exactly `spawn_denied|native_absent`. At most one nonterminal cleanup exists for one `(scope,item_id,release_receipt,attempt_id)`. The row persists `release_receipt` and `cleanup_manifest_digest`; validators recompute both the map key and `H("ciel.wait-boost.cleanup-manifest.v1", exact immutable cleanup row identity)` from row fields. The same id plus identical persisted/recomputed cleanup-manifest digest is exact replay; the same id with changed input conflicts. The backend premints/derives this id and manifest digest before the first cleanup effect, never after one has begun. Entries have exact keys:

```text
cleanup_id,cleanup_trigger,cleanup_manifest_digest,release_receipt,attempt_id,
scope,root_id,root_created_at,item_id,lease_id,workspace_operation_id,
bind_manifest_digest,state,native_absence_receipt_digest,
bind_disposition,no_bind_proof_digest,bind_rollback_receipt_digest,
bind_rollback_operation_digest,coord_before_version,coord_after_version,
coord_detach_receipt_digest,ledger_before_version,ledger_after_version,
ledger_detach_receipt_digest,workspace_cleanup_receipt_digest,
lease_release_receipt_digest,boost_sidecar_before_version,
boost_sidecar_after_version,boost_restore_receipt_digest,
boost_restore_operation_digest,cleanup_operation_digest,created_at,updated_at,terminal_at
```

| Cleanup state | Required proof added at this edge | Required nulls / next edge |
|---|---|---|
| `prepared` | immutable parent identity including `release_receipt`, exact `cleanup_manifest_digest`, and exact native-denial/absence receipt | `bind_disposition` and bind proofs null; `bind_manifest_digest` is null when no BindSaga exists, otherwise the exact matching saga digest; every cleanup effect/restore receipt/version and terminal time null |
| `bind_rolled_back` | `bind_disposition`; if `rolled_back`, non-null exact matching `bind_manifest_digest`, exact bind rollback receipt+operation, and null no-bind proof; if `not_created`, `bind_manifest_digest=null`, exact no-bind proof, and null rollback fields | later detach/remove/release/restore fields null; fabricated or arbitrary BindSaga digest forbidden |
| `coord_detached` | bind-disposition branch + exact coordination before/after versions + detach receipt | ledger/remove/release/restore fields null |
| `ledger_detached` | prior fields + exact ledger before/after versions + detach receipt | remove/release/restore fields null |
| `workspace_removed` | prior fields + exact-owned workspace cleanup receipt | lease release/restore fields and terminal time null |
| `lease_released` | prior fields + exact lease/run release receipt | boost sidecar before/after versions, restore fields, cleanup operation and terminal time null; parent BoostRow not restored yet |
| `done` | exact parent boost before/after versions, restore receipt+operation, cleanup operation digest and terminal time | no proof null; only from lease_released by the atomic parent-restore+saga-done CAS; restored parent audit/history cross-link required |
| `recovery_required` | immutable identity plus all proofs known at last committed phase | known fields never cleared/changed; disposition null only before bind phase; nonterminal |

The saga is persisted before the first denial cleanup effect. Edges are monotonic and each phase resumes only the missing effect. Exact current/prior-pin replay recomputes the applicable domain object and returns the same/current phase; field mutation conflicts. No BoostRow restore occurs before `lease_released`. Because BoostRow and CleanupSaga share one sidecar, one strict sidecar CAS atomically restores the exact parent BoostRow and moves `lease_released -> done`, setting `boost_sidecar_before_version`, `boost_sidecar_after_version=before+1`, exact `boost_restore_receipt_digest`, `boost_restore_operation_digest`, final cleanup operation digest and terminal time. There is no separate restore-then-done window. The lost-response resolver validates the complete requested manifest against both committed rows and returns exact done without a second restore. The restore receipt/operation must equal the parent BoostRow's restored terminal audit or retained history identity; the cross-link validator compares attempt/scope/root/item/lease, transition, outcome, receipt and operation digest. `cleanup_operation_digest` covers the immutable saga identity, every edge receipt/version/digest, bind disposition and its rollback/no-bind proof, plus the parent boost restore receipt/operation; arbitrary proof objects are rejected. Nonterminal cleanup is never pruned. Terminal done retains 7 days/newest 32 and stays cross-reference protected while any parent/child recovery or replay names it.

`TransitionHistory` keys are exactly `history_id,scope,item_id,release_receipt,attempt_id,kind,transition,outcome,receipt_digest,operation_digest,manifest,terminal_at,result`. `kind` is one of `completion|restore|send_rollback|cancel|bind_terminal|workspace_terminal`. `manifest` is a discriminated exact object. New completion writes use `completion_enriched_v1`: immutable parent identity `scope,item_id,root_id,root_created_at,release_receipt` plus the exact completion fields. For lossless compatibility only, a history row read from the previously emitted four-key schema-1 sidecar may carry the exact legacy seven-field completion manifest `completed_attempt_id,completed_attempt_kind,completed_ledger_before_version,completed_ledger_after_version,completed_lease_id,completed_receipt_digest,operation_digest`; its exact key shape is the discriminator and it remains byte-semantically unchanged through the next CAS. No new write may create that legacy variant. A legacy completion row validates and replays only its outer scope/item/release/attempt plus the seven exact completion fields; because root identity is unavailable and must not be fabricated, it can never authorize or satisfy a BindSaga/WorkspaceChild/CleanupSaga parent cross-link. It remains bounded retained audit until normal retention/expiry. After the first authenticated four-key migration persists the canonical seven-key document, these exact legacy rows are grandfathered and remain readable after restart; static canonical-file validation accepts their exact shape but grants no authority. Every strict-CAS mutation compares the before/after legacy-history subset under the sidecar lock: an existing legacy row may remain only byte-for-byte equal, or may be deleted only by the deterministic eligible retention/prune operation; adding a legacy row, replacing an enriched row with legacy, changing a legacy key/value, or deleting it outside eligible prune fails closed. New code can emit only enriched completion rows. Tests cover four-key read → unrelated CAS → restart, byte-identical preservation, insertion/replacement/mutation refusal, eligible prune, and zero child authority. Restore/cancel carry full pin+transition+outcome; send rollback carries full pin+rollback receipt; bind terminal carries all BindSaga keys except timestamps/state; workspace terminal carries all WorkspaceChild immutable/receipt keys except timestamps/state. History semantics are exact even when live children were pruned. Allowed tuples `(kind,transition,outcome,result.state,result.outcome)` are exhaustive: completion `native_bound` uses `(completion,complete_bound_launch,native_bound,consumed,native_bound)`; completion `reacquired|running_notified` uses `(completion,complete_consume,<same>,consumed,<same>)`; restore uses transition `running_notify_failed|launch_denied|capacity_no_commit|native_absent`, outcome `restored_boosted`, result `(boosted,restored_boosted)`; `native_absent` is emitted only by an exact completed native-absence CleanupSaga and preserves that parent audit through later archive/replay; send rollback uses `(send_rollback,reacquire_send_failed_rolled_back,delivery_failed_rolled_back,boosted,delivery_failed_rolled_back)`; cancel uses `(cancel,cancel_open,<one of reblocked|terminal|identity_mismatch>,cancelled,<same>)`; bind/workspace `done` use transition `done`, outcome/result `(native_bound,native_bound)` with result state `consumed`; bind/workspace `rolled_back` use transition `rolled_back`, outcome/result `(restored_boosted,restored_boosted)` with result state `boosted`. No other string tuple is valid. Workspace done history uses `coord_attach_receipt_digest` as receipt and `H("ciel.wait-boost.workspace-terminal.v1", full terminal workspace manifest)` as operation digest; rolled_back uses its rollback receipt/operation. `result` is the exact bounded terminal projection `{state,outcome,recovery_required,operation_ref}` with no internal ids or arbitrary extension. Every enriched/new retained terminal tuple requires `recovery_required=false`. Its `operation_ref` is exactly the first 12 lowercase hex characters of `SHA256(operation_digest ASCII bytes)` for that same history row; validators recompute it from the outer `operation_digest`, and another boolean or reference conflicts. The grandfathered legacy seven-field completion variant is the sole exception: its existing exact result bytes use the legacy grammar (`state` and `operation_ref` bounded text, `outcome` null|bounded text, `recovery_required` boolean) and remain byte-identical audit/replay-only data. They are not normalized to the derived ref or false flag, cannot authorize child cross-links, and the CAS legacy-subset rule forbids changing their result/ref/flag. New code never emits this exception. Both nested objects are included in the 65,536-byte row bound.

Cross-links are mandatory: every nonterminal BindSaga, WorkspaceChild and CleanupSaga names the same consuming reserve BoostRow attempt/scope/root/time/item/lease; CleanupSaga done names the exact restored BoostRow terminal audit/history receipt+operation; every BindSaga requires its `coord_attached|done` workspace; a CleanupSaga references the matching workspace and any BindSaga without inventing absent authority; a consumed `native_bound` row must name matching done BindSaga and done WorkspaceChild completion identities. A terminal child remains protected while an open/consuming BoostRow or recovery operation references it. Public projections omit all open, bind, workspace, completion and history internals.

# Board and sidecar order

1. Under the board transaction, run the exact leaf release helper and commit the validated `wait_boost` recovery object in the same `side_decisions` receipt.
2. After board commit, create the sidecar row by strict CAS. A status/pump recovery pass recreates a missing row only from a validated committed receipt.
3. Never write the sidecar first. A row must not exist for a board release whose exact-text/same-goal/leaf checks failed.
4. Selection always rereads the board. A stale, re-blocked, deleted, terminal or root-identity-mismatched row is cancelled rather than dispatched.

External SSE text remains untrusted. A release changes wait state and scheduling order only; it is not execution approval.

# Scheduling order

On an all-new scheduler fleet:

1. effective P0 work;
2. boosted rows with an exact parked `waiting_input` lease, newest release first;
3. other boosted rows, newest release first;
4. ordinary P1-P5 candidates in their existing order.

Tie-break within each boost group: `(-release_at, release_receipt, item_id)`. The canonical root priority and physical root queue order never change. Boosts do not preempt a running lease or take an occupied slot. Non-P0 boosts obey enabled/paused state, their canonical priority capacity, launch rate and total lease bound.

A queued goal is projected into the boost lane without becoming the active root. A non-P0 row whose root phase is `planning` remains boosted but **ineligible** until existing execution approval changes the root to `executing`; status says `waiting_for_execution_approval`. The release itself never grants approval.

In a mixed-version fleet, this ordering is best-effort only. 57dbfb3 does not read the sidecar and may reserve an ordinary item while a boost waits. New code cannot guarantee global boost order while any old scheduler is active. It still prevents a new-code duplicate by reconciling an exact old-created active lease.

# Durable consume protocol

Ledger success followed by sidecar consumption is not atomic. Use a durable sidecar intent before any ledger mutation:

1. strict-CAS `boosted -> consuming`, minting `attempt_id`, recording the complete pre/post transition and returning an opaque full pin whose `store_version` is the committed sidecar version;
2. each subsequent sidecar mutation requires that full pin and exact CAS version, compares every pin field, and returns a refreshed full pin when it advances the sidecar;
3. parked reacquire completes only from the exact slot CAS proof; running notify only from the exact send outcome; new reserve stays consuming until native bind as specified below;
4. failure restores only through the exact current full pin. Ambiguous or later-version history remains consuming/recovery_required; no loose unique current-state match is treated as proof.

Replay is keyed by the full boost identity plus `attempt_id`, exact store version, exact pre/post transition and ledger before version. Consumed rows retain durable completion identity (attempt, ledger versions, lease, receipt digest and framed operation digest); a different pin/attempt/version/lease/outcome cannot replay, restore or cancel it.

Final mixed-version proof rule is fail closed:

- new reserve may reconcile across later ledger versions only through exact persisted `dispatch_name=work-wait-boost-<attempt_id>` plus its durable launch record;
- parked reacquire has no legacy attempt marker, so receipt-free recovery is allowed only at exactly `ledger.version == before+1` with the exact same lease's `waiting_input slot=false -> slot=true` transition; later/unrelated versions remain consuming/recovery_required;
- running notify completes only from the exact send result; unknown remains recovery_required.

No legacy semantic ledger field is repurposed and no loose current-state match consumes a boost.

## Parked worker

Before the board release transaction, the authenticated host application reads a bounded ledger projection keyed by scope and exact released item/root. `apply(..., scope=None, parked_projection=None)` accepts only `{ledger_version,rows[{item_id,root_id,lease_id,state:'waiting_input',slot:false}]}` (maximum existing release batch); it contains no host id, token or text. Under the board lock the helper pins `parked_lease_id` only when item/root match. Unavailable/unreadable/wrong-root projection produces null and the item enters the other-boost lane. A stale pre-read only loses parked preference; it never authorizes resume.

`reacquire(..., wait_boost=pin)` later rereads and requires the pinned lease id, same scope/item/host, `waiting_input`, `slot=false`, exact store version and full pin. Backend premints/persists a send receipt in that pin. Capacity refusal leaves/restores the row boosted. Successful slot CAS records exact slot proof and returns a refreshed `send_pending` pin while the boost stays consuming; it does **not** consume. The existing SendMessage path then runs. Exact correlated delivery true consumes outcome `reacquired`; false performs exact backend slot rollback. A committed rollback returns the row to boosted but retains terminal `delivery_failed_rolled_back` attempt/send/rollback receipt and operation-digest identity in the separate transition history for 7 days and at least newest 32 terminal transitions; a next attempt/release clears only active-row terminal fields, not retained audit, so stale-pin exact replay returns the same failure and changed delivery conflicts. If rollback is unproven/lost it stays consuming/uncertain/recovery_required; throw/unknown also records uncertain and never resends automatically. An exact pre-read race test must prove changed leases refuse parked reacquire and safely fall back without duplicate execution.

## New launch: consuming through native bind

A new launch is **not consumed at ledger reserve**. The row stays `consuming` through reserve, assignment issuance, native spawn and bind.

1. `begin_consume(kind='reserve')` records exact absent pre-state, ledger before version, expected reserved post-state and deterministic `expected_dispatch_name='work-wait-boost-'+attempt_id`.
2. Backend reserves exactly once and durably records a boost launch/assignment record tied to attempt, lease, assignment generation, dispatch name and capability derivation before returning it. The reserve commit proves the reserved lease but the boost remains consuming/`launch_pending`.
3. Before the assignment is returned, derive its worker capability as `HMAC-SHA256(shared_assignment_key, assignment_frame)`, where `assignment_frame = u64be(len(domain_utf8)) || domain_utf8 || u64be(len(canonical_json_utf8)) || canonical_json_utf8`, domain is exactly `ciel.wait-boost.assignment-capability.v1`, and canonical JSON has sorted keys, `,`/`:` separators, UTF-8 and no NaN. Its exact fields are `scope,root_id,root_created_at,item_id,attempt_id,lease_id,assignment_generation,dispatch_name,purpose:"worker_report"`; root time is the canonical JSON number and dispatch name is mandatory. No delimiter-concatenation or alternate wrapper is valid.
4. `shared_assignment_key` is normatively the existing accepted shared file `work-p0-assignment.key` beside `work-parallel.json`; there is no wait-specific key or alternate path (wire status may retain the field name `assignment_key`). It uses the accepted lifecycle: exact lowerhex64 file bytes, POSIX `0600`/Windows exact current-user+Administrators+SYSTEM DACL, symlink/nonregular/permissive/invalid refusal and no read-time ACL mutation. Its atomic dependency fence includes every P0 nonterminal intent/open send plus every wait consuming BoostRow, nonterminal BindSaga, WorkspaceChild and CleanupSaga. Both P0 and wait dependent-intent creators acquire the single `work-assignment-key.creation.lock` beside the ledger and hold it through both zero-dependency reads plus existing-key validation or key publication. Global lock order is exactly key-creation lock -> P0 sidecar lock -> wait sidecar lock -> key-file lock; no path may acquire these in reverse, and status reads never create/harden/mutate. Creation is allowed only when that combined set is empty; otherwise missing key is recovery-required and creates nothing. It is never rotated/recreated while a dependency exists. Missing/unreadable/ACL-invalid/invalid bytes fail assignment/spawn/reacquire pre-write. Backup uses the same exact secret classifier/path, encrypts it, restores without overwriting a different key and never exposes key or worker-token bytes.
5. `wait_boost_assignment` replays the identical assignment/name/token derivation until bind. No second reserve is issued. Exact lost-response replay is keyed by attempt plus the complete transition manifest and returns the current refreshed pin; changed input conflicts.
6. Every persisted digest has a versioned domain and exact relation. `capability_derivation_digest` hashes the same complete canonical object used by the HMAC—minus only key/token—including `root_created_at` and `dispatch_name`; assignment manifest, reserve before/after transition, full pin, launch manifest, each workspace phase receipt and bind saga/receipt have distinct domains. Arbitrary hex is not accepted as proof.
7. Before any workspace file-system effect, the authenticated host sends a ref-less `prepare` with canonical absolute `repo_path` plus exact trusted repo identity/base/target policy. The host constructs/overwrites it from the session launch workspace and discards caller input. On Windows canonicalization uses absolute normalized drive/UNC spelling with separator/case normalization for comparison and final-handle resolution; on POSIX it uses absolute `realpath`. The resolved path must be one existing repository root, not an ambiguous nested/multi-worktree match or missing path. Backend recomputes `repo_identity_digest` from that canonical repository identity, verifies base head and target policy, then premints `workspace_operation_id`, persists only digests/refs in `WorkspaceChild{state:prepared}`, and returns `workspace_ref`. `repo_path` exists only in authenticated host memory/request and is forbidden from persistence/status/log/error/backup/public output. The mod never mints proof; every later workspace operation requires this ref plus current pin. Recovery proves one of: all target facts absent -> resume the same operation; exact owned target/branch/gitdir -> finalize `ready`; mismatch, partial or unowned presence -> `recovery_required`. `ready -> lease_attached -> coord_attached` records separate immutable receipt digests. Rollback is allowed only through the cleanup saga below and never deletes an unowned path.
8. Only `coord_attached` may enter spawn. The mod records spawning and spawns exactly the persisted name. On exact native discovery, persist `BindSaga{state:prepared}` before ledger bind. Ledger bind records exact before/after versions and bound digest, then coordination bind records its exact before/after versions and bound digest, then one backend receipt binds attempt/lease/dispatch/agent/name/generation/assignment plus both commits; `done` and boost outcome `native_bound` occur only after that exact receipt validates.
9. Explicit spawn denial starts a durable exact-owned cleanup saga; it never immediately restores. First prove the spawn was denied and no native effect exists. Cancel/roll back any BindSaga, then detach/cancel coordination and ledger workspace attachments with existing safe exact primitives—or retain the reservation if any detach is unproven. Remove the workspace only with exact ownership plus a durable cleanup receipt; transition WorkspaceChild to `rolled_back` with terminal time; release/cancel the exact lease/run; only then restore the BoostRow. Any unproven native, bind, attachment, filesystem, lease or run effect leaves all authority `recovery_required`, never boosted.
10. Unknown spawn or bind outcome stays consuming. Reconciliation resumes the persisted workspace child and BindSaga phase without repeating a committed effect. Exact current/stale replay returns the same phase/receipt; changed field conflicts. Complete native absence plus backend-proven dead/reused owner may run exact rollback; ambiguous presence/history remains recovery_required.
11. Crash before workspace mutation resumes the same manifest; after file-system effect finalizes only exact ownership; between ledger and coordination bind resumes the missing bind; after either store response loss rereads exact versions/digests; after receipt/done response loss returns the same terminal result. An unbound reservation, workspace or partial bind never disappears solely because a later step failed.

P0 and wait-boost may share launch primitives, but their intents, names and authority remain distinct.

## Already-running worker

A direct `session.send` has no capacity or reserve operation and is distinct from reacquire. Backend creates a `running_notify` consume intent pinned to the existing running lease and premints/persists a lowerhex64 `send_receipt` inside the full pin/operation digest before the mod sends. The mod echoes that exact receipt; it never mints proof. Only `isDelivered:true` correlated to that precommitted receipt completes outcome `running_notified`. Send failure returns the row to boosted and creates/keeps the existing main handoff; it must not be mislabeled `reacquired`. An uncertain crash during notification stays `consuming` and is surfaced for the existing handoff/retry path rather than guessed from mere lease presence.

## P0 release

Always write an audit row directly as `consumed`, `outcome='p0'`, `lease_id=null` after the board release. If the process crashes first, receipt recovery creates that same consumed P0 row. A P0 release is never represented as an ordinary boosted row and therefore cannot independently dispatch twice.

# Bounds, retention and migration

The exact top-level schema is `schema,version,rows,bind_sagas,workspaces,cleanups,history`; each collection has a maximum of 2,000 rows and the serialized file remains bounded at 2 MiB. Both previously emitted schema-1 shapes migrate losslessly in memory: `{schema,version,rows}` adds empty `history`, `bind_sagas`, `workspaces` and `cleanups`; `{schema,version,rows,history}` preserves every existing history row byte-semantically and adds only empty `bind_sagas`, `workspaces` and `cleanups`. Either migrated shape persists only on the next successful CAS; no active row changes and no history is fabricated, dropped or rewritten. Unknown top-level fields/schema, duplicate JSON keys, oversized/corrupt data or invalid cross-relations fail closed and block writes.

Whenever a terminal active row is replaced, prune expired history **before** checking capacity and archiving it, then archive one immutable domain-framed history row. For every new/enriched/nonlegacy row, `history_id = H("ciel.wait-boost.history-id.v1", {kind,scope,item_id,release_receipt,attempt_id,transition,outcome,receipt_digest,operation_digest})`; the map key and persisted `history_id` must equal this recomputed digest. A pruned id can therefore never represent a different operation in a later CAS. Grandfathered legacy completion rows retain their existing historical map key/id byte-for-byte under the separate locked provenance rule and cannot be newly inserted. During one CAS, every key removed by deterministic pruning—legacy or enriched—must remain absent from the entire final `history` map; same-CAS key reuse is forbidden. Terminal history, terminal BindSaga, terminal WorkspaceChild and terminal CleanupSaga each retain 7 days and at least newest 32 of their own collection (`terminal_at,id` deterministic order); startup and every admission/replacement run the same algorithm. Open/consuming rows and nonterminal bind/workspace/cleanup rows are never pruned. A terminal BindSaga or WorkspaceChild is additionally reference-protected while its linked BoostRow is open/consuming, while parent terminal completion has not been archived durably, or while recovery/replay still names it; age/newest eligibility alone cannot prune it. Pruning begins only after parent completion identity is terminal or archived and every cross-reference is safe. If the relevant collection is still full after eligible pruning because every entry is open/protected/within retention, admission visibly refuses rather than erasing audit. Exact replay finds active or retained identity; a changed request conflicts, while an exact identity whose retained record was pruned returns bounded `operation_history_expired` and is never treated as a new operation.

# Re-block and terminal rules

A new non-empty `waiting_on`, exact-item `blocked` state, done state, deletion or root-created-at mismatch cancels the matching row before selection. Because board and sidecar cannot commit atomically, selection must validate the board every time. Cancellation records `reblocked`, `terminal` or `identity_mismatch`. A later exact release creates/replaces it with a newer receipt/time.

# 57dbfb3 compatibility measurement

The isolated real-process harness passed at 2026-11-14T05:37:38Z without importing current product modules into the child:

1. 57dbfb3 read and wrote a board containing the exact bounded `result.wait_boost` object; it remained exactly equal after old progress, priority and phase writes;
2. those board writes and old ledger pause/reserve/release operations left the new sidecar byte-identical;
3. old dispatch of the boosted item produced exactly one active matching lease; its second same-scope/item attempt produced no second lease;
4. the new reconciliation predicate found one unique exact match and deterministically projected `consumed`;
5. in a separate board, the old runnable order was newer ordinary `n3` before boosted `n2`, and the old scheduler reserved ordinary first. This confirms mixed-version global boost ordering is not guaranteed.

All six checks are true and the child return code is 0. Evidence:

- [Harness](evidence/old_reader_wait_boost_roundtrip_probe.py), SHA-256 `413c0fd8ac6a3688221b6bfabbedd42d860b2f7e4694dccabe46562d75aa2780`;
- [Result](evidence/old-reader-wait-boost-roundtrip-result.json), SHA-256 `9673d3af5bc80731b5d769e32a791d16ba1f68159ee4fc8ec4c81192e76aab4e`.

The harness proves receipt preservation, sidecar isolation, one exact old-dispatch match and the ordinary-first limitation. It does not implement the new consuming protocol; zero/multiple-match fail-closed behavior remains an implementation test requirement.

# Store durability

Every successful write flushes+fsyncs the temporary file, atomically replaces the target, and on POSIX opens/fsyncs/closes the parent directory before returning. A parent-directory fsync failure after replace is `ambiguous committed`: caller must reread/reconcile and must not retry as absent. Windows performs temp fsync + replace; directory handles do not provide the same POSIX durability contract and no parent fsync is attempted. Instrumented order/failure tests are required.

# Four-surface projection and privacy

The store base projection accepts explicit `mixed_version_best_effort: bool` (the host supplies compatibility knowledge; the store does not guess) and a validated caller-derived per-item status. Menu, web, REST and `ciel-runtimectl` expose behavior, not internal outcomes/capabilities:

```text
mixed_version_best_effort: boolean
mixed_version_warning: "Older Ciel schedulers do not honor released-wait ordering; order is best-effort while they are active." | null
rows[].released_unblocked: true
rows[].boost_state: boosted | consuming | consumed | cancelled
rows[].boost_position: 1..N | null
rows[].released_at
rows[].release_ref: bounded hash/prefix reference
rows[].parked_worker: present | null
rows[].resume: reacquired | running_notified | first_eligible | capacity_wait | p0 |
               waiting_for_execution_approval | recovery_required | null
help: exact accepted help text below
```

Dynamic `resume` is derived only by a host application projection with board+ledger+operation context. Raw internal outcomes (`native_bound`, cancellation reasons, `None`) are never mapped directly into public `resume`; `reserved` is not a consumed outcome anywhere.

The sidecar contains only hashes and opaque ids, no wait text, prompt, token, assignment key or model content; internal bind/workspace rows may contain bounded opaque host/agent/name/path digests but never path text or capability bytes. It may remain an ordinary encrypted backup chunk under current schema, while the assignment key is always a SecretBundle entry. Export/privacy tests must assert zero full receipt/lease/host/agent/dispatch/internal-pin values in public manifests/projections and zero plaintext wait/key/token/path values in ordinary chunks. If the schema ever adds plaintext/user content, it must move to SecretBundle before release. Human menu/web output never exposes full receipt, host id or lease id. REST/ctl omit opaque full ids by default; an existing explicitly authorized admin/debug projection may expose them, but this feature creates no new authorization path. Public `release_ref` is display-only and never accepted for lookup/replay/write identity. It starts at 12 hex (48-bit); if two visible rows collide at 12, both extend deterministically in 4-hex steps until distinct (maximum full digest), while item_id remains a separate display field. Every mutation continues to use the full receipt internally.

Help text: "Released waits run after P0 and before ordinary P1-P5 work at the next available slot on updated schedulers. They do not preempt running work, change stored priority, or grant execution approval. Ordering is best-effort while older schedulers are active."

# Exact tests

1. blocked exact waiting leaf -> only that leaf pending + wait empty + recovery receipt;
2. waiting-only leaf -> status unchanged; ancestor/descendant bytes unchanged;
3. blocked non-leaf release refused without descendant reopening;
4. stale/foreign/wrong-root/exact-text mismatch -> no board or sidecar write;
5. ancestor block remains and prevents selection;
6. receipt exact schema validation, bounds and privacy exclusions;
7. P0, parked boost, other boost, ordinary order on an all-new fleet;
8. newest release and deterministic tie-break; canonical priority unchanged;
9. planning root remains boosted/ineligible until execution approval;
10. parked same lease/host reacquires; capacity leaves it boosted;
11. running notify success is distinct; failure retains boost and handoff;
12. consuming intent replay across pre-ledger, post-ledger and post-consume crashes;
13. zero/multiple reconciliation matches fail closed; one exact match consumes;
14. re-block/terminal/delete/root identity mismatch cancels; later release replaces;
15. P0 receipt recovery always creates consumed/p0, never ordinary boost;
16. real 57dbfb3 receipt and sidecar round trip, old dispatch-first convergence, and ordinary-first limitation;
17. menu/web/REST/ctl privacy, status/help and mixed-version best-effort warning;
18. exact HMAC byte vectors cover domain length, canonical root time, every field including dispatch, key grammar/ACL/create-once/no-rotation/missing-key gates and secret backup/restore;
19. BindSaga crash at prepared, ledger-bound, coordination-bound, done and rollback; exact current/prior-pin replay, distinct bind/rollback receipts and domains, every field mutation conflict, no duplicate bind/rollback, terminal retention/restart/prune/bound refusal;
20. WorkspaceChild crash before FS, after exact owned creation, after ready, lease attach and coordination attach; absence resume, exact-owned finalize, mismatch/unowned recovery_required and authenticated no-effect rollback; crash between BindSaga done, parent `native_bound` consumption and workspace `coord_attached -> done` rolls forward without repeating bind or losing the immutable attachment receipt;
21. both old schema-1 shapes (`schema,version,rows` and `schema,version,rows,history`) migrate without row loss, with exact existing-history preservation for the latter; duplicate/unknown/corrupt/oversize failure, collection and byte bounds, prune-before-archive at full-but-expired history, open rows never pruned and `operation_history_expired` after prune;
22. privacy census proves no key/token/path/prompt/wait text in sidecar, digest frames, errors, logs, backups or four human surfaces and no full internal bind/workspace identity outside authenticated host responses.

# Ownership boundary

- continue-p0: exact release helper, receipt schema, boost store/projection, compatibility harness and surface wording.
- continue-work-backend: ledger pin validation, reserve/reacquire consume lifecycle and exact-match recovery.
- continue-work-pump: selection plus reacquire/send ordering and running-notify outcome.
