---
type: implementation-journal
title: Work parallel main-review finalization backend
description: Strict work-parallel-finalization.json saga for main-session review (approve and rework), durable review wake, rework launch recovery with an opaque launch_ref, and the host-versus-model status split.
status: implemented-uncommitted
timestamp: 2026-11-11T12:03:20Z
tags: [work-parallel, main-review, finalization, rework-launch, launch-ref, backend]
---

# Scope

Backend for replacing reviewer forks with an explicit main-session review. Files: `ciel_runtime_support/work_parallel_finalization.py` (new), `work_parallel.py`, `work_parallel_mcp.py`, `work_parallel_workspace.py`, `work_mode_launch.py`; tests `tests/test_work_parallel_finalization.py` (new), `tests/test_work_parallel.py`, `tests/test_work_parallel_mcp.py`. The mod (`register.js`), side intake, REST and web UI are owned separately ([host and UI journal](implementation.md)).

# Storage

- `work-parallel.json` keeps its schema, fields and states. New rows use only existing states (`reserved`, `submitted`, `accepted`, `done`) and the existing `resume` role. Old reviewer rows stay valid.
- `work-parallel-finalization.json` is a separate strict CAS journal: `{schema:1, version, reviews:{lease_id: ...}}`. Unknown fields or a corrupt file block writes.
- No lock spans two files. Every saga stage commits under its own store's CAS and is then marked in the journal.

# Review publication

A row is published only after (1) a durable `completion` receipt in coordination, (2) the lease is `submitted` with its slot released, and (3) a positive host `reconcile state=idle` observation, which also marks the owned workspace idle. `review_observe` takes a fresh bounded snapshot (HEAD, dirty flag, up to 100 changed files plus the total, diff stat) and a snapshot digest bound to lease, generation, artifact and receipt.

# Decision

`work_parallel_item_review` accepts calls only from main: an authenticated host with no `agent_id`. Before a decision is stored, the backend checks four things again: the artifact and plan version match the submitted lease, the receipt and fingerprint match coordination, the root approval generation and fingerprint are current, and a new snapshot digest matches. If any check is stale, the result stays pending. Rework admission (max_rework, per-priority slot, launch rate, lease bound) is checked before the decision is stored.

- Approve stages: `accepted` (ledger submitted→accepted), `workspace` (retained-workspace proof), `board` (item done with artifact evidence), `lease` (accepted→done with the cleanup proof), `archive` (coordination run archived as `main accepted`).
- Rework stages: `accepted` (a deterministic `resume` lease is reserved with no capability), `workspace` (`handoff_rebind` moves the retained worktree to the replacement and attaches it), `board` (coordination assignment for generation+1), `lease` (old run superseded; old lease submitted→done with the rebind proof), `archive` (state `rework_ready`).
- The rework result returns a dispatch intent without a token. The authenticated coordinator fork host calls `work_parallel_event event=rework_dispatch`, which stores only the token hash on the reserved resume lease and returns the token to the host alone.
- New reviewer reservations and reviewer reports are refused.

# Verification

- Evidence: [backend-tests.txt](evidence/backend-tests.txt). Backend group run alone: `221 passed, 142 subtests passed`. Router/web group, run after it: `179 passed, 3 skipped, 33 subtests passed`.
- One earlier backend-group run overlapped the router/web group. `test_unpushed_and_other_path_approval_retained` failed there because `git rev-parse HEAD` hit its 30 s subprocess limit (147 s total). This change does not touch that test or `work_parallel_workspace.py`. Run alone, the test passed 3 out of 3 times and the whole group passed. The cause was not investigated further; the only recorded fact is that it happened while both groups ran at once. The 3 skips are the existing opt-in browser tests in `tests/test_work_web_ui.py`.
- Covered: projection gating and bounds; approve and rework end to end; the replacement capability reporting a completion; denial for a worker, a fork or a bad host token; stale artifact, HEAD, untracked tree and approval fingerprint; stale finalization version; a conflicting decision; capacity and max-rework refusal leaving the result pending; a crash before every journal mark for both decisions, replayed with no duplicate generation or archive; a lost workspace attach; legacy readers (`git show 4eebfdc|5c91ea8:ciel_runtime_support/work_parallel.py`) validating the ledger after approve and rework.
- Ruff and `git diff --check` passed on the owned files. All tests ran with every `CIEL_RUNTIME_*` variable unset and a temporary config directory. No live runtime was started.

