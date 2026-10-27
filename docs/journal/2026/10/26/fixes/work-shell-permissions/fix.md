---
type: Fix Verification
title: Native shell authority under Work
description: Work no longer denies Bash or PowerShell based on board planning or status availability.
tags: [work, shell, permissions]
timestamp: 2026-10-27T04:00:00Z
---
# Confirmed cause and change

Work tool.call previously denied shells when status was unavailable; tool.check denied unclassified or mutating shells while planning. Native bypass did not remove these independent mod decisions. Work now delegates Bash/PowerShell unchanged, without a Work-status RPC. It never manufactures a native allow verdict or changes permission/sandbox flags. Dedicated Edit/Write/NotebookEdit/apply_patch and work_execute approval/version/session protections remain.

Planning before side effects remains a workflow instruction. Since shells can modify files, Work is not an all-writes security boundary. Native policy can still deny or ask. This does not exit native Plan Mode or elevate privileges.

# Verification

- [Mod tests](evidence/mod-tests.txt): 50 pass. Both shell tools across planning/executing/disabled/offline/malformed, main/child and isReadOnly true/false/missing preserve native allow/ask/deny and perform no Work lookup. Existing exact plan-file, edit-denial and stale/forged approval regressions pass.
- [Launch tests](evidence/python-tests.txt): 11 pass. Launch flags are unchanged; no YOLO permission broadening.
- Actual CLI 2.1.310, isolated HOME/config/ports, loopback model and egress sentinel:
  - [Old installed bypass](evidence/baseline/result.json): both shell marker attempts blocked; no markers created while planning.
  - [Fixed bypass](evidence/bypass/result.json): Bash writes WORK-BASH-OK, Bash invokes actual powershell.exe which writes WORK-POWERSHELL-OK; Work remains planning and direct Write stays blocked. [PNG](evidence/bypass/01-shell-execution.png).
  - [Fixed default](evidence/default/result.json): same success with native default mode and explicit Bash/Work-status allow rules. [PNG](evidence/default/01-shell-execution.png).
- PowerShell is not exposed as a standalone native tool by this CLI build. Direct PowerShell hook behavior is covered by mod tests; actual PowerShell process execution is covered through Bash. No claim of standalone native PowerShell E2E.
- First default harness omitted the plugin's work_status native approval and legitimately paused at the native approval dialog. Adding the exact fixture Work-status allow rule made it pass; native restrictions were not removed from product code. First harness syntax error and two mock metadata/state assertion failures were fixed and not counted as successes.
- Every real run preserved live router 9505/9524 PID/fingerprint. No managed host or live install changed. These are source changes only; no commit, push, or deployment performed for this task.
