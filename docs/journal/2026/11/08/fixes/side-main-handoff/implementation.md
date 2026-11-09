---
type: fix
title: Side actors use the selected model, only triage, and wake main for its Work
description: Explicit spawn model for live side-model changes, a fail-closed read/reply tool surface with no native fork, and a durable side-to-main handoff that wakes main and lets it clear only recorded waits.
tags: [side-intake, sse, work, model-routing, safety, handoff]
timestamp: 2026-11-08
---

# Confirmed causes

## 1. Changed side model, previous model requested

- Work UI and `work_side_claim.settings.model` carried the newly selected model.
- `side-intake.js` re-registered `ciel-work:side-intake` with that model and spawned it in the same turn **without a `model`**.
- The Claude Code mod API says `agent.register` applies "from the next turn on" and that a spawn `model` explicitly selects the subagent model.
- Prody evidence: actors created at 18:40–18:50Z still recorded `requestedModel = ciel-runtime-cielairouter-_walkie_haiku_terra`, while main was `_walkie_opus55-sol61-fast`.

## 2. Side actors had an execution-capable surface

- `availableMcp()` inherited every well-formed MCP tool. The prompt said "use any relevant inherited MCP ... and to act when the event requests it".
- `model=inherit` spawned a native `fork`, which inherits the parent's whole tool surface.
- `side_context_router.project()` copied the parent's full tool schemas into the model request.
- Prody evidence (40 latest actors):
  - Bash, PowerShell, Read, Grep, ListAgents and `work_node_update` were each refused as unavailable;
  - one actor queued `restart_session` successfully at 18:27:22Z (`ok:true, queued:true`).

## 3. Side consumed Work events but main never woke

- A main handoff was created only for `prioritize`/`advise`.
- `update`/`create` recorded `handoff: not_required`.
- `shareWithMain()` used `$.session.append`. The mod API documents that as a stored row, not a turn; `$.prompt.submit` is the call that starts a turn once the session is idle.
- A side `update` called `tree.update_node(note=...)`. The tree treats a note change as a scope change and moves the goal back to planning, and it never cleared `waiting_on`.
- `side_handoff.acknowledge` could only activate a goal; nothing could clear the wait the event satisfied.
- These code paths match the reported Andy timeline: n993's wait stayed after the approval, the goal returned to planning, and main stayed idle until a human prompt arrived.

# Changes

| File | Change |
| --- | --- |
| `claude_mods/ciel-work/hooks/side-intake.js` | The event's routed model is bound in the plugin's `agent.spawn` hook (`sideSpawnInput`), for primary and comparison actors. Every external event spawns the registered `ciel-work:side-intake`, never `fork`. `sideToolAllowed()` offers only router `work_status`/`work_parallel_status`/`send_message` and source tools whose name is a read/list/search/get/status or a final send/reply/ack/react operation with no mutating word. The model can no longer call `work_side_resolve` itself. Prompt rewritten for triage only. `main_wakes` go through the `work_side_wake` ledger and `$.prompt.submit`. `decisionOf` keeps only decision fields and drops nulls. |
| `side_wake.py` (new) | Durable wake ledger on the handoff: `prepare` → `submitting` with an attempt id, then `queued`, `uncertain` or `failed`. |
| `work_mode_launch.py` | `work_side_wake` added to the Work tool names (Codex approval list and schema exposure). |
| `side_context_router.py` | A primary projection keeps parent tool bytes and order, but only for the tools the actor's AgentSpec offers. Shadow comparison is unchanged. |
| `side_intake_mcp.py` | `work_side_claim` returns `main_wakes` (requested handoffs whose wake is `pending` or `failed`) and `settings.context_ready`. `work_side_resolve` marks a decision on a `handoff:` follow-up as a main reply. `inherit` no longer waits for a parent capture; it triages in isolation until one exists. `work_side_handoff` accepts `clear_wait`. |
| `side_work_decision.py` | Records target-to-root gates. A handoff (`kind` = advise / resume / p1 / priority, `gates`, `waits`, `wake.pending`) is required for advise/prioritize, and for update/create on a waiting or blocked target or a P1 goal; never for answer/clarify or a main-reply follow-up. A side `update` appends to the note without changing the phase. |
| `side_handoff.py` | `clear_wait` clears only waits that were recorded on this handoff and whose current text is unchanged, in the same version-checked transaction. Replays are idempotent; the wake is marked acknowledged. |
| `work_mcp_tools.py` | `work_side_status` reports `main_handoffs` with `kind`, `wake_state` and `wake_age_seconds`. It never includes the external original. |
| `claude_mods/ciel-work/hooks/register.js` | Main's prompt context and Stop block include `kind`/`waits` and the `clear_wait` instruction; the Stop block skips handoffs whose wake prompt is already queued. The `agent.spawn` hook applies `sideSpawnInput`. The board is refreshed right after a side actor completes. |

