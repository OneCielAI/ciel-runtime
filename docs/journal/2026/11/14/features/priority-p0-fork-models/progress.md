---
type: progress-log
title: P0 fork and per-priority model, continue-p0 progress
description: Step log of the continue-p0 lane for the P0 immediate-fork and per-priority model requirement; Phase 1 measurement (M-P1), inventory, P0 bypass points and settings design. Updated after each step.
tags: [work-parallel, priority, fork, model, measurement, progress]
timestamp: 2026-11-14T02:55:00Z
---

# How to resume

Contract: [requirement](requirement.md). New-root create amendment: [unified P0 create operation](new-root-p0-operation.md). Reserve-before-board evidence: [measurement](reserve-before-board-measurement.md). Evidence: `evidence/` (bundle excerpt helper, old-reader probe and its result). No product file was edited; scratch candidates remain separate. Next step: the lead's ruling.

# Step 25. Superseding key, status and backup package accepted (2026-11-14T09:07:41Z)

continue-ctl reviewed the superseding immutable package `.local/mod-gates/key-status-package-20261114T090525Z`, manifest SHA-256 `194a99868e6caddeb365a0591c65c076e882c809529e7b238ec87aa72ef1b7b5` with 1,155 rows. Verdict: **ACCEPT for the bounded key/status/backup package scope**.

Independent evidence: the root and a copied package both passed every manifest entry. The copied package, run with stripped runtime/provider environment and temporary isolated configuration, passed P0 events 27/27 with snapshot-origin proof and backup 56/56. The previous executable spawn-after-key-loss repro now reports recovery-required/missing status and `p0_launch_spawning` refuses with typed `spawn blocked (missing)` before any transition; the intent remains reserved.

Accepted bounded scope: dependency-fenced key creation with no rotation while intents or open sends depend on it; pre-write key readiness for assignment, spawn and reacquire; typed bounded reasons; byte-identical sidecar and ledger on refusal; Windows status DACL validation without mutation, with hardening limited to create/restore; exact three-ACE DACL and Everyone rejection; WinAPI cleanup, identity-swap, POSIX, link and race refusals; exact backup secret classification, encryption, no-overwrite restore, cleanup and privacy. The older `085930Z` root and its regenerated manifest `332456eb...` are superseded and do not contain the spawn gate. Four-surface UI status remains a later integration item, outside this package acceptance.

No product file was edited.

# Step 24. Refrozen design and disabled-surface plan accepted (2026-11-14T08:46:48Z)

continue-ctl independently reviewed `revised-encoding.md` at SHA-256 `667132e98dba01847feeb5a918316bd59be5365e43c4e2a29e4881180ea30a60`. Verdict: **ACCEPT design**. The exact validator now distinguishes pre-reserve cancelled rows with null pins from post-reserve cancelled rows retaining immutable reserve, lease and workspace rollback identity. Priority operations now bind scoped external operation identity to a framed request digest, derived clear id and marker current-operation relation, with exact replay/conflict/restart/retention/pruned-history rules. Previously accepted schema, bounds, privacy, corruption fail-closed, workspace, ReacquireSend, planning gate, four-surface recovery status, beta new-root refusal, old-drift and model NO-GO contracts remain intact.

continue-ctl also reviewed `model-disabled-implementation-plan.md` at SHA-256 `a4c95d5407fc533358c674d8904781b59e31a268056adcfb3b38dc3a02a99e8c`. Verdict: **ACCEPT plan**. `UnsupportedNativeForkIdentity(ValueError)` now definitively uses the existing MCP exact-reason-text plus `is_error:true` transport with `work_mcp_tools.py` unchanged. Exact HTTP 422, refusal-before-value/save/fingerprint/CAS precedence, all-provider and retired-key presence-only `stored_inert`, no selector/Apply/update UI, exact file scope, no router/provider/request-builder changes and real-surface evidence gates are consistent.

No product or design file was edited by the reviewer.

# Step 23. Integrated key and status package review (2026-11-14T08:39:41Z)

continue-ctl independently combined the announced exact candidates: `work_parallel_mcp.py` `daa71df9...`, event tests `bcce8008...`, hardened key helper `79a1401e...`, backup collect `504a6e34...`, restore ops `4923eeca...` and backup tests `93b9cdb5...`. Verdict: **CHANGE**.

- An isolated executable repro proved that deleting the assignment key with an active reserved intent makes status report `recovery_required/missing`, but a second P0 reserve calls `ensure_before_first_intent`, recreates a different key, flips status back to ready/spawn-allowed and makes the first assignment replay conflict as already pinned differently. Key creation must be fenced on zero nonterminal intents/sends; otherwise it is recovery-required and creates nothing.
- The status read invokes key `read()`, whose Windows path calls `_restrict_windows_acl`/`icacls`; therefore the claimed read-only status mutates the DACL. Status must inspect the exact DACL without hardening it; hardening belongs to create/restore.
- Full four-surface `p0_status` projection remains outside this partial backend package. Exact DACL enumeration, WinAPI cleanup, identity-swap and POSIX/link/race refusals, encrypted secret backup, different-key no-overwrite and restore cleanup passed bounded review.

No product file was edited.

# Step 22. Unified priority caller contract review (2026-11-14T08:44:06Z)

continue-ctl independently reviewed `priority-caller-contract.md` at SHA-256 `4430abe7d77b4e574a07bb77e37d63cc569e510dbd9e218f0d0066bbcc9a6f38`. Verdict: **CHANGE** despite the backend architecture ACCEPT.

