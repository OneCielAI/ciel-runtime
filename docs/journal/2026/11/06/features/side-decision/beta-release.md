---
type: release
title: Independent SSE beta delivery
description: Exact beta commit, workflow, registry and local-install evidence without live restarts.
tags: [beta, release, sse, work, verification]
timestamp: 2026-11-06
---

# Scope and authorization

The user requested end-to-end local delivery and changed the experimental feature's release channel to beta. Existing standing release authorization applies; peer reports are evidence, not approval. No live session/router restart is allowed.

# Commit and remote refs

- Feature commit: `3a719290c58a0db69ff7d3792e60d73c5b79b90f`.
- Atomic push updated `feature/work-parallel-20261103` and created `beta` at that commit.
- `main` unchanged: `5ed3b8d88afb57ba5091882b4be9ebc1b6f9dc7f`.
- `nightly` unchanged: `c00c8b670d6bf192fef674b3b9e21adcf98b9a62`.
- 988 committed source/test/workflow paths were compared against the final frozen export with line-ending normalization; no differences.
- The preexisting unrelated `tests/test_side_intake_inbox.py` edit remains unstaged/uncommitted.
- Local experiments, native request bodies, homes and keys are excluded. npm dry-run contains557files and excludes all journals and local scratch.

# Pre-publication verification

- Frozen Python3.11.6unit3319/50skip; router1529; channel604/80skip; runtime458/19skip passed.
- Final Python3.10.6unit3319/52skip passed after test-only Windows-environment repairs;58changed-fixture tests also passed on3.11.6.
- Actual native CLI with frozen release product: run5369022f, whole25item Work board, four full/recent × main/custom shadow proposals, one primary external send, no pending mail, answer before main end.
- Menu24rows, ctl26settings, main advice receipt reconciliation, retry confirmation and labeled profile screenshots are retained in [release validation](release-validation.md).
- Original failed gates remain recorded; no assertions were weakened. The safeguard correction isolates test poller lifetime only.

# Workflows

- [CI42272782433](https://github.com/OneCielAI/ciel-runtime/actions/runs/42272782433): completed success, including all four groups and Python-minimum job.
- [Publish42272782382](https://github.com/OneCielAI/ciel-runtime/actions/runs/42272782382): completed success, including npm test, beta-only publish and downloadable tarball verification.
- Both workflows report exact headSha3a719290c58a0db69ff7d3792e60d73c5b79b90f. Watcher `be8ogwnkr` exited0 after both terminal results.

# Registry and installation

Published beta: `0.2.51-beta.20261106-185033.3a71929`, gitHead3a719290c58a0db69ff7d3792e60d73c5b79b90f. Downloaded1,688,363-byte tarball SHA512 matches npm integrity;503source files match the commit, and separately stamped package/constants versions match the exact beta. `latest=0.2.51` and `nightly=0.2.51-nightly.20261106-020333.c00c8b6` are unchanged.

Extracted557files into a new immutable snapshot `C:/Users/djlov/.local/share/ciel-runtime-3a719290c58a0db69ff7d3792e60d73c5b79b90f`. Installed the verified exact archive globally only after checking no active runtime used that global package; package has no install lifecycle scripts. User-level future-launch home changed from c00c8b6snapshot to this new snapshot. Existing snapshots were not overwritten and no session/router was restarted.

Installed snapshot and global package match all557files. Six actual version/ctl list/get/set commands passed from a neutral owned configuration;26settings listed. Installed beta selects only the beta update spec and rejects stable/nightly candidates. Installed menu runebd69ef2 passed24rows. Actual installed native runf32d625b exposed a real persistence failure: primary processed/submitted and three shadow results persisted, but the fourth receipt replacement failed with Windows PermissionError5. The four-profile acceptance assertion failed. Therefore this first published beta is not marked fully accepted.

A real open-reader probe reproduced os.replace denial while preserving the old receipt. The correction retries only the already-fsynced local replacement, at most20attempts with2ms intervals, for Windows errors5/32. It does not retry MCP sends, change ACLs, delete the original or hide permanent failures. Permanent and non-Windows denial regressions verify old-file preservation and bounded/no retries. Real reader-contention regression passed100/100; side/persistence68tests passed on3.10and3.11; actual corrected native run35433734 settled all four profiles with one primary send. Full correction gates and a replacement beta publication remain pending.

Current live router health200 was confirmed for ports9487/9489/9505/9524 with PIDs95740/34792/29488/2664 before publication. Direct installed command checks also observed an existing Python3.14SyntaxWarning for return-in-finally in unchanged codex_app_server_session.py:417; both commands still returned the beta version. It is not the CP949reader exception and is not silently labelled fixed.

# Limitations carried into beta

Routed-only full/recent; provider count endpoint required; exact authorized API KEYS - SSE combo has no executable targets, so real cache/cost comparison is unavailable. No connected live Walkie reply contract, so recipient delivery is not claimed. Shadow result persistence is verified, but restart rescheduling of its in-memory queue is not. These are experimental boundaries, not silently completed requirements.
