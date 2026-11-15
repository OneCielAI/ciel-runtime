---
type: api-contract
title: Unified priority caller contract
description: Exact operation identity sources and routing for MCP, REST, web, ctl and side decisions through the P0 priority service.
timestamp: 2026-11-14T07:25:00Z
tags: [work, priority, p0, mcp, rest, ctl, side]
---

# Rule

Every **new-code explicit** priority mutation routes through one application priority service before any direct `tree.*priority` call. Existing old-writer canonical drift remains canonical-only and does not end effective P0. The service owns operation replay, exact root identity and PriorityClear/marker transitions.

New-root `priority=P0` is the separate beta refusal contract; it returns `p0_new_root_unsupported_mixed_version` before writes. Existing-root P0 activation and P0 end/set remain supported.

# Exact operation identity by caller

| Caller | Source | Exact rule |
|---|---|---|
| MCP `work_node_update` priority | host wrapper | schema accepts optional `operation_id:lowerhex32`; if absent, authenticated MCP wrapper mints lowerhex32 before first write and retains it for this tool invocation/retry. It calls the application service, not direct `_run`/tree. Process-loss after an unreturned mint has the documented same omission limitation; no guessed replay. |
| MCP `work_plan_set` existing `root_id` priority | host wrapper | same optional/minted lowerhex32 rule; replacement payload and priority operation share one application receipt. New root P0 refuses before receipt/board write. |
| MCP root `work_node_add` priority | host wrapper | same optional/minted rule; non-P0 create retains normal path; P0 refuses before write. |
| REST board commands | HTTP header/body | `Idempotency-Key` or body `operation_id`; both must match. Existing controller lines 364-370 already pass this id to `WorkApplication.command`. P0 service uses that exact id. The authenticated mutation response may return it only as `priority_operation_id` alongside bounded status; ordinary REST status/list responses never do. |
| web | retained pending operation | existing generic `command()` mints a fresh key per call and is insufficient. Priority UI mints once, stores the id+request digest in page pending state before POST, and displays bounded `priority_operation_ref`. The full id remains page-internal request state and is never rendered, logged or copied into public status. Outcome-unknown forces audit/reconcile with that same id; a user retry cannot mint a replacement until audit settles. |
| `ciel-runtimectl` | CLI id or per-invocation mint | `--operation-id` gives exact cross-invocation replay. If omitted, `work_ctl.post` mints one id only for that POST invocation. Only the authenticated JSON/machine **mutation receipt** returns the full id as `priority_operation_id`, plus bounded `priority_operation_ref`; text output and every ctl status/list command expose only the ref. A later CLI invocation mints a different id and is not exact replay. Help instructs machine callers to retain the authenticated receipt field or pass `--operation-id` for retry/reconcile. No unproven internal retry loop is claimed. |
| side decision priority | receipt identity | use the exact lowerhex64 side receipt id as the service operation identity domain-framed as `side-priority:<receipt_id>` (bounded visible ASCII accepted by current WorkApplication). Replay of the same decision digest returns the same priority outcome; different digest conflicts. No separate model-provided id. |

Scoped operation key is `(scope, external_operation_id)`. The versioned request digest is `SHA256(frame("ciel.work.priority-command.v1", canonical{scope,root_id,root_created_at,target_priority,set_by,caller_kind,external_operation_id,full_combined_manifest_digest}))`. Every prepare/replay relation persists and revalidates all fields. Full `external_operation_id` is confined to request input, durable internal state, and the two named authenticated mutation receipt fields: REST `priority_operation_id` and ctl JSON/machine `priority_operation_id`. Coordinator MCP receives only bounded `priority_operation_ref`; its exact retry identity is the optional caller-supplied id or the same in-memory wrapper invocation, not a model-visible minted id. Web/human/model/public status and every log show only the bounded ref.

# Application saga and caller changes

## REST / web / ctl

