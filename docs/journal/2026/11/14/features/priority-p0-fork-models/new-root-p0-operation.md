---
type: design-amendment
title: New-root P0 support boundary
description: Beta refusal contract for unsupported new-root P0 creation under unprovable mixed-version access, with deferred all-new saga notes.
timestamp: 2026-11-14T06:04:00Z
tags: [work, p0, idempotency, mcp, rest, ctl, recovery]
---

# Beta ruling: unsupported and refused before write

New-root P0 creation is **unsupported in this beta** because mixed-version safety cannot be proven or enforced. Real 57dbfb3 evidence shows every proposed old-readable/pre-reserve fence remains removable or bypassable. The runtime cannot prove that no already-running old writer/scheduler can access a scope: the attachment store records ownership/times, not build capability.

Exact active branch detection:

- `work_plan_set(priority=P0, root_id absent)` is new-root creation and is refused **before any write**;
- root-form `work_node_add(priority=P0, parent_id absent)` is new-root creation and is refused **before any write**;
- `work_plan_set(priority=P0, root_id present)` is supported existing-root activation and routes the existing-root P0 service;
- child `work_node_add(parent_id present)` remains under existing rules and cannot assign root priority.

A refused create performs no board, P0 marker, create-operation, ledger or coordination write. No operation id/root id is durably minted, no partial row is created, and normal non-P0 create remains unchanged. Existing-root P0 activation remains a separate supported path; this ruling does not weaken it.

Exact machine response on MCP, REST, web and `ciel-runtimectl`:

```json
{
  "ok": false,
  "code": "p0_new_root_unsupported_mixed_version",
  "message": "New P0 goal creation is unavailable in this beta because concurrent older Work writers cannot be safely excluded. Create the goal at P1-P5, then use supported existing-root P0 activation.",
  "writes": false,
  "recovery_required": false
}
```

Human/menu/web text uses the exact message, with no internal ids. REST returns the existing bounded conflict/unsupported status mapping; ctl exits nonzero and prints the same reason. Help text states that P0 may be activated only on an existing root in this beta.

Scheduler fail-closed policy: read the store without creating/writing it. A missing file is clean. If `work-p0-create-operations.json` exists from a partial/newer process, every valid row in `prepared|board_created|marker_written|conflict` projects through `p0_activation.incomplete/conflicts`; a known exact root id gates that root across P0, wait-boost and ordinary dispatch. If the store is corrupt, unknown schema, contains an unparseable row, or an active row has no extractable exact root id, scheduling is gated **globally** across P0, wait-boost and ordinary lanes with `p0_create_recovery_required`. The beta never advances, repairs, prunes or deletes an unknown create row automatically. A `done` row is allowed only after the ordinary P0 marker reader independently verifies effective P0.

Required beta tests:

1. `plan_set(P0,root_id absent)` and root `node_add(P0,parent absent)` fail before every store write/id mint;
2. `plan_set(P0,root_id present)` reaches supported existing-root activation and is not caught by refusal;
3. child add priority behavior remains existing/refused and non-P0 create bytes/responses remain compatible;
4. REST/web/ctl return the exact code/message/writes=false for both refused create shapes;
5. injected prepared/board/marker/conflict rows with known root gate that root in all lanes;
6. corrupt/unknown/unparseable/no-root active store globally gates all scheduler lanes without creating/writing/pruning the store;
7. missing store is clean and no attachment/process heuristic claims mixed-version capability.

# Deferred all-new saga (not active, no implementation GO)

The material below is retained only for a future runtime with an enforceable authenticated build/capability boundary. It must not be exposed as supported UI or implemented in this beta.

# Deferred command semantics

A future all-new runtime may use one application-service saga shared by MCP, REST, web and `ciel-runtimectl`. Existing non-P0 commands and existing-root priority operations remain unchanged.

The unified command is `p0_root_create`. It always means **create one new root**. It never replaces an existing root:

