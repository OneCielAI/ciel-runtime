---
type: requirement
title: P0 immediate fork and per-priority LLM model for Work forks (2026-11-14)
description: The user's new requirement, received 2026-11-14 about 02:22Z. P0 items always get a fork session at once, and each priority P0-P5 has its own LLM model setting, on the web dashboard, ciel-runtimectl and the initial settings screen. Facts checked so far and open questions.
tags: [work-parallel, priority, fork, model, settings, requirement]
timestamp: 2026-11-14T02:24:44Z
---

# Requirement (user's words)

"P0 우선순위로 들어오는것은 무조건 포크세션을 즉시 추가하는것으로..추가. 환경설정에서 p0-p5까지 설정이 따로있는데 각각의 우선순위에 LLm 모델을 별도로 정할수있게.. 이러한 환경설정이 웹대시보드, ciel runtime ctl과 초기환경설정화면에서 다 나올수있게."

1. An item with priority **P0** always gets a fork session **immediately**.
2. Settings exist per priority, **P0 to P5**. Each priority can have its own **LLM model**.
3. These settings appear in the **web dashboard**, **ciel-runtimectl** and the **initial settings screen** (the menu). The standing UI rule adds REST: [UI exposure](requirement.md) requires menu, web, REST and ctl.

# Facts checked by the lead (code, 02:24Z)

- Priorities today are `P1`–`P5` only: `work_parallel.py:68` `PRIORITIES = ("P1", "P2", "P3", "P4", "P5")`, `work_plan_tree.py:44`, plus the board validation at `work_plan_tree.py:180/270/355/412` ("priority must be one of P1, P2, P3, P4, P5"). P0 does not exist yet.
- Per-priority fork concurrency limits exist: `DEFAULT_PRIORITY_LIMITS = dict(P1=5, P2=3, P3=1, P4=1, P5=1)`, `MAX_PRIORITY_LIMIT = 16` (`work_parallel.py:71-72`). The policy validator requires exactly the P1–P5 key set (`:118-119`). The menu edits them in `work_parallel_menu.py:117/180`.
- Per-priority model settings do not exist.
- Forks inherit the main model today. In routed mode `CLAUDE_CODE_SUBAGENT_MODEL=inherit` with `..._FORCE=1` is set for non-Anthropic providers (`claude_environment.py`, build). The SSE side actor contract ([memory note]) says the side actor is a native fork that inherits the main model, with no separate side-model selection.
- Fork requests carry `x-claude-code-agent-id` and the main session id (router-fork-turn-watch M1, measured).

# Not yet known (to be established, not assumed)

- Whether Claude Code 2.1.325's `$.agent.spawn` with `subagentType:'fork'` accepts a model override, and what model the fork's request then names.
- Whether the router can route one fork's requests to a different upstream model by agent id, and what breaks when an inherited conversation (thinking blocks, cache) moves to another model or provider.
- Whether adding `P0` and per-priority models to `work-parallel.json` or the board breaks old-version readers (57dbfb3) that validate the exact key and value sets.

# Beta capability ruling (2026-11-14T07:07:07Z)

Native fork per-priority and side-fork model selection is NO-GO on the current host. First-request bridge evidence `work-fork-first-request-bridge-design.json`, SHA-256 prefix `82d6ff19106e1ff4`, shows the first fork request precedes durable bind by at least 0.428 s and carries no authenticated dispatch/lease/generation/priority identity. Sole runtime Work capture ended with no fork request and cannot establish Assignment JSON position. Prompt/worker-token parsing and later-request-only remap are forbidden.

For this beta, all four surfaces show the settings unsupported/disabled and forks inherit main. Historical values remain inert; there are no active selectors and no silent runtime effect. Required future host capability is an authenticated first-request dispatch/launch field, preminted native agent id, or guaranteed pre-request correlation callback.

# Open user decisions