1. The controller derives the exact idempotency key as above and calls `WorkApplication.operator_prepare(command,payload,key,origin)` before any board/P0 write. The prepared operator intent stores exact request digest and operation identity.
2. The application invokes the priority service. For combined board fields+priority, the service prepares marker/PriorityClear then performs **one board CAS** applying all non-priority fields plus canonical target priority. No earlier board-field commit is allowed.
3. On exact success, `operator_commit` stores the bounded response summary. Exact replay returns that committed summary without repeating effects. At the authenticated mutation boundary only, the REST formatter and ctl JSON/machine formatter add `priority_operation_id` from the already-authenticated request identity; that full id is not part of the stored bounded summary and cannot flow into status/list/audit/log projections.
4. A known no-effect failure before any external/store commit calls `operator_abandon`. If operator-intent atomic replace returns post-replace parent-fsync OSError/ambiguous commit, caller catches it, rereads the exact scoped operation key and request digest/state: matching prepared/committed state continues/replays; absent state is the only proven noncommit; mismatch conflicts. Store-then-throw, response loss or any ambiguous board/P0 effect leaves receipt `prepared/outcome_unknown`; no abandon/blind retry. Reconciliation rereads board+P0 operation and records exact outcome.
5. Web and ctl use this same REST path/key; they do not implement a second saga.

## MCP

Exact interception file/order: `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`, inside `workToolCall` **after** existing gate read/block, `_host` stripping and `session_id` injection (around the current 1084-1090 flow), and before final `next(input)` forwarding. This preserves the outer completion-gate path for `work_node_update status=done` and existing coordinator identity enforcement; intercepting earlier `parallelToolCall` is forbidden.

For model/coordinator `work_plan_set`, root `work_node_add` and `work_node_update` containing priority, this wrapper validates/uses optional operation_id or premints lowerhex32 once for that tool call, replaces raw forwarding with a host-only `work_priority_command` event, and returns only bounded `priority_operation_ref` plus bounded state metadata to the coordinator/model. It never returns the full minted id as model-visible result/status; an explicitly supplied id remains request input, not response projection. The host event resolves the same session `WorkApplication` and operator-intent repository used by REST and executes prepare -> external priority service -> commit/abandon. It never calls cross-store service inside `_apply`/board lock. Combined fields follow one-board-CAS manifest. Own-fork hook skip does not authorize bypass: only the coordinator/model path may issue this command, and direct MCP callers still hit host authentication/schema.

Process-loss after an omitted minted id but before response has the documented limitation: a later model retry without explicit id may mint another operation and must read audit before retry. Tests cover register real-load, gate preservation, completion status=done path, identity injection, optional id replay, and zero raw `next(input)` priority bypass.

## Side decision

Beta ruling uses option B for side decisions targeting effective P0. Normalize/validate outside the board lock. If the decision contains priority and its target root is effective P0, refuse the **entire decision before board mutation**: no note, wait release, reorder, canonical priority change or applied side receipt. Persist/return an idempotent bounded `p0_priority_requires_main` handoff keyed by receipt+decision digest with exact item/root/ref/requested priority; external text remains untrusted and grants no execution approval. Same receipt/digest replay returns the same refusal/handoff; different digest conflicts. Main/new-code authenticated `priority_command` performs the change.

Ordinary non-P0 side priority remains the existing one-board-transaction decision path. It does not use cross-store P0 sidecar operations. This beta deliberately avoids a side combined cross-store saga.

## Concrete path requirements