Routing, scoped framed idempotency, outcome-unknown recovery, side option B, old-writer canonical drift and selective service-only P0 all pass. One cross-surface privacy/status contradiction remains: the MCP wrapper is said to return a full operation id to the model/coordinator, ctl machine output is allowed a full id, while the four-surface section requires the same bounded fields with only `priority_operation_ref` and says human surfaces expose bounded references. The contract must separate exact authenticated mutation receipts from bounded public/model status, name every response allowed to carry the full external id and test that public/model status and logs do not expose it.

No code or design file was edited.

# Step 21. Superseded frozen design and disabled-plan findings (2026-11-14T08:33:02Z)

The earlier frozen P0 design SHA `10f8c1d173b139bffd63b6410a492deeb5c5b5c87681e455f1e1f7c82428e9ec` was **CHANGE** because its validator allowed only pre-reserve cancelled rows with null pins while coordination and workspace rollback produced post-reserve cancelled rows requiring retained audit identity. The earlier disabled plan SHA `9bc4abb2a6116b7af9a885c578e477caf9825d117a75ddac3f237b4d6b2a6986` was **CHANGE** because one line definitively excluded `work_mcp_tools.py` while another still conditionally allowed including it. Both findings were corrected by the accepted hashes in Step 24.

No product file was edited.

# Step 20. Consolidated P0 design exact-schema review (2026-11-14T07:46:37Z)

continue-ctl independently reviewed consolidated `revised-encoding.md` at SHA-256 `30cced3b1c95c9eac6294b08cce53226f4a683be7109bf4f9ce8624ea654e273`. Verdict: **CHANGE**. The previously accepted model NO-GO surface and old-writer canonical-drift semantics remain PASS, but five active consistency blockers prevent overall design acceptance.

1. The exact top-level sidecar schema contains only `roots`, `intents` and `clears`, while section 6a requires durable `ReacquireSend` child rows and retention. No sends collection, exact row key/field grammar, row/collection/global bounds or validator relation is defined.
2. Bounds are illustrative (`e.g.`) rather than exact. The design lacks a global sidecar byte limit and bounded scalar grammars for ids/text such as native names and send receipts, so exact validation and privacy/recovery envelopes are not implementable from the contract.
3. `LaunchIntent` enumerates workspace and recovery states, but the workspace protocol advances only `workspace_state`; no authoritative LaunchIntent transition is specified. Admission counting excludes only `reserved|spawning|bound`, so using the enumerated workspace/recovery states would count the same P0 lease as ordinary P1 capacity. State transitions, validation, recovery and counting must share one model.
4. `PriorityClear.allowed_reserving` exists in the schema, but the priority-control constructor does not explicitly validate and record the one exact reserving pin (or null) in the same CAS that creates the prepared clear and clears the marker. The launch prose alone does not define the clear service's deterministic authority.
5. The active `New root` paragraph still describes creation of a P0 root. This contradicts the accepted beta refusal contract at `new-root-p0-operation.md` SHA-256 `baa7c6043c5ba98fc2d2de731938a20d3f4457c9794c07b2c77563f3a24df878`; it must be replaced by an active refusal cross-reference rather than an implementation saga.

Model NO-GO remains clean: one exact read-only reason, no selector/update/remap/parser/fallback and byte-preserved inert historical values. Old/direct canonical writes still do not clear active P0; only authenticated new-code priority control does. Workspace unknown-outcome recovery correctly retains and rereads exact facts, though its pre-commit wording should be narrowed to workspace/attachment commits because the lease and run already exist.

No code or design file was edited.

# Step 19. Corrected active new-root refusal clauses accepted (2026-11-14T07:39:26Z)

After backend ACCEPT, continue-ctl completed the bounded final re-review of `new-root-p0-operation.md` at SHA-256 `baa7c6043c5ba98fc2d2de731938a20d3f4457c9794c07b2c77563f3a24df878`. Verdict: **PASS** for the two previously blocking active clauses; this is not approval of the deferred saga or a backend implementation.

- Active branch detection now refuses only `work_plan_set(priority=P0)` with `root_id` absent and root-form `work_node_add(priority=P0)` with `parent_id` absent before every write/id mint. A present `root_id` reaches supported existing-root activation, and child add remains under existing rules. Focused requirements exercise both refused shapes, zero writes/ids and existing-root non-capture.
- Scheduler policy reads without creating the store. A missing store is clean. A known exact root gates that root across P0, wait-boost and ordinary lanes. Corrupt/unknown-schema/unparseable state or an active row without an extractable exact root id globally gates all three lanes with `p0_create_recovery_required`; automatic advance, repair, prune and deletion are forbidden. Focused requirements cover each classification.

No implementation or design file was edited.

# Step 18. Disabled model surface and PriorityClear contract review (2026-11-14T07:23:12Z)

continue-ctl independently reviewed `revised-encoding.md` at SHA-256 `baae0d58d11d4fcd351dd623534c9eadcbebcfa9bc299c585a085dfc946238f0`. Verdict: **PASS** for the model-setting surface and PriorityClear clarification; this is not a backend implementation approval.

- Section 9 exposes one exact disabled reason across initial settings/menu, web, REST and `ciel-runtimectl`. All are read-only unsupported status surfaces with no choices, selector, update action or writable model field; attempted updates return `unsupported_native_fork_identity` with no config mutation.
- Historical values and unknown model ids are preserved byte-for-byte and remain inert. They are never applied, normalized, deleted or rewritten. Router remap, agent/model pin maps, prompt/token parsers and second-request fallback are absent. Direct-path/cache observations are explicitly historical informational evidence only, so no later normative remap contract leaks through.
- PriorityClear is clarified as the sole authenticated new-code priority-control path: old/direct canonical drift does not clear the active marker. The exact `clears`/`PriorityClear` schema is reused in both directions with no `kind` or `priority_ops`; an active paired marker identifies activation and a cleared paired marker identifies ending. Marker and operation commit in one CAS; exact root identity, one nonterminal operation, replay-or-refuse and corrupt/missing-pair fail-closed invariants are explicit.