See the lead's question to the user (2026-11-14), recorded in `docs/journal/2026/11/12/features/work-parallel-open-items/log.md`.

# User answers (2026-11-14, about 02:28Z)

1. **P0 immediacy:** "제한·일시정지·승인 모두 무시". A P0 item gets a fork at once, regardless of the per-priority limit, a paused or disabled parallel policy, or a pending approval.
2. **Which forks:** the user did not choose an option. They answered "사이드 액터도 별도 모델 설정이 있는데 니가 또 잘못알고 있는거 같은데??", meaning the side actor also has its own model setting.
3. **Model range:** "현재 프로바이더 모델만". Choices come from the current provider's model list only; the default is to inherit the main model.
4. **Release:** "이번 beta에 포함".

# Lead's check of the side model claim (02:31Z)

- On the current worktree and in the installed beta 57dbfb3, `side_intake_settings.py` lists `model` in `RETIRED`: "Native forks always inherit main. Historical overrides remain harmless on disk but are never projected to runtime or exposed by active settings UI". `observatory.test.ts:128` asserts that the `work_side_intake_model` setting is absent. The live `config.json` has no `work_side_intake` key.
- History:
  - 11-07: `side-fork-lifecycle/fix.md` "preserves selectable same-provider models".
  - 11-11 07:52Z: the lead asked "Claude Code의 진짜 fork는 메인 모델을 강제 상속해 별도 사이드 모델 선택이 불가능합니다. 기존 사이드 모델 선택 옵션은 어떻게 할까요?", and the user answered "항상 진짜 fork".
  - Commit 34966b6 then retired the setting.
- The lead's premise, "a true fork cannot use another model", was **never measured**. The user's statement is right that the side model option existed. The lead's earlier statement that "the user decided" no side model selection was a reading of an answer to a question built on that unmeasured premise.
- Ruling: the side model setting is reopened. It is restored as part of this requirement, unless measurement shows a fork cannot run on another model. In that case the evidence goes to the user before anything is removed.

# Measurement required before design (M-P1)

Measure on Claude Code 2.1.325, with an owned run, a stand-in, and the router:
- What model does a `subagentType:'fork'` request name? Can `$.agent.spawn` accept a model argument, and does the engine honour it?
- Can the router route an agent-id-tagged fork request to another model of the same provider, and does the inherited conversation survive that model change (thinking blocks, cache, tool ids)?
- Check the shipped bundle first. Use the claude.exe slot only if the bundle does not settle it.

# Lead rulings on continue-p0 Phase 1 (2026-11-14, 02:50Z by clock)

**M-P1 accepted.** It was proven from the bundle code at the offsets continue-p0 cited, not from a run. A native fork in Claude Code 2.1.325 always runs on main's model:
- `$.agent.spawn` accepts `model`, but forks force `inherit` (328590628, 320980672, 321938229);
- `CLAUDE_CODE_SUBAGENT_MODEL` / `_FORCE` cannot change a fork.

The only remaining way to give a fork a per-priority model is router-side remapping keyed on the fork's `x-claude-code-agent-id`. Its viability is unproven.

**Q4, probe P-R1: GO, bounded.**
- Use an isolated router, a temp config dir and a spare port. Copy only the current provider's entry and key into the temp config; never print the key.
- Spend at most 12 live requests with small conversations (no large context).
- Send a main-shaped request on model A that returns thinking plus tool_use. Then send its fork-shaped continuation, with an agent-id header, to model B of the same provider.
- Record status, thinking-block handling, tool-id round-trip and cache usage for each model family offered.
- No claude.exe.
- ~~The user's current provider is `nvidia-hosted` (live config, read-only).~~ **Corrected at 02:55Z by file time (lead's error; first stamped "03:01Z", which was ahead of the clock).**
  - The lead had read the shared legacy `%APPDATA%\ciel-runtime\config.json`. The router reads the workspace config instead (runtime_paths.py:152-163).
  - For the project workspace that was digest `bd5f642b3583`, with `current_provider=cielairouter` and `current_model=_Walkie_Sol61-Opus55-Fast`. Measured by the lead with `workspace_digest()`, read-only, no key printed. continue-p0 reported the same at 02:58Z.
  - **P-R1 probes `cielairouter` only.** That is option (a), the user's "현재 프로바이더 모델만".
  - The budget stays at 12 requests. Cover the Claude families plus one non-Claude family offered by the provider.
  - Copy only the cielairouter entry and key into the temp config.
  - If the provider offers fewer than two usable models, report that instead of switching providers.