# Durable host ledgers (schema 2)

- The finalization schema is now 2. A schema-1 file is migrated in memory on read, and the next mutation writes schema 2.
- `decision_digest` covers lease, verdict, evidence, findings and rework instructions. An exact replay continues the stored decision. Any different body is refused. Later stages use only the evidence stored in the journal.
- Wake states are `pending`, `submitting`, `appended`, `queued`, `uncertain`, `failed` and `acknowledged`.
  - `review_wake prepare` is allowed only from `pending` or `failed`.
  - `record` must name the current `attempt_id`.
  - A decision commit sets the wake to `acknowledged`.
- Launch states are `none`, `launch_intent`, `spawn_denied`, `spawn_observed` and `bound`.
  - `rework_dispatch` mints a token only from `none` or `spawn_denied`.
  - Bind of a resume lease marks the launch `bound`.
- Admission treats `worker` and `resume` as one execution family. The submitted predecessor fences duplicates until rework retires it.
- The lifecycle invariant matrix rejects any impossible state/decision/stage/wake combination when the file is read.

# Admission after a durable rework decision

- Before a rework decision is recorded, `rework_limit`, a non-reworkable result and an unknown lease are refused, and the result stays pending.
- After the decision, if the result is paused, at capacity, at the launch rate or at the lease bound, the row becomes `awaiting_capacity` with `blocked:{code,message,at}`. The submitted predecessor stays as the duplicate fence.
- The host pump calls `work_parallel_event event=review_roll_forward lease_id`. The saga advances from the journal alone, and no caller argument reaches any stage.
- If any other stage fails after the decision, `blocked:{code:'stage_failed'}` is recorded and the row stays `deciding`. The same event retries it, so a row is never silently stuck.

# Launch name

- `rework_launch_intent` records the exact `dispatch_name` on the reserved resume lease. This is an existing optional field, and status shows it.
- A ledger bind of a main-review replacement is refused before it commits unless the agent was observed (`rework_spawn_observed`) or its `agent_name` matches the recorded intent name.
- Legacy resume leases without a review row are not affected.

# Legacy bind partial commit (fixed)

- Before the fix, binding a lease that had no coordination run committed the ledger bind and then raised "run is not bindable". A run that conflicted was also checked only after the ledger bind had committed.
- The coordination run is now checked before the ledger commit. A lease with no run, which an older build mints, binds ledger-only, like `release` does. A run that exists but conflicts, or an agent already bound to another live run, is refused, and the ledger stays unchanged.
- Regression tests: `test_legacy_resume_lease_binds_without_a_review_row`, `test_legacy_worker_lease_without_coordination_run_binds` and `test_conflicting_coordination_run_fails_before_ledger_bind`. All three failed against the pre-fix bind code (a temporary copy, since deleted).

# Bind CAS-window partial commit (fixed)

- **Defect** (reproduced by review-backend on `8e5781e9`): after the ledger bind committed, the coordination bind did one read followed by one CAS write. `work-parallel-coordination.json` is shared by the whole device, so any unrelated write in between from any session made that CAS raise "Work coordination changed; reread". The ledger was left `running` while the run stayed `reserved/None`. That state was permanent: a retried bind took the idempotent branch, which never bound the run, and native observation then failed the whole event with "unknown bound run".
- **Fix**, in `_bind_coordination_run` and `_run_bind_refusal`:
  - The coordination bind retries a CAS conflict up to 8 times and re-reads bindability on every attempt. A missing run (legacy lease) and a run already bound to this agent both count as success.
  - When retries are exhausted it raises "Work coordination state changed; reread". The mod's `parallelEvent` retries that wording, and the retry takes the idempotent branch, which now completes the run.
  - `native_observation` completes the same lost bind when the row's agent equals the lease's ledger `host_id`. This covers the restart path, where hydrate never rebinds.
  - A run bound to another agent is still refused.
