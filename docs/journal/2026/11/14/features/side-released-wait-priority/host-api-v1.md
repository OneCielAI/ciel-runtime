---
type: api-contract
title: Wait boost host API v1
description: Superseding exact host-only JSON contract for released-wait selection, notification, reacquire and launch-until-bind recovery.
timestamp: 2026-11-14T10:04:40Z
tags: [work, wait, boost, mcp, host-api, recovery]
---

# Authority and common types

This document supersedes every earlier wait-boost wrapper proposal. The mod never reads the sidecar, reconstructs a pin, mints proof, classifies owner death, or infers capacity.

All calls are host-internal `work_parallel_event` events. Unknown fields refuse. Human/public projections never include any full pin, receipt, lease, host, agent, dispatch, assignment, capability or operation identity.

```text
ConsumePin = exact opaque object returned by backend/store
PinDigest = lowerhex64 SHA256(domain("ciel.wait-boost.pin.v1") + canonical length-framed ConsumePin)
SendReceipt = lowerhex64, backend-minted and persisted when running_notify begins
OperationDigest = lowerhex64, backend/store-computed framed transition identity; backend/store-only
BindSagaRef = bounded opaque reference to one persisted bind saga; host-only
WorkspaceRef = bounded opaque reference to one persisted workspace child; host-only
```

Before the first `begin_consume` mutation, the authenticated backend wrapper premints a stable lowerhex32 `attempt_id`; for running/reacquire it premints the lowerhex64 `send_receipt` in the same durable transition manifest. Reserve also always receives an explicit preminted attempt id. The store product API does not mint either identity. Exact retry of the same host event reuses those values; omission is normalized once at the wrapper boundary before any store call.

Every response that creates or advances a consuming sidecar row returns both `consume_pin` and `pin_digest`. A caller must replace its prior pin and echo only the latest one. An identical lost-response retry using the immediately prior pin is allowed only when backend proves the exact requested transition manifest already committed; it returns the current row/current refreshed pin. Different inputs conflict.

Common bounded error response:

```json
{"ok":false,"code":"conflict|stale_pin|not_found|capacity_wait|recovery_required|operation_history_expired|invalid_request","message":"bounded host-owned text","recovery_required":false}
```

No prompt, wait text, proof text or model content appears in arguments/errors/status/receipts. The sole secret-bearing response exception is `wait_boost_assignment.launch.assignment.worker_token`: backend returns that exact bounded worker capability once (and exact replay may return the same assignment bytes) to the authenticated host wrapper for the fork prompt. It is never accepted in a request, persisted in sidecar/ledger/coordination, logged, projected, included in an error/receipt/digest, or returned by any other response. Tests assert the value exists only at this assignment location and has zero value hits everywhere else.

`operation_digest` is computed and validated by backend/store only from the domain frame defined below. The mod never receives, supplies, overrides or echoes it. The opaque `consume_pin` plus backend-computed `pin_digest` is the caller correlation boundary.

# Status projection

Host-only `work_parallel_status` adds:

```json
{
  "wait_boost": {
    "schema": "wait-boost-host/1",
    "version": 7,
    "ordered": [{
      "item_id": "n2",
      "root_id": "n1",
      "state": "boosted|consuming",
      "release_ref": "bounded-ref",
      "release_at": 1.0,
      "boost_position": 1,
      "root_phase": "planning|executing",
      "eligibility": "eligible|waiting_for_execution_approval|capacity_wait|global_capacity_wait|launch_rate_wait|paused|recovery_required",
      "kind": "parked|running|reserve",
      "parked_lease_id": null,
      "lease_id": null,
      "host_id": null,
      "agent_name": null,
      "attempt_id": null
    }]
  }
}
```

This is internal. P0 consumed audit rows are excluded. Store order is parked group, then other rows; each group newest release then receipt/item tie-break.

# `wait_boost_next`

Request:

```json
{"event":"wait_boost_next"}
```

Backend rereads board, ledger, sidecar, coordination and native-status projection; it owns all eligibility and `ordinary_allowed` decisions.

No row:

```json
{"dispatch":"none","reason":"empty","ordinary_allowed":true}
```

Ineligible/front row:

```json
{"dispatch":"ineligible","reason":"waiting_for_execution_approval|capacity_wait|recovery_required","item_id":"n2","boost_position":1,"ordinary_allowed":true}
```

```json
{"dispatch":"ineligible","reason":"global_capacity_wait|launch_rate_wait|paused","item_id":"n2","boost_position":1,"ordinary_allowed":false}
```

`ordinary_allowed=true` only for planning, recovery-required and canonical-priority capacity because independently eligible ordinary work may proceed. It is exactly false for paused, launch-rate and total/global lease bound. The reason and boolean combinations above are exhaustive; another combination is invalid. Mod obeys the boolean and never derives it.