**Q1, approval: P0 ignores approval, per the user's answer** ("제한·일시정지·승인 모두 무시").
- A P0 item dispatches even when its root is in planning, and even when `work_plan_approval_ask=ask`.
- The assignment records that it was dispatched as P0 without approval. The report/accept re-check must not reject it for missing approval. It still rejects it for a changed item, a changed lease, a changed artifact or a wrong owner.
- The edit gate lets that P0 fork write in its own workspace.

**Q2, limits.**
- P0 ignores the per-priority limits and `launches_per_minute` ("즉시").
- `MAX_LEASES = 100` (work_parallel.py:26) is a storage bound on the ledger, not a concurrency setting. It stays. If P0 hits it, the refusal is visible on all four surfaces with its reason. This is reported to the user as a known bound.

**Q3, old readers: an old-reader-safe encoding is required.** This is the standing contract: "work-parallel.json의 구버전 호환 스키마·필드·상태는 바꾸지 않는다". The measured hazard is that a 57dbfb3 router on the same device quarantines the whole board, or blocks every parallel write. That would hit this machine's live 57dbfb3 sessions and other beta machines.
- P0 must not appear as a priority value in the board, the ledger, leases, receipts or the side inbox that 57dbfb3 reads.
- Use P1 in those files, plus a P0 marker in a new sidecar, or a field that old readers ignore and that a strip by an old writer cannot turn into wrong behaviour.
- continue-p0 proposes the exact encoding, with a 57dbfb3 read/write round-trip test, before implementation.

**Q5, side model: one side fork model setting** (`work_side_intake_fork_model`, default `inherit`, current provider only). This restores the setting the user described. `by_priority` was not requested.

**Settings storage: approved as proposed.**
- Per-provider `work_parallel_models` in config.json, never in the ledger.
- A stored id missing from the provider's list resolves to inherit, with a visible status.
- All four surfaces go through work_live_settings.

**Implementation order.**
1. P-R1 first. Only if P-R1 shows that same-provider model switching works does the router remap get implemented.
2. If P-R1 fails, the per-priority model part cannot run on native forks. The evidence goes to the user before anything else is built for it.
3. P0 immediacy does not depend on P-R1 and proceeds now: the encoding proposal, then the backend, pump, UI and ctl changes.

# Lead ruling on the Q3 encoding proposal (2026-11-14, 03:01Z by clock)

The proposal is progress.md Step 5. Its header says 03:02Z, but the file time is 02:58Z, so the header was ahead of the clock.

Verdict: **CHANGE.** Items 2 to 6 are approved with the conditions below. Item 1 must change.

**Item 1 (board marker): change required.**
- The measured fact: a 57dbfb3 writer strips an unknown board field on its next ordinary write. continue-p0 measured this in a real old process.
- 57dbfb3 sessions share the workspace board on this machine. So any old session's progress write would silently turn a P0 root back into P1.
- That breaks the user's requirement that P0 always forks immediately ("무조건 … 즉시", "제한·일시정지·승인 모두 무시").
- Required: the P0 root fact must survive an ordinary old write.
- Any explicit priority change, by any writer, must end P0. That includes an old writer going P1 → P2 → P1.
- continue-p0 proposes the exact mechanism, for example a sidecar root entry plus a guard that detects a priority change. Its tests must cover:
  - an old progress write with no priority change, after which P0 holds;
  - an old priority change, after which P0 ends and stays ended;
  - delete and recreate, after which P0 is not inherited.