No code or design file was edited.

# Step 17. Latest narrowed new-root refusal contract review (2026-11-14T07:19:39Z)

continue-ctl independently re-reviewed `new-root-p0-operation.md` at SHA-256 `f45187fe177a48a06517dd9e4c746477de405ad72bd30940bd587a9edf9209df`. Verdict: **CHANGE**, with two active-contract ambiguities.

1. Active beta line 13 still says `work_plan_set` requesting `priority=P0` is refused while preserving existing-root activation, without defining the executable branch boundary. The active contract must state that refusal applies only to `work_plan_set(priority=P0)` with `root_id` absent and root-form `work_node_add(priority=P0)` with `parent_id` absent; `root_id`-present existing-root activation and child add remain on their existing paths. The precise distinction currently appears only in the explicitly inactive deferred saga and therefore cannot govern beta behavior.
2. Active scheduler policy excludes a row's root id only "when present". For an unknown/corrupt create store from which no root id can be extracted, the contract must explicitly block all scheduler selection globally across P0, wait-boost and ordinary lanes until compatible recovery/operator action. A test requirement saying fail-closed does not itself define this runtime behavior.

The remaining narrowed refusal requirements pass on the reviewed bytes: exact MCP/REST/web/ctl response, `writes:false`, refusal before all listed writes with no durable operation/root identity, unchanged non-P0 creation, preserved existing-root support intent, and an unmistakably inactive deferred saga.

No product or design file was edited.

# Step 16. Narrowed new-root refusal contract review (2026-11-14T07:00Z)

continue-ctl reviewed `new-root-p0-operation.md` at SHA-256 `c02df1a9f560ac1b63ac53af65c137396676f40db5d48d0a1929c1a5241f662f`. Verdict: **CHANGE**, one exact-detection ambiguity remains.

The narrowed beta contract otherwise passes: exact cross-surface code/message/`writes:false`, pre-write refusal with no durable operation/root id, non-P0 compatibility, existing incomplete/conflict scheduler gates across P0/boost/ordinary, corrupt-store fail-closed, and a clearly non-active deferred saga. However active line 13 says `work_plan_set` requesting P0 is refused while also saying existing-root P0 activation remains supported. It does not distinguish `root_id` absent from present. The deferred future section has the right distinction but explicitly cannot define active beta behavior. The beta section must say exactly that `work_plan_set(priority=P0, root_id absent)` and root-form `work_node_add(parent_id absent, priority=P0)` are refused, while `work_plan_set(root_id present, priority=P0)` reaches supported existing-root activation; tests must prove both branches and zero writes for refusal. It must also say that an unreadable/corrupt create store with no extractable root id globally blocks scheduler selection, not merely excludes a root id "when present".

No product file was edited.

# Step 15. Disabled model-setting and ReacquireSend contract review (2026-11-14T06:58Z)

continue-ctl reviewed `revised-encoding.md` at SHA-256 `516279dbc2f9db7a03515cb8d6403153e39da89a05dc74d0474855e344e7f553`. Verdict: **CHANGE** for the model-setting surface contract; ReacquireSend wording is consistent with the accepted separate-child/no-spawn ruling.

- Section 9 lines 205–223 correctly declares native model remap NO-GO, forbids prompt/token/later-request heuristics, makes historical values inert, and requires menu/web/REST/ctl disabled status with one exact reason. The menu is the initial settings surface.
- Lines 225–275 remain ambiguous/contradictory: later present-tense headings still specify direct-path `effective=inherit,status=direct_inherit` per-model rows, a direct warning, target-limit behavior and cache help for a "remapped fork". The one sentence at line 223 does not clearly bound all later subsections as historical/non-implementable. A final contract must explicitly supersede/remove that active-looking surface behavior.
- Inert-value handling also needs exact semantics: disabled surfaces are read-only status/reason, expose no selector/options/update action, preserve existing config bytes (including unknown historical ids) unchanged, never apply/delete them, and behave identically on initial menu, web, REST and ctl.
- ReacquireSend lines 162–178 define a separate child under the original bound intent, same lease/host/native name, zero spawn, exact send and rollback receipts, one open child, uncertain lost outcomes and no automatic resend; no wording conflict was found.

No product file was edited.

# Step 14. Revised new-root P0 design re-review (2026-11-14T06:25Z)

continue-ctl re-read `new-root-p0-operation.md` at SHA-256 `ef61662491e2079d9cc99fa0c00770c43e016bbdc592837e7cf37e03b51a3e54`. Verdict: **CHANGE**, one blocker remains.

- The separate strict-CAS 8 MiB / 128-row store, 1 MiB spec and 1.25 MiB manifest bounds, retention/prune/corruption rules fix the manifest-store/headroom blocker.
- Active RootMarker plus prepared P1 in the same CAS, with cleared marker forbidden from activation, fixes the marker blocker.
- The proposed old-readable activation wait is not protected from old writers. Real 57dbfb3 exposes `work_node_update waiting_on=""` at `ciel_runtime_support/work_mcp_tools.py:545-576`, and its side decision path clears any exact observed wait at `ciel_runtime_support/side_work_decision.py:191-207`. An old process can therefore clear `ciel:p0-activation:<operation_ref>`, approve executing at `work_mcp_tools.py:634-644`, and admit leaves at `work_plan_tree.py:661-684` before private activation is done. A new-only prefix refusal does not constrain old code. The real-old harness must exercise clear + approve + reserve, and the design needs an old-code-unmodifiable gate or must weaken the guarantee.