Parked begin descriptor:

```json
{
  "dispatch":"parked",
  "consume_pin":{},
  "pin_digest":"lowerhex64",
  "lease_id":"opaque",
  "host_id":"opaque",
  "agent_name":"bounded",
  "send_receipt":"lowerhex64",
  "ordinary_allowed":false
}
```

Running begin descriptor:

```json
{
  "dispatch":"running_notify",
  "consume_pin":{},
  "pin_digest":"lowerhex64",
  "send_receipt":"lowerhex64",
  "lease_id":"opaque",
  "host_id":"opaque",
  "ordinary_allowed":false
}
```

The backend mints `send_receipt` **before** the response, persists it in the consuming attempt/operation digest, and returns it. The mod never mints it and must echo it unchanged only for the matching attempt.

New or replayed consuming reserve launch:

```json
{
  "dispatch":"launch_pending",
  "consume_pin":{},
  "pin_digest":"lowerhex64",
  "launch": {
    "attempt_id":"lowerhex32",
    "lease_id":"opaque",
    "assignment_generation":1,
    "dispatch_name":"work-wait-boost-<attempt_id>",
    "state":"assignment_issued|spawning|uncertain",
    "assignment_ref":"bounded-ref"
  },
  "ordinary_allowed":false
}
```

At startup and every selector pass, an existing front consuming reserve attempt returns this same `launch_pending`; it never reserves again. Full assignment is issued only by `wait_boost_assignment`.

# Item-pinned `wait_boost_begin`

Immediate side running notification must not select a different newer row.

Request:

```json
{"event":"wait_boost_begin","item_id":"n2","kind":"running_notify","lease_id":"opaque"}
```

Success is exactly the `running_notify` descriptor above. No exact eligible row/lease:

```json
{"dispatch":"none","reason":"not_exact|not_running|already_consuming"}
```

# Running notification

Complete request, only after `$.session.send` returns `isDelivered:true`:

```json
{"event":"wait_boost_running_notify","operation":"complete","consume_pin":{},"pin_digest":"lowerhex64","delivery":{"delivered":true,"send_receipt":"backend-preminted-lowerhex64"}}
```

Backend validates the receipt equals the precommitted attempt receipt. Ledger drift does not invalidate a proven delivery because no ledger transition is claimed. Response:

```json
{"state":"consumed","outcome":"running_notified","delivery_committed":true,"recovery_required":false}
```

Definite failure (`isDelivered:false`):

```json
{"event":"wait_boost_running_notify","operation":"failed","consume_pin":{},"pin_digest":"lowerhex64","delivery":{"delivered":false,"send_receipt":"backend-preminted-lowerhex64"}}
```

The wrapper must echo the backend-preminted receipt for failed sends exactly as shown. Exact full-pin restore occurs. Response:

```json
{"state":"boosted","outcome":null,"main_handoff":true,"recovery_required":false}
```

Throw/unknown:

```json
{"event":"wait_boost_running_notify","operation":"uncertain","consume_pin":{},"pin_digest":"lowerhex64","delivery":{"delivered":null,"send_receipt":"backend-preminted-lowerhex64"}}
```

No state inference or resend occurs. Response:

```json
{"state":"consuming","outcome":null,"main_handoff":true,"recovery_required":true}
```

All three operations use the one exact `delivery:{delivered:true|false|null,send_receipt}` schema, and `send_receipt` is always required and must equal the precommitted pin value. Exact replay returns the same response. A missing/different receipt or pin conflicts.

# Parked reacquire

Reacquire uses a durable **send intent** because slot CAS is not send completion. The boost remains consuming through reacquire and native `session.send`; ledger slot acquisition alone never consumes it. Backend premints/persists a `send_receipt` for the reacquire attempt before returning the parked descriptor (the descriptor therefore also includes `send_receipt`).

Request:

```json
{"event":"wait_boost_reacquire","operation":"acquire","consume_pin":{},"pin_digest":"lowerhex64","send_receipt":"backend-preminted-lowerhex64"}
```

Success after the exact same `waiting_input slot=false -> slot=true` lease CAS returns a refreshed pin but remains consuming:

```json
{"state":"consuming","outcome":null,"phase":"send_pending","consume_pin":{},"pin_digest":"lowerhex64","send_receipt":"backend-preminted-lowerhex64","lease_id":"opaque","host_id":"opaque","agent_name":"bounded","recovery_required":false,"ordinary_allowed":false}
```

After the existing `SendMessage` call, the wrapper uses:

```json
{"event":"wait_boost_reacquire","operation":"complete|failed|uncertain","consume_pin":{},"pin_digest":"lowerhex64","delivery":{"delivered":true|false|null,"send_receipt":"backend-preminted-lowerhex64"}}
```

