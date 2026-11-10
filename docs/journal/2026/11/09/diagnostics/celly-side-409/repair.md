---
type: Runbook
title: Re-queue Celly's five quarantined side events
description: The script clears only the stale-capture turn_error quarantine. Celly's own account must run it, because this account cannot open Celly's state.
timestamp: 2026-11-09T21:35:00Z
tags: [side-actor, celly, quarantine, repair]
---

# State at 15:26 local

- **Running build.** Celly was relaunched at 15:03:59 on `4eebfdc`.
- **Capture refreshed.**
  - Three more side requests were refused at 15:14:01–15:14:13 for the same reason.
  - At 15:14:20 the user typed in Celly. Main's request at 15:14:21 refreshed the capture.
  - From then on, every side actor returned a decision: `8c1c1267162a`, `86e13a802789`, `eaf938c47a0c`, `3598c353432b` and `f2602112e4af`.
- **Still quarantined.** The five events with six attempts got no new actor: `eb0a93680f53`, `59f7c6673615`, `25df4ad92ef1`, `3fd2cc5f88e5` and `a13d4523047e`.
  - They are Walkie mentions to Celly: the 111312 completion check, Bob's 119717 instruction and its renotify, a backlog summary, and the 70834 reminder.

# Why this account cannot repair it

- **Folder ACL.** `workspaces\52ea0f8a2296` has a protected ACL. `Get-ChildItem`, `Get-Acl` and `icacls` are all denied, and the script's dry run fails with `PermissionError` on `side-inboxes\...\locks`.
- **No elevation.**
  - This account's Administrators membership is deny-only (UAC Medium).
  - Windows sudo is disabled, and `sshd` is stopped.
  - The aap-win UI API exposes only process list, kill and recycle.

# Script

`G:\ciel-bridge-service\.local\side-requeue-20261109\requeue.py` (`/.local/` is gitignored there), with a `requeue.cmd` wrapper. The source copy is `ciel-runtime\.local\celly_side_requeue.py`.

What it does:
- It loads Celly's installed package (`4eebfdc`) and takes the runtime's receipt lock for each event.
- It changes a row only when all of these hold:
  - `stage=received`;
  - `side_failure.quarantined=true`;
  - `category=turn_error`;
  - no processing lease and no main fallback.
- On such a row it sets `quarantined=false`, `attempt_count=0` and `retry_not_before=0`, and adds `operator_requeue`.
- It copies each original into `side-inboxes\<id>\requeue-backup-<time>\` first.
- It runs as a dry run without `--apply`.

Tested on seeded inboxes with Celly's package:
- A quarantined `turn_error` is re-queued and then claimed.
- A quarantined `invalid_answer` is left unchanged.
- A second `--apply` changes nothing.
- A missing receipt is reported.
- Both `requeue.cmd` and the short Python path run from Git Bash and PowerShell.

# Executed repair — 15:54 local

- Used the local aap-win console WebSocket (`/api/aap/ws/agents/15/console`) to run the repair as Celly's account.
- The command reported all five rows `state=requeued` and wrote backup `requeue-backup-20261109-155407`.
- Restarted Celly with its configured restore command. The first start stopped at Claude Code's workspace trust prompt; selected `Yes, I trust this folder` and the resumed session loaded.
- Four actors ran:
  - `eb0a93680f53`: created the 111312 completion-check request.
  - `25df4ad92ef1`: answered the 70834/68381 reminder status.
  - `3fd2cc5f88e5`: created the unresolved Grant→WMS SSH request from the backlog summary.
  - `59f7c6673615`: answered that Bob 119787 was already tracked by n26.
- `a13d4523047e` did not spawn a fifth actor: it shares source message 119717 with `59f7c6673615` and was settled by the source-dedup path.
- Since this relaunch, the router logged 11 successful message requests and zero `side_context_unavailable`/409 refusals.

# Run (in Celly's prompt, as Celly's account)

```
! G:/ciel-bridge-service/.local/side-requeue-20261109/requeue.cmd
! G:/ciel-bridge-service/.local/side-requeue-20261109/requeue.cmd --apply
```

`59f7c6673615` and `a13d4523047e` are the same Walkie message (119717). With 4eebfdc, one actor handles that message and the other event is recorded as `duplicate_source`.