- **Regression tests:** `test_bind_retries_coordination_cas_after_unrelated_write`, `test_exhausted_coordination_retries_raise_mod_retryable_and_replay_repairs`, `test_idempotent_bind_repairs_run_left_reserved`, `test_idempotent_bind_refuses_run_bound_to_another_agent` and `test_native_observation_repairs_lost_coordination_bind_after_restart`. All five failed against a throwaway copy of the `8e5781e9` code (hash-verified, then deleted).
- **Interleaving and byte-identity tests** (requested by team-lead):
  - `test_unrelated_update_native_in_window_leaves_both_stores_bound` and `test_unrelated_persist_receipt_in_window_leaves_both_stores_bound` run a real coordination write inside the window and check that both stores end up bound.
  - `test_real_conflict_on_fresh_bind_leaves_both_files_byte_identical` and `test_real_conflict_on_replayed_bind_leaves_both_files_byte_identical` check that a real conflict changes neither file, byte for byte.
  - Against the `8e5781e9` copy, both window tests and the replayed-bind test fail. The fresh-bind conflict test already passed there, because that path was correct before this fix.

# Crash-window residual after the CAS fix (fixed)

- **Defect**, reproduced by review-backend on `818a8752`:
  - A crash between the ledger bind and the coordination bind leaves the run reserved with no agent.
  - The worker's completion report then arrives before any repair. `persist_receipt` makes the run terminal with `agent_id` still None.
  - From then on, `native_observation` raised "run is not bindable" and aborted the whole batch, so a healthy agent-B row kept `native_status` pending.
  - This repeated until review archived the run.