`complete` + true marks consumed/reacquired. `failed` + false performs an exact slot-release/park rollback if still safe. On committed rollback, the row returns boosted but retains terminal transition identity `{attempt_id,send_receipt,rollback_receipt_digest,operation_digest,outcome:'delivery_failed_rolled_back',terminal_at}` in the bounded transition audit for 7 days and at least the newest 32 terminal transitions; a later exact release/reacquire may clear the active-row terminal fields but never erases that retained audit before pruning; response is `{state:'boosted',outcome:'delivery_failed_rolled_back',main_handoff:true,recovery_required:false}`. Exact stale-pin retry returns that same result without another send/rollback; changed delivery/receipt conflicts. If rollback fails or its response is lost, the same attempt remains consuming/uncertain/recovery_required and blocks another attempt. `uncertain` + null never resends or infers. Exact send outcome and receipt, not slot state alone, authorize completion.

Capacity/no commit restores boosted:

```json
{"state":"boosted","outcome":null,"reason":"capacity_wait","recovery_required":false,"ordinary_allowed":true}
```

Ambiguous or later ledger version:

```json
{"state":"consuming","outcome":null,"reason":"recovery_required","recovery_required":true,"ordinary_allowed":true}
```

Receipt-free recovery of the **slot transition** is allowed only at exactly ledger before+1 with the exact same lease/role/state/slot transition; it reconstructs `send_pending`, not consumed. Later versions never guess. A boost becomes consumed/reacquired only from the exact correlated delivery result.

# Reserve assignment and launch

Backend first commits exact ledger reservation with `dispatch_name=work-wait-boost-<attempt_id>` and exact boost launch record, while boost remains consuming. Lost response replay with the old begin pin and the exact same reservation manifest returns the current refreshed pin; changed lease/generation/digests/name conflicts.

Assignment request/replay:

```json
{"event":"wait_boost_assignment","consume_pin":{},"pin_digest":"lowerhex64"}
```

Response:

```json
{
  "state":"assignment_issued",
  "consume_pin":{},
  "pin_digest":"lowerhex64",
  "launch":{
    "attempt_id":"lowerhex32",
    "lease_id":"opaque",
    "assignment_generation":1,
    "dispatch_name":"work-wait-boost-<attempt_id>",
    "assignment":{"worker_token":"sole-secret-bearing-field","prompt":"bounded fork assignment"},
    "assignment_digest":"lowerhex64",
    "capability_derivation_digest":"lowerhex64",
    "reserve_receipt_digest":"lowerhex64"
  }
}
```

Assignment replay returns identical assignment bytes and refreshed/current pin. Worker capability is deterministically rederived by backend; no plaintext token is persisted in the sidecar or logs.

The derivation is byte-exact: `HMAC-SHA256(shared_assignment_key, u64be(len(domain_utf8)) || domain_utf8 || u64be(len(canonical_json_utf8)) || canonical_json_utf8)`, with domain `ciel.wait-boost.assignment-capability.v1`. Canonical JSON is UTF-8, sorted keys, `,`/`:` separators and no NaN, with exact fields `scope,root_id,root_created_at,item_id,attempt_id,lease_id,assignment_generation,dispatch_name,purpose:"worker_report"`. `root_created_at` is the canonical JSON number and `dispatch_name` is mandatory; changed spelling/type/value conflicts. `shared_assignment_key` is the existing accepted shared `work-p0-assignment.key` beside the ledger, not a new wait-specific key/path (wire status may call it `assignment_key`). Its atomic creation fence covers both features: zero P0 nonterminal intents/open sends and zero wait consuming rows/nonterminal BindSaga/WorkspaceChild/CleanupSaga. Both P0 and wait intent creators take `work-assignment-key.creation.lock` beside the ledger and hold it across both zero-dependency reads plus key validation/publication. Exact global order is creation lock -> P0 sidecar lock -> wait sidecar lock -> key-file lock; reverse acquisition is forbidden and status reads never mutate. When any dependency exists, missing key returns recovery_required and creates nothing; it is never rotated/recreated. The accepted exact lowerhex64, private POSIX/Windows ACL, link/nonregular/race, read-only validation and secret backup classifier/path rules apply unchanged. It is never supplied by the mod or exposed in a response/digest/log/ordinary chunk.

Before spawning, the backend must prepare and finish the workspace child:

Prepare is the only ref-less workspace request. The authenticated host controller constructs `_host` from trusted repository/workspace policy and overwrites/discards caller-supplied `_host`:

```json
{"event":"wait_boost_workspace","operation":"prepare","consume_pin":{},"pin_digest":"lowerhex64","_host":{"internal":true,"repo_path":"canonical-absolute-existing-repo-root<=1000","repo_identity_digest":"lowerhex64","base_head":"bounded-commit","target_ref":"bounded-policy-ref","target_path_digest":"lowerhex64","branch_digest":"lowerhex64","gitdir_digest":"lowerhex64"}}
```

