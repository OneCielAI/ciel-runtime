# Change log

## 2026-11-07

- Found the Walkie urgency field (`thread_task.priority`), the aged-before-priority claim order, and stale-version decision refusals; reproduced the #116841 refusal on a board copy.
- `39f0c97`: P1 indexing and claim order, reserved P1 slot, current-board decision commits, P1 shared with main.
- `ac4789d`: refusal reasons stored on `side_failure.reason`.
- 352 focused tests passed; installed local `0.2.51-local.20261107-ac4789d`; replays on live-data copies pass. Live Walkie relaunch pending.