- model `work_plan_set` with `priority=P0` is routed here only when `root_id` is absent;
- `work_plan_set` with `root_id` remains the existing replacement command and cannot use this saga;
- root-form `work_node_add` with no `parent_id` and `priority=P0` is routed here;
- child `work_node_add` remains unchanged.

The stable operation identity is exactly lowercase hex32. Root identity and time are host-owned.

# Deferred surface fields

Normalized command payload:

```text
operation_id: lowerhex32
expected_version: nonnegative integer
requester_scope: host-derived runtime-qualified session identity
create_kind: "plan_set_create" | "node_add_root_create"
spec: NormalizedRootSpec
requested_priority: "P0"
```

## MCP

`work_plan_set` and root-form `work_node_add` accept `priority:"P0"` and optional `operation_id:lowerhex32`. The host wrapper validates or mints the id **before its first write** and calls `p0_root_create` instead of `_run`.

A model omission is exact only within that live tool invocation: the wrapper retains the id across its internal retries and returns it in host metadata. If the process dies after durable preparation but before any response, a later model retry that again omits the id can create a second operation. No stable Claude tool-invocation id is currently available as a proven dedupe source; the implementation must document this limitation and must not claim transport-exact replay across process loss. A caller-supplied returned operation id, REST/ctl id, or future proven invocation id gives exact replay.

Model response:

```text
{root_id, canonical_priority:"P1", effective_priority:"P0",
 operation_ref, operation_state, phase:"planning", dispatchable:false}
```

The full operation id is host-internal metadata. `operation_ref = sha256("ciel.p0-create.operation-ref.v1\0" + operation_id_ascii)[:16]`, exactly 16 lowercase hex characters. Model/human text uses only this ref. The runtime wait gate length is exactly `len("ciel:p0-activation:") + 16 = 35`, within `tree.MAX_WAIT_CHARS=120`, and is validated before normalization. Recovery never derives identity from this display ref: private state binds/compares full operation id, operation digest and the exact expected gate string.

## REST/web

```text
POST /ca/work/api/v1/commands/p0-root-create
Idempotency-Key: <lowerhex32>          # preferred
body.operation_id: <lowerhex32>       # allowed when header absent; mismatch refuses
body = {expected_version, create_kind, spec, requested_priority:"P0"}
```

REST/web may omit identity on the first request; the authenticated application layer mints and returns it. Their existing operation state retains it for transport retries.

## ciel-runtimectl

```text
ciel-runtimectl work <session> p0-root-create \
  --kind plan-set-create|node-add-root-create --title ... \
  [--note ... --ref ... --waiting-on ... --children-json ...] \
  [--expected-version N] [--operation-id lowerhex32]
```

ctl mints once before its first POST when omitted and reuses it for every retry.

# Durable private operation store

Use a separate strict-CAS private file `work-p0-create-operations.json` beside the session Work/P0 state. It is not part of the exact legacy P0 sidecar.

```text
CreateOperationStore = {
  schema: 1,
  version: nonnegative integer,
  rows: {"<operation_id lowerhex32>": CreateManifest}
}
MAX_BYTES = 8 MiB
MAX_ROWS = 128
MAX_ACTIVE_ROWS = 64
```

Reads use the shared cross-process advisory lock plus in-process lock, bounded read and duplicate-key rejection. Every mutation supplies exact expected version, deep-copies, validates the entire document, writes canonical UTF-8 JSON to an exclusive temporary file, fsyncs and atomically replaces. Invalid/corrupt/oversized state is quarantined (or, if quarantine fails, blocks writes); it is never silently replaced.

Active recoverable states (`prepared|board_created|marker_written`) are never evicted automatically. `conflict` is terminal fail-closed, not active: it retains full payload for 7 days and at least the newest 32 terminal rows together with `done`, then deterministic write-time pruning may remove older terminal rows. During retention, exact replay returns the same conflict. There is no transition out of conflict; a changed request needs a new operation id. Admission refuses before exceeding row/byte/active bounds and never deletes a recoverable operation to make space.