Every `_host` key is required and no extra key is accepted. `repo_path` is an absolute string <=1000/no NUL; other opaque text is nonempty <=200/no NUL and digests are lowerhex64. The authenticated host constructs/overwrites `repo_path` from the session launch workspace and discards caller-supplied values. Windows resolves an absolute normalized drive/UNC path, normalizes separators/case for comparison and verifies final-handle identity; POSIX uses absolute `realpath`. It must resolve to one existing repository root; nonexistent, nested/ambiguous or identity-mismatched resolution refuses before persistence/FS effect. Backend recomputes `repo_identity_digest` from canonical repository identity and verifies base head and target policy. `repo_path` is request/host-memory only: only its digest may persist, and path text is forbidden in public/internal status, logs, errors and backups. Backend premints `workspace_operation_id`, persists the child, and returns `{state:"prepared",workspace_ref,consume_pin,pin_digest}`. The mod never mints ref, operation id or proof. Later calls require the returned reference:

```json
{"event":"wait_boost_workspace","operation":"resume|attach_lease|attach_coord|rollback|complete","consume_pin":{},"pin_digest":"lowerhex64","workspace_ref":"bounded-ref"}
```

`prepare` persists exact operation/manifest identity before any file-system mutation. `resume` returns `absence_resume`, `exact_owned_finalize`, or `recovery_required`; it never adopts/deletes a mismatched or unowned target. `resume`, `attach_lease`, `attach_coord`, `rollback`, and `complete` each carry a state-specific exact receipt object and backend stores separate immutable `ready_receipt_digest`, `lease_attach_receipt_digest`, `coord_attach_receipt_digest`, or rollback receipt+operation digest. `rollback` requires the durable cleanup saga and exact ownership; it cannot erase an attached workspace merely from absence. Every success returns the refreshed pin, `workspace_ref`, exact bounded state `prepared|ready|lease_attached|coord_attached|recovery_required|done|rolled_back`, and no path text. Only `coord_attached` permits the spawning operation below. It stays nonterminal and protected while launch/BindSaga is nonterminal. After exact BindSaga `done` and parent boost `native_bound`, the same CAS or an exact replayable follow-up transitions `coord_attached -> done`, sets `terminal_at`, and retains the immutable manifest/attachment receipt; rollback likewise sets terminal `rolled_back` plus `terminal_at`. A crash between those three facts rolls forward without repeating bind.

Spawning:

```json
{"event":"wait_boost_launch","operation":"spawning","consume_pin":{},"pin_digest":"lowerhex64"}
```

Response returns `{state:"spawning",consume_pin,pin_digest}`. Exact lost-response replay using the prior pin and same transition returns the same current refreshed pin.

Explicit spawn denial:

```json
{"event":"wait_boost_launch","operation":"denied","consume_pin":{},"pin_digest":"lowerhex64"}
```

Denial first persists top-level `CleanupSaga{state:"prepared"}` before any cleanup effect; it is not an unconditional release/restore. The exact monotonic phases are `prepared -> bind_rolled_back -> coord_detached -> ledger_detached -> workspace_removed -> lease_released -> done`, with `recovery_required` from any ambiguous phase. Each edge persists its own before/after versions and receipt/digest; retries resume only the missing phase, and boost restore occurs only after `lease_released` with the exact cleanup done receipt. Any BindSaga rollback persists distinct `rollback_receipt_digest` and `rollback_operation_digest` under `ciel.wait-boost.bind-rollback.v1`; exact current/prior-pin replay returns the same rollback/cleanup phase without repeating effects, while changed prior phase, proof, cleanup fact, agent/name or receipt conflicts. Backend must prove no native effect, roll back/cancel any BindSaga, detach/cancel coordination and ledger workspace attachments with safe exact primitives (otherwise retain the reservation), remove only an exact-owned workspace with cleanup receipt, terminalize WorkspaceChild `rolled_back`, release/cancel exact lease/run, and finally restore boost.

Successful complete cleanup returns:

```json
{"state":"boosted","outcome":null,"cleanup":"rolled_back","recovery_required":false,"ordinary_allowed":true}
```

Any absent/ambiguous/mismatched proof returns without restoring:

```json
{"state":"consuming","outcome":null,"cleanup":"recovery_required","recovery_required":true,"ordinary_allowed":true}
```

Unknown spawn result:

```json
{"event":"wait_boost_launch","operation":"uncertain","consume_pin":{},"pin_digest":"lowerhex64"}
```

Response returns `{state:"uncertain",consume_pin,pin_digest,recovery_required:true}`; boost remains consuming.

# Startup native discovery and absence

Backend/host lists native agents with complete-list proof and applies the same owner/process-identity rules as P0. The mod does not classify owner state.

