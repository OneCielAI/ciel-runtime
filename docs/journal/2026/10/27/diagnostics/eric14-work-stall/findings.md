---
type: Diagnostic
title: Work queue handoff and panel bounds
description: Runnable queued Work could be selected by continuation but blocked by active-slot permission; panel work tracked separately.
tags: [work, continuation, queue, ui]
timestamp: 2026-10-27
---
# Scope

User screenshots #15/#16: unfinished tasks, queued goals, and excessive Work rows obscuring other information. User approved source changes plus local/nightly release. No remote installation, queue/config mutation, session injection or restart is authorized by that local/nightly instruction.

# Verified local regression

`dc9914c` introduced a disagreement: `continuation()` chooses the first runnable queue goal when the active goal has only waiting/blocked leaves, but `select_root()` forbids changing any unfinished active root without `user_requested=true`. The Stop instruction also said to select only under the user-priority rule. Reproduced by a new unit test before code changes: `queue-unit-before.txt` (39 existing pass, 1 new error).

The narrow fix permits `work_select` without `user_requested` only for `continuation.next.root_id`. It does not permit interrupting a runnable active goal, choosing a later runnable goal, bypassing an urgent hold, clearing waits, or inheriting execution approval. Root ordering and per-root phases are retained. Stop now explicitly directs the model to select the queued candidate, plan and execute it if needed.

Python Work suite before final UI integration: 141 pass (including MCP CAS and approval); Python 3.14 queue tree: 42 pass; mod suite after queue instruction change: 55 pass. Fixtures are scripted tool calls through real native CLI, not evidence of remote model compliance.

# Actual CLI comparison

- Source `work-queue-yield-d6da1c8b`: Claude 2.1.311, 10/10 checks, one user prompt. Waiting active goal -> Stop continuation -> select first runnable queued goal without user flag -> execute -> Write -> verified done -> waiting goal resumes. Live router identities before/after identical.
- Previous installed `eec7d69`, strict fixture `work-queue-yield-d8c6a127`: continuation arrived but `work_select` was refused; no queued Write or completion. `baseline-queue/result.json` and screenshot retain this failed baseline. An earlier baseline fixture unconditionally advanced after selection failure and created misleading later success flags; its log `queue-baseline.txt` is retained as invalid fixture evidence, not success proof. The strict fixture gates all later operations on a successful selection.
- Strict-gated source rerun `work-queue-yield-e0f5e286` passes 10/10 with one user prompt; `source-queue-exact/` retains results and screenshot. Installed final proof remains pending.
- Egress sentinel rejected updater CONNECTs to downloads.claude.ai and github.com. This is not an OS sandbox or proof of all possible network isolation.

# Screenshot session identification limit (correction)

`.14:1004` currently reports host DESKTOP-GBJII5F. Its AAP database maps agent21 to Ara (`u21`), agent23 to Eric (`u23`). The screenshot instead reads `Eric Ai · agent 21` and pool `aap-pool-greenland`, absent from the current `.14` container names. No Eric processes, runtime installation or Claude transcript were found at the `.14` mapped Eric home/profile. The active title `88742 P2` did not appear in bounded tails of the `.14` profiles inspected. Therefore screenshot Eric cannot be assigned a runtime version or root cause from these observations.

Probes p1-p6 initially followed agent21 and inspected **Ara, not Eric**. Those results must not be used as Eric evidence. Ara on `.14` is running `eec7d69`; that is only Ara evidence. Work state directory access was denied; no ACL elevation or bypass attempted. An overly broad read-only directory lookup timed out; subsequent probes were bounded.

Screenshot #16's active goal text `90981` and queue `83576` match Kobe transcript text on `.14`. Its package is `5054af2`; sampled Stop continuations pointed to active-root leaves n189/n192, not a queued root. Thus the latest selection regression cannot simply be claimed as its cause. Screenshot #16 shows a running PostToolUse hook, not by itself a completed idle turn. Exact stalled-turn evidence remains unproven.

# UI follow-up

Requested: separate progress, queue and completed tabs; bounded above-prompt height preserving space for teammates; all rows reachable through pages and on-demand inspector. Read-only review found the enabled AbovePrompt handler did not call `next(e)` and generated unbounded rows. Preserve native returned content as well as bounding Work rows. Previous installed `eec7d69` actual CLI UI fixture `work-panel-tabs-475f4af8` reproduced both completed rows appearing in the initial band and a missing synthetic companion AbovePrompt row (`baseline-panel/`). Full inspector still works, and the display checks did not mutate the board. The initial synthetic companion fixture used an unsupported `site` filter / `ui.elements`; it was corrected to the observed `component` / `ui.resolve(e)` API. Therefore its earlier absence alone is not a valid composition proof. The companion also needed `hooks/hooks.json` to register its module; final experiment `work-panel-tabs-0492d6f7` includes that manifest and passes 11/11 on an isolated runtime copy with native content as an unsized sibling. Final repository source/installed reruns still required. A synthetic companion proves render composition only, not actual native teammate UI placement. Initial source UI fixture `work-panel-tabs-1724c830` exposed a real host validation failure: `engine node under a Box with prop "height"`; the host skipped the AbovePrompt hook, so neither open rows nor the synthetic companion were visible. Mod unit tests alone did not catch this. UI implementation and final actual-host screenshot checks remain pending.

# Release

Final repository source proof: `source-panel-final/` (12/12, tabs/height/registered companion/inspector/page/display-only) and `source-queue-final/` (10/10) on Claude 2.1.311. Work Python tests 144 pass, mod tests 63 pass; Ruff and compile pass. Native engine content is an unsized sibling, never clipped or paged by Work. The earlier Native mode was removed because host validation rejects sizing ancestors of opaque native content. CI/Publish, registry identity/digests, fresh snapshot installed proof and local switch remain required; no new release claimed yet.

# Published acceptance

Published and locally installed `0.2.51-nightly.20261027-080148.ea59a61`; CI/Publish succeeded, exact tarball SHA1/SHA512 verified, fresh snapshot 460 files matched. Installed queue 10/10 and panel 12/12 actual CLI checks pass. See [release verification](../../releases/work-queue-panel-nightly/verification.md). Live 9505/9524 unchanged; no remote deployment.
