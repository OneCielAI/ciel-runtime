---
type: design-proposal
title: P0 encoding and lifecycle design
description: Old-reader-safe P0 sidecar and lifecycle design, with native-fork model selection explicitly unsupported for this beta.
tags: [work-parallel, p0, compatibility, crash-safety, 57dbfb3, router, model]
timestamp: 2026-11-14T04:12:00Z
---

# Status

Design under review; no product file was edited. P0 lifecycle sections are normative where accepted. Model-remap implementation is NO-GO/disabled as stated in section 9; its historical measurements are non-normative. Measured inputs:

- real 57dbfb3 round trip: [old-reader-p0-roundtrip-result.json](evidence/old-reader-p0-roundtrip-result.json) (probe [old_reader_p0_roundtrip_probe.py](evidence/old_reader_p0_roundtrip_probe.py));
- P-R1 live probe: [pr1-result.json](evidence/pr1-result.json) (probe [pr1_probe.py](evidence/pr1_probe.py));
- semantic ruling: [requirement](requirement.md), section "Lead ruling on P-R1 and the old-writer blocker (03:58Z)".

Superseded: the 03:59Z draft of this file, which required every old priority change to end P0. That requirement was withdrawn by the 03:58Z ruling. A 57dbfb3 write never clears P0.

# 1. Files and what each holds

| File | Writer | P0 content |
|---|---|---|
| board `work-plan.json` (per session) | old and new | none. A P0 root is canonical `priority: "P1"`. No new field. |
| `work-parallel.json` (ledger) | old and new | none. A P0 lease is `priority: "P1"`. Existing fields only. |
| `work-parallel-coordination.json` | old and new | none. Run rows `priority: "P1"`. Existing schema. |
| receipts, finalization, stranded-v1, side inbox | old and new | none. P1-P5 only. No `priority/P0` inbox index. |
| `work-parallel-p0.json` (new, beside the ledger) | new only | root markers, launch intents, priority-clear operations, reacquire send children |

57dbfb3 never reads `work-parallel-p0.json`. The probe measured it byte-identical across every old write (board progress, priority cycle, delete/recreate, coordination reconcile, approval re-check, worker report refusal, `reserve_next`, explicit `reserve`, ledger pause on/off, ordinary P1 reserve/release).

# 2. Sidecar schema `work-parallel-p0.json`

Strict CAS, same pattern as the ledger (`exclusive_file_lock`, version, temp fsync, `os.replace`, POSIX parent-directory fsync; post-replace directory-fsync failure is ambiguous committed). Exact key sets; unknown keys are rejected. A missing sidecar is clean/empty. An existing unreadable, corrupt, oversized, unknown-schema or invalid sidecar is `p0_store_error`: all **new-code** admission, scheduling, P0/wait/ordinary selection and P0-related mutations fail closed globally until compatible recovery. It is never treated as "no P0" and never auto-replaced. Legacy files are not mutated by this guard; 57db processes are outside this new-code fence.

```text
schema = 1
version = int
roots   : { "<scope>\0<root_id>": RootMarker }  # MAX_ROOTS=256
intents : { "<intent_id>": LaunchIntent }        # MAX_INTENTS=256
clears  : { "<clear_id>": PriorityClear }        # MAX_CLEARS=64
sends   : { "<send_id>": ReacquireSend }         # MAX_SENDS=256
MAX_BYTES = 8 MiB
MAX_ID_CHARS=200; MAX_PATH_CHARS=1000; all digests lowerhex64; all times finite nonnegative <=1e15

RootMarker = {
  scope, root_id, root_created_at,            # exact identity
  state: "active" | "cleared" | "ended",
  set_at, set_by: "user" | "side_decision" | "operator",
  current_operation_id: lowerhex64,           # PriorityClear.clear_id for the operation that set this state
  ended_at: null | number,
  ended_reason: null | "priority_changed" | "root_terminal" | "root_missing"
}

LaunchIntent = {
  intent_id, scope, root_id, root_created_at, item_id,
  continuation: "initial" | "rework" | "stranded_recovery",
  predecessor_lease_id: null | text,          # rework / stranded
  dispatch_name,                              # "work-p0-<intent_id>", deterministic
  state: "intent" | "reserving" | "reserved" | "workspace_preparing" |
         "workspace_attached" | "spawning" | "bound" | "recovery_required" |
         "cancelled" | "terminal",
  generation: positive int,
  reserve_operation_id: null | lowerhex32,
  reserve_operation_digest: null | lowerhex64,
  reserve_sidecar_version: null | int,
  ledger_before_version: null | int,
  reservation_manifest_digest: null | lowerhex64,
  lease_id: null | text,
  workspace_operation_id: null | lowerhex32,
  workspace_state: null | "preparing" | "ready" | "lease_attached" | "coord_attached" |
                   "recovery_required" | "rolled_back",
  workspace_manifest_digest: null | lowerhex64,
  workspace_receipt_digest: null | lowerhex64,
  dispatch_without_approval: true,
  created_at, updated_at,
  terminal_at: null | number            # set exactly on cancelled/terminal
}

PriorityClear = {
  clear_id: lowerhex64,                 # SHA256(frame("ciel.p0.priority-op-id.v1", canonical{scope,external_operation_id}))
  scope, external_operation_id: text<=200,
  request_digest: lowerhex64,           # exact priority-caller-contract request digest
  root_id, root_created_at,
  target_priority: "" | "P1".."P5",
  set_by: "user" | "side_decision" | "operator",
  caller_kind: "mcp" | "rest" | "web" | "ctl" | "side",
  combined_manifest_digest: lowerhex64,
  state: "prepared" | "board_committed" | "done",
  board_version_before,
  board_version_after: null | int,
  allowed_reserving: null | {
    intent_id, generation, reserve_operation_id, reserve_operation_digest,
    reserve_sidecar_version, ledger_before_version, reservation_manifest_digest
  },
  created_at, updated_at,
  done_at: null | number                # set exactly on done
}

ReacquireSend = {
  send_id, parent_intent_id, scope, root_id, item_id, lease_id, host_id, native_name,
  state: "prepared" | "slot_acquired" | "delivered" | "rolled_back" | "uncertain",
  send_receipt: lowerhex64,
  pre_lease_digest: lowerhex64,
  pre_state: {state:"waiting_input", slot:false, usage:0,
              idle_proof_digest:lowerhex64, evidence_digest:lowerhex64},
  slot_before_version, slot_after_version,
  rollback_receipt_digest: null | lowerhex64,
  outcome: null | "delivered" | "delivery_failed_rolled_back",
  operation_digest: lowerhex64,
  created_at, updated_at, terminal_at: null | number
}
```