The first durable write stores the **full normalized root spec**, not only its digest and not a pointer to a later receipt:

```text
CreateManifest = {
  operation_id: lowerhex32,
  state: "prepared" | "board_created" | "marker_written" | "done" | "conflict",
  requester_scope: text<=200,
  create_kind: "plan_set_create" | "node_add_root_create",
  requested_priority: "P0",
  canonical_priority: "P1",
  prepared_board_version: nonnegative integer,
  spec: NormalizedRootSpec,
  payload_digest: lowerhex64,
  operation_digest: lowerhex64,
  root_id: "n" + positive decimal,
  root_created_at: finite nonnegative number,
  prepared_at, updated_at,
  board_version: null | nonnegative integer,
  marker_version: null | nonnegative integer
}
```

`NormalizedRootSpec` is the exact replay payload:

```text
{title, note, ref, waiting_on, children:[NormalizedNodeSpec...]}
NormalizedNodeSpec = {title, note, ref, waiting_on, children:[...]}
```

Priority is not accepted inside descendants. The persisted root canonical priority is fixed separately as P1.

## Hard payload bounds

Validation occurs before the first durable write:

- maximum depth: existing `tree.MAX_DEPTH` = 8 levels below root;
- maximum nodes in this operation payload: 1,000 including root;
- title: existing `tree.MAX_TITLE_CHARS` = 300 per node, nonempty after normalization;
- note: existing `tree.MAX_TEXT_CHARS` = 2,000 per node;
- ref: existing `tree.MAX_REF_CHARS` = 64 per node;
- waiting_on: existing `tree.MAX_WAIT_CHARS` = 120 per node, one line;
- unknown keys, non-lists and duplicate/cyclic object structures refuse;
- canonical UTF-8 JSON of normalized `spec`: maximum **1 MiB**;
- canonical encoded `CreateManifest`: maximum 1.25 MiB;
- full store: 8 MiB / 128 rows / 64 active rows as above, leaving explicit envelope/other-row overhead.

Boundary tests construct a canonical payload exactly at 1 MiB (accepted), one byte over (refused), a manifest at 1.25 MiB (accepted) and one byte over (refused), plus full-store/row/active bounds.

`payload_digest = SHA256(canonical_json(spec))`. `operation_digest` covers a framed canonical object containing operation id, requester scope, create kind, requested/canonical priority, prepared board version, payload digest, root id and root creation time. Replay reconstructs the tree only from the durable `spec` and validates both digests before use.

The sidecar is private local native-home/session state. It may contain sensitive titles, notes and waits. Payload content is never included in public projection, logs, telemetry, errors, journals or human operation status. Packaging/export must follow the existing private native-home exclusion; only bounded refs, state and hashes may leave it.

# Compatible root identity and board insertion

The prepare read observes board `next_id=N` and premints `root_id="n"+N` plus `root_created_at`. This is an intent, not authority to overwrite. Children remain deterministic under the monotonic allocator: any unrelated normal allocation must first consume `nN` and advance `next_id`, which makes the prepared operation conflict before child allocation; if `nN` remains unused and `next_id<=N`, no normal allocator could have consumed the later child range.

Host-only board operation:

```python
tree.create_root_exact(
    doc, normalized_spec, *, root_id: str, root_created_at: float,
    canonical_priority: Literal["P1"]
) -> str
```

Under the board lock it requires:

1. `root_id` matches `^n[1-9][0-9]*$` and is unused;
2. `int(root_id[1:]) >= doc.next_id`; if another concurrent create already used that or a higher id, the operation conflicts and never chooses a replacement identity;
3. insert exact root and children, starting child allocation after `max(doc.next_id, root_numeric+1)`;
4. advance `next_id` past every allocated child;
5. force canonical P1, root phase planning and non-dispatchable state;
6. never replace an existing root or unrelated data.