Additional measured 57db mutation path: old `work_node_remove` is exposed at `ciel_runtime_support/work_mcp_tools.py:602-605`, and `work_plan_tree.remove_node` permits deleting an unfocused root and its entire subtree at `ciel_runtime_support/work_plan_tree.py:488-507`. Therefore even a reserved root field cannot guarantee that the exact prepared root survives an old writer: old removal followed by normal root creation may consume/reuse later identity space. A revised design/harness must cover old root removal, recreation and collision, or explicitly enforce mixed-version exclusion. Treating `blocked` status as the gate is not accepted without proof because old update/remove surfaces can mutate it too.

No product file was edited.

# Step 13. Independent new-root P0 design re-review (2026-11-14T06:23Z)

continue-ctl re-read `new-root-p0-operation.md` at SHA-256 `ed219a82a5151a3751d7c773bbf7cd75d544a95b81c5feab8b393ad3f8d58d65`. Verdict: **CHANGE**; two of the three prior blockers remain.

- **Manifest store/bounds remain undefined** (`new-root-p0-operation.md:72-121`). The design still names no concrete manifest file/container schema, CAS/version, global row/byte bound or pruning rule. It allows a full 2 MiB normalized spec without reserving headroom for the manifest envelope or other rows.
- **Marker mapping is fixed** (`new-root-p0-operation.md:160-169`). The exact relation is now active `RootMarker` plus prepared `PriorityClear` targeting canonical P1; a cleared marker ends P0 and cannot activate. This supersedes the stale marker sentence in the earlier independent review note.
- **Old-reader pre-done dispatch remains unprotected** (`new-root-p0-operation.md:191-220,236`). The proposed exclusion is private to new hosts. In the real 57dbfb3 extract, `work_mcp_tools._execute` checks readiness and writes phase `executing` at `ciel_runtime_support/work_mcp_tools.py:634-644`; `work_plan_tree.runnable_leaves` then admits leaves whose root phase is executing at `ciel_runtime_support/work_plan_tree.py:661-684`. No old-readable durable gate is specified, and no real-57db planning-to-executing approval/dispatch harness evidence exists. The design must add a gate old code honors or explicitly weaken the guarantee and its test claim.

No product file was edited and no implementation verdict was made.

# Step 12. Unified new-root P0 create saga (2026-11-14T05:47Z to 05:49Z)

Designed the [unified P0 create operation](new-root-p0-operation.md) after the backend proved that existing MCP root-create calls have no stable operation identity and mint root identity only inside the board transaction. The exact lowerhex32 operation manifest is durable before board mutation; the host premints root id/time; board stores a planning, canonical P1, non-dispatchable root; marker/activation then roll forward; every crash point resumes the same operation. MCP may omit operation id because the host wrapper mints before its first write; REST/ctl may pin it. Human surfaces expose only a bounded reference. Non-P0 create remains unchanged. No implementation was made pending ruling.

# Step 1. M-P1, the engine (2026-11-14T02:33Z to 02:40Z)

Claude Code 2.1.325 binary, sha256 `87e0cb6ae0f4775da388cd3043f4d8097ef961dcddbf55fe90bc61ffc3f57a30`, read with mmap only through `evidence/bundle_excerpts.py`. Offsets are byte offsets in that file.

