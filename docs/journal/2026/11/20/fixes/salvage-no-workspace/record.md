---
type: fix
title: Host salvage of a fork reserved without a workspace releases its slot
description: A fork reserved with no workspace that ended without a report kept its P1 slot forever, because host_salvage refused every lease without a workspace. The salvage now records a receipt with workspace None.
timestamp: 2026-11-20T18:05:00Z
tags: [work, parallel, salvage, workspace, kobe]
---

# Report

Observed by the 17:35Z monitoring tick on KOBE ([log](/docs/journal/2026/11/20/operations/live-monitoring/log.md)):
- n964 (lease 1f7c4d9b, no workspace): the lease is `running` with a P1 slot. Its coordination run is `terminal`, and native is `failed` from 17:19:55Z.
- The fork transcript shows every turn from 17:16:11Z ending in `API Error: This request was blocked by our safety systems`. That includes the mod's four exit-contract prompts and main's two resumes. The fork never reported.
- Main tried `work_parallel_event` with `help`, `native_exit`, `stopped` and `cancel`. All four were refused "unsupported parallel event". TaskStop answered "not running (status: failed)".
- KOBE reserves every fork without a workspace, because its session folder is not a git repository.

# Cause

Confirmed in source (bd21399):
- `host_salvage` (`work_parallel_mcp.run`) refused any lease without a workspace, with "unknown salvage workspace".
- `parallelEnsureExitContract` in the mod swallows that refusal. A no-workspace fork that ends without a report therefore never becomes `unreported`, and its slot is never released.
- 1446629 (11-19) allowed leases without a workspace on the review path only.

# Change

- `host_salvage` accepts a lease with no workspace. It writes a `host_salvage` receipt with `workspace` None, the assigned todos as `remaining`, and the given reason as the blocker. The existing unreported path then releases the slot and keeps the item.
- A lease that has a workspace record but an empty one is still refused.
- `_validate_detail` accepts a None workspace only for `host_salvage` receipts. A worker's own handoff still needs a workspace.
- `_salvage_unavailable_reason` and the replay answer treat a None workspace as "no workspace", not "workspace unavailable".
- No model-facing text changed.

# Verification

[Evidence](/docs/journal/2026/11/20/fixes/salvage-no-workspace/evidence/).

- Fail-first (`fail-first.txt`): the new test, run on a bd21399 tree copy, ends with `WorkParallelError: unknown salvage workspace`.
- After the change: `test_work_parallel_mcp.py` passes on Python 3.10 in Docker (35 tests, 0 failures).
- Linux Docker, python:3.11-slim with pytest 9.1.1, archive of 0be8eec, the 90-file affected bundle: 1324 tests, 0 failures, 111 skipped ([evidence/bundle-linux.txt](/docs/journal/2026/11/20/fixes/salvage-no-workspace/evidence/bundle-linux.txt)).
- Windows, isolated config, same set minus the nine pytest-only suites (81 files): 1324 tests, 1 failure, 2 errors ([evidence/bundle-windows.txt](/docs/journal/2026/11/20/fixes/salvage-no-workspace/evidence/bundle-windows.txt)).
  - All three are subprocess timeouts during a machine stall (18:45–18:55Z, CPU 97%): a node harness after 240 s, a python child after 300 s, and a writer thread still alive.
  - Each passed alone afterwards: 0.05 s, 2.3 s and 6.6 s. The three files together were re-run in [Bundles](#bundles).

# Installed (not yet effective)

Two files were overlaid with hash checks and backups (`*.backup-hotfix10`). Every target held 2cb27cc's bytes first.
- Local: Andy home, default home and global npm ([install-local.txt](/docs/journal/2026/11/20/fixes/salvage-no-workspace/evidence/install-local.txt)).
- Celly ([install-celly.txt](/docs/journal/2026/11/20/fixes/salvage-no-workspace/evidence/install-celly.txt)).
- KOBE ([install-kobe.txt](/docs/journal/2026/11/20/fixes/salvage-no-workspace/evidence/install-kobe.txt)).

Hashes: `work_parallel_mcp.py` ACBBB30FE657D007, `work_parallel_receipts.py` 0342735E7FC5A596. These match the files in commit 0be8eec.

No process was restarted. The change applies at each router's next restart.

# Bundles