A concurrent non-P0 create that wins the board lock and consumes the preminted id causes this operation's board step to conflict/recovery-required; no collision overwrite or second identity is minted under the same operation.

# Board version and unrelated progress

`prepared_board_version` proves the snapshot used for identity minting; it is not a permanent demand that the whole board remain byte-identical.

At board insertion recovery, the service rereads under the board lock:

- if the exact root identity already exists, verify exact root time, normalized durable spec, P1 and planning; matching facts advance;
- if identity is absent and `doc.next_id <= root_numeric`, insert into the fresh document using `create_root_exact`, preserving all unrelated roots/progress;
- if identity is occupied, `doc.next_id > root_numeric`, or the exact root mismatches, fail closed as conflict;
- never replace the board with the prepared snapshot and never overwrite unrelated changes.

Thus unrelated progress that does not consume/collide with the preminted identity can roll forward through a fresh board CAS.

# Old-readable activation gate

Planning phase is insufficient because 57dbfb3 can approve the root during a crash window. `create_root_exact` therefore writes a reserved runtime-owned root wait before the root becomes visible:

```text
waiting_on = "ciel:p0-activation:" + operation_ref
```

The prefix and bounded operation_ref are exact; it is not a user wait and side-release logic refuses this reserved prefix. The user's requested root `waiting_on` remains only in the private durable spec until activation completes. Every old scheduler's path gate sees the root wait even if an old process changes phase to executing, so no descendant is runnable/reservable.

Only the final `done` board transaction, after exact effective P0 verification, replaces the reserved gate with the user's original normalized `waiting_on` (possibly empty). The exact operation/root/marker relation is revalidated under the board lock. Crash before this replacement remains gated; replay is idempotent. Public status labels it `p0_activation_incomplete`, not a user dependency. Model/side release cannot clear it.

# Exact P0 marker operation

After the exact board root exists, use only the accepted exact-schema PriorityClear record in the existing `clears` collection; do not invent a new operation kind/shape.

For create activation:

- the **active** RootMarker plus the prepared PriorityClear targeting canonical P1, written in the same sidecar CAS, means activate effective P0 under the accepted exact-schema correction; a cleared marker means end P0 and can never activate this root;
- the PriorityClear's scope/root identity, operation id relation, requested priority and prepared state must match this CreateManifest;
- roll-forward verifies the exact relation before changing marker/operation state;
- a mismatched or unrelated clear never activates this root.

The service transitions:

```text
prepared -> board_created -> marker_written -> done
```

`done` requires exact root + active marker + rolled-forward related PriorityClear targeting P1 + effective P0. The final board CAS then removes the reserved activation wait/restores the user's wait and records the resulting board version before the manifest state becomes done.

# Crash recovery

| Durable point | Recovery |
|---|---|
| before manifest | caller-held id can retry; omitted model id is not recoverable across process loss and limitation is explicit |
| prepared full payload | validate payload/digests; reread board; insert exact root if identity remains available |
| board root | verify exact durable spec/identity/P1 plus reserved activation wait; write active marker+related PriorityClear |
| marker/clear | verify exact active-marker relation; roll forward; verify effective P0; final board CAS restores user wait |
| done | verify gate removed/user wait restored; return same root without writes |

Every surface calls the same reconciliation before returning. Same id+same payload is idempotent. Same id with changed payload, scope, kind or identity conflicts. Recovery never invents children from a digest and never chooses a replacement root.

# Dispatch gates and status

Old 57dbfb3 sees canonical P1 plus the reserved root wait gate. Planning alone is not claimed safe: even if old `work_execute` changes it to executing, the root wait keeps every descendant out of `runnable_leaves` while the gate remains. New host projection:

```text
p0_activation: {
  incomplete:[{root_id,state,operation_ref}],
  conflicts:[{root_id,operation_ref,reason}]
}
```

Incomplete/conflict root ids are excluded from P0, wait-boost and ordinary dispatch. A manifest disappears from the gate only after backend verifies effective P0, not merely because its state text says done. Pump consumes root id/state/ref only, never full operation id.