1. `work_mcp_tools.py` plan_set/node_add/node_update branch before tree priority validation and invoke the application saga.
2. Add a pre-`_apply` `priority_command` application boundary. It prepares full combined manifest and external P0 operation, then calls one board transaction. `_apply` never invokes a cross-store service; it only consumes the already-prepared single board manifest inside the lock and returns its board result.
3. Legacy `/ca/work/nodes/*` schema currently excludes priority; preserve and test that refusal. It is not an active bypass and must not be widened to generic P0.
4. `ciel-runtimectl` adds explicit `work priority ROOT P0|P1..P5 --expected-version --operation-id` through REST `priority-command`; no generic board-priority action currently exists.
5. `side_work_decision.apply` performs the effective-P0 whole-decision refusal before its existing board transaction; ordinary non-P0 direct tree priority remains inside that atomic board path.
6. Recovery/sweep never interprets old canonical P2-P5 as a clear. Only validated new-code service operations change marker authority.

# Selective P0 schema rule

Never add `P0` to `tree.PRIORITIES`, persisted board/ledger schemas, or the generic `_PRIORITY` definition used where legacy P1-P5 values are stored.

- model-facing existing-root `work_plan_set(root_id present)` and root `work_node_update` may accept a selective service-only P0 token before generic tree validation;
- new-root `work_plan_set(root_id absent)` and root-form `work_node_add(parent absent)` accept P0 only to return the exact unsupported pre-write response;
- child priority P0 refuses;
- canonical board/lease values remain P1-P5, with effective P0 only from marker authority.

Tool help must distinguish these branches and tests must prove the generic/persisted enums remain legacy-only.

# Authenticated mutation receipts versus four-surface status

The exact full-id exception is limited to successful/accepted authenticated mutation responses:

```text
REST authenticated priority mutation response: priority_operation_id, priority_operation_ref
ctl JSON/machine priority mutation receipt:      priority_operation_id, priority_operation_ref
```

`priority_operation_id` is the exact external operation id supplied or minted for that mutation. It is never copied into a later status/list/audit projection or log. Web does not render it. Coordinator MCP is intentionally not an exception: its model-visible mutation result contains only the bounded ref; callers needing cross-invocation exact replay supply `operation_id` in the next authenticated request.

MCP model result/status, web, ordinary REST status/list and ctl text/JSON status/list project the same bounded fields:

```text
priority_operation_ref
canonical_priority
requested_priority
effective_priority
operation_state: prepared | board_committed | done | conflict
mixed_version_warning
```

These bounded projections and all human/public/model surfaces and logs contain no full external operation id, marker/clear ids, internal manifests or tokens.

# Tests

- each caller source above produces one stable operation identity before its first write;
- REST header/body mismatch refuses;
- web pending state retains id through outcome_unknown/audit reconcile; ordinary new user call cannot silently replace it;
- ctl explicit `--operation-id` replays across invocation; omission limitation/help/output tested;
- REST authenticated mutation response and ctl JSON/machine mutation receipt contain the full id only in exact `priority_operation_id`, paired with bounded `priority_operation_ref`; no other response field carries it;
- seeded full-id canary is absent from web render, MCP/model result and status, ordinary REST status/list, ctl text/JSON status/list, bounded audit summaries and every captured log; those surfaces contain only `priority_operation_ref`;
- MCP register.js interception preserves gate/identity/completion behavior; explicit/minted replay and process-loss limitation; model-visible MCP mutation result never returns the full minted id;
- side effective-P0 priority refuses entire decision with idempotent main handoff; ordinary non-P0 remains atomic;
- fail-before tests prove every former direct priority path bypasses the service today;
- pass-after spies assert zero direct tree priority calls from all new-code surfaces;
- combined non-priority+priority commands use one board CAS and are all-or-recoverable, never partial silent success;
- known no-effect failure abandons; store-then-throw/response loss remains prepared outcome_unknown and reconciles;
- side effective-P0 refusal handoff persists/replays exactly and writes no board/sidecar priority effect; ordinary non-P0 side decision remains one board transaction;
- legacy `/nodes/*` priority remains refused;
- selective P0 accepted only on ruled model-facing branches; generic/tree/persisted enums remain P1-P5;
- new-root P0 refuses before service/board/P0 store writes;
- old canonical drift does not clear active P0.
