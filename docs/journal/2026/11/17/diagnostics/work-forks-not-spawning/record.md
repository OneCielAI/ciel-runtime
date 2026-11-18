---
type: diagnosis
title: Work forks do not spawn on Celly and Andy, for two different reasons
description: Celly reserves Work items but never spawns a fork. The likely cause is workspace preparation failing on git's dubious-ownership check under its sandbox account, which is unconfirmed; three defects that leave runs reserved were reproduced. Andy's dispatcher finds no eligible item, because its P1/P5 leaves all wait and every runnable P2/P3 leaf already holds an open lease.
timestamp: 2026-11-17T18:20:00Z
tags: [work-parallel, fork, dispatch, celly, andy, workspace, waiting_on]
---

# Question (user, 2026-11-17)

> celly 세션이 보고한 바에 따르면.. 실행할 아이템이있어도 포크가 결함으로 생기지않는다고 보고했어 최신 트랜스크립트 확인해봐

In English: "Celly reports that forks are not created, as a defect, even when there are items to run. Check its latest transcript."

> 앤디세션도.. sse 는 포크가 잘되지만 실제 할일 워크보드의 포크는 전혀 안생겨남

In English: "In the Andy session too, side forks are created fine, but no Work board forks are ever created."

> 이정도 워크보드면.. 최소3개 포크세션은 동시에 돌고있어야해 · 각각의 프라이티별로...

In English: "With a Work board this size, at least 3 fork sessions should be running at the same time, for each priority."

Celly's own report was read in full (its sandbox notes, `work-fork-bind-defect-20261117.md`).

# Celly (read-only; [evidence/celly-observed.txt](evidence/celly-observed.txt))

**Confirmed**
- **No Work forks.** Both recent sessions spawned only side forks (233 + 31). There were 0 Work forks.
- **24 runs stuck reserved.** Celly's last status lists 24 coordination runs, all `reserved` with no `agent_id` or `bound_at`.
  - n964 was reserved 13 times, n954 6 times and n959 twice.
  - n969, n970 and n971 (root n916, effective P0) had `work-p0-*` names. No fork with those names exists.
- **No Work worktree exists.** The repository has no `work-parallel/*` branch and no `.claude/worktrees` directory.
- **Git refused the repository at 03:09Z.** Celly's own git call failed with "detected dubious ownership". The `.git` directory is owned by the machine owner, and the current user is the sandbox account. Celly then added `-c safe.directory='*'` to its own git calls. The runtime's git calls have no such setting, and system git config has none either.

**Reproduced** with workspace preparation failing on that exact git error ([evidence/celly-repro.txt](evidence/celly-repro.txt), [script](evidence/repro_workspace_dubious.py)):
- **P0 lane:** every attempt raises "P0 workspace preparation outcome unknown; recovery required". The lease and the run stay `reserved` and keep their slot. This matches n969/n970/n971 holding P1 3/3 until restart, and n969's completion being refused.
- **Ordinary lane:** the lease is released, but its coordination run stays `reserved`. The next pump reserves the same item again. Three attempts leave three runs, the same shape as n964's 13.

**Not confirmed**
- That Celly's own preparation failed with this error. Its router runs under the sandbox account, so the router process and its state directory could not be read from this account.
- The defects below are reproduced regardless of the cause.

**Defects found**
- A: a failed P0 preparation keeps its lease and run reserved.
- B: a failed ordinary preparation leaves its run reserved, and runs accumulate.
- C: the git failure text reaches no log, status or failure record. Celly found "telemetry files=[]" and no cause.

# Andy (read-only; [evidence/andy-dispatch.txt](evidence/andy-dispatch.txt))

These figures come from the dispatcher's own functions (`runnable_leaves`, `candidate`, the open-lease fence), run on copies of its board and ledger.
- **Work forks did spawn earlier.** The session has 201 Work forks; the last one started at 17:45Z.
- **Now nothing is eligible.** There are 7 runnable leaves, and all 7 already hold an open lease:
  - P2: 1 `running`, 1 `submitted`;
  - P3: 3 `waiting_input`, 2 `submitted`.
- **P1 has nothing runnable.** All 122 unfinished leaves are under `waiting_on`: 85 on the leaf itself, 32 under a waiting ancestor and 5 under a waiting root.
- **P5 has nothing runnable.** All 65 unfinished leaves wait.
- **Open leases fence their items:**
  - 42 `waiting_input` (a fork asked and waits for an answer);
  - 33 `submitted` (the result waits for main's review).
- **Slots are free.** 0 of 8 are in use, so capacity is not the limit.

# Answer

| Session | Why no Work fork | Defect |
|---|---|---|
| Celly | Items are reserved, but the fork is never spawned; workspace preparation likely fails (unconfirmed) | A, B, C |
| Andy | The dispatcher finds no eligible item | None found; the board's waits and open leases leave nothing to dispatch |

The requirement "at least 3 forks per priority" cannot be met on Andy's board under the current dispatch rules. Those rules exclude:
- a leaf under `waiting_on`;
- a root still in planning;
- an item already holding an open lease.

Nothing was changed.