Marker identity is `(scope, root_id, root_created_at)`. Delete/recreate yields a new id and creation time (measured: n1/1000.0 vs n3/1004.0), so a marker can never elevate a recreated root.

Exact validator rules: top-level keys are exactly `schema,version,roots,intents,clears,sends`; reserve fields are all null outside `reserving|reserved|workspace_*|spawning|bound|recovery_required|terminal` and post-reserve `cancelled`; terminal post-reserve rows retain the immutable reserve pin for audit/planning-write projection after restart. `cancelled` has two exact variants: pre-reserve cancelled has null reserve/lease/workspace fields; post-reserve cancelled (coordination failure or authenticated workspace rollback) retains the immutable reserve pin, exact lease id, and any workspace operation/manifest/receipt plus `workspace_state=rolled_back` facts. A post-reserve cancelled row never has missing lease identity and a pre-reserve cancelled row never has lease/workspace facts; `reserving` requires every reserve pin field and no lease id; post-reserve states require the same immutable pin plus exact lease id. PriorityClear validates `clear_id == SHA256(frame("ciel.p0.priority-op-id.v1", canonical{scope,external_operation_id}))` and stores the exact request digest/fields defined by priority-caller-contract. Every RootMarker `current_operation_id` must name an existing operation for the same exact root unless that marker/operation pair is being archived together. `PriorityClear.allowed_reserving` is null when no exact reserving intent exists; otherwise it is the one validated nonterminal generation for that root and must match every immutable pin field. Bounds remain 256 intents/64 clears; at most one nonterminal intent per scope+item+continuation and one allowed reserving generation per clear. Terminal timestamp validators: LaunchIntent `terminal_at` is null except in `cancelled|terminal`; PriorityClear `board_version_after` is null only in `prepared` and required in `board_committed|done`; `done_at` is null in `prepared|board_committed` and required only in `done`; ReacquireSend `terminal_at` is null except `delivered|rolled_back`. Retention/pruning is deterministic on admission and startup using these persisted terminal timestamps and host `now`: open intents (`intent|reserving|reserved|workspace_*|spawning|bound|recovery_required`), open sends (`prepared|slot_acquired|uncertain`), and non-done clears are never pruned. Terminal/cancelled intents and terminal sends (`delivered|rolled_back`) retain 7 days and at least newest 32 of each collection, then oldest `(terminal_at, id)` first. Done clears retain by `(done_at, clear_id)` for 7 days/newest 32, except the marker's `current_operation_id` remains protected while that marker remains active/cleared or has open authority. Once a root is ended/missing and has no active lease/run/review/recovery and every related intent/send is terminal, its marker and `current_operation_id` clear may be archived/pruned together after retention. Lookup is by `(scope, external_operation_id)` -> `clear_id`. Same scoped id + identical request digest replays exactly; same id + different digest conflicts. After pruning, the same id returns bounded `operation_history_expired` and is not treated as new. Different ids with the same target are distinct operations. If all 256 intents/sends or 64 clears are protected/open/retained, admission visibly refuses `p0_sidecar_bound_reached`; it never evicts authority. Restart runs the same prune algorithm before admission. Parent terminal compatibility: open send child prevents/prioritizes parent close as recovery_required; terminal delivered/rolled_back child remains valid audit even after parent/lease terminal and does not require parent bound. No prompt, token, workspace path content or proof text is added—idle/evidence proof remain legacy ledger data and only their digests are stored.