- **Fix**, in `work_parallel_mcp.py`:
  1. `_worker_event` and `host_salvage` call `_repair_lost_coordination_bind` before they persist any receipt. The authenticated capability proves the ledger binding, so a run that is still reserved/None is bound to the lease's ledger agent first. A terminal run is therefore never left with agent None while the ledger has an agent.
  2. `native_observation` checks each row on its own (`_partition_native_rows`):
     - Rows with bad fields, a foreign lease, the wrong agent, a bad status or name, or a duplicate are skipped. The response lists them as `skipped:[{lease_id,reason}]`, capped at 64.
     - Healthy rows are still applied. A batch with no valid row at all is still refused and writes nothing.
     - A batch with skipped rows is not treated as `complete`, so a skipped lease is never marked missing.
     - A per-row coordination failure is skipped with one `native_uncertain` diagnostic. Consecutive repeats collapse into one entry with a count.
  3. An existing run that has left reserved with agent None (an older build's write) is reported as `observed_terminal` without raising and without adding a diagnostic. Its own exit receipt stays authoritative.
- **Regression tests:** `test_completion_report_repairs_split_before_receipt_terminalizes_run`, `test_exact_residual_repro_keeps_healthy_rows_flowing`, `test_existing_exited_unbound_run_is_observed_terminal_without_raising`, `test_unrepairable_row_is_skipped_with_diagnostic_and_batch_commits`, `test_mismatched_row_is_skipped_and_healthy_rows_commit` and `test_host_salvage_repairs_split_before_salvage_receipt`. All six failed against a hash-verified copy of `818a8752`.

# Stuck rework launch (fixed; revised per team-lead decisions)

- **Defect**, found by review-backend with the real register.js:
  - If `agent.spawn` throws and no agent ever appears, the launch stays `launch_intent` forever. The resume lease stays reserved and keeps its slot.
  - The same holds for `spawn_observed` when the agent vanishes before bind.
- **Finalization schema 4.** Schema 1, 2 and 3 files migrate in memory.
  - Each launch has `host_nonce`, the per-process nonce the mod sends with the intent. An intent without one counts as an unknown process.
  - Each launch has `manual_from`, which records where a manual request came from.
  - Each launch keeps an audit of at most 20 rows. Row kinds are `denied`, `absent`, `manual_requested`, `manual` and `present`.
  - New launch state `manual_requested`.
  - Every `spawn_denied` must end with an audit row for its own name and attempt.
- **Host marker: `_host.internal === true`.** All six launch events require it and refuse any non-null `agent_id`: intent, denied, spawn_observed, absent, manual_resolve and present. So a call from main or from a fork is refused even with forged fields.
  - Model-supplied `_host` is not trusted:
    - The MCP input schema does not advertise `_host`, but it does not forbid extra properties either (`work_parallel_mcp.py:60`).
    - `register.js:1425` strips `_host` from model tool calls.
    - `parallelCall` (`register.js:1327-1332`) builds a fresh `_host:{...observed, token}` after spreading `args`, so a top-level `args._host` is overwritten.
    - A forged `_host` without the out-of-band host token fails `host_context`.
  - `reserve` and `rework_dispatch` keep their existing `_host.fork` gate. Only the launch events moved to `internal`.
- **`rework_launch_absent`.** The host sends `_host.launch_nonce`, `_host.absent_dispatch_name` and `_host.absent_agent_id`.
  - It accepts an unresolved intent or an observed-unbound launch when the observer nonce differs from the intent's nonce, or the intent has none.
  - For an observed launch, the host must also name that exact agent as absent.
  - The effect is a positive denial: the next `rework_dispatch` mints a fresh token for the same lease and generation.
  - An identical replay is idempotent. A stale name, the same nonce, or a wrong or missing assertion is refused.
- **Manual handshake.** The router never asserts absence.
  - `request_rework_launch_manual(store, identity, lease_id, dispatch_name, expected_version=)` records the person's request and nothing else. The state becomes `manual_requested`, and no fresh token is issued.
  - At the next pump boundary the host mod checks `$.agent.list` and sends one of two events:
    - `rework_launch_manual_resolve` with an exact absence assertion, plus the exact agent id for an observed launch. The same process is allowed here.
    - `rework_launch_present` with `_host.present_dispatch_name` and `_host.present_agent_id`. This moves the launch to `spawn_observed` with proof "agent present", so the normal bind proceeds.
- **Late reappearance.** After absent or manual resolution, the vanished agent is refused at bind. A fresh dispatch binds exactly once, and the old capability is revoked.
- **Real-backend integration.** main-review-mod-ui's `tests/test_work_mod_real_backend.py` fails 4 of 7 scenarios against this backend. register.js still sends `{fork:true}` on the launch events and does not send `launch_nonce`. That integration test is not part of the router/web group counted above. The mod must switch to `{internal:true, launch_nonce}` before a release.
- **Regression tests.**
  - Eight new tests all fail against hash-verified copies of `430aaf4b` and `4f484d34`:
    - forged main `_host`
    - fork-originated call
    - model-supplied `_host`
    - absence refusal paths
    - manual request then absent
    - manual request with the agent present
    - observed launch that never binds
    - observed manual request needing the exact agent
  - Earlier tests that still pass: restart, then a single bind; late agent refused; pre-nonce intent; bounded audit; schema migration.

# Opaque launch reference, status split, review pump gate (schema 5)

Per team-lead's decisions. Changed files: `work_parallel_finalization.py`, `work_parallel_mcp.py`, `tests/test_work_parallel_finalization.py`. register.js, the REST controller and the web UI were not edited.

- **launch_ref.**
  - Every launch row has `launch_ref`, a 32-hex value from `secrets.token_hex(16)`.
  - A new value is minted when the launch enters `launch_intent`, enters `spawn_observed`, or is restored to `spawn_observed` by `rework_launch_present`.
  - It is kept through `manual_requested`.
  - It is cleared to null on `spawn_denied` (denied, absent or manual) and on `bound`.
  - The validator requires a non-null ref exactly while the state is `launch_intent`, `spawn_observed` or `manual_requested`.
  - An identical `rework_spawn_observed` replay returns without a write and keeps the ref.
- **Migration.** Schemas 1 to 4 migrate to 5 in memory.
  - An unresolved launch without a ref gets one derived from a SHA-256 of its row identity and launch fields. Reads therefore return the same ref until the next write persists it.
  - `test_schema_four_unresolved_launch_gets_a_stable_reference_until_written` checks this.
- **Manual request.**
  - `request_rework_launch_manual(store, identity, lease_id, launch_ref, *, confirmed, expected_version)` takes the reviewed worker lease id (the review row key the browser shows) and the row's current ref.
  - It refuses a malformed ref ("exact rework launch reference required"), a stale or other-row ref ("stale rework launch reference"), and a resolved or bound launch ("not unresolved or observed-unbound").
  - A repeat with the same ref is idempotent and causes no version bump.
  - It returns `{lease_id, state, attempt, launch_ref, finalization_version}`.
- **Host events.**
  - The host events `rework_launch_manual_resolve` and `rework_launch_present` now also require `args.launch_ref`.
  - The menu event `rework_launch_manual_request` takes args `{lease_id: review lease, launch_ref}` with `_host {internal:true, user_confirmed:true}`.
  - `rework_launch_status` rows include `launch_ref`.
  - `rework_launch_status` still lists every unbound launch, including `spawn_denied` rows with a null ref. The register.js pump (2070-2083) dispatches attempt+1 from those rows. Only a row with a non-null ref accepts manual request, manual resolve or present.
  - The `_launch_view` host event results include `launch_ref`.
- **Public view.** Model-facing and public review rows show `launch` as `{state, attempt}`, plus `launch_ref` while the launch is unresolved. The ref is not a capability: it is useful only with the person's confirmation through the host's internal path.
- **Status split.**
  - `project(..., host_view=False)` is the default.
  - `work_parallel_status` uses `host_view=True` only when `_host` passes `host_context`'s HMAC check and has no `agent_id`.
  - Event and configure results use `host_view=True` only when `_host.internal is True` and there is no `agent_id`.
  - Host views keep the lease `dispatch_name`/`agent_name` and the fork `agent_name` that register.js reconciles with (1422, 1649, 1795, 2213).
  - A missing or wrong token, a worker `agent_id`, or a non-dict `_host` gets the redacted view, not an error.
  - If the finalization file is unreadable, the redacted view hides the names of every resume lease in the scope.
- **Review pump gate.** `review_wake` and `review_roll_forward` require `_host.internal === true` and no `agent_id`, with the refusal "verified mod-internal review host required". Forged-main, fork and empty-host calls leave the ledger, coordination, native and finalization bytes unchanged.
- **Verification.** All runs had `CIEL_RUNTIME_*` unset and used a temporary config directory. Groups ran serially.
  - Backend group: `231 passed, 193 subtests passed`.
  - Router/web group: `184 passed, 4 skipped, 38 subtests passed`.
  - Ruff and `git diff --check` are clean.
  - The 8 new or changed tests fail on hash-verified copies of `7a1fdc0a`/`6b3ab438`, with 17 failures including subtests.
- **Real-backend integration.** `tests/test_work_mod_real_backend.py` against the unchanged register.js (`acaa2c53`) and controller (`bf199eba`): 6 failed, 5 passed.
  - A scratch copy of register.js was made under `.local/probe-mod`; the repository file was not touched. It differs only by `{internal:true}` at the two `review_wake` calls and the `review_roll_forward` call. With that copy the run is 2 failed, 9 passed.
  - Both remaining failures raise "exact rework launch reference required" from the old `_parallel_launch_resolve` and the pane's manual request, which still send `dispatch_name`.
  - Probe: `.local/probe_real_backend.py`. Output: `.local/probe-real-backend.txt`.
- **Review material.** `.local/main-review-delta-8/` holds the byte-exact before-files `7a1fdc0a`/`6b3ab438`/`08a7c28c`, diffs to final from those and from `d2a748a7`/`7a650de7`/`6f3c8be3` (delta-7), and `verify_delta.sh`, which rebuilds the final files and checks their SHA-256.

# Known limits

- The web console's manual-request button and the host pump's handling of `manual_requested` belong to main-review-mod-ui. The backend exposes `request_rework_launch_manual` and the two host events.

- `_public_reviews` in REST still recomputes `changed_count` from the clipped list. The backend now projects the true total as `workspace.changed_count`. main-review-mod-ui owns that fix.