**Item 3 (ordering): approved with two additions.**
- Reserving the P1-labelled lease must bypass P1 capacity at reserve time, before the sidecar exists. State the exact parameter and the read that authorizes it.
- State what happens on a crash after the reserve and before the sidecar commit: which existing recovery path reclaims the unbound lease, and the test for it.
- In that window, new-code admission counting the lease as P1 is accepted as conservative.

**Item 5 (approval): approved with required old-reader assertions.**
The round trip must show what 57dbfb3's coordination validator and reconcile do with an assignment that exists while the root is in planning:
- not corrupt;
- not a blocked write;
- no revoke or cancel of the P0 lease.

If 57dbfb3 revokes it, report the measured behaviour before implementing. Do not work around it silently.

**Item 7 (round trip): add the following.**
- A 57dbfb3 pump does not dispatch the P0 item a second time while the P0 lease is active.
- The sidecar stays byte-identical across every old write.

**Items 2, 4 and 6: approved as written.**
- P0 counts toward MAX_LEASES=100.
- P0 bypasses enabled/paused, P1 capacity and launches_per_minute.
- P0 stays off the ordinary rate history.
- No P0 is stored in the side inbox structure.

**Ownership after the final GO:**
- continue-work-backend: the Python encoding, the sidecar, admission and the approval bypass.
- continue-work-pump: register.js admission, with pump's F-X3-1 comment fix folded in.
- continue-p0: settings, the four surfaces and the compatibility harness (real 57dbfb3).
- continue-ctl: review.

All pinned edits go scratch → announce → GO.

# Gate result recorded (2026-11-14, 03:01Z by clock)

backend's faef7e7b gate is in `.local/mod-gates/run-faef-021353/`. The lead read meta.txt and the tails:
- write_gate: 132 collected, 132 OK, ended 02:15:27Z.
- real_backend: 110 collected, 110 OK in 1292 s, ended 02:37:02Z.
- Snapshot VERIFY after: 1149 listed, 0 changed, 0 unlisted.
- Beta extract VERIFY after: 0 changed lines (sums 840fa7d5).
- 0xC0000142 hits: 0.

register.js faef7e7b is the gated candidate before the P0 change.

# Lead ruling on backend Step 161 facts (2026-11-14T03:06Z)

**Confirmed pre-existing crash window.** An owned lease that is reserved but never bound cannot be reclaimed by the dead-owner orphan path: `orphan_release` fails validation with `unbound worker`, while only same-scope `release_unbound` removes it. Startup intentionally retains an unknown unbound reservation unless it finds the exact durable dispatch name. The isolated probe was `.local/mod-gates/p0-prep/orphan_unbound_probe.py` on snap-faef-021353; VERIFY remained clean.

Consequence for Q3 item 3:
- reject the proposed `reserve -> P0 sidecar -> spawn` order;
- revise to a durable P0 launch intent before reserve, then reserve against that exact intent, or prove an equally safe automatic recovery that cannot reclaim today's intentionally retained unknown reservations;
- test crashes at every boundary.

**Confirmed approval-generation mismatch to measure.** A run row requires approval_generation >= 1, while a root that never entered executing has generation 0. The 57dbfb3 compatibility run must measure its validator/reconcile behavior for this exact case and prove no corrupt/block and no revoke/cancel. A failure blocks implementation pending a revised design.

**P0 continuation scope.** While the root remains effectively P0, all of these bypass enabled/paused, per-priority capacity and launches_per_minute:
- initial reserve;
- rework replacement;
- stranded recovery;
- reacquire.

They still obey MAX_LEASES=100 and every exact-identity, owner, token and duplicate fence. An explicit priority change ends P0 for later continuation.