Human/menu/web/model status shows operation_ref, bounded state/reason, title display already authorized by the board, `P1 (effective P0 pending)` and planning. It never shows payload, full operation id, root creation time or marker identity. Authenticated REST/ctl machine output may retain the existing full operation id authorization.

Help text:

> New P0 goals are durably stored, created as planning P1 roots, then activated through a recoverable P0 operation. They cannot dispatch until activation is verified. Retry with the same operation reference; a changed request is refused.

# Real 57dbfb3 crash-point harness

Before implementation acceptance, an isolated child importing only the real 57dbfb3 extract must exercise copied production boundaries:

1. prepared manifest contains the full normalized payload and survives restart;
2. recovery creates the exact planning P1 root and all children from only that payload;
3. old read/write at prepared/board/marker stages preserves private store bytes;
4. at board_created and marker_written, real 57dbfb3 attempts both public wait-clear paths, executing approval, reserve and reserve_next; capture whether the mutable gate is bypassed;
5. real 57dbfb3 removes the entire root, recreates/collides and retries reserve; capture exact board/manifest/marker/lease effects;
6. separately measure the reserve-before-board hypothesis: host-only preminted absent-board reserve feasibility, old startup/orphan/release behavior, and duplicate fence once board appears;
7. inventory lock ordering across board, ledger, coordination and P0 sidecar and reject the hypothesis if ledger-through-board CAS can deadlock any existing path;
8. exact active marker+PriorityClear relation rolls forward only after the chosen B3 contract is proven; each crash point must converge without duplicate execution;
9. occupied preminted identity conflicts without creating another root.

# Exact tests

1. normalized payload bounds: node count/depth/field lengths/exact 1 MiB payload + 1.25 MiB manifest/global envelope/unknown keys;
2. manifest persists full spec before board write; payload and operation digests validate;
3. prepared-process restart recreates exact nested tree without memory/REST receipt;
4. model omission boundary is documented/tested within-call only; explicit id resumes across restart;
5. REST header/body, ctl mint/retry and scope validation;
6. `plan_set_create` cannot carry root_id and never replaces;
7. compatible `n<next_id>` premint and child next_id advancement;
8. unrelated board progress before insert is preserved;
9. concurrent identity collision fails closed, no replacement id/root;
10. crash points prepared/board/marker/done converge to one root;
11. same operation exact replay succeeds; changed payload/scope/kind conflicts;
12. exact PriorityClear/clears relation required; stale/mismatched clear refuses;
13. operation_ref grammar/hash/fixed 16-char length, exact 35-char gate, 120-char boundary and refusal of any truncation/mismatch; full private identity never recovered from ref;
14. old 57dbfb3 attempts wait clear through work_node_update and side release, executing approval, reserve/reserve_next, and root remove/recreate at every pre-done point; record the exact result without claiming an unproven fence;
14. new scheduler gates incomplete/conflict root across P0/boost/ordinary;
15. done requires verified effective P0 and enables only existing P0 launch intent;
16. non-P0 create and existing-root replacement behavior remain compatible;
17. public status/logs contain no persisted payload/full ids;
18. all four surfaces resume the same explicit operation after every crash point.

# Ownership

- continue-p0: unified surface contract, MCP wrapper/schema and privacy/help after GO, real crash harness.
- continue-work-backend: full private manifest validator/store, exact board insertion, PriorityClear relation/recovery and application command.
- continue-work-pump: root-scoped activation gate across all dispatch lanes.
- continue-ctl: bounded independent surface/privacy/retry review when assigned.

# Review status

The beta contract is narrowed to unconditional pre-write refusal because no enforceable mixed-version capability boundary exists. Reserve-before-board evidence is NO-GO and no further bypass/lock design is in scope for this release. The deferred saga remains documentation only and has no implementation GO. ctl/backend review should judge the refusal surfaces and scheduler fail-closed behavior, not approve the deferred saga.