Exactly one native agent with exact dispatch name:

```json
{"event":"wait_boost_launch","operation":"bind","consume_pin":{},"pin_digest":"lowerhex64","agent_id":"opaque","dispatch_name":"work-wait-boost-<attempt_id>"}
```

Backend ordering is recoverable through a separate persisted `BindSaga`:

1. validate exact latest pin, coord-attached workspace, launch record, lease, name and native identity;
2. before any bind mutation, persist `BindSaga{state:prepared}` with attempt/scope/root+created-at/item/lease/dispatch/agent id+name/generation/assignment identity plus immutable `bind_manifest_digest` over those known facts and ledger/coord before versions; final bind receipt and operation digest remain null;
3. bind the exact ledger row, persist `ledger_bound` plus exact ledger before/after versions and bound digest;
4. bind the exact coordination generation, persist `coord_bound` plus exact coordination before/after versions and bound digest;
5. construct and persist the exact bind commit receipt/digest, transition saga `done`, then `complete_bound_launch` consumes outcome `native_bound`;
6. return the same terminal response on exact current or prior-pin lost-response replay. A changed field conflicts. A crash resumes the persisted phase without repeating a committed bind; missing/mismatched evidence becomes `recovery_required`.

`BindSaga` is bounded independently from active boost rows. Nonterminal `prepared|ledger_bound|coord_bound|recovery_required` entries are never pruned. Terminal `done|rolled_back` entries retain seven days and at least newest 32, prune deterministically by `(terminal_at,attempt_id)` at startup/admission, and return `operation_history_expired` after prune rather than becoming a new bind. A full collection with no eligible terminal eviction returns a visible bound error.

Response:

```json
{"state":"consumed","outcome":"native_bound","lease_id":"opaque","agent_id":"opaque","dispatch_name":"work-wait-boost-<attempt_id>","recovery_required":false}
```

Complete native-list absence plus backend-proven dead/reused owner:

```json
{
  "event":"wait_boost_launch",
  "operation":"absent",
  "consume_pin":{},
  "pin_digest":"lowerhex64",
  "_host":{
    "internal":true,
    "launch_nonce":"lowerhex32",
    "session_pid":123,
    "process_identity":"bounded opaque process-start identity",
    "absent_dispatch_name":"work-wait-boost-<attempt_id>",
    "absent_from_complete_list":true,
    "observed_native_names":["bounded exact names, max 256"],
    "observed_native_agent_ids":["bounded exact ids, max 256"],
    "native_observed_at":1.0,
    "owner_state":"dead|reused",
    "owner_observed_at":1.0
  }
}
```

Exact `_host` rules: `internal` and `absent_from_complete_list` must be literal true; nonce lowerhex32; `session_pid` positive integer; process identity, dispatch name, each native name/id are nonempty strings <=200 with no NUL; arrays have <=256 unique strings; observation times are finite nonnegative numbers; owner state is only `dead|reused`. Every listed field is required and no other field is allowed for this operation. The authenticated host controller constructs/overwrites `_host` from its own process registry and one complete native-list observation; any model/caller-supplied `_host` is discarded before dispatch, and a direct unauthenticated `_host` refuses. Caller does not classify death/reuse.

Backend validates absence, owner death/reuse and the exact unbound reservation, then creates or resumes the same persisted `CleanupSaga` used by explicit denial with `cleanup_trigger:"native_absent"`; it does not directly release/cancel/restore. The native-absence observation is the saga's exact prepared proof. Cleanup proceeds through bind disposition, coordination detach, ledger detach, exact-owned workspace cleanup, lease/run release and the atomic parent-restore+saga-done CAS defined below. Any unproven phase remains `recovery_required`. Exact replay with the same pin, cleanup id and observation identity returns the same/current cleanup phase; changed nonce/process/dispatch/list/owner/cleanup input conflicts and never authorizes an effect.

Multiple matching native names, name/id mismatch, incomplete list, or live/unknown owner: no bind/absent call is made; backend/status returns recovery_required and keeps consuming.

# Digest domains and bind receipt

All digests use one byte-exact framing: `SHA256(u64be(len(domain_utf8)) || domain_utf8 || u64be(len(canonical_json_utf8)) || canonical_json_utf8)`, where canonical JSON is UTF-8, object keys sorted, separators `,`/`:`, no NaN. No alternative `{domain,value}` wrapper is allowed. Domains and committed fields:

```text
assignment_digest = H("ciel.wait-boost.assignment.v1", exact coordination assignment manifest)
capability_derivation_digest = H("ciel.wait-boost.capability.v1",
  scope,root_id,root_created_at,item_id,attempt_id,lease_id,assignment_generation,
  dispatch_name,purpose)
# The canonical object is byte-for-byte the HMAC object; only key/token are absent.
reserve_receipt_digest = H("ciel.wait-boost.reserve.v1",
  ledger_before_version,ledger_after_version,exact before-absence frame,exact reserved lease frame)
pin_digest = H("ciel.wait-boost.pin.v1", exact ConsumePin)
```