**Final-semantics amendment:** in this chronological sentence, “explicit priority change” means an authenticated **new-code priority-control operation** through the unified priority service. Legacy 57db or direct board writes only change the canonical P1-P5 value; they do not end effective P0. Surfaces show the canonical/effective mismatch with the mixed-version canonical-drift warning. See the final semantic ruling below.

# Lead ruling on P-R1 and the old-writer blocker (2026-11-14T03:58Z)

## P-R1: PASS

The lead independently read `evidence/pr1-result.json`.
- Provider: cielairouter only.
- Live requests: 9 of the 12-request bound.
- No claude.exe.
- Source `_Walkie_Sol61-Opus55-Fast` returned signed thinking plus tool_use.
- A fork-shaped continuation carried the agent header, the unchanged signed thinking block and exact source tool id. It returned 200/end_turn on:
  - Fable 5.2;
  - Opus 5.5;
  - Sonnet 5.5;
  - Haiku 5.5;
  - non-Claude `cx/gpt-6.1-sol`.
- `cx/gpt-5.5` returned 400 account unsupported. This is model availability, not a model-switch protocol failure.
- Cache usage is recorded for each target in the evidence.

This proves router-side same-provider remapping is viable for the measured model families.

## Old-writer priority-cycle blocker and corrected requirement

continue-p0 proved in a real 57dbfb3 process that the final legacy board state after an explicit old P1→P2→P1 cycle can be byte-identical (apart from the document clock) to an ordinary old progress/status sequence. A passive sidecar plus board-state reader cannot distinguish them.

The lead's earlier requirement that **every** explicit priority change by **any** writer must end P0 was lead-added scope, not a user requirement. It is withdrawn.

**Final semantic ruling:**
- The strict-CAS P0 sidecar is authoritative for a marked root until:
  1. a new-code explicit priority-change operation clears the P0 root marker;
  2. the exact root terminates;
  3. the exact root is deleted/recreated (id/created_at mismatch).
- A 57dbfb3 write cannot clear P0. It may display or canonicalize a legacy P1–P5 value, but new code continues to project and execute the marked root as P0.
- On all four surfaces, mixed-version status explains that legacy writers see canonical P1 and cannot change effective P0; the new-code priority operation must be used.
- Do not add filesystem interception, polling inference, an old-build change or a compatibility subscriber.

The discovered `work_plan_store.subscribe_work_plan_changes(callback)` is rejected for this release:
- a subscriber cannot be installed in an already-running 57dbfb3 process;
- adding a compatibility hook/module to old processes expands scope;
- it does not repair current mixed-version processes.

## Accepted 57dbfb3 measurements

From `evidence/old-reader-p0-roundtrip-result.json`:
- planning root generation 0 plus run generation 1 validates;
- reconcile writes and leaves the run unchanged;
- `_approval_current` is false, but neither that nor the worker report refusal revokes/cancels the run or lease;
- beta reserve and reserve_next do not mint a second lease while the P0 lease is active;
- the sidecar remains byte-identical across all measured old writes;
- delete/recreate does not inherit P0.

## Revised encoding requirements

continue-p0 now writes the final lifecycle text with these bounds:
- root marker and launch intents in strict-CAS `work-parallel-p0.json`;
- exact launch intent before reserve;
- reserve validates and consumes that intent as its P0 authorization;
- exact lease identity is bound into the intent before spawn;
- crash recovery for intent-only, intent+lease and spawned/bound boundaries without reclaiming unrelated unknown reservations;
- new-code priority changes clear the marker, with a durable roll-forward operation across the board and sidecar stores;
- old round trip preserves the sidecar and effective P0, including an old priority cycle.

The router mapping design must also settle the first request before ledger bind: derive desired model from an authenticated launch identity available in the first fork prompt/request, validate it against the P0 launch intent/config/current-provider catalog, then pin agent-id→model for continuation.

# Lead ruling on revised-encoding.md bbdb550d (2026-11-14T04:16Z)