| Offset | Shipped code | What it shows |
|---|---|---|
| 323867254 | `[ft]:"runs the Agent tool: that is $.agent.spawn"` | `$.agent.spawn` runs the Agent tool's call path. |
| 328586789+ | `model:ee?void 0:He` (hits 328588556, 328589035) | For a fork (`ee`), the `agent.spawn` hook input carries no model. |
| 328590628 | `let Ye=ee?"inherit":He` | After the hooks, a fork's call model is forced to `inherit`; a hook's rewritten `model` is ignored. |
| 328590838 | `Ae=Z6(YFe(w,Dt),Dt,Ye,Ze)` | The fork model is resolved with call model `inherit` and parent model `Dt`. |
| 320980672 | `if(r==="inherit")return k();` | `Z6` returns the parent's main-loop model at once for `inherit`; `CLAUDE_CODE_SUBAGENT_MODEL` (read later, `eme()`) is never reached for a fork. |
| 321938229 | `model:"inherit",permissionMode:"bubble"` | The built-in fork definition `jY` is `inherit`. |
| 320981106+ | `function Jst` | With `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, only an `inherit` call model survives; other call and frontmatter models are dropped. |
| 328575409 | `i.CLAUDE_CODE_SUBAGENT_MODEL_FORCE?s.omit({model:!0}):s` | Under FORCE the Agent tool schema has no `model` parameter. |
| 328533812 | `Nt=s?.isObserver?void 0:L?"inherit":s?.model` | A resumed fork also runs on `inherit`. |

The vendored mod types say the same (`.claude-plugin/types/claude-code/index.d.ts:303` "Ignored for forks, which always inherit", `:337`).

Verdict (bundle, not a run): in 2.1.325 a native fork always runs on the parent's model. `$.agent.spawn` takes a `model` field, but it is ignored for forks. Neither env variable can change a fork's model.

# Step 2. M-P1, the router option (02:36Z to 02:45Z)

- The router maps the request's model alias at `claude_router.py:423` and `:534` (`resolve_requested_model`). Only compaction reads `x-claude-code-agent-id` today (`runtime_compaction_binding.py:344,464`). There is a precedent for switching a running conversation's model inside one provider: `forced_model` (`model_override.py`, `router_http.py:326`, `provider_model_selection.py:494`).
- Fork request headers (bundle 316481582): `x-claude-code-agent-id`, `x-claude-code-parent-agent-id`. The agent name is not sent.
- Timing, measured from the existing owned run R2 (CC 2.1.325, `run-r2-20261114T004308Z`): the rework fork's first upstream request arrived at 1794617029.217. The ledger sample at 1794617029.645 still showed the lease not bound; `bound` first appears in the sample at 1794617030.944. So the first fork request was sent at least 0.43 s before the bind was durable. That fork's request named `claude-opus-5-5`, the same model as main.
- Model switch with thinking blocks: Anthropic's thinking page (fetched 2026-11-14 about 02:39Z, platform.claude.com `build-with-claude/thinking.md` lines 1105-1119) says switching models mid-conversation is supported on the Claude API. Blocks the target model cannot read are dropped with no error, and the docs say not to strip them. Changing thinking mode or effort invalidates the prompt cache (lines 1131 and prompt-caching.md line 677).
- Not proven: cache reuse across a model change; behaviour of third-party gateways (cielairouter and others); context window, max_tokens and effort sized by Claude Code for main's model on a smaller target; tool-id handling when the target model uses the Responses conversion. A DeepSeek precedent shows a provider rejecting cross-model history (`docs/journal/2026/09/18/fixes/providers/deepseek-thinking-passthrough.okf`).

# Step 3. Old-reader risk, measured (02:42Z)

`evidence/old_reader_p0_probe.py` ran real 57dbfb3 readers (git archive extract, separate `python -I -B`) in an isolated env. Result: `evidence/old-reader-p0-result.json`.

- Board with a `P0` root: the 57dbfb3 `read()` **quarantined the whole board** (renamed to `board-p0.json.corrupt-<ns>`; cause "work goal has an invalid priority"). By code, `read_snapshot()` (retention, status) only raises and skips.
- Board root with an unknown `priority_models` field: read OK, field dropped (an earlier test shows a 57dbfb3 write strips it).
- `work-parallel.json` with an added policy key or `P0` in `priority_limits`: "corrupt parallel state; writes blocked" (causes "policy fields" and "priority limits"). The file stays in place. A `P0` lease fails the same way by code (`work_parallel.py:151` at 57dbfb3).
- Coordination receipts at 57dbfb3 require P1-P5 (`work_parallel_receipts.py:158,209,315,509`), code reading only.
- Side inbox at 57dbfb3: P1-P5 only; Walkie integer 0 maps to no priority.

# Step 4. Inventory, P0 bypass points, settings design

Sent to the lead in the Phase 1 report (2026-11-14T02:48Z by `date -u`). It holds the inventory with file:line, the P0 bypass points, what must still hold, the settings design and the owner split.

Settings design as proposed:
- Per-priority models live in config.json, keyed by provider. They are not in the ledger policy, because of step 3.
- Keys `work_parallel_p0_model` to `work_parallel_p5_model`. Choices are `inherit` plus the current provider's cached model ids.
- The side model is restored under a new key, `work_side_intake_fork_model`, so stale historical `model` values do not come back.
- Every surface uses `work_live_settings.settings()`.
- The model is applied in the router per agent id: before bind from the fork directive's `lease_id` or `event_id`, then pinned in memory, and after bind from `lease.host_id`.

Open questions sent to the lead:
1. "Ignore approval" against the plan-approval rule and the approval-generation re-check at report time.
2. Whether "ignore the limit" includes `launches_per_minute` and MAX_LEASES.
3. How to store P0 so that 57dbfb3 readers on the same device do not quarantine boards or block the ledger.
4. GO for probe P-R1 (router plus provider, no claude.exe) before the router mapping design is final.
5. Side model: one setting, or a `by_priority` choice.

State at 02:50Z: the lead accepted M-P1 and ruled Q1–Q5 in requirement.md. P-R1 is bounded and approved for nvidia-hosted, but its start is held for clarification: the active workspace configuration and this session say `cielairouter`; only the legacy/shared config says `nvidia-hosted`, and Windows has no nvidia key in the path the runtime reads. No live request was sent.

# Step 5. Exact old-reader-safe P0 encoding proposal (2026-11-14T03:02Z)

This proposal is awaiting the lead's approval before backend implementation.

## Board

The board continues to store `priority: "P1"`. A P0 root adds one optional root-only boolean, `priority_zero: true`. It is a marker, not a priority value. New code projects it as effective priority P0 only while canonical `priority` is P1. A 57dbfb3 reader ignores this unknown field and strips it on its next write; that safely downgrades future scheduling to P1 instead of quarantining the board or accidentally retaining P0. If an old writer changes the canonical priority to P2–P5, new code ignores any concurrently surviving marker.

The user-facing command/MCP/OpenAPI accepts P0; its transaction encodes P1 plus the marker. New readers and every surface project P0. No literal P0 enters the canonical board `priority` field.

## Ledger and coordination

`work-parallel.json`, its leases, `work-parallel-coordination.json`, receipts and the inbox keep priority P1. P0 lease identity and the required audit phrase live in a new strict-CAS file beside the ledger, `work-parallel-p0.json`:

```text
schema=1, version=N,
leases[lease_id] = {
  scope, item_id, root_id, root_created_at,
  priority_zero=true,
  dispatch_without_approval=true,
  created_at
}
```

The marker pins the exact lease, root id, root creation time, scope and item. It cannot elevate another lease after delete/recreate. A reservation is not spawned until this sidecar entry commits. If the sidecar write fails, the still-unbound reservation is released. Terminal cleanup deletes the marker only after the terminal ledger transition. It survives restart and rework; a replacement lease gets its own marker before spawn.

The ledger stores the P0 lease as P1 for 57dbfb3. New admission gets the pinned P0 lease-id set from the sidecar and excludes those rows from P1 capacity, but includes them in MAX_LEASES=100 and duplicate/owner fences. A P0 reserve bypasses enabled, paused, P1 capacity and launches_per_minute, and does not append its launch to the ordinary rate history. A 57dbfb3 process may conservatively count a running P0 as P1, but remains safe and readable.

For a planning root, the coordination row remains old-valid: priority P1, phase planning, and a nonzero approval_generation. The P0 sidecar authorizes new `record_assignment` and report/accept code to bypass only the phase/approval check. Changed item/root identity or creation time, changed scope/fingerprint, lease/artifact, and wrong owner still reject. The assignment projection includes the exact phrase `P0 dispatch without approval`; it is not added to an old-reader file.

The side inbox never stores P0 structurally. An incoming raw source may say priority 0; 57dbfb3 already maps that to no priority. New code can project it as effective P0 and encode a created/updated Work root as P1 plus `priority_zero: true`. No `priority/P0` inbox directory is created, so old cleanup does not leak stale index files.

## Required compatibility round trip

The fail-before/after test runs a real 57dbfb3 package in a separate `python -I -B` process:
1. New code writes a P0 board (P1 plus boolean marker), a P0 lease (P1), old-valid coordination run (P1), and the new sidecar.
2. 57dbfb3 reads all three legacy files and performs real writes: board progress update, ledger paused toggle and one ordinary P1 reserve/release, coordination root reconcile.
3. Assert: no quarantine; no blocked write; all legacy files still validate; their priority values are only P1–P5; the unknown board marker may be stripped; the new sidecar is byte-identical.
4. New code rereads. If the board marker survived, it projects P0. If 57dbfb3 stripped it, the root safely projects P1. An already running lease still projects P0 from its exact lease marker until terminal cleanup.
5. Delete/recreate a root and prove the old sidecar marker cannot elevate it because root id/created_at differ.

# Step 6. CHANGE ruling and backend facts read (2026-11-14T03:16Z)

Read the complete requirement and steps 161-162 of the backend journal. The old-reader proposal above is superseded:

- a 57dbfb3 ordinary board write strips the unknown root field, so the root P0 fact must move wholly into the sidecar;
- `reserve -> sidecar -> spawn` is unsafe because a crash leaves an owned, unbound reservation that `orphan_release` cannot reclaim; an exact launch intent must be durable before reserve;
- the real 57dbfb3 process must measure a planning root at approval generation 0 against a schema-valid run generation >=1;
- initial reserve, rework, stranded recovery and reacquire all retain the P0 bypass until an explicit priority change ends it.

No product file was edited.

# Step 7. Real 57dbfb3 root/coordination/pump measurement (2026-11-14T03:34Z to 03:38Z)

Probe and result:

- [old_reader_p0_roundtrip_probe.py](evidence/old_reader_p0_roundtrip_probe.py), sha256 `bbda5842...`;
- [old-reader-p0-roundtrip-result.json](evidence/old-reader-p0-roundtrip-result.json), sha256 `3e5f59b8...`.

The probe used the immutable backend-gate 57dbfb3 extract, separate `python -I -B`, a temporary config and only temporary stores.

Measured pass facts:

- planning root generation 0 plus run generation 1 validates;
- beta `reconcile_roots` writes the planning root and leaves the run unchanged;
- beta `_approval_current` returns false but does not revoke/cancel or change lease/run;
- beta worker report refuses `Work approval scope changed; reconcile before continuing`, but lease/run/receipts stay unchanged;
- beta pump `reserve_next` and explicit `reserve` mint no second lease while the P0-labelled-as-P1 lease is active, including after making the root executing;
- delete/recreate gets a different root id/creation time;
- the new sidecar stayed byte-identical across all old board, ledger, coordination and pump writes.

Measured blocker:

- leaf progress/evidence and executing-phase writes leave the root timestamp alone;
- root progress and leaf status ancestor propagation change the root timestamp;
- one old history with `P1 -> P2 -> P1` and one history with ordinary root progress can end with byte-identical final node lists;
- a second pair stays byte-identical after a leaf status write.

Therefore no passive sidecar plus final board-state guard can both survive every ordinary old write and detect every old `P1 -> P2 -> P1` cycle. 57dbfb3 does ship an in-process `subscribe_work_plan_changes(before, after)` callback contract, which could distinguish the transition exactly, but new product bytes cannot register it into an already-running 57dbfb3 process.

# Step 8. Revised encoding text, first draft (2026-11-14T03:59Z)

Wrote the first [revised-encoding.md](revised-encoding.md): sidecar-only root identity, durable launch intent before reserve, exact intent identity in the existing dispatch name, the `admission_bypass="p0"` parameter, crash table, planning-root approval and old-reader assertions. It ended with "do not implement until the root-change blocker is ruled". No product edit.

# Step 10. Lead ruling 03:58Z read; final encoding written (2026-11-14T04:00Z to 04:12Z)

The lead withdrew "every old priority change ends P0" as lead-added scope. Final semantics: the strict-CAS sidecar is authoritative until a new-code priority change, root terminal, or delete/recreate; a 57dbfb3 write never clears P0; mixed-version status on all four surfaces; no subscriber/interception.

Rewrote [revised-encoding.md](revised-encoding.md) for GO:

- file table: P0 only in `work-parallel-p0.json`; every legacy file stays P1-P5;
- exact sidecar schema (root markers, launch intents, priority clears);
- effective-priority rule and mixed-version status text;
- the three ways P0 ends; a three-step board+sidecar roll-forward for new-code priority changes, with its crash/conflict table;
- launch protocol for initial/rework/stranded/reacquire: intent -> `reserve(..., admission_bypass="p0", p0_intent=<re-read intent>)` -> bind lease id into intent -> coordination -> spawn -> native bind; crash table with exact adoption by the deterministic `work-p0-<intent_id>` dispatch name; unknown reservations never adopted;
- 57dbfb3 planning-root table (measured);
- router model mapping before bind: identity from the fork prompt, validated against the ledger by worker-token hash, pinned per agent id, applied before `resolve_requested_model` (claude_router.py:423, :534).

Measured during this step from an existing recording (read-only): for the side actor, the first agent-id request's last user turn holds the side JSON with `parent_session_id`; later requests keep it in history. Not measured: the Work fork `Assignment JSON` position (no recording found). Marked unverified in the text: which process serves a fork's `work_parallel_report`; `record_assignment` idempotency is a required code change, not current behaviour.

# Step 11. bbdb550d GO-with-changes; scratch implementation (2026-11-14T04:16Z to 04:52Z)

Lead GO is scratch only; pinned files remain untouched. Full ruling: requirement.md lines 248 onward. Implemented in `.local/p0-scratch/`, manifest `CANDIDATE-SHA256SUMS.txt` sha `c14e0b7a`:

- per-provider P0-P5 model settings and one side-fork model setting;
- model choices from the current provider cache; missing id -> inherit with visible status;
- actual/configured direct data path -> effective inherit with exact warning; current workspace has no configured direct path;
- the cache-per-model help line on menu, web, REST and ctl;
- router remap before model/protocol resolution with Work worker-token validation, side claim-generation validation, bounded pins, target request-local profile and observability assignment scrubbing;
- target limit probe: max_tokens 32000 -> 1024; target context 8192 drove compaction of a 50K-char input; target model remained target;
- setting P0 on existing P2-P5 roots marker-first then board P1; new-root predicted-id marker-first; crash rows in revised-encoding.md;
- MCP report process proof from runtime_launch/router_process_lifecycle; no secret boundary; direct and cache status text.

Scratch validation: 13 focused tests OK; ruff clean; all scratch Python compiles. Dependencies sent to backend/pump: router expects a P0 read-view lease query; side prompt needs `claim_generation`. Candidate announced to main, independent review requested from continue-ctl. Waiting for lead apply GO; no pinned product file was edited.

# Step 11. Work-fork request identity measurement preflight (2026-11-14T06:31Z onward)

Lead assigned a read-only measurement of the actual router-bound request made by a native Work fork. No product/config/session/router change was made.

Evidence/preflight: [work-fork-request-identity-preflight.json](evidence/work-fork-request-identity-preflight.json), SHA-256 `8ad309d14ae687b2…`.

Verified desk facts:

- Claude Code 2.1.325 bundle (installed exe `87e0cb6ae0f4775…`) constructs `x-claude-code-agent-id` from the native agent context `agentId` and `x-claude-code-parent-agent-id` from `parentAgentId` (bundle offset 316481582). It sends no agent name, Work lease, assignment generation, dispatch name or priority header.
- `parallelAssignment` (`register.js` `faef7e7b…`) places `item_id`, `lease_id`, `plan_version`, `worker_token`, role and optional integer `generation` in the fork prompt. It does not put dispatch name or priority in Assignment JSON.
- Router path preserves this relationship: `router_http.py:1817-1849` keeps inbound headers on the request handler while dispatching the Messages body; `claude_router.py:367-374` already passes those headers with the raw body to request observers before upstream model resolution at :423/:534; `runtime_compaction_binding.py:343-359,453-475` reads the session header and explicitly excludes a request carrying the agent header from main-session proof. No current code maps an agent header to a Work lease.
- The intended trustworthy bridge is therefore: request session header -> scope; request agent header -> native identity; assignment lease/token -> `work_parallel.py:57-63` SHA-256/hash-compare against durable `lease.report_hash`; assignment generation -> exact coordination run (`work_parallel_receipts.py:42-45`); effective priority and dispatch name derived from validated durable stores, never trusted from model text. This remains a design fact until prompt position is measured.
- Existing owned generic-fork evidence (`request-structures.jsonl` `a4c055e0…`, five rows) proves fork requests are structurally distinct but carries no Work assignment marker. Existing nine-run header evidence (`cf84373b…`) proves one shared session id and fork-only agent header, not a Work-specific assignment.
- The earlier focused Work capture `work-fork-capture-20261113-232128` is **not evidence**: `result.json` `89bc30c2…` records zero main and zero fork requests and failure before the model wire. It does not settle Assignment JSON position.

The sole corrected owned rerun executed under lead slot GO and ended without a Work fork request. Evidence: `evidence/work-fork-identity-20261114-004500/result.json`, SHA-256 `3f964d2fa73de793…`; driver `8eb779f78472ea40…`.

- Non-Claude preflight passed before launch: MCP health 200/name, legacy initialize 200/protocol 2025-06-18, tools/list 200 with 37 tools, Messages count_tokens 200.
- Both Work plugins loaded. The driver sent one initial prompt and captured exactly four bounded main model requests, then hit the main cap/hard terminal without any fork request. Main request structural evidence: session header present; agent and parent-agent headers absent; safe header names and body top-level keys only. No values/text were retained.
- The backend did receive host `reserve_next`, but each attempted reservation failed before spawn because workspace preparation rejected the repo as not the exact Work worktree root; later attempts hit launch rate. Therefore `fork_requests=0`, `fork_shape=null`. This is a measured harness terminal outcome, not evidence that Work forks omit assignment data.
- Cleanup killed only the owned claude.exe by exact pid/creation time, leaf-first machinery; owned survivors 0, protected overlap 0, protected roots absent 0. Bad inherited env 0. Evidence value hits: host token 0, worker token value 0, fixture auth 0.
- Per lead ruling this was the sole rerun. **No further rerun is allowed regardless of outcome.**

Final verdict: header/native identity is established from bundle/prior generic-fork evidence, but actual Work Assignment JSON request position remains **unknown** because both focused Work captures observed zero fork requests. Prompt-based lease/token/generation correlation is therefore unproven at runtime. No heuristic or model-text-only mapping may be claimed as proven.

## First-request host bridge design verdict

Desk design evidence: [work-fork-first-request-bridge-design.json](evidence/work-fork-first-request-bridge-design.json).

**NO-GO with current Claude Code host metadata.** The durable launch record already knows exact scope, dispatch name, lease, assignment generation, effective priority and selected model before spawn. But the first router-bound request exposes only session id, opaque native agent id and parent agent id; it exposes no agent/dispatch name, lease, generation, priority or launch ref. The installed bundle constructs those headers directly from native `agentId`/`parentAgentId` (offset 316481582).

The current host sequence persists launch state, calls and awaits `$.agent.spawn`, then receives `spawned.agentId` and can send the authenticated bind. Existing R2 timing proves this is too late for request one: the first fork upstream request arrived at `1794617029.217`; the ledger was still unbound at `1794617029.645` (at least 0.428 s later), and the first bound sample was `1794617030.944`. Therefore a post-spawn `(scope,agent_id)` mapping covers second/later requests only.

A pre-spawn claim keyed by durable `dispatch_name` cannot be matched because dispatch name is not router-observable on the request. `$.agent.spawn` has no API to premint/expose the native agent id before child request dispatch. Assignment-text/token parsing is forbidden and its runtime position is unmeasured.

Required host support is one of: an engine-authenticated dispatch-name/launch-ref request field; a native agent id exposed before the child can request; or an authenticated spawn-correlation callback guaranteed to reach the router before child request dispatch. Until then, the router cannot safely remap the first Work-fork request.

Lead verdict: do not implement a second-request-only mapping. Switching models after the first response violates the configured per-fork model contract and introduces model/history/prompt-cache inconsistency. No prompt/worker-token parsing and no router remap code.

Product consequence: per-priority Work-fork model and side-fork model settings cannot be honestly enabled on current native forks. Every menu/web/REST/ctl/settings scratch for those choices must remain disabled/unsupported (or hidden), not offer a nonfunctional selection. They may be enabled only after Claude Code exposes and measurement proves either a pre-spawn native agent identity or a router-visible authenticated dispatch/lease claim.

# Step 9. P-R1 live probe (2026-11-14T03:50Z to 03:53Z)

Probe and aggregate result:

- [pr1_probe.py](evidence/pr1_probe.py), sha256 `3dd4ca10...`;
- [pr1-result.json](evidence/pr1-result.json), sha256 `88bdb836...`;
- raw bounded runs `pr1-run1-result.json` (`3159772c...`), `pr1-run2-result.json` (`5d37bad4...`) and `pr1-run3-result.json` (`45fadaa6...`).

Isolation: workspace digest `bd5f642b3583`, provider `cielairouter`, current model `_Walkie_Sol61-Opus55-Fast`; temporary config containing only that provider plus its credential; copied key-scoped model cache; spare port; `CIEL_RUNTIME_TEST_ISOLATED=1`; all inherited CIEL/Claude/Anthropic/Codex/OpenAI/MAX_MCP variables stripped; no claude.exe. The provider key appeared in zero evidence files and zero router log lines.

Budget: 9 live Messages requests of the approved 12. Run 1 spent one source request and stopped because effort-low produced tool_use without thinking. Run 2 spent one source plus five continuations. Run 3 spent one source plus one replacement non-Claude continuation.

Source at effort high returned signed thinking plus tool_use. Each continuation sent the source assistant content unchanged, returned the exact source tool id in a tool_result, and carried `x-claude-code-agent-id`.

| Target family/model | Status | Thinking handling | Tool id | Cache usage |
|---|---:|---|---|---|
| Fable 5.2 / `cc/claude-fable-5-2` | 200 | source Opus thinking accepted; response text only | accepted | create 8008, read 0 |
| Opus 5.5 / `cc/claude-opus-5-5` | 200 | signed source thinking accepted; response signed thinking + text | accepted | create 291, read 7717 |
| Sonnet 5.5 / `cc/claude-sonnet-5-5` | 200 | source Opus thinking accepted; response signed thinking + text | accepted | create 7905, read 0 |
| Haiku 5.5 / `cc/claude-haiku-5-5` | 200 | source Opus thinking accepted; response signed thinking + text | accepted | create 7905, read 0 |
| non-Claude / `cx/gpt-6.1-sol` | 200 | source thinking accepted by provider bridge; response text only | accepted | provider reported input 5748/output 191, no cache fields |

An offered `cx/gpt-5.5` first returned 400 `invalid_request_error` because that model was unsupported for this account; the approved same-provider replacement `cx/gpt-6.1-sol` returned 200. No provider switch occurred.

P-R1 verdict: same-provider router remapping is viable for all Claude families tested and one usable non-Claude family. Prompt cache is model-scoped in this measurement: the same Opus target read 7717 cached tokens; Fable/Sonnet/Haiku created new caches; Sol reported no cache fields.