# Corrections found by review and the owned real run

| Finding | Evidence | Correction |
| --- | --- | --- |
| A spawn `model` such as `ciel-runtime-cielairouter-_walkie_haiku_terra` was refused by the native Agent tool schema | Owned console debug log: `Agent tool input error: issues=invalid_value`; the schema accepts only `sonnet`/`opus`/`haiku`/`fable` | The spawn omits `model`. The plugin's own `agent.spawn` hook (`sideSpawnInput`) sets the event's full routed model after schema validation. |
| Composite names passed the allowlist (`get_shell`, `read_and_post`, `reply_and_close`, `ack_then_delete_all`, `search_and_replace`) | Independent review evaluated `sideToolAllowed` directly | The mutating-word list was extended. A response verb is allowed only as the final operation, and only when the rest of the name has no mutating word. Read verbs never override a mutating word. |
| The real Walkie tools (`walkie_send_message`, `walkie_read_messages`) were rejected | Production tool names in the 2026-11-07 evidence | Connector-prefixed names are matched. The tests use the real names. |
| An answer/clarify naming a P1 or gated item created a handoff, and main's answer relayed back created another | Review reproduction; owned run: 4 handoffs in about 5 minutes | Only update/create (on a wait/block or P1), advise and prioritize create a handoff. A decision on a `handoff:` follow-up event never does. |
| The same handoff reached main three times: user-row append, Stop block, wake prompt | Owned run: main replied "Duplicate notification" twice | The durable wake prompt is the only path to main. The user row was removed; P1 keeps a system notice for the person. The Stop block skips handoffs whose wake is `submitting` or `queued`. |
| A successful wake was deduplicated only in memory | Review | New `work_side_wake` prepare→queued/uncertain/failed ledger in the board. Only `pending` and `failed` wakes are offered again. |
| A valid decision was refused for an extra key (`reason_for_advise`) or `item_id: null` | Owned run receipts (`invalid side decision fields`, `invalid side decision item_id`) | `decisionOf` keeps only the seven decision fields and drops nulls; the host still validates every kept value. |
| A wait under a P1 goal was labelled `p1` | Owned run | `resume` (gates) takes precedence over `p1`. |

# Boundaries kept

- External text is still never execution approval: `execution_approved:false`. Activation and `clear_wait` do not change a goal's approval phase.
- Main is the only authority that clears a wait. The side records the exact wait text; main confirms it at a safe boundary.
- Raw SSE never enters main. The wake text carries host-derived handoff fields only.
- The pending handoff is reported, not used as restart authority.

# Not changed in this fix

- `recover_sse_delivery` still reports input-queue delivery only. Pending main handoffs are visible through `work_side_status.main_handoffs`.
- A side actor calling `work_status` directly without `session_id` is refused with `conversation identity is unavailable`, both on Prody and in the owned run. The side already receives the complete Work projection, so this does not block decisions; it is left as observed.