**Verdict: GO with these required changes.** Implementation proceeds in scratch only. Every pinned file goes scratch → announce → lead GO.

**Approved as written:**
- Sections 1–4.
- Section 6, the intent-before-reserve lifecycle and its crash table, including the `record_assignment` change that makes an identical-manifest replay idempotent, with its own test.
- Sections 7, 8 and 10.
- The exact-name adoption rule. Unknown reservations stay untouched.

**Required changes:**
1. **Setting P0 contradicts section 1.** Section 1 says a P0 root is canonical P1. Section 5's last paragraph leaves an existing root's canonical value "as is".
   - Setting P0 on an existing P2–P5 root must also write canonical P1.
   - Specify the order and crash rows for setting P0, as for clearing it. The marker is written first and is authoritative, then the board is written P1.
   - A crash between the two leaves effective P0 plus the "canonical changed" status. It never leaves a silent non-P0.
2. **The direct data path is not covered.** With `launch_data_path=direct`, fork requests do not pass through the router (`runtime_launch.py:653-659`, `launch_data_path.resolve`), so the section 9 remap cannot apply there. Measured: this workspace's cielairouter config has no `launch_data_path`, so it currently uses the router path.
   - On the direct data path, the per-priority and side-fork model settings are not applied.
   - All four surfaces show "per-priority models apply only on the router data path; this launch uses direct, so forks inherit main's model".
   - No silent behaviour. This limitation is reported to the user.
3. **The clamp claim needs evidence.** "The router clamps to the target's catalog limits as it already does" must be cited with file:line or tested. Cover a fork whose conversation exceeds the target model's context window, and a max_tokens above the target's limit. Either result is acceptable if it is real: a clamp, the existing context-window recovery (claude_router.py:582-633), or a visible provider refusal. A silent fallback to main's model is not acceptable.
4. **Which process serves a fork's MCP report** must be settled before section 8 is called safe. Read it from the launch code: each session's `--mcp-config` (runtime_launch.py:793) and the router MCP that a native fork inherits. If code cannot settle it, the compatibility harness measures it.
5. **No secret logging.** The router reads `worker_token` from the fork's assignment block only to hash-compare it with `lease.report_hash`. The token, the prompt text and the assignment block are never logged, persisted or put in the pin map. The pin map holds agent_id, model, lease_id and pinned_at only. Add a test asserting that the router log and the status hold 0 tokens.
6. **Cache cost.** P-R1 shows that the cache is per model. A remapped fork pays a cache write on its first request. Show this fact once in the settings help text on all four surfaces; it is not a blocker.

**Open item 1 (Work fork Assignment JSON position):** one owned capture is approved. It is scheduled in the claude.exe slot after retry's amendment 3b. Same safety conditions as retry's runs. A run ends after one Work fork request; no rerun without a ruling.

**Ownership:**
- continue-work-backend: sidecar store, intents, reserve bypass and admission, approval-only bypass, record_assignment idempotency, crash tests.
- continue-work-pump: register.js spawn/reconcile with intents. It folds in F-X3-1 from `%TEMP%/wp-open-items-7c3e/x3/scratch2`.
- continue-p0: settings, four surfaces, the router remap (claude_router.py), the mixed-version and direct-path status, the real-57dbfb3 compatibility harness, and the open-item-1 capture.
- continue-ctl: independent review of each announcement.

# Lead rulings during P0 scratch implementation (2026-11-14, recorded 04:24Z by clock)

## Sidecar priority operations

backend found that an active RootMarker alone cannot tell two states apart:
- an incomplete activate-P0 operation, which must still canonicalize the board to P1;
- a completed activation later edited by 57dbfb3, which must not be recanonicalized.