# 3. Effective priority (new readers and all four surfaces)

```text
effective_priority(root) =
  "P0"  if a RootMarker exists with matching scope, root_id, root_created_at
           and state == "active" and root.status != "done"
  else  canonical root.priority (P1..P5 or unranked)
```

The canonical board value is ignored while the marker is active. That is the ruling: a 57dbfb3 write, including P1->P2->P1, does not clear P0. The probe measured that such a cycle leaves the marker byte-identical.

Mixed-version status, shown on menu, web, REST and ctl next to any active P0 root:

> "P0 (effective). Older Ciel builds show this goal as P<canonical>. Changing its priority in an older build does not end P0; use this build's priority control."

When the canonical value differs from P1 (an old writer changed it), the status adds "canonical value changed by an older build to P<n>; P0 still applies". Nothing is auto-corrected on disk.

# 4. Ending P0 (exactly three ways)

1. **New-code explicit priority change** (MCP `work_node_update`/`work_plan_set` with priority, REST, web, ctl, side decision `prioritize`/`update`): uses the roll-forward in section 5.
2. **Root terminal**: root `status == "done"` ends effective P0 at read time. A sweep sets `state=ended, ended_reason=root_terminal` with CAS; the sweep is cosmetic, the read rule is authoritative.
3. **Root missing or identity mismatch**: no matching root id + created_at -> no effect. Sweep sets `ended_reason=root_missing`.

Ending P0 never revokes, cancels or re-labels a lease already launched under P0; that lease keeps its intent and runs to its own terminal state. Later continuations (rework, stranded recovery, reacquire) use the root's effective priority at that time.

# 5. Authenticated new-code priority-control operation: durable roll-forward across two stores

Only this authenticated service operation ends/changes effective P0. Legacy 57db or direct board priority writes change canonical P1-P5 only, never marker authority, and produce the visible canonical-drift warning in section 3.

Board and sidecar cannot be written atomically together. Order:

1. Sidecar CAS constructor: read the exact active marker and all open intents for that root. If exactly one is `reserving`, copy its immutable reserve pin into `allowed_reserving`; if none, store null; if more than one or any malformed reserving pin, refuse `p0_clear_reservation_conflict` and write nothing. In the same CAS derive `clear_id` from `(scope, external_operation_id)`, persist the complete request identity (`external_operation_id, request_digest, root_id, root_created_at, target_priority, set_by, caller_kind, combined_manifest_digest`), add `PriorityClear{state: prepared, board_version_before, allowed_reserving, board_version_after:null, done_at:null}`, and set RootMarker `state: "cleared", current_operation_id: clear_id`. If that `clear_id` exists, identical request digest is exact replay and different digest conflicts; if expired, return `operation_history_expired`. Only open rows whose exact state is `intent` are cancelled with `terminal_at` in this same CAS. The one `reserving` row is copied to `allowed_reserving`; `reserved|workspace_preparing|workspace_attached|spawning|bound|recovery_required` generations already crossed reserve linearization and survive marker clear under the non-revocation rule. From this point no new P0 admission can start.
2. Board CAS (`expected_version = board_version_before`, the caller's normal CAS): write `priority = target_priority`.
3. Sidecar CAS: exact prepared clear + observed board commit -> `board_committed`, set `board_version_after`.
4. Sidecar CAS: exact board-committed clear -> `done`, set `done_at`; later retention may prune.

Crash and conflict table:

| Stop point | Durable state | Recovery (any new process, on next status/pump read) |
|---|---|---|
| before 1 | nothing | nothing; caller retries |
| after 1, before 2 | marker cleared, board unchanged | roll forward: re-read board; if root identity matches and `board.version == board_version_before`, do step 2; if the board moved, re-apply only the priority field with fresh CAS (the user's explicit choice); then step 3 |
| board CAS conflict at 2 | marker cleared | caller gets the normal "plan changed; retry" error; the prepared clear rolls forward as above, so the user's change is not lost and P0 is not silently restored |
| after board CAS, clear still prepared | board target written, clear prepared | verify exact board/root/target; CAS prepared→board_committed and set board_version_after; then separate CAS board_committed→done and set done_at |
| target_priority == "P1" | board unchanged | step 2 is a no-op; P0 still ends (explicit change to P1 from P0) |

## 5a. Setting P0 (exact-schema correction)

Setting P0 always makes the canonical board value P1. The exact serialized `clears` / `PriorityClear` record is reused in both directions with the same immutable caller operation identity; no `kind` or `priority_ops` field exists. Existing-root activation writes RootMarker `state: active, current_operation_id: clear_id` and a prepared operation targeting P1 in the same CAS.

Operation identity tests: replay after lost sidecar/board/finalize response returns the same clear and marker link; same scoped id with changed scope/root/root_created_at/target/set_by/caller_kind/manifest conflicts; two different ids with the same target create distinct ordered operations; concurrent same-target operations obey one marker/current-op CAS; restart validates marker→op relation; retained replay versus pruned `operation_history_expired`; pruning never removes a current operation needed by active/cleared marker or open authority.

**Existing root activation.** One sidecar CAS writes the active RootMarker and one prepared PriorityClear targeting P1. Recovery infers activation only from the paired marker's `active` state, never from board priority. The board CAS writes P1, then the operation moves `board_committed` -> `done`, becomes retention-eligible, and is pruned only by the section 2 retention algorithm. A crash before the board write leaves effective P0 plus the canonical-changed status. After done/prune, a later 57dbfb3 P2-P5 edit remains canonical-only and is never recanonicalized.

**Ending P0 / setting priority.** One sidecar CAS writes the cleared marker and one prepared PriorityClear targeting the requested `""|P1..P5`. Recovery infers this direction only from marker state `cleared`. The board CAS writes the target, then the operation moves `board_committed` -> `done`, becomes retention-eligible, and is pruned only by the section 2 retention algorithm. P0 stays ended through every crash point.

Invariants from the authoritative correction: marker+op one CAS; at most one nonterminal op per exact root; another operation is exact replay or refused; exact root identity before every board write; corrupt/missing marker-op pair fails closed and mutates no board.

**New root (beta refusal).** This beta does not run a new-root P0 saga. `work_plan_set(priority=P0, root_id absent)` and root-form `work_node_add(priority=P0, parent_id absent)` return the exact pre-write refusal in [new-root P0 support boundary](new-root-p0-operation.md). `work_plan_set(priority=P0, root_id present)` continues through the existing-root activation above. Any deferred preminted-root saga exists only in that document's non-active appendix.

# 6. Launch: durable intent before reserve

One **new-launch** protocol for initial reserve, rework replacement and stranded recovery. Reacquire is explicitly excluded: it keeps the same lease, host and native name and performs no new spawn.

1. **Authorize (read)**: board read + sidecar read. Require: RootMarker active and identity-matched; item belongs to that root; item not done/blocked; no active worker/resume lease of the same scope+item (the existing execution-family fence); for rework/stranded the predecessor lease matches scope+item.
2. **Intent (sidecar CAS)**: create `LaunchIntent{state: intent, lease_id: null, dispatch_name: "work-p0-<intent_id>"}`. A second concurrent authorizer finds the existing open intent for the same scope+item+continuation and reuses it (no duplicate).
3. **Linearize admission (sidecar CAS)**: before entering the ledger, transition the exact intent `intent -> reserving` in the same sidecar transaction that revalidates the active RootMarker. Persist `reserve_operation_id`, `ledger_before_version`, immutable scope/root/item/continuation/dispatch pin and generation. Priority clear uses the same sidecar store: if clear wins first it clears marker and cancels `intent`; no reserve may start. If `reserving` wins first, clear records the exact in-flight generation and may clear effective P0 for later work, but that generation is allowed to finish because ending P0 never revokes launched/in-flight work.
4. **Reserve (ledger CAS)** with new keyword arguments on the existing store methods:
   - `WorkParallelStore.reserve(..., admission_bypass="p0", p0_intent=<immutable reserving pin from step 3>)`
   - `reserve_rework(...)` and `reserve_stranded_recovery(...)` take the same two arguments.

   4a. Inside the ledger lock the caller passes the immutable `reserving` pin, not a lock-free `exact_intent` snapshot. The store re-checks `state == reserving`, `lease_id is None`, exact operation/generation/ledger-before, scope/item/continuation and deterministic name. Sidecar state cannot be silently reread as authority under the ledger lock. `admission_bypass="p0"` without the exact reserving pin raises "P0 bypass requires an exact reserving intent".

   4b. Bypassed: `enabled`, `paused`, per-priority capacity (P1 count), `launches_per_minute`; the launch is not appended to `launches`. Kept: `MAX_LEASES=100` (refusal text "lease bound reached (100); P0 waits for a slot"), candidate field validation, the active worker/resume duplicate fence, owner/identity, `report_hash`, every state-machine precondition.

   4c. The lease is written with `priority="P1"`, `mode="fork"` (legal for P1), and `dispatch_name = intent.dispatch_name`. `dispatch_name` is an existing optional lease field (57dbfb3 `validate`, `text(.., 200)`), so the intent identity is durable in the legacy file without any new field.
5. **Adopt reservation (sidecar CAS)**: exact `reserving -> reserved, lease_id:<exact>` for the same operation/generation. Lost response replays by deterministic name/scope/item and never releases an unknown-outcome lease merely because adoption returned an error.
6. **Coordination**: `record_assignment` with priority P1. For a planning root the P0 path writes the run with `approval_generation = 1` while the root row is phase planning / generation 0 (section 8). Existing release-on-failure rule: if this fails, release the exact unbound lease and cancel the intent.
7. **Workspace prepare/attach**: exact top-level CAS `reserved -> workspace_preparing` mints `workspace_operation_id`, sets `workspace_state=preparing` and persists a domain-framed manifest digest covering operation, lease, repo identity, base head, target path/branch/gitdir and generation. The authoritative workspace manifest is the existing per-lease workspace manifest; sidecar stores its digest/state and coordination stores the exact binding receipt digest. Because current `prepare()` retains `preparing` and rejects simple replay, add an explicit recovery operation:
   - verify target path/branch/gitdir absent and repo/base relation unchanged -> safely resume creation with the same manifest;
   - verify exact owned target+branch+gitdir present and matching manifest -> finalize `ready` idempotently;
   - ambiguous/unowned/mismatched target -> `recovery_required`, never delete/adopt.
   Because lease and run are already committed before this step, rollback here concerns only workspace effects: a known failure proven before any workspace manifest, filesystem worktree effect, lease-workspace attachment or coordination-workspace attachment may trigger the separately authenticated exact unbound lease/run/intent rollback. Store-then-throw, response loss or unknown attach commit retains reservation+intent. Exact transitions remain top-level `workspace_preparing` while `workspace_state` advances `preparing -> ready -> lease_attached`. The exact coordination attach receipt CAS changes top-level `workspace_preparing -> workspace_attached` and `workspace_state -> coord_attached`. Only `workspace_attached` may advance to `spawning`. Ambiguous/unowned recovery changes top-level to `recovery_required` and `workspace_state=recovery_required`; exact known non-commit rollback changes top-level to `cancelled`, `workspace_state=rolled_back`, sets `terminal_at`. Replay compares the full workspace operation/manifest/receipt and returns the current committed state; changed facts conflict.
8. **Spawn (mod)**: sidecar CAS exact `workspace_attached -> spawning`, then `$.agent.spawn({subagentType:'fork', name: dispatch_name, ...})`.
9. **Native bind**: existing bind path; sidecar CAS exact `spawning -> bound`.
10. **Terminal**: after existing terminal proof, one sidecar CAS changes the exact intent to `state: terminal` and sets finite `terminal_at` in the same write; cancellation likewise sets `state: cancelled, terminal_at`. Retention never infers terminal time from updated_at.

## Crash table

| Stop point | Durable state | Recovery |
|---|---|---|
| before 2 | nothing | nothing |
| after intent, before reserving | intent, no lease | active marker allows exact reserving CAS; priority clear first cancels it |
| reserving, before ledger write | immutable reserving pin, no lease | retry exact ledger version/generation; priority clear may proceed but records this allowed in-flight generation |
| ledger commit/response unknown | reserving + possible reserved lease | retain recovery_required; reread ledger by deterministic name/scope/item, never immediate release |
| reserved lease, before sidecar adopt | reserving + exact reserved P1 lease | exact adoption to reserved; zero match retries only with proven non-commit; multiple conflicts |
| reserved pair, no run | replay `record_assignment`; identical manifest is success |
| assignment, before workspace | exact lease/run/intent, no workspace | prepare/attach |
| known workspace failure after lease/run, with workspace manifest absent, no filesystem worktree effect, no lease-workspace attach and no coordination-workspace attach | authenticated positive absence for all four workspace layers | separately authenticated rollback of exact unbound lease/run/intent; any unknown layer retains recovery_required |
| manifest `preparing`, before git effect | exact preparing manifest, no target | recovery verifies absence then resumes same manifest |
| git effect, before ready save | target/branch/gitdir may exist, manifest preparing | exact owned match finalizes ready; absent resumes; ambiguous/unowned recovery_required |
| ready save, before lease attach | exact ready manifest, lease unbound workspace | attach exact manifest digest to lease |
| lease attach, before coordination attach | lease workspace attached, coordination missing | replay exact coordination binding receipt |
| coordination attach response loss | all stores may be committed | reread exact manifest/lease/run; identical is success, mismatch recovery_required |
| attached workspace, before spawn | exact reserved pair + run/workspace | spawn once |
| spawning, before native bind | spawn outcome unknown | exact-name native reconcile: present bind; positive complete absence + dead owner exact rollback; ambiguous retain |
| bound | exact bound pair | ordinary lifecycle |

Unknown reservations retained by today's startup rule stay untouched: adoption requires the sidecar intent, the deterministic `work-p0-` name and identical scope/item. A reservation without a matching open intent is never adopted, released or treated as P0. The backend's measured fact (dead-owner `orphan_release` refuses an unbound lease) is therefore not on the P0 path.

## 6a. Reacquire: same-lease send intent, no spawn

Reacquire is option A and has a separate durable child lifecycle under the original authoritative launch intent. It never creates a new `LaunchIntent`, lease, dispatch name, assignment or native agent.

The exact `ReacquireSend` schema is defined in section 2. Its pre-state digest binds the legacy lease's `waiting_input`, slot=false, usage=0 and the existing `idle_proof`/`evidence` relation. Rollback cannot manufacture a new waiting proof: the rollback receipt must prove the restored row matches that pre-state digest. States are `prepared|slot_acquired|delivered|rolled_back|uncertain`; outcomes are null until terminal, then `delivered|delivery_failed_rolled_back`. At most one **open** child (`prepared|slot_acquired|uncertain`) exists for the exact parent intent+lease. Terminal children do not block a later child with a new send id/receipt.

1. Validate the original bound launch intent, exact same lease/scope/item/host/native name and `waiting_input slot=false`, **and current exact RootMarker active/effective P0 for the same root identity**; create/replay `prepared` child with backend-preminted send receipt. If priority clear wins first, no P0 child is created and the same-lease continuation follows ordinary non-P0 semantics. If child creation wins first, later marker clear does not revoke that already-authorized child/generation, matching the non-revocation rule.
2. Exact ledger reacquire CAS flips the same lease slot false->true and advances the same child to `slot_acquired`; no spawn or name change occurs. Lost CAS response replays the exact child+slot transition and returns current state.
3. Authorize the existing native resume and call `next(e)`/SendMessage exactly once for this child.
4. Classify the actual host result. Definite success if `sent===true || success===true || result?.success===true || isDelivered===true`. Definite failure if `sent===false || deny===true || success===false || result?.success===false || isDelivered===false`. Throw, null, undefined or no recognized boolean is uncertain. Definite success with exact receipt records terminal `delivered`, outcome `delivered`.
5. Definite failure attempts exact backend slot rollback. If rollback commits, record terminal `rolled_back`, outcome `delivery_failed_rolled_back`, slot=false and durable rollback receipt/operation digest. A later reacquire may create a **new** child/receipt. If the rollback call response is lost, caller knowledge is uncertain and it replays the same completion without another SendMessage/rollback; backend returns terminal `rolled_back` if the exact rollback committed. It must never overwrite a committed rollback with uncertain. Only when replay cannot prove commit or non-commit does the same child remain `uncertain`, open and recovery_required, blocking another child.
6. Throw/null/undefined/unrecognized send outcome records the same child `uncertain`, open and recovery_required; no automatic resend.
7. Lost completion response replays exact child+send receipt+outcome and returns the committed terminal/uncertain state without another SendMessage or rollback. Changed receipt/outcome/rollback proof conflicts.
8. Exact ledger before+1 may prove only `slot_acquired`, never delivery. Later ledger versions remain uncertain unless an exact rollback/completion receipt resolves them.
9. Parent launch intent remains authority. Terminal delivered/rolled-back children retain audit identity for 7 days and at least newest 32 before deterministic pruning. Open children are never pruned.

Required tests: priority-clear-before-child routes ordinary; child-CAS-before-clear retains exact authorized generation; no second child across race; exact pre-lease digest including waiting_input/slot=false/usage=0/idle_proof/evidence; rollback restores that exact proof relation and never manufactures text; same lease/host/name unchanged; zero spawn calls; one-open-child rule; slot-CAS response loss; each classifier shape `sent:true/false`, `success:true/false`, nested `result.success:true/false`, `isDelivered:true/false`, `deny:true`, throw, null, undefined and unrecognized object; false+rollback success; rollback failure/lost response exact replay; lost completion response exact replay; changed outcome conflict; terminal pruning/open retention; parent terminal after retained delivered/rolled_back audit remains valid; parent close with open uncertain child is recovery_required; fill/restart bounds protect open rows and visibly refuse when all protected; exact+1 versus later ledger recovery; no duplicate send and no second execution-family lease.

Required crash/race tests (new code, temp stores): clear versus each intent state—`intent` cancels; `reserving` is exact allowed_reserving; `reserved|workspace_preparing|workspace_attached|spawning|bound|recovery_required|terminal` survive; post-reserve terminal retains reserve pin across restart; cancelled pre-reserve has null reserve/lease/workspace facts; post-reserve cancelled from coordination failure retains reserve pin+lease; workspace rollback cancelled retains reserve pin+lease+workspace rollback facts; validator rejects swapped/missing variants after restart and exact replay returns the same cancelled variant; reserving wins -> exact generation may finish and later work sees cleared marker; mutate every reserving/allowed-clear pin field -> refuse; crash before/after ledger commit and adopt response loss retain/reconcile one lease; known workspace pre-manifest/noncommit failure exact rollback; stop after preparing manifest, after git worktree effect before ready save, after ready save, after lease attach and after coordination attach; absence resume, exact owned finalize and ambiguous/unowned recovery_required; store-then-throw/lost response replay converges; after each row exactly one of {no lease, one exact reserved lease, one exact bound lease}; never two leases/workspaces for item; pre-existing unknown reservation stays byte-identical; 57dbfb3 reads legacy files after each row.

# 7. Admission counting in new code

New admission reads the sidecar and collects `lease_id`s of intents in `reserved|workspace_preparing|workspace_attached|spawning|bound|recovery_required` when non-null, and excludes those leases from the P1 capacity count. They still count toward `MAX_LEASES`. A 57dbfb3 process cannot see the sidecar and counts a P0 lease as P1; accepted as conservative.

# 8. Approval: planning root, measured in 57dbfb3

New behaviour (Q1 ruling): an item under a P0 root dispatches while the root is planning and even with `work_plan_approval_ask=ask`. The assignment projection carries `P0 dispatch without approval`.

Exact phase/generation-only bypass matrix, only for the exact identity-matched launch generation (`reserved|workspace_preparing|workspace_attached|spawning|bound|terminal`) whose lease, scope, root id, root_created_at, item, dispatch and reserve pin match:

| Operation using shared approval check | Bypass phase/approval generation? |
|---|---|
| new `record_assignment` | yes |
| worker report events: question, result/completion, handoff, resume, verify/review, accept, cleanup, done, rework | yes |
| review/stranded revalidation and stranded completion | yes |
| legacy accept path for that same P0 lease | yes |
| non-P0 or unmatched/forged/recreated root/item/lease | no |
| missing/unreadable sidecar or `p0_store_error` | no; fail closed |

Only phase and approval-generation comparison is skipped. Scope fingerprint, artifact, worker token, owner, duplicate and every state-machine precondition remain enforced. Tests cover every row with P0/non-P0/changed identity/corrupt sidecar.

## 8a. Four-surface P0 recovery status

Initial/menu where relevant, web, REST and `ciel-runtimectl` project one bounded host status object; model views receive only the bounded refs/states below.

```text
p0_status = {
  store: "ready" | "p0_store_error",
  store_reason: null | "unreadable" | "corrupt" | "oversized" | "unknown_schema" | "invalid",
  assignment_key: "not_required" | "ready" | "recovery_required",
  assignment_key_reason: null | "missing" | "unreadable" | "acl_invalid" | "invalid_bytes",
  create_store: "clean" | "root_gated" | "global_gated",
  create_gated_roots: [bounded root ids],
  canonical_drift: [{root_id, canonical_priority, effective_priority:"P0"}],
  launches: [{root_id, item_id, operation_ref, state}],
  scheduling_blocked: boolean,
  help: bounded text
}
```

Rules: `p0_store_error` globally blocks new-code admission/scheduling/P0 mutations. Assignment key status is read-only validation only when a nonterminal intent/send exists, never ensure/create/rotate; no path, SID, key bytes or hash. `recovery_required` blocks assignment issuance, capability derivation, spawn and reacquire continuations; already-running bound workers keep using their existing issued token, but restart/replay requiring derivation surfaces recovery_required. Create-store gate follows the accepted new-root contract. Lease/intent identities are bounded refs only. Tests cover key present/missing/ACL-invalid with empty versus active sidecar, no file creation, assignment/scheduler refusal, store corruption fail-closed and all four surfaces/help/privacy.

## 8b. Planning-write gate contract

The existing gate plugin `ciel-work-gate/hooks/register.js:219-229` denies Work fork file writes while the board is planning. P0 therefore needs this exact delta.

- **Ownership:** pump owns the gate delta; backend owns a host-only read-only lease projection.
- **Projection:** the boolean is derived at read time and is not persisted in LaunchIntent. `planning_write_authorized: true` and `authorization: "p0_dispatch_without_approval"` appear only on the bound host lease view when the exact lease's launch intent is `workspace_attached|spawning|bound` (or terminal only for an already-running exact lease awaiting report) and scope, root id, root_created_at, item, lease, dispatch name, reserve pin and workspace manifest match. No token, full ids or sidecar data are exposed.
- **Authority:** derived from the exact launched generation, not current marker. A later authenticated priority clear does not revoke an already-launched/in-flight exact generation. Cancelled/replaced intent, recreated root/item mismatch, forged model fields, missing/unreadable/corrupt sidecar or projection mismatch deny.
- **Gate skips only planning denial.** Bound-state, worktree projection, cwd confinement, absolute target confinement and shell checks remain unchanged. The gate never reads the sidecar and never trusts prompt/name.
- **Tests:** P0 planning write inside exact worktree allowed; non-P0 planning denied; outside worktree/target denied; missing/forged/mismatched projection denied; recreated root denied; unreadable state denied; after clear, already launched exact generation still allowed while later admission blocked; shell behavior unchanged; real ciel-work-gate load and observed hook evidence.

Measured in a real 57dbfb3 process (planning root generation 0, run generation 1, lease running, run bound):

| 57dbfb3 operation | Result |
|---|---|
| coordination `validate` / `read` | passes |
| `reconcile_roots` on that planning root (write) | succeeds; run unchanged |
| public `record_assignment` on the planning root | `stale root approval` (old API cannot create it; new code writes it) |
| `_approval_current` | false; lease and run unchanged |
| worker `result` report through the beta path | `Work approval scope changed; reconcile before continuing`; lease, run, receipts unchanged |
| pump `reserve_next`, planning root | `no eligible candidate`; ledger bytes unchanged |
| pump `reserve_next` and explicit `reserve`, root switched to executing | `no eligible candidate`; still one lease |
| final state | lease `running`, run `bound`; nothing revoked or cancelled |

Consequence to note for the user: if a 57dbfb3 process handles that P0 worker's report, it refuses it (measured). Which process serves a fork's `work_parallel_report` call is not measured here. I expect it to be the router of the session that spawned the fork (the new build), but that is unverified; the compatibility harness must measure it before this is called safe.

# 9. Router model mapping — NO-GO / disabled for this beta

The earlier remap proposal below is superseded and must not be implemented. Sole Work-fork capture produced no fork request; first-request host bridge evidence `work-fork-first-request-bridge-design.json`, SHA-256 prefix `82d6ff19106e1ff4`, proves the current native runtime exposes no authenticated correlation in time: the first fork request precedes durable bind by at least 0.428 s and carries agent/session/parent headers but no dispatch name, lease, generation or effective priority. The spawned agent id is returned only after spawn and is too late for the first request.

Therefore this beta contract is:

- per-priority Work-fork and side-fork model settings are unsupported/disabled on the current native fork runtime;
- no second-or-later-request fallback, because first-request coverage is the requirement;
- no Assignment JSON, prompt, worker-token or text parsing/heuristics;
- stored historical values remain inert and never silently affect runtime;
- menu, web, REST and `ciel-runtimectl` show a disabled status row/reason, not an active selector;
- required capability: an authenticated host dispatch/launch field in the first request, a preminted native agent id, or a guaranteed pre-request correlation callback;
- direct-path inherit warning remains true but secondary to the native capability NO-GO.

Exact visible reason:

> Per-priority and side-fork model selection is unavailable on this native fork runtime because its first request has no authenticated dispatch identity. Forks inherit the main model; prompt/token parsing and later-request remap are disabled.

## Exact disabled surface contract

Initial settings/menu, web, REST and `ciel-runtimectl` expose one read-only unsupported status with the exact reason above. They expose no model choices, selector, update action or writable model field. Any attempted update returns `unsupported_native_fork_identity` without mutating config.

Historical config values and unknown model ids are preserved byte-for-byte, never applied, normalized, deleted or rewritten. They may be reported only as `stored_inert:true` without exposing a selector. Fork runtime always inherits main. There is no router remap code, no agent/model pin map, no prompt/token parser, and no second-request fallback.

Direct-path and cache observations are informational historical evidence only and are not selector help or runtime requirements. All rejected remap/P-R1/target-limit/assignment-parsing material was removed from the active design to prevent implementation ambiguity; the evidence files remain in the journal bundle.

# Rejected alternative: old-process board subscriber

57dbfb3 ships `work_plan_store.subscribe_work_plan_changes(callback)` (beta `work_plan_store.py:48-85`). The callback gets detached `before` and `after` documents after each commit in the same process, so it could tell an explicit priority change from ordinary root progress. Rejected by the lead's ruling:

- a subscriber cannot be installed into an already-running 57dbfb3 process, so it cannot repair the mixed-version processes running now;
- making old processes load a compatibility hook or module is an old-build or deployment change, which is outside the ruled scope;
- the user asked for P0 immediacy, not for cross-version recognition of old priority edits.

No subscriber, hook, module integration, file interception or polling inference is part of this design.

# 10. Required compatibility harness (owned by continue-p0 after GO)

Separate real 57dbfb3 process (`python -I -B`, extract sums 840fa7d5), temp config, then new code re-reads:

1. New code: P1 root + active RootMarker, LaunchIntent, P1 lease (`dispatch_name=work-p0-<id>`), P1 run on a planning root.
2. 57dbfb3: leaf progress/evidence/status, root progress, phase, focus, queue reorder -> effective P0 holds; sidecar byte-identical.
3. 57dbfb3: P1->P2->P1 -> effective P0 still holds (ruling); mixed-version status shows the canonical value; sidecar byte-identical.
4. New code: explicit priority change -> P0 ends; crash at each roll-forward row converges; old priority change afterwards cannot restore it.
5. 57dbfb3: delete/recreate -> new root is not P0.
6. 57dbfb3: ledger pause toggle, ordinary P1 reserve/release, coordination reconcile, `_approval_current`, worker-report refusal, `reserve_next`, explicit `reserve` -> no quarantine, no blocked write, no revoke/cancel, no second lease.
7. Launch crash table rows, each followed by a 57dbfb3 read of every legacy file; unknown reservations byte-identical.
8. Every legacy priority value stays within P1-P5; the side inbox never contains P0.

# Citations

[1] [requirement and rulings](requirement.md)
[2] [57dbfb3 round-trip result](evidence/old-reader-p0-roundtrip-result.json)
[3] [P-R1 result](evidence/pr1-result.json)
[4] backend step 161-162, `docs/journal/2026/11/12/features/work-parallel-open-items/progress-worktree.md`