No key/token is included in capability derivation identity.

Backend-issued bind commit receipt:

```json
{
  "schema":"wait-boost-bind-commit/1",
  "attempt_id":"lowerhex32",
  "lease_id":"opaque",
  "dispatch_name":"work-wait-boost-<attempt_id>",
  "ledger_before_version":1,
  "ledger_after_version":2,
  "ledger_bound_digest":"lowerhex64",
  "coord_before_version":1,
  "coord_after_version":2,
  "coord_bound_digest":"lowerhex64",
  "agent_id":"opaque",
  "agent_name":"bounded",
  "assignment_generation":1,
  "assignment_digest":"lowerhex64",
  "operation_digest":"lowerhex64"
}
```

`ledger_bound_digest` frames exact bound lease fields including id/scope/item/root relation/role/state/slot/created_at/host_id/agent_name/dispatch_name. `coord_bound_digest` frames exact run state, lease/root/item, agent id/name, generation and assignment digest. `operation_digest` frames full latest pin + launch digests + workspace receipt + BindSaga identity + entire receipt excluding operation_digest. Field mutation fails closed.

Cleanup domains use the same u64be framing and exact canonical objects. `cleanup_id = H("ciel.wait-boost.cleanup-id.v1", {scope,item_id,release_receipt,attempt_id,cleanup_trigger})`, where trigger is exactly `spawn_denied|native_absent`; `release_receipt` and `cleanup_manifest_digest` are persisted in the row, the map key must equal the recomputed id, at most one nonterminal cleanup may exist for that source identity, identical persisted/recomputed manifest is replay and changed manifest conflicts. `ciel.wait-boost.cleanup-manifest.v1` covers cleanup id/trigger/release receipt/attempt/scope/root+created-at/item/lease/workspace plus the bind branch: `bind_manifest_digest` is null exactly when no BindSaga exists and otherwise is the exact matching saga digest; validators recompute and require the persisted `cleanup_manifest_digest` to equal this frame; `.native-absence.v1` adds exact denial/native absence observation; `.bind-rollback.v1` adds the exact BindSaga prior phase/proofs and committed rollback facts, while `.no-bind.v1` proves no BindSaga/effect existed and is mutually exclusive with rollback; `.coord-detach.v1` and `.ledger-detach.v1` add exact before/after versions and whitelisted detached state; `.workspace-cleanup.v1` adds exact ownership/removal receipt; `.lease-release.v1` adds exact lease/run before/after committed state; `.boost-restore.v1` adds exact parent BoostRow prior/restored terminal audit identity plus `boost_sidecar_before_version` and `boost_sidecar_after_version=before+1`; `.cleanup-done.v1` covers the immutable cleanup identity plus every edge receipt/version/digest, bind disposition/proof, both boost sidecar versions and boost restore receipt/operation. Every prior-pin replay recomputes the relevant object. No arbitrary proof or alternate domain is accepted.

`bind_disposition` is exactly `rolled_back|not_created`: rolled_back requires non-null exact matching `bind_manifest_digest`, bind rollback receipt+operation and null no-bind proof; not_created requires `bind_manifest_digest=null`, an exact no-bind proof and null rollback fields. In prepared, disposition/proofs are null while `bind_manifest_digest` is null iff no BindSaga exists, otherwise the exact matching digest; an arbitrary lowerhex value conflicts. Cleanup done requires `boost_sidecar_before_version`, `boost_sidecar_after_version=before+1`, `boost_restore_receipt_digest` and `boost_restore_operation_digest` matching the parent restored BoostRow terminal audit or retained history row, plus final `cleanup_operation_digest`; parent restore and CleanupSaga `lease_released -> done` are one strict sidecar CAS. A lost-response resolver compares the full manifest and both committed rows and returns done without a second restore; the cross-link validator compares attempt/scope/root/item/lease/transition/outcome/receipt/operation.