Ruling:
- Replace `clears` with bounded `priority_ops`.
- `PriorityOperation = {operation_id, kind: activate_p0|set_priority, scope, root_id, root_created_at, target_priority, state: prepared|board_committed|done|cancelled, board_version_before, created_at}`.
- `activate_p0` targets P1. `set_priority` targets `""` or P1–P5.
- RootMarker may carry the operation id. Its semantic states stay active, cleared and ended.
- Existing-root activation:
  1. One sidecar CAS writes the active marker and `activate_p0/prepared`.
  2. Board CAS sets P1.
  3. The operation moves to `board_committed`, then `done`.
- After `done`, a later old canonical edit is visible but not effective, and is not recanonicalized.
- Priority change:
  1. One sidecar CAS writes the cleared marker and `set_priority/prepared`.
  2. Board CAS sets the target.
  3. The operation moves to `done`.
- New P0 root:
  - Create it with a pre-minted root identity, canonical P1, as a non-dispatchable planning root.
  - Then commit activate_p0 before any new-code pump runs.
  - A crash between the two surfaces "P0 activation incomplete" and never dispatches as P1.
  - Retry with the same external operation identity. If the add API cannot carry that identity, report the gap before coding.
  - **Superseding beta amendment:** the four new-root lines above are chronological history only. Real 57dbfb3 evidence showed old writers can clear waits, approve, reserve and remove/recreate roots, and reserve-before-board was NO-GO. Accepted beta contract `new-root-p0-operation.md` SHA `baa7c6043c5ba98fc2d2de731938a20d3f4457c9794c07b2c77563f3a24df878` refuses new-root P0 before any write; existing-root P0 activation remains supported.

## Gate plugin planning write

pump found that `ciel-work-gate/hooks/register.js:219-229` denies every Work fork file write while the board is planning, so the P0 requirement cannot hold without a gate delta. The gate pin is 157fc95c.
- **Ownership.** pump owns the gate delta. backend owns a read-only projection on the bound lease view, for example `planning_write_authorized: true`, `authorization: "p0_dispatch_without_approval"`.
- **When the projection is set.** Only when the exact lease's sidecar intent is reserved, spawning or bound, and scope, root, root_created_at and item match. The gate never reads the sidecar directly and never trusts the prompt.
- **What the gate skips.** Only the planning denial, and only for an authorized lease. Bound-state, worktree projection, cwd confinement, target and shell checks are unchanged. An unreadable state is fail-closed.
- **Tests:**
  - a P0 planning write inside the worktree is allowed;
  - a non-P0 planning write is denied;
  - a P0 write outside the worktree is denied;
  - a stale or mismatched intent is denied;
  - a missing projection fails closed;
  - real-load coverage.

## Correction: exact-schema priority operation reuse (2026-11-14, recorded 04:51Z by clock)

backend found an exact-schema solution. This supersedes the 04:24Z requirement to add `PriorityOperation.kind` / `priority_ops`.

The existing exact operation record (currently named PriorityClear / `clears`) is reused in both directions. Direction is encoded by the RootMarker state written in the same sidecar CAS:
- active RootMarker + prepared operation targeting P1 = activate P0;
- cleared RootMarker + prepared operation targeting the requested priority = end P0 / set priority.

Required invariants:
1. RootMarker and the prepared operation are one sidecar CAS.
2. At most one nonterminal operation exists for the exact `(scope, root_id, root_created_at)`.
3. Recovery infers direction only from that paired marker state, never from board priority.
4. A concurrent operation is an exact idempotent replay or is refused.
5. Root identity is verified before every board write.
6. Active+target P1 rolls the board to P1. After done/prune, later old P2–P5 edits are never recanonicalized.
7. Cleared marker rolls the board to the requested priority and P0 stays ended through every crash point.
8. Prune only after `board_committed` / `done` is durable.
9. A corrupt or missing marker/op pair fails closed, is surfaced and does not mutate the board.
10. Tests cover activate crash rows, clear rows, replay, conflicting operation, old edit after prune and corrupt pair.

The serialized sidecar schema therefore stays exact and unchanged.