Workspace domains are likewise byte-exact: `ciel.wait-boost.workspace-manifest.v1` covers attempt/lease/scope/root+created-at/item, repo identity, base head and target/branch/gitdir digests; distinct `ciel.wait-boost.workspace-ready.v1`, `.lease-attach.v1`, `.coord-attach.v1`, and `.rollback.v1` receipts cover operation id, manifest digest, exact prior/next state and phase-specific committed facts. Retained terminal workspace history has a separate exact `ciel.wait-boost.workspace-terminal.v1` operation domain over the full terminal WorkspaceChild manifest: `done` uses `coord_attach_receipt_digest` as receipt plus this recomputed operation digest, while `rolled_back` uses its exact rollback receipt/operation. New completion retained history uses the exact enriched-v1 manifest with immutable parent `scope,item_id,root_id,root_created_at,release_receipt` plus completion fields. Lossless read compatibility accepts the exact previously emitted seven-field legacy completion manifest from a four-key schema-1 history-bearing sidecar and grandfathers that exact row after migration persists the canonical seven-key document. Exact key shape discriminates it; it cannot authorize BindSaga/WorkspaceChild/CleanupSaga cross-links because no root identity may be inferred. Under the sidecar lock every CAS compares the before/after legacy subset: a pre-existing row may remain only byte-identical, or be deleted only by the deterministic eligible retention/prune path; insertion into a canonical document, enriched-to-legacy replacement, mutation, and non-prune deletion fail closed. New code never emits the legacy variant. Standalone history validators enforce an exhaustive tuple table: completion `native_bound -> (complete_bound_launch,consumed,native_bound)` and `reacquired|running_notified -> (complete_consume,consumed,<same outcome>)`; restore transitions `running_notify_failed|launch_denied|capacity_no_commit|native_absent -> (restored_boosted,boosted,restored_boosted)`; `native_absent` requires the exact terminal native-absence CleanupSaga cross-link and is retained unchanged through later archive/replay; send rollback `reacquire_send_failed_rolled_back -> (delivery_failed_rolled_back,boosted,delivery_failed_rolled_back)`; cancel `cancel_open` with outcome `reblocked|terminal|identity_mismatch -> (cancelled,<same outcome>)`; BindSaga/WorkspaceChild `done -> (done,native_bound,consumed,native_bound)`, `rolled_back -> (rolled_back,restored_boosted,boosted,restored_boosted)`. Another transition/outcome/result tuple is invalid, including in a standalone retained row with no live child. Every enriched/new tuple also requires `result.recovery_required=false` and `result.operation_ref = SHA256(operation_digest ASCII bytes)[:12]` as lowercase hex; validators recompute the reference from the outer operation digest, so an arbitrary bounded ref or `true` recovery flag conflicts. The sole grandfathered legacy seven-field completion exception preserves its existing exact legacy result bytes (`state`/`operation_ref` bounded text, `outcome` null|bounded text, `recovery_required` boolean) without normalization; it is audit/replay-only, grants no child authority, cannot be newly emitted, and the locked before/after provenance rule rejects any change to its ref/flag/result. Bind domains are `ciel.wait-boost.bind-saga.v1` for immutable `bind_manifest_digest` over the prepared known identity/before facts (available before effects), `ciel.wait-boost.ledger-bound.v1`, `ciel.wait-boost.coord-bound.v1`, `ciel.wait-boost.bind-receipt.v1`, and distinct `ciel.wait-boost.bind-rollback.v1`. Final `bind_receipt_digest` and `operation_digest` are null through prepared/ledger_bound/coord_bound and required only at done; they frame the produced final receipt rather than serving as prepared identity. The rollback object exactly covers saga identity, latest pin, prior phase and every known ledger/coord/bind proof, agent id/name, cleanup effects and their before/after committed facts; `rollback_operation_digest` binds latest pin+saga+the entire rollback receipt excluding that digest. `rollback_receipt_digest` and `rollback_operation_digest` never overload `bind_receipt_digest`/bind operation identity. All use the same u64be framing; arbitrary lowerhex values and alternate wrapper encodings refuse.

# Schema, recovery and bounds

The persisted sidecar exact top-level keys are `schema,version,rows,bind_sagas,workspaces,cleanups,history`, maximum 2,000 entries per collection, maximum 65,536 canonical bytes per row and 2 MiB total. All collection values use the exact keys/state/null/type/edge tables in [the design](design.md): ids/text <=200/no NUL, lowerhex32/64 grammar, finite bounded times, safe integers, phase-specific receipts, terminal timestamps, discriminated bounded history manifest/result, monotonic transitions and mandatory parent/child cross-links. Unknown fields or invalid state predicates fail closed. Both emitted schema-1 shapes migrate losslessly: `{schema,version,rows}` adds empty `history`, `bind_sagas`, `workspaces` and `cleanups`; `{schema,version,rows,history}` preserves every history row byte-semantically and adds only empty `bind_sagas`, `workspaces` and `cleanups`. Either shape persists only on the next successful CAS; it never fabricates, drops or rewrites existing rows/history. Unknown/duplicate/corrupt/oversized/invalid cross-linked state blocks writes.

Startup and admission prune eligible terminal transition, bind, workspace and cleanup records before capacity checks. Workspace states `prepared|ready|lease_attached|coord_attached|recovery_required` are nonterminal and never pruned; only `done|rolled_back` are terminal. Each terminal collection retains seven days and at least newest 32; open boost, bind, workspace and cleanup authority is never pruned. Terminal BindSaga/WorkspaceChild remains reference-protected while a linked BoostRow is open/consuming, its parent completion identity is not durably terminal/archived, or recovery/replay names it; pruning begins only when every cross-reference is safe. At full capacity with no eligible terminal record, the exact operation refuses with a bounded recovery/bound response. Crash recovery replays each persisted phase: workspace absence resumes the same manifest, exact owned facts finalize, mismatch is recovery_required; BindSaga resumes only its missing ledger/coord/done phase. Exact stale response replay returns the same current state; mutation conflicts; a pruned exact operation returns `operation_history_expired`. Every new/enriched/nonlegacy history row uses `history_id = H("ciel.wait-boost.history-id.v1", {kind,scope,item_id,release_receipt,attempt_id,transition,outcome,receipt_digest,operation_digest})`; map key and row id equal the recomputed digest. Grandfathered legacy rows alone preserve their prior key/id under locked provenance and cannot be newly inserted. A strict CAS records all keys removed by its deterministic pre-mutation prune and requires them absent from the complete final history map, forbidding same-CAS reuse; later different-operation reuse also fails id recomputation.

# Idempotency and privacy

Every mutation is strict CAS. Exact same transition replay succeeds and returns the current exact response/refreshed pin. Different pin field, digest, receipt, assignment, generation, lease, dispatch name, agent, outcome or transition conflicts. A stale prior pin is accepted only by the transition-specific lost-response resolver when its complete requested manifest exactly equals the already committed transition; it is never accepted as generic authorization.

Host events may carry opaque internal ids only over the authenticated host boundary. Human menu/web/model surfaces and normal REST/ctl projections expose only bounded release/operation refs, behavior state and accepted warnings/help. They never expose ConsumePin, full receipt, lease/host/agent/dispatch ids, BindSaga/WorkspaceChild internals, assignment, prompt, capability data, key bytes, path text, proof/free text or digest frames. The authenticated `wait_boost_assignment` response's one worker-token exception is exclusively host-to-mod and is neither a human/public projection nor an ordinary REST/ctl response.

Required contract tests cover: standalone retained completion/restore/send-rollback/cancel/bind/workspace rows after live-child pruning, every exhaustive tuple above plus arbitrary-string/recovery-boolean/operation-ref rejection, native-absence cleanup done → restored parent `native_absent` → later release/archive → restart/exact replay without transition rewriting, full parent root/time/release identity mutation, workspace done terminal receipt/operation and rollback proof mutation; exact HMAC and matching capability-digest vectors including root time/dispatch plus every field/type mutation; shared `work-p0-assignment.key` create/read/ACL/race/no-rotation/missing-key/secret-backup lifecycle, one creation lock/order, P0-vs-wait races in both directions, process crash/deadlock and cross-feature deletion/no-create; ref-less trusted-host workspace prepare with canonical repo_path, Windows/POSIX resolution, backend identity recomputation, caller forgery/path mismatch/nonexistence/ambiguity refusal, path privacy and backend-minted id/ref; workspace crash rows before FS, after effect, ready, lease attach and coordination attach; every phase receipt/current+prior-pin replay, absence resume, exact-owned finalize, mismatch/unowned refusal, full CleanupSaga crash/mutation/current+prior-pin replay at every prepared/bind/coord/ledger/workspace/lease/boost-restore/done edge, every cleanup canonical field/domain mutation, explicit no-bind versus rollback branches, parent restore cross-link mismatch and recovery_required branches, atomic restore/done crash-before-CAS, response loss after CAS, current/prior-pin replay, version/receipt mutation and no-double-restore, and roll-forward across BindSaga done, parent native_bound and workspace done terminalization; BindSaga prepared manifest versus final receipt nullability and crashes at prepared/ledger-bound/coord-bound/done/rollback, exact current and prior-pin replay, distinct bind/rollback receipt and operation domains, every prior-phase/cleanup/receipt/agent/name/version/digest mutation, and no duplicate bind/rollback; both old schema-1 shapes migrate without loss and the history-bearing shape preserves every existing history row byte-semantically, including exact legacy seven-field completion manifests across read → next CAS → restart; CAS before/after-diff tests prohibit legacy insertion/replacement/mutation/non-prune deletion, including any legacy result/ref/recovery-flag change; allow byte-identical unrelated writes and eligible prune; preserve an arbitrary bounded legacy ref/boolean through read → CAS → restart; and prohibit legacy child-authority cross-links; per-collection/byte bounds, prune-before-archive when full-but-expired, open-row retention, seven-day/newest-32 restart pruning and expired replay; new history-id byte vectors and field mutation, enriched/nonlegacy map-key recomputation, same-CAS pruned-key reuse refusal, later different-operation key collision refusal, and grandfathered legacy key preservation/no-new-insert; and a privacy census over host errors/status, human surfaces, logs and backups.
